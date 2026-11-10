#!/usr/bin/env python3
"""friend: describe an app, get interviewed, approve a spec, get independently tested implementations.

Start here:
  friend                         same as `friend new`: tell it your idea, it interviews you, then builds it
  friend new                     describe your idea, answer questions → approved spec + sprint contract
  friend new --from spec.md      start from a spec you already wrote: friend splits it into its layout
  friend generate                tests and code from the approved spec (plan → tests → build → interop → audit)
  friend verify                  prove the sprint contract: every check re-run, no agent → .friend/proof.md
  friend run TARGET [args…]      run what was built

More:
  friend doctor                  check Claude Code login, toolchains, spec and approvals
  friend spec draft | refine "…" | check | contract | lint | approve
  friend questions · friend answer ID "decision" | --dismiss · friend answer all --recommended
  friend status · friend report · friend clean [--all] · friend approve spec|plan|tests · friend init
  friend generate [--fresh] [--rebuild] [--yes] [--budget USD] · friend verify [--no-holdout]

Global: --root DIR (default: nearest directory upwards with friend.toml), --json (machine output).
Exit codes: 0 ok · 1 failed · 2 blocked on spec questions · 3 refused (config, preflight, approval, busy)
            · 4 waiting for you (questions/approval) · 5 paused (budget) · 130 interrupted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import approvals  # noqa: E402
import checks  # noqa: E402
from config import ConfigError, load, load_plan  # noqa: E402
from controller import Controller, config_hash, preflight  # noqa: E402
import project  # noqa: E402
import specflow  # noqa: E402
from runstate import Run  # noqa: E402
from speclib import lint  # noqa: E402

EXIT = {"passed": 0, "failed": 1, "blocked": 2, "awaiting_approval": 4, "paused": 5}
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
        case "update.planned":
            what = " · ".join(f"{len(ev[k])} {k} ({', '.join(ev[k][:6])}{' …' if len(ev[k]) > 6 else ''})"
                              for k in ("changed", "added", "removed") if ev[k]) or "no rule changed"
            return (f"== spec changed since the last build: {what}\n== editing the existing tests and code "
                    f"({', '.join(ev['targets']) or 'no target can be kept'}); --rebuild starts from scratch")
        case "update.none":
            return None if ev["reason"] == "nothing built yet" else f"== full build: {ev['reason']}"
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
        case "audit.mutant":
            return f"{p}  mutant violating {ev['rule']}: {ev['outcome']}" + (f" by {', '.join(ev['killed_by'][:3])}" if ev.get("killed_by") else "")
        case "audit.summary":
            return f"[audit] kill rate {ev['kill_rate']:.0%} ({ev['killed']}/{ev['valid']})" + (f"; survived: {', '.join(ev['survived'])}" if ev["survived"] else "")
        case "run.finished":
            return f"== run {ev['run']} {ev['status'].upper()} · ${ev['cost_usd']} · " + ", ".join(f"{k}={v}" for k, v in ev["tasks"].items())
        case "approval.rejected":
            return f"{p}rejected {ev['artifact']}: regenerating with your feedback"
        case "budget.exceeded":
            return f"== paused: spent ${ev['spent']} of the ${ev['budget']} budget. Rerun with a higher --budget to continue"
        case "verify.result":
            return f"== verify {'OK' if ev['ok'] else 'FAILED'}"
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
            final = Controller(cfg, run, auto, reviewer=tty_reviewer if interactive else None, budget_usd=args.budget,
                               rebuild=args.rebuild).execute()
        except KeyboardInterrupt:
            run.state["status"] = "interrupted"
            run.save()
            run.emit("run.interrupted")
            print("\ninterrupted; state saved. Run `friend generate` again to resume.", file=sys.stderr)
            return 130
        finally:
            qs = [q for t in run.state["tasks"].values() for q in t.get("questions", [])]
            added = specflow.add_questions(cfg, qs, source=f"run {run.state['run_id']}")
            (mf_dir / "report.md").write_text(project.render_report(cfg, run))
            (cfg.root / "CONTEXT.md").write_text(project.render_context(cfg, run))
        if not args.json:
            print(f"report: {mf_dir / 'report.md'}" + (f" · {len(added)} new spec questions: `friend questions`" if added else ""))
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
        extra += f"  kill rate {t['kill_rate']:.0%}" if "kill_rate" in t else ""
        print(f"  {name:20} {t['status']:18}{extra}" + (f"  error: {t['error']}" if t.get("error") else ""))
        for q in t.get("questions", []):
            print(f"  {'':20} ? {'BLOCKING ' if q.get('blocking') else ''}{q.get('rule', '')} {q['question'][:150]}")
    return 0


def cmd_verify(args, cfg) -> int:
    """Prove the sprint contract: re-run every deterministic check (no agent) and write .friend/proof.md."""
    from contract import Check
    out = make_sink(args.json, False)
    ev = lambda type, **f: out({"type": type, "run": "verify", **f})  # noqa: E731
    say = (lambda msg: None) if args.json else (lambda msg: print(msg, flush=True))  # noqa: E731
    proof: list[Check] = []
    def add(c: Check) -> None:
        proof.append(c)
        ev("proof.check", name=c.name, status=c.status, detail=c.detail)

    arts = {k: approvals.status(cfg, k) for k in approvals.KINDS}
    bad = {k: v for k, v in arts.items() if v[0] != "approved"}
    plan = load_plan(cfg)
    data = approvals.load(cfg)
    spec_hash = (data.get("spec") or {}).get("hash", "")
    add(Check("Spec, plan and tests approved", "fail" if bad or plan is None else "pass",
              "; ".join(f"{k} {s}" + (f" ({w})" if w else "") for k, (s, w) in bad.items())
              or f"spec {spec_hash[:12]} · plan and tests approved against it"))
    if bad or plan is None:
        return finish_verify(args, cfg, spec_hash, [], proof, ev)
    tests_hash = data["tests"]["hash"]

    say("checking the test suite (coverage, and that every test fails against a do-nothing program) …")
    manifest = checks.read_tests_manifest(cfg) or {}
    untestable = manifest.get("untestable") or []
    gate_fails, rep = checks.test_gates(cfg, untestable)
    names = {f["name"] for f in gate_fails}
    if rep:
        add(Check("Every MUST/SHOULD rule has a test", "fail" if names & {"gate:coverage", "gate:unknown-rules", "gate:untestable-ratio"} else "pass",
                  f"{len(rep['covered'])}/{rep['must_rules']} covered by {rep['tests']} tests"
                  + (f" · {len(rep['untestable'])} declared untestable ({', '.join(rep['untestable'])})" if rep["untestable"] else "")
                  + (f" · uncovered: {', '.join(rep['uncovered'])}" if rep["uncovered"] else "")))
        add(Check("Tests have teeth (every test fails against a do-nothing program)",
                  "fail" if names & {"gate:vacuous", "gate:test-crash"} else "pass",
                  f"vacuous: {', '.join(rep['vacuous'])}" if rep["vacuous"] else f"{rep['tests']}/{rep['tests']} tests fail against the null program"))
    else:
        add(Check("Test suite imports", "fail", gate_fails[0]["detail"][:300] if gate_fails else "no tests"))
    untestable_map = {u["rule"]: u.get("reason", "") for u in untestable}

    suites = [None] + list(cfg["layout"]["holdout"] if not args.no_holdout else [])
    cases: dict[str, list[dict]] = {}
    def show_failures(label: str):
        def on_case(c: dict) -> None:
            if args.json:
                ev("test.case", task=label, **{k: v for k, v in c.items() if k != "type"})
            elif c["status"] != "pass":
                first = c["detail"].splitlines()[0][:140] if c.get("detail") else ""
                print(f"  ✗ {label}: {c['name']}" + (f" — {first}" if first else ""), flush=True)
        return on_case
    for name, t in plan["targets"].items():
        say(f"building and testing {name} …")
        fails = checks.build(cfg, t)
        add(Check(f"{name}: builds", "fail" if fails else "pass", fails[0]["detail"][:300] if fails else "ok"))
        if fails:
            continue
        for suite in suites:
            label = name + (f" · {suite}" if suite else "")
            res = checks.run_suite(cfg, ["--target", name], show_failures(label), suite=suite)
            cases[label] = res["cases"]
            total = res["passed"] + res["failed"]
            add(Check(f"{name}: {'holdout suite ' + suite if suite else 'generated suite'} passes",
                      "pass" if res["failed"] == 0 and total else "fail", f"{res['passed']}/{total} tests pass"))
        static = checks.static_gates(cfg, name, t, spec_hash) + checks.manifest_check(cfg, name, t, spec_hash, tests_hash)
        add(Check(f"{name}: generated from this spec, untouched by hand", "fail" if static else "pass",
                  "; ".join(f["detail"][:160] for f in static[:3]) or "headers, TRACE coverage and manifest match"))
    tests_listed, _ = checks.list_suite(cfg)
    if len(plan["targets"]) > 1:
        for suite in suites:
            listed = tests_listed if suite is None else (checks.list_suite(cfg, suite)[0] or [])
            if not any(t["interop"] for t in listed or []):
                continue
            say("testing the targets together (interop) …" if suite is None else f"interop · {suite} …")
            label = "interop" + (f" · {suite}" if suite else "")
            res = checks.run_suite(cfg, ["--interop", ",".join(plan["targets"])], show_failures(label), suite=suite)
            cases[label] = res["cases"]
            total = res["passed"] + res["failed"]
            add(Check(f"targets work together ({label})", "pass" if res["failed"] == 0 and total else "fail",
                      f"{res['passed']}/{total} mixed-target runs pass"))
    n_mut = cfg["workflow"]["audit_mutants"]
    run = Run.load(cfg.root / ".friend")
    audit = (run.state["tasks"].get("audit") if run and run.state.get("spec_hash") == spec_hash else None) or {}
    if not n_mut:
        add(Check("Mutation audit", "skipped", "disabled in friend.toml"))
    elif "kill_rate" in audit:
        need = cfg["workflow"]["audit_min_kill_rate"]
        add(Check("Mutation audit: tests catch deliberately broken code", "pass" if audit["kill_rate"] >= need else "fail",
                  f"kill rate {audit['kill_rate']:.0%} (needs {need:.0%}) in run {run.state['run_id']}"))
    else:
        add(Check("Mutation audit: tests catch deliberately broken code", "unproven",
                  "not measured for this spec; `friend generate` runs it"))
    import contract
    items, _ = contract.parse(cfg.root, cfg.spec_dir)
    return finish_verify(args, cfg, spec_hash, contract.judge_items(items, cases, untestable_map), proof, ev)


def finish_verify(args, cfg, spec_hash: str, items: list, proof: list, ev) -> int:
    import contract
    from config import agent_cli_version
    done = all(c.status in ("pass", "skipped") for c in [*items, *proof])
    for c in items:
        ev("contract.item", name=c.name, status=c.status, detail=c.detail)
    has_contract = contract.path(cfg.root, cfg.spec_dir).exists()
    header = [f"- spec `{spec_hash[:12]}` · model `{cfg['agent']['model']}` · {agent_cli_version(cfg) or 'agent CLI not found'}",
              f"- verified {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    if not has_contract:
        header.append("- no sprint contract (spec/DONE.md); only the proof checks apply. `friend spec contract` writes one.")
    text = contract.render(cfg.root.name, header, items, proof, done)
    (cfg.root / ".friend").mkdir(exist_ok=True)
    (cfg.root / ".friend" / "proof.md").write_text(text)
    if not args.json:
        print("\n" + text + f"\n(saved to .friend/proof.md)")
    ev("verify.result", ok=done)
    return 0 if done else 1


def cmd_clean(args, cfg) -> int:
    root = cfg.path(cfg.generated_root)
    if root.exists():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                shutil.rmtree(d)
                print(f"removed {d.relative_to(cfg.root)}/")
    if args.all:
        for kind in ("plan", "tests"):
            p = cfg.path(approvals.artifact_path(cfg, kind))
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            else:
                continue
            approvals.revoke(cfg, kind)
            print(f"removed {approvals.artifact_path(cfg, kind)} (approval revoked)")
    return 0


def cmd_init(args, root: Path) -> int:
    return project.init(root, args.name, args.describe, args.lang or [], install=not args.no_install, force=args.force)


def cmd_new(args) -> int:
    """Idea → interview → brief → spec + sprint contract → review questions → approval. Resumable at every step."""
    root = Path(args.root).resolve() if args.root else find_root(Path.cwd())
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.json
    sink = make_quiet_sink(steps=False)
    doc = project.document(args.source) if args.source else None
    if args.source and not doc:
        print(f"no such file: {args.source}", file=sys.stderr)
        return 3
    if doc and (root / "friend.toml").exists() and (root != Path.cwd().resolve() or any((root / "spec/core").glob("*.md"))):
        print(f"you are inside the friend project {root}; importing here would overwrite its spec.\n"
              f"run it from another folder: cd ~ && friend new --from {doc}", file=sys.stderr)
        return 3
    if not (root / "friend.toml").exists():
        idea = args.idea or ("" if doc else project.ask(
            "What do you want to build? Describe it in your own words (or drag in a spec file)") if interactive else "")
        doc = doc or project.document(idea)
        idea = doc.read_text() if doc else idea
        if not idea:
            print("describe your idea: friend new --idea \"...\" --lang python  (or: friend new --from spec.md)", file=sys.stderr)
            return 3
        args.idea = idea
        if args.root:
            root = Path(args.root).resolve()
        else:
            suggested = project.slug(project.title(idea) or doc.stem) if doc else project.slug(idea)
            name = args.name or project.ask("Folder name for it (press Enter to use the suggestion)", suggested) or suggested
            root = project.free_dir(Path.cwd(), project.slug(name))
            args.name = name
        made = not root.exists()
        root.mkdir(parents=True, exist_ok=True)
        import contextlib
        with contextlib.redirect_stdout(sys.stderr if args.json else sys.stdout):
            code = project.init(root, args.name, idea, args.lang or [], install=not args.no_install, force=False, show_next=False,
                                suggest=project.parse_languages(" ".join(re.findall(r"(?im)^#+.*platform.*$", idea))) if doc else None)
        if code:
            if made and not any(root.iterdir()):
                root.rmdir()
            return code
        if root != Path.cwd().resolve():
            print(f"your project lives in {root}")
    try:
        cfg = load(root)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 3
    from speclib import all_spec_files, parse_rules, platform_spec_files
    state = specflow.load_interview(cfg)
    if state is None:
        brief = cfg.path(cfg.spec_dir) / "BRIEF.md"
        idea = doc.read_text() if doc else args.idea or (brief.read_text().split("\n", 2)[-1].strip() if brief.exists() else "")
        if not idea or "(describe the application here" in idea:
            idea = project.ask("What do you want to build? Describe it in your own words") if interactive else ""
        if not idea:
            print("describe your idea: friend new --idea \"...\"", file=sys.stderr)
            return 3
        state = specflow.new_interview(idea)
        if doc:
            specflow.write_document_brief(cfg, cfg.raw.get("project", {}).get("name") or cfg.root.name, doc, state)
            print(f"read your spec ({len(idea.splitlines())} lines from {doc.name}); no interview needed")
        specflow.save_interview(cfg, state)
    langs = [Path(p).stem for p in platform_spec_files(cfg.root, cfg.spec_dir)]

    if state["status"] == "interviewing":
        if args.answer:
            specflow.answer_interview(state, dict(a.split("=", 1) for a in args.answer if "=" in a))
        if args.done:
            state["ready"] = True
        specflow.save_interview(cfg, state)
        while not state["ready"]:
            qs = specflow.open_interview_questions(state)
            if not qs:
                if len(state["rounds"]) >= args.max_rounds:
                    state["ready"] = True
                    break
                print("thinking about your idea …" if not state["rounds"] else "thinking about your answers …", flush=True,
                      file=sys.stderr if args.json else sys.stdout)
                if not specflow.interview_round(cfg, state, langs, sink):
                    print("the interviewer failed (see .friend/spec/runs); run `friend new` again to retry", file=sys.stderr)
                    return 1
                continue
            if not interactive:
                if args.json:
                    print(json.dumps({"status": "questions", "understanding": state["understanding"], "questions": qs}, indent=2))
                else:
                    print(state["understanding"] + "\n")
                    for q in qs:
                        print(f"{q['id']}. {q['question']}\n    " + "\n    ".join(f"{i}) {o}" for i, o in enumerate(q["options"], 1))
                              + (f"\n    recommended: {q['recommendation']}" if q.get("recommendation") else ""))
                    print('\nanswer with: friend new --answer I1=2 --answer I2="your words"   (or --done to stop asking)')
                return 4
            if len(state["rounds"]) == 1 and not any(q.get("answer") for q in state["rounds"][0]["questions"]):
                print("(answer in your own words; type 'done' any time and I'll write the spec with what I have)")
            if state.get("reply"):
                print("\n" + state["reply"])
            answers, stop = specflow.ask_interview(qs, input)
            specflow.answer_interview(state, answers)
            state["ready"] = stop
            specflow.save_interview(cfg, state)
        name = cfg.raw.get("project", {}).get("name") or cfg.root.name
        specflow.write_brief(cfg, name, state)
        state["status"] = "briefed"
        specflow.save_interview(cfg, state)
        print(f"\nhere is what I understood (saved to {cfg.spec_dir}/BRIEF.md):\n\n{state['understanding']}\n")

    if state["status"] == "briefed":
        print("writing the spec and the sprint contract …", flush=True)
        ok, out, problems = specflow.draft(cfg, sink)
        if show_spec_result(ok, out, problems, cfg, hint=False, brief=True):
            return 1
        state["status"] = "drafted"
        specflow.save_interview(cfg, state)

    while state["status"] in ("drafted", "reviewed"):
        if state["status"] == "drafted":
            print("checking the spec for gaps two builders could read differently …", flush=True)
            ok, found = specflow.review(cfg, sink, 6)
            added = specflow.add_questions(cfg, found, source="spec review") if ok else []
            if added and interactive:
                print(f"{len(added)} more question(s) to make the spec precise:")
                apply_decisions(cfg, specflow.interactive_answers(added, input), sink, hint=False)
            elif added:
                print(f"{len(added)} open spec question(s): `friend questions`, then `friend answer ID \"decision\"`")
            state["status"] = "reviewed"
            specflow.save_interview(cfg, state)
        rules = parse_rules(cfg.root, all_spec_files(cfg.root, cfg.spec_dir))
        import contract
        items, _ = contract.parse(cfg.root, cfg.spec_dir)
        levels = {lv: sum(r.level == lv for r in rules) for lv in ("MUST", "SHOULD", "MAY")}
        print(f"\nspec: {len(rules)} rules ({levels['MUST']} MUST, {levels['SHOULD']} SHOULD, {levels['MAY']} MAY) in {cfg.spec_dir}/")
        if items:
            print(f"sprint contract ({cfg.spec_dir}/DONE.md), done means:")
            for it in items:
                print(f"  [ ] {it.id} {it.text}")
        problems = lint(cfg.root, cfg.spec_dir)
        if problems:
            print("spec lint failed:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        if not interactive:
            print("\nreview spec/ and spec/DONE.md, then: friend spec approve && friend generate")
            return 4
        a = input("\napprove the spec and contract? [y]es · [a]sk me more · [e]dit it myself > ").strip().lower()
        if a.startswith("a"):
            state["status"] = "drafted"
            continue
        if not a.startswith("y"):
            print(f"edit {cfg.spec_dir}/ (or `friend spec refine \"...\"`), then `friend spec approve` and `friend generate`")
            return 0
        rec = approvals.approve(cfg, "spec", by="human")
        state["status"] = "approved"
        specflow.save_interview(cfg, state)
        print(f"approved spec {rec['hash'][:12]}")
    if state["status"] != "approved":
        return 0
    here = cfg.root if cfg.root == Path.cwd().resolve() else None
    cd = "" if here else f"cd {os.path.relpath(cfg.root)} && "
    if interactive and input("\nbuild it now? this takes a while (20-40 min) [Y/n] > ").strip().lower() in ("", "y", "yes"):
        code = cmd_generate(argparse.Namespace(json=False, verbose=False, fresh=False, auto_approve=True, budget=None), cfg)
        if code == 0:
            code = cmd_verify(argparse.Namespace(json=False, no_holdout=False), cfg)
        if code == 0:
            targets = sorted((load_plan(cfg) or {}).get("targets", {})) or ["TARGET"]
            print(f"\ntry it: {cd}friend run {targets[0]} <arguments>   (built: {', '.join(targets)})")
        else:
            print(f"\nnot done yet; `{cd}friend generate` resumes where it stopped")
        return code
    print(f"\nnext: {cd}friend generate   (builds tests and code; then `friend verify` proves the contract)")
    return 0


def cmd_doctor(args, cfg) -> int:
    rows = project.doctor(cfg)
    if args.json:
        print(json.dumps([{"check": c, "ok": ok, "detail": d} for c, ok, d in rows], indent=2))
    else:
        for c, ok, d in rows:
            print(f"{'✓' if ok else '✗'} {c:18} {d}")
    hard = [c for c, ok, _ in rows if not ok and not c.endswith("approval")]
    return 0 if not hard else 1


def cmd_run(args, cfg) -> int:
    return project.run_target(cfg, args.target, args.args)


def cmd_report(args, cfg) -> int:
    run = Run.load(cfg.root / ".friend", args.run)
    if run is None:
        print("no runs yet", file=sys.stderr)
        return 1
    text = project.render_report(cfg, run)
    (cfg.root / ".friend" / "report.md").write_text(text)
    print(text)
    return 0


def show_spec_result(ok: bool, out: dict, problems: list[str], cfg, hint: bool = True, brief: bool = False) -> int:
    if brief:  # friend new: the author's assumptions are recorded, not asked; the reviewer's questions are the quiz
        added = specflow.add_questions(cfg, out.get("spec_questions") or [], source="spec author", status="assumed")
        if out.get("files_written"):
            print("wrote " + ", ".join(out["files_written"]))
        if added:
            print(f"{len(added)} assumption(s) made where you hadn't decided (see `friend questions --all`)")
        if not ok:
            print("spec still has problems:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        return 0
    if out.get("summary"):
        print(out["summary"])
    if out.get("files_written"):
        print("files: " + ", ".join(out["files_written"]))
    added = specflow.add_questions(cfg, out.get("spec_questions") or [], source="spec author", status="assumed")
    for q in added:
        tag = "BLOCKING, answer with `friend answer`" if q["status"] == "open" else "assumed"
        print(f"  {q['id']} [{', '.join(q['rules']) or '-'}] {q['question']}" + (f"  ({tag}: {q['recommendation']})" if q["recommendation"] else f"  ({tag})"))
    if not ok:
        print("spec still has problems:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    if hint:
        print("\nreview the changes in spec/, then: friend spec check (find ambiguities) · friend spec approve")
    return 0


def cmd_spec(args, cfg) -> int:
    sink = make_sink(args.json, getattr(args, "verbose", False)) if args.json or getattr(args, "verbose", False) else make_quiet_sink()
    if args.spec_cmd == "lint":
        problems = lint(cfg.root, cfg.spec_dir)
        print("\n".join(problems) or "ok")
        return 1 if problems else 0
    if args.spec_cmd == "approve":
        args.artifact = "spec"
        return cmd_approve(args, cfg)
    if args.spec_cmd == "draft":
        brief = cfg.path(cfg.spec_dir) / "BRIEF.md"
        if not brief.exists() or "(describe the application here" in brief.read_text():
            print(f"write what you want to build in {brief.relative_to(cfg.root)} first", file=sys.stderr)
            return 3
        if any((cfg.path(cfg.spec_dir) / "core").glob("*.md")) and not args.force:
            print("spec/core/ already has files; use `friend spec refine \"...\"` to change it (or --force to redraft)", file=sys.stderr)
            return 3
        print("drafting the spec from BRIEF.md …")
        return show_spec_result(*specflow.draft(cfg, sink), cfg)
    if args.spec_cmd == "refine":
        print("revising the spec …")
        return show_spec_result(*specflow.refine(cfg, args.instruction, sink), cfg)
    if args.spec_cmd == "contract":
        print("writing the sprint contract (spec/DONE.md) …")
        return show_spec_result(*specflow.contract(cfg, sink), cfg)
    if args.spec_cmd == "check":
        problems = lint(cfg.root, cfg.spec_dir)
        if problems:
            print("spec lint failed:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        print("reviewing the spec for ambiguities …")
        ok, found = specflow.review(cfg, sink, args.max)
        if not ok:
            print("review failed (see .friend/spec/runs)", file=sys.stderr)
            return 1
        added = specflow.add_questions(cfg, found, source="spec review")
        if not added:
            print("no new questions")
            return 0
        if args.json or not sys.stdin.isatty():
            for q in added:
                print(f"{q['id']} [{', '.join(q['rules']) or '-'}] {q['question']}")
            print("answer with `friend answer ID \"decision\"`")
            return 0
        decided = specflow.interactive_answers(added, input)
        return apply_decisions(cfg, decided, sink)
    return 3


def apply_decisions(cfg, decided: dict, sink, hint: bool = True) -> int:
    if not decided:
        return 0
    specflow.set_status(cfg, decided)
    answered = [q for q in specflow.load_questions(cfg) if q["id"] in decided and decided[q["id"]][0] == "answered"]
    if not answered:
        return 0
    print(f"\nwriting {len(answered)} decision(s) into the spec …")
    ok, out, problems = specflow.apply_answers(cfg, answered, sink)
    return show_spec_result(ok, out, problems, cfg, hint=hint)


def cmd_questions(args, cfg) -> int:
    qs = [q for q in specflow.load_questions(cfg) if args.all or q["status"] == "open"]
    if args.json:
        print(json.dumps(qs, indent=2))
        return 0
    if not qs:
        assumed = sum(q["status"] == "assumed" for q in specflow.load_questions(cfg))
        print("no open questions" + (f" ({assumed} assumptions recorded: `friend questions --all`)" if assumed else ""))
    for q in qs:
        print(f"{q['id']} [{q['status']}] [{', '.join(q['rules']) or '-'}] {q['question']}")
        for i, o in enumerate(q.get("options") or [], 1):
            print(f"     {i}) {o}")
        if q.get("recommendation"):
            print(f"     recommended: {q['recommendation']}")
        if q.get("answer"):
            print(f"     answer: {q['answer']}")
    return 0


def cmd_answer(args, cfg) -> int:
    qs = {q["id"]: q for q in specflow.load_questions(cfg)}
    if args.id == "all":
        if not args.recommended:
            print("`friend answer all` needs --recommended (accept every open question's recommendation)", file=sys.stderr)
            return 3
        decided = {i: ("answered", q["recommendation"]) for i, q in qs.items() if q["status"] == "open" and q.get("recommendation")}
        print(f"accepting the recommendation for {len(decided)} open question(s)")
        return apply_decisions(cfg, decided, make_quiet_sink())
    if args.id not in qs:
        print(f"unknown question {args.id}", file=sys.stderr)
        return 3
    if args.dismiss:
        return apply_decisions(cfg, {args.id: ("dismissed", None)}, make_quiet_sink())
    text = args.decision
    opts = qs[args.id].get("options") or []
    if text and text.isdigit() and 1 <= int(text) <= len(opts):
        text = opts[int(text) - 1]
    if not text:
        print("give a decision (text or option number) or --dismiss", file=sys.stderr)
        return 3
    return apply_decisions(cfg, {args.id: ("answered", text)}, make_quiet_sink())


def make_quiet_sink(steps: bool = True):
    shown = {"gate.failed", "policy.denied", "agent.retry"} | ({"step.started", "step.finished"} if steps else set())

    def sink(ev: dict) -> None:
        if ev["type"] in shown:
            line = human(ev, False)
            if line:
                print(line, flush=True)
    return sink


def main() -> int:
    ap = argparse.ArgumentParser(prog="friend", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="project root (default: nearest directory upwards containing friend.toml)")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("init", help="scaffold a project in the current directory")
    i.add_argument("--name")
    i.add_argument("--describe", help="what you are building (written to spec/BRIEF.md)")
    i.add_argument("--lang", action="append", help="target language (repeatable): python, typescript, javascript")
    i.add_argument("--no-install", action="store_true", help="don't run npm for the TypeScript compiler")
    i.add_argument("--force", action="store_true")
    nw = sub.add_parser("new", aliases=["idea"], help="start here: describe your idea, answer questions, get an approved spec + contract")
    nw.add_argument("--idea", help="what you want to build (asked interactively if omitted)")
    nw.add_argument("--from", dest="source", metavar="FILE", help="start from a spec you already wrote (skips the interview)")
    nw.add_argument("--name")
    nw.add_argument("--lang", action="append", help="target language (repeatable): python, typescript, javascript")
    nw.add_argument("--answer", action="append", default=[], help="non-interactive: ID=answer (option number or text)")
    nw.add_argument("--done", action="store_true", help="stop the interview and write the spec now")
    nw.add_argument("--max-rounds", type=int, default=6)
    nw.add_argument("--no-install", action="store_true")
    d = sub.add_parser("doctor")
    sp = sub.add_parser("spec")
    ssub = sp.add_subparsers(dest="spec_cmd", required=True)
    sd = ssub.add_parser("draft")
    sd.add_argument("--force", action="store_true")
    sr = ssub.add_parser("refine")
    sr.add_argument("instruction")
    sc = ssub.add_parser("check")
    sc.add_argument("--max", type=int, default=8)
    sl = ssub.add_parser("lint")
    sk = ssub.add_parser("contract", help="write spec/DONE.md (the sprint contract) for an existing spec")
    sa = ssub.add_parser("approve")
    q = sub.add_parser("questions")
    q.add_argument("--all", action="store_true")
    an = sub.add_parser("answer")
    an.add_argument("id")
    an.add_argument("decision", nargs="?")
    an.add_argument("--dismiss", action="store_true")
    an.add_argument("--recommended", action="store_true", help="with ID 'all': accept every recommendation")
    rn = sub.add_parser("run")
    rn.add_argument("target")
    rn.add_argument("args", nargs=argparse.REMAINDER)
    rp = sub.add_parser("report")
    rp.add_argument("--run")
    a = sub.add_parser("approve")
    a.add_argument("artifact", nargs="?", default="spec", choices=approvals.KINDS)
    g = sub.add_parser("generate")
    g.add_argument("--fresh", action="store_true", help="start a new run (new sessions) instead of resuming")
    g.add_argument("--auto-approve", "--yes", "-y", action="store_true", help="approve generated plan/tests automatically (unattended runs)")
    g.add_argument("--budget", type=float, default=None, help="pause the run once total agent spend reaches this many USD")
    g.add_argument("--rebuild", action="store_true", help="rebuild tests and code from scratch instead of editing them for a spec change")
    g.add_argument("-v", "--verbose", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--run")
    v = sub.add_parser("verify")
    v.add_argument("--no-holdout", action="store_true", help="skip the hand-written suites in [layout].holdout")
    c = sub.add_parser("clean")
    c.add_argument("--all", action="store_true")
    for p in (a, g, s, v, c, i, d, sd, sr, sc, sl, sk, sa, q, an, rn, rp, nw):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args(sys.argv[1:] or ["new"])
    if args.cmd == "init":
        return cmd_init(args, Path(args.root).resolve() if args.root else Path.cwd())
    if args.cmd in ("new", "idea"):
        return cmd_new(args)
    root = Path(args.root).resolve() if args.root else find_root(Path.cwd())
    if not (root / "friend.toml").exists():
        print(f"no friend.toml in {root} or any parent directory. Start a project with `friend init`.", file=sys.stderr)
        return 3
    try:
        cfg = load(root)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 3
    return {"approve": cmd_approve, "generate": cmd_generate, "status": cmd_status, "verify": cmd_verify,
            "clean": cmd_clean, "doctor": cmd_doctor, "spec": cmd_spec, "questions": cmd_questions,
            "answer": cmd_answer, "run": cmd_run, "report": cmd_report}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
