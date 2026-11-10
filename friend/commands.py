#!/usr/bin/env python3
"""friend: describe an app, approve a spec, get independently tested implementations.

Start here:
  friend spec approve            lint the spec (spec/, next to friend.toml) and record what you approved
  friend generate                tests and code from the approved spec (plan → tests → build → interop)

More:
  friend spec lint | approve
  friend status · friend approve spec|plan|tests
  friend generate [--fresh] [--yes]

Global: --root DIR (default: nearest directory upwards with friend.toml), --json (machine output).
Exit codes: 0 ok · 1 failed · 2 blocked on spec questions · 3 refused (config, preflight, approval, busy)
            · 4 waiting for you (questions/approval) · 130 interrupted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import approvals  # noqa: E402
from config import ConfigError, load  # noqa: E402
from controller import Controller, config_hash, preflight  # noqa: E402
from runstate import Run  # noqa: E402
from speclib import lint  # noqa: E402

EXIT = {"passed": 0, "failed": 1, "blocked": 2, "awaiting_approval": 4}
QUIET_TOOLS = {"Read", "Glob", "Grep"}


def make_sink(as_json: bool, verbose: bool):
    def sink(ev: dict) -> None:
        if as_json:
            print(json.dumps(ev, ensure_ascii=False), flush=True)
        elif (line := human(ev, verbose)) is not None:
            print(line, flush=True)
    return sink


def human(ev: dict, verbose: bool) -> str | None:
    t, task = ev["type"], ev.get("task")
    p = f"[{task}] " if task else ""
    match t:
        case "run.started" | "run.resumed":
            v = ev["versions"]
            return (f"== {t.split('.')[1]} run {ev['run']} · spec {ev['spec_hash']} · model {v['model']} · {v['agent_cli']}"
                    + (" · auto-approve" if ev["auto_approve"] else ""))
        case "task.status":
            return f"{p}{ev['previous']} -> {ev['status']}" + (f"  ({ev['error']})" if ev.get("error") else "")
        case "step.started":
            if ev.get("role"):
                return f"{p}> {ev['role']} agent (session {ev['session_id'][:8]}{', resume' if ev.get('resume') else ''})"
            return f"{p}> {ev['step']}"
        case "step.finished":
            if ev.get("step") == "prepare":
                return f"{p}  {ev['detail']}"
            return f"{p}  agent finished: {ev['subtype']}, {ev['turns']} turns, ${ev['cost_usd']}"
        case "step.skipped":
            return f"{p}skipped: {ev['reason']}"
        case "agent.tool":
            return None if ev["tool"] in QUIET_TOOLS and not verbose else f"{p}  · {ev['tool']} {ev['input']}"
        case "agent.text":
            return f"{p}  “{ev['text'][:160]}”" if verbose else None
        case "files.changed":
            return f"{p}  {ev['path']}: +{len(ev['added'])} ~{len(ev['modified'])} -{len(ev['deleted'])}"
        case "test.case":
            first = ev["detail"].splitlines()[0][:140] if ev["detail"] else ""
            return f"{p}  {'✓' if ev['status'] == 'pass' else '✗'} {ev['name']} ({ev['duration_s']}s)" + (f"  — {first}" if first else "")
        case "test.summary":
            return f"{p}  checks: {ev['passed']} passed, {ev['failed']} failing"
        case "tests.report":
            return f"{p}  suite: {ev['tests']} tests · {ev['covered']}/{ev['must_rules']} MUST/SHOULD rules covered · {ev['untestable']} untestable · {ev['vacuous']} vacuous"
        case "gate.failed":
            return f"{p}  ✗ {ev['gate']}: {ev['detail'][:200]}"
        case "spec.questions":
            return "\n".join(f"{p}  ? [{'BLOCKING' if q.get('blocking') else 'note'}] {q.get('rule', '')} {q['question'][:200]}" for q in ev["questions"])
        case "policy.denied":
            return f"{p}  ⛔ {ev['tool']} {ev['input'][:80]}: {ev['reason'][:120]}"
        case "agent.retry" | "agent.session_lost":
            return f"{p}  ! {t}: {ev.get('reason', ev.get('session_id'))}"
        case "approval.required":
            return f"{p}awaiting approval: review {ev['path']}, then run `{ev['command']}` and `friend generate` again"
        case "approval":
            return f"{p}approved {ev['artifact']} ({ev['by']})"
        case "plan.written":
            return f"[plan] targets: {', '.join(ev['targets'])}"
        case "dispute.opened":
            return f"{p}  ⚖ dispute: {', '.join(ev['tests'])}"
        case "dispute.verdicts":
            return "\n".join(f"{p}  ⚖ {v['test']}: {v['verdict']} — {v['explanation'][:160]}" for v in ev["verdicts"])
        case "tests.amended":
            return f"[tests] amended after arbitration: {', '.join(ev['tests'])}; all targets will be re-checked"
        case "run.finished":
            return f"== run {ev['run']} {ev['status'].upper()} · ${ev['cost_usd']} · " + ", ".join(f"{k}={v}" for k, v in ev["tasks"].items())
        case "approval.rejected":
            return f"{p}rejected {ev['artifact']}: regenerating with your feedback"
    return None


def find_root(start: Path) -> Path:
    """Nearest directory at or above `start` containing friend.toml (like git finds .git); else `start`."""
    for d in [start, *start.parents]:
        if (d / "friend.toml").exists():
            return d
    return start


class Busy(Exception):
    pass


def acquire_lock(mf_dir: Path) -> Path:
    mf_dir.mkdir(exist_ok=True)
    lock = mf_dir / "lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(lock.read_text() or 0)
            os.kill(pid, 0)
            raise Busy(f"another friend generate is running (pid {pid})")
        except (ProcessLookupError, ValueError):
            lock.unlink(missing_ok=True)  # stale
            return acquire_lock(mf_dir)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def cmd_approve(args, cfg) -> int:
    if args.artifact == "spec":
        problems = lint(cfg.root, cfg.spec_dir)
        if problems:
            print("spec lint failed:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 3
    try:
        rec = approvals.approve(cfg, args.artifact, by="human")
    except approvals.ApprovalError as e:
        print(str(e), file=sys.stderr)
        return 3
    print(json.dumps(rec) if args.json else f"approved {args.artifact} {rec['hash'][:12]}")
    return 0


def cmd_generate(args, cfg) -> int:
    mf_dir = cfg.root / ".friend"
    sink = make_sink(args.json, args.verbose)
    try:
        spec_hash = approvals.spec_hash(cfg)
    except approvals.ApprovalError as e:
        print(str(e), file=sys.stderr)
        return 3
    versions, problems = preflight(cfg)
    if problems:
        print("preflight failed:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 3
    try:
        lock = acquire_lock(mf_dir)
    except Busy as e:
        print(str(e), file=sys.stderr)
        return 3
    try:
        chash = config_hash(cfg.root)
        run = None if args.fresh else Run.load(mf_dir, sink=sink)
        if run and run.unfinished() and run.state["spec_hash"] == spec_hash and run.state["config_hash"] == chash:
            run.state["resumed"] += 1
            run.state["status"] = "running"
            run.state["versions"] = versions
            run.save()
        else:
            run = Run.create(mf_dir, spec_hash, chash, versions, sink=sink)
        auto = args.auto_approve or cfg["workflow"]["auto_approve"]
        interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.json
        try:
            final = Controller(cfg, run, auto, reviewer=tty_reviewer if interactive else None).execute()
        except KeyboardInterrupt:
            run.state["status"] = "interrupted"
            run.save()
            run.emit("run.interrupted")
            print("\ninterrupted; state saved. Run `friend generate` again to resume.", file=sys.stderr)
            return 130
        return EXIT[final]
    finally:
        lock.unlink(missing_ok=True)


def tty_reviewer(kind: str, summary: str, detail: str) -> tuple[str, str]:
    print("\n" + summary)
    while True:
        try:
            a = input(f"{kind}: [a]pprove  [v]iew details  [r]eject with feedback  [q]uit > ").strip().lower()
        except EOFError:
            return "quit", ""
        if a in ("a", "approve", "y", "yes"):
            return "approve", ""
        if a.startswith("v"):
            print(detail)
        elif a.startswith("r"):
            fb = input("what should change? > ").strip()
            if fb:
                return "reject", fb
        elif a.startswith("q"):
            return "quit", ""


def cmd_status(args, cfg) -> int:
    run = Run.load(cfg.root / ".friend", args.run)
    arts = {k: approvals.status(cfg, k) for k in approvals.KINDS}
    if args.json:
        print(json.dumps({"run": run.state if run else None, "artifacts": {k: {"status": s, "reason": w} for k, (s, w) in arts.items()}}, indent=2))
        return 0
    for k, (s, why) in arts.items():
        print(f"{k:6} {s}" + (f"  ({why})" if why else ""))
    if run is None:
        print("no runs yet")
        return 0
    s = run.state
    print(f"\nrun {s['run_id']}: {s['status']} · spec {s['spec_hash'][:12]} · model {s.get('versions', {}).get('model')} · ${s['cost_usd']:.2f} · updated {s['updated']}")
    for name, t in s.get("tasks", {}).items():
        lc = t.get("last_checks")
        extra = f"  checks {lc.get('passed', '-')} passed / {lc['failed']} failing" if lc else ""
        extra += f"  repairs {t['repairs']}" if t.get("repairs") else ""
        print(f"  {name:20} {t['status']:18}{extra}" + (f"  error: {t['error']}" if t.get("error") else ""))
        for q in t.get("questions", []):
            print(f"  {'':20} ? {'BLOCKING ' if q.get('blocking') else ''}{q.get('rule', '')} {q['question'][:150]}")
    return 0


def cmd_spec(args, cfg) -> int:
    if args.spec_cmd == "lint":
        problems = lint(cfg.root, cfg.spec_dir)
        print("\n".join(problems) or "ok")
        return 1 if problems else 0
    if args.spec_cmd == "approve":
        args.artifact = "spec"
        return cmd_approve(args, cfg)
    return 3


def main() -> int:
    ap = argparse.ArgumentParser(prog="friend", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="project root (default: nearest directory upwards containing friend.toml)")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spec")
    ssub = sp.add_subparsers(dest="spec_cmd", required=True)
    sl = ssub.add_parser("lint")
    sa = ssub.add_parser("approve")
    a = sub.add_parser("approve")
    a.add_argument("artifact", nargs="?", default="spec", choices=approvals.KINDS)
    g = sub.add_parser("generate")
    g.add_argument("--fresh", action="store_true", help="start a new run (new sessions) instead of resuming")
    g.add_argument("--auto-approve", "--yes", "-y", action="store_true", help="approve generated plan/tests automatically (unattended runs)")
    g.add_argument("-v", "--verbose", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--run")
    for p in (a, g, s, sl, sa):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else find_root(Path.cwd())
    if not (root / "friend.toml").exists():
        print(f"no friend.toml in {root} or any parent directory", file=sys.stderr)
        return 3
    try:
        cfg = load(root)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 3
    return {"approve": cmd_approve, "generate": cmd_generate, "status": cmd_status, "spec": cmd_spec}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
