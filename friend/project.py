"""Project-level commands: init (scaffold), doctor (environment check), run (launch a target), report."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_GENERATED = "app"

# Languages friend can target out of the box. Adding one = a platform template + an entry here.
LANGUAGES = {
    "python": {"template": "python.md", "needs": ["python3"],
               "toolchain": {"python": {"cmd": "python3"}}},
    "typescript": {"template": "typescript.md", "needs": ["node", "npm"], "npm_tools": True,
                   "toolchain": {"node": {"cmd": "node"},
                                 "tsc": {"cmd": "tools/node_modules/.bin/tsc", "install": "cd tools && npm ci"}}},
    "javascript": {"template": "javascript.md", "needs": ["node"],
                   "toolchain": {"node": {"cmd": "node"}}},
}

FRIEND_TOML = """# friend configuration for {name}. Hand-edited; hashed into every run.
# Targets are not listed here: the planner derives them from spec/platforms/ into plan.json.

[project]
name = "{name}"

[layout]
spec = "spec"
generated = "{generated}"            # each target is generated into {generated}/<target>/
tests = "tests/generated"
plan = "plan.json"
approvals = "approvals.json"
holdout = []                       # hand-written suites never shown to agents (`friend verify --holdout`)

[agent]
command = "claude"
model = "{model}"          # pinned; recorded in every manifest. Upgrade deliberately, then re-verify.
max_budget_usd = 20.0              # per agent invocation
step_timeout_s = 2400

[workflow]
max_repairs = 3
max_test_rounds = 3
max_disputes = 2
max_untestable_ratio = 0.25
audit_mutants = 4                  # mutation audit per target (0 disables)
audit_min_kill_rate = 0.5

{toolchain}
# Hand-written components targets work with (e.g. a server) go here; tests start them with ctx.service(name):
# [services.server]
# launch = ["python3", "server/server.py", "--port", "0"]
# ready = "LISTENING (\\\\d+)"
# describe = "what it is and how tests use it"
"""


def ask(prompt: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    try:
        ans = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return ans or default


def detect() -> dict[str, bool]:
    return {lang: all(shutil.which(n) for n in spec["needs"]) for lang, spec in LANGUAGES.items()}


def init(root: Path, name: str | None, describe: str | None, langs: list[str], install: bool, force: bool,
         show_next: bool = True) -> int:
    if (root / "friend.toml").exists() and not force:
        print(f"{root}/friend.toml already exists (use --force to overwrite the scaffold)", file=sys.stderr)
        return 3
    available = detect()
    name = name or ask("Project name", root.name) or root.name
    if describe is None:
        describe = ask("What are you building? (one or two sentences; used to draft the spec)", "")
    if not langs:
        default = "python" if available["python"] else next((l for l, ok in available.items() if ok), "python")
        langs = [l.strip() for l in ask(f"Target languages, comma-separated ({', '.join(LANGUAGES)})", default).split(",") if l.strip()]
    bad = [l for l in langs if l not in LANGUAGES]
    if bad or not langs:
        print(f"unknown or no languages {bad or langs}; choose from {sorted(LANGUAGES)}", file=sys.stderr)
        return 3
    missing = [l for l in langs if not available[l]]
    if missing:
        print(f"warning: toolchain not found for {missing} (needs {[LANGUAGES[l]['needs'] for l in missing]}); "
              "generation will refuse to start until it is installed", file=sys.stderr)

    toolchain: dict[str, dict] = {}
    for l in langs:
        toolchain.update(LANGUAGES[l]["toolchain"])
    tc = "\n".join(f'[toolchain.{n}]\ncmd = "{t["cmd"]}"' + (f'\ninstall = "{t["install"]}"' if t.get("install") else "") + "\n"
                   for n, t in toolchain.items())
    (root / "spec/core").mkdir(parents=True, exist_ok=True)
    (root / "spec/platforms").mkdir(parents=True, exist_ok=True)
    (root / "spec/extensions").mkdir(parents=True, exist_ok=True)
    (root / "friend.toml").write_text(FRIEND_TOML.format(name=name, generated=DEFAULT_GENERATED, model=DEFAULT_MODEL, toolchain=tc))
    (root / "spec/README.md").write_text((TEMPLATES / "spec_README.md").read_text())
    (root / "spec/extensions/README.md").write_text((TEMPLATES / "extensions_README.md").read_text())
    (root / "spec/BRIEF.md").write_text(f"# Brief: {name}\n\n{describe or '(describe the application here, then run `friend spec draft`)'}\n")
    for l in langs:
        text = (TEMPLATES / "platforms" / LANGUAGES[l]["template"]).read_text().replace("{generated}", DEFAULT_GENERATED)
        (root / "spec/platforms" / LANGUAGES[l]["template"]).write_text(text)
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    gi.write_text("\n".join(lines + [x for x in (".friend/", "__pycache__/", "tools/node_modules/") if x not in lines]) + "\n")
    print(f"created friend.toml, spec/ ({', '.join(langs)}), .gitignore in {root}")
    if any(LANGUAGES[l].get("npm_tools") for l in langs):
        (root / "tools").mkdir(exist_ok=True)
        shutil.copy(TEMPLATES / "tools_package.json", root / "tools/package.json")
        if install and shutil.which("npm"):
            print("installing the pinned TypeScript compiler into tools/ …")
            r = subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=root / "tools", capture_output=True, text=True)
            print("  ok" if r.returncode == 0 else f"  npm failed:\n{r.stderr[-800:]}")
        else:
            print("run `cd tools && npm install` to install the pinned TypeScript compiler")
    if show_next:
        print("\nnext:\n  friend spec draft      # an agent drafts spec/core/ from spec/BRIEF.md (or write it yourself)\n"
              "  friend spec check      # find ambiguities and answer them\n  friend spec approve\n  friend generate")
    return 0


def doctor(cfg) -> list[tuple[str, bool, str]]:
    """(check, ok, detail) rows."""
    import approvals
    from config import agent_cli_version, probe_toolchain
    from speclib import lint
    rows = []
    cli = agent_cli_version(cfg)
    rows.append(("Claude Code CLI", bool(cli), cli or f"{cfg['agent']['command']!r} not on PATH; install Claude Code"))
    if cli:
        try:
            st = json.loads(subprocess.run([cfg["agent"]["command"], "auth", "status"], capture_output=True, text=True, timeout=30).stdout)
            rows.append(("Claude login", bool(st.get("loggedIn")), st.get("authMethod", "") if st.get("loggedIn") else "run `claude auth login`"))
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            rows.append(("Claude login", False, f"could not check: {e}"))
    versions, problems = probe_toolchain(cfg)
    for n, v in versions.items():
        rows.append((f"toolchain {n}", True, v))
    for p in problems:
        rows.append(("toolchain", False, p))
    lint_problems = lint(cfg.root, cfg.spec_dir)
    rows.append(("spec lint", not lint_problems, "; ".join(lint_problems[:3]) or "ok"))
    for k in approvals.KINDS:
        st, why = approvals.status(cfg, k)
        rows.append((f"{k} approval", st == "approved", st + (f" ({why})" if why else "")))
    return rows


def run_target(cfg, target: str, args: list[str]) -> int:
    import checks
    from config import expand, load_plan
    plan = load_plan(cfg)
    if not plan or target not in plan["targets"]:
        print(f"unknown target {target!r}; generated targets: {sorted((plan or {}).get('targets', {}))}", file=sys.stderr)
        return 3
    t = plan["targets"][target]
    fails = checks.build(cfg, t)
    if fails:
        print(fails[0]["detail"], file=sys.stderr)
        return 1
    argv = [str(cfg.root / a) if (cfg.root / a).exists() else a for a in expand(t["launch"], t["dir"])] + args
    os.execvp(argv[0], argv)


def render_report(cfg, run) -> str:
    """Markdown summary of a run, for humans (also written to .friend/report.md after every run)."""
    s = run.state
    ev = [json.loads(l) for l in (run.dir / "events.jsonl").read_text().splitlines()] if (run.dir / "events.jsonl").exists() else []
    out = [f"# friend run {s['run_id']}: {s['status']}", "",
           f"- spec `{s['spec_hash'][:12]}` · model `{s['versions'].get('model')}` · {s['versions'].get('agent_cli')} · cost ${s['cost_usd']:.2f}",
           f"- started {s['created']}, updated {s['updated']}", "", "| task | status | details |", "|---|---|---|"]
    for name, t in s["tasks"].items():
        lc = t.get("last_checks") or {}
        det = []
        if "passed" in lc:
            det.append(f"{lc['passed']} passed, {lc['failed']} failing")
        if t.get("repairs"):
            det.append(f"{t['repairs']} repairs")
        if "kill_rate" in t:
            det.append(f"kill rate {t['kill_rate']:.0%}")
        if t.get("error"):
            det.append(f"error: {t['error']}")
        out.append(f"| {name} | {t['status']} | {'; '.join(det)} |")
    rep = next((e for e in reversed(ev) if e["type"] == "tests.report"), None)
    if rep:
        out += ["", f"Test suite: {rep['tests']} tests, {rep['covered']}/{rep['must_rules']} MUST/SHOULD rules covered, "
                    f"{rep['untestable']} untestable, {rep['vacuous']} vacuous."]
    disputes = [e for e in ev if e["type"] == "dispute.verdicts"]
    if disputes:
        out += ["", "## Disputes"] + [f"- `{v['test']}`: **{v['verdict']}**: {v['explanation']}" for e in disputes for v in e["verdicts"]]
    muts = [e for e in ev if e["type"] == "audit.mutant"]
    if muts:
        out += ["", "## Mutation audit"] + [f"- {e['task']}: violate {e['rule']} → {e['outcome']}" for e in muts]
    qs = [(n, q) for n, t in s["tasks"].items() for q in t.get("questions", [])]
    if qs:
        out += ["", "## Spec questions raised (see `friend questions`)"] + [f"- [{n}] {q.get('rule', '')}: {q['question']}" for n, q in qs]
    denials = [e for e in ev if e["type"] == "policy.denied"]
    out += ["", f"Policy denials: {len(denials)}. Events: `{run.dir / 'events.jsonl'}`."]
    return "\n".join(out) + "\n"
