#!/usr/bin/env python3
"""Solo baseline: can one agent session, given only the spec, match what the full pipeline produces?

Each run copies the spec, the hand-written server and the toolchain into a fresh workspace, gives ONE
Claude Code session the spec (same model, same per-step budget, same policy hook and sandbox flags as
a builder, but no planner, no independent tester, no repair loop, no arbiter, no audit), then scores
whatever it produced on suites the agent never saw:

  holdout   tests/conformance  (hand-written, never shown to any agent)
  generated tests/generated    (the approved suite from the full pipeline)

  python3 evals/solo_baseline.py --runs 3 --work /tmp/solo [--project examples/messaging]

The project must have been generated once (`friend generate`), because the approved plan (launch commands)
and generated suite are used for scoring. The harness is snapshotted first, so the repository can change
while runs are in progress.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--work", required=True, help="scratch directory for snapshots and workspaces")
    ap.add_argument("--project", default=str(REPO / "examples" / "messaging"))
    ap.add_argument("--out", default=str(REPO / "evals" / "results"))
    args = ap.parse_args()
    project = Path(args.project).resolve()
    work, out = Path(args.work).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    kit = work / "harness"
    if not kit.exists():
        shutil.copytree(REPO / "friend", kit, ignore=shutil.ignore_patterns("__pycache__", "selftest"))
        for p in ("tests/conformance", "tests/generated"):
            shutil.copytree(project / p, work / "hidden" / p, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(project / "plan.json", work / "hidden" / "plan.json")
    sys.path.insert(0, str(kit))
    import agent, checks, roles  # noqa: E402
    from config import expand, load  # noqa: E402
    from controller import prompt  # noqa: E402

    for n in range(1, args.runs + 1):
        ws = work / f"run-{n}"
        if (out / f"solo-{n}.json").exists():
            print(f"run {n}: already scored, skipping", flush=True)
            continue
        if ws.exists():
            shutil.rmtree(ws)
        ws.mkdir(parents=True)
        for p in ("spec", "server"):
            shutil.copytree(project / p, ws / p, ignore=shutil.ignore_patterns("__pycache__"))
        (ws / "tools").mkdir()
        (ws / "tools" / "node_modules").symlink_to(project / "tools" / "node_modules")
        toml = (project / "friend.toml").read_text().replace('holdout = ["tests/conformance"]', "holdout = []")
        (ws / "friend.toml").write_text(toml)
        (ws / "clients").mkdir()
        cfg = load(ws)
        run_dir = ws / ".friend" / "solo"
        (run_dir / "agent").mkdir(parents=True)

        role = roles.Role("builder", "solo", roles.WRITER, write=["clients"], read=["spec", "server"],
                          execs=["python3 clients/", "python3 server/server.py", "node clients/",
                                 "tools/node_modules/.bin/tsc"])
        system = prompt("common") + "\n\nRole: **solo implementer**. You implement every target in the spec yourself."
        events = open(run_dir / "events.jsonl", "a")
        def on_event(type: str, **f) -> None:
            events.write(json.dumps({"type": type, **f}) + "\n")
            events.flush()
        t0 = time.time()
        print(f"run {n}: agent working in {ws}", flush=True)
        res = agent.invoke(cfg, role, run_dir, str(uuid.uuid4()), False, (REPO / "evals" / "solo_prompt.md").read_text(),
                           system, "solo", on_event)
        agent_s = time.time() - t0
        print(f"run {n}: agent finished ok={res.ok} cost=${res.cost_usd:.2f} turns={res.turns} in {agent_s/60:.1f} min", flush=True)

        # Scoring: reveal the hidden suites and the approved plan only now.
        shutil.copytree(work / "hidden" / "tests", ws / "tests")
        shutil.copy(work / "hidden" / "plan.json", ws / "plan.json")
        plan = json.loads((ws / "plan.json").read_text())
        result = {"run": n, "agent_ok": res.ok, "subtype": res.subtype, "cost_usd": res.cost_usd, "turns": res.turns,
                  "agent_minutes": round(agent_s / 60, 1), "model": res.model, "scores": {}}
        for suite in ("tests/conformance", "tests/generated"):
            for name, t in plan["targets"].items():
                fails = checks.build(cfg, t)
                key = f"{suite.split('/')[1]}:{name}"
                if fails:
                    result["scores"][key] = {"passed": 0, "failed": None, "build_error": fails[0]["detail"][-500:]}
                    continue
                r = checks.run_suite(cfg, ["--target", name], suite=suite, timeout=1800)
                result["scores"][key] = {"passed": r["passed"], "failed": r["failed"],
                                         "failing": sorted({f.get("test", f["name"]) for f in r["failures"]})}
            r = checks.run_suite(cfg, ["--interop", ",".join(plan["targets"])], suite=suite, timeout=1800)
            result["scores"][f"{suite.split('/')[1]}:interop"] = {"passed": r["passed"], "failed": r["failed"]}
        (out / f"solo-{n}.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"run {n}: " + ", ".join(f"{k} {v['passed']}/{(v['passed'] or 0) + (v['failed'] or 0)}"
                                       for k, v in result["scores"].items()), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
