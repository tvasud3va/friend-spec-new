"""Project-level commands: init (scaffold), doctor (environment check), run (launch a target), report."""
from __future__ import annotations

import difflib
import json
import os
import re
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


STOPWORDS = {"a", "an", "the", "i", "me", "my", "to", "for", "of", "and", "that", "which", "with", "in", "on",
             "can", "use", "want", "build", "make", "app", "simple", "little", "from", "where", "it", "is", "lets",
             "wanna", "gonna", "would", "like", "need", "just", "some", "something", "thing", "please", "really",
             "basically", "kind", "sort", "so", "um", "id", "im", "create", "write", "program", "tool", "application",
             "spec", "specification"}


def slug(text: str) -> str:
    """A short folder name from an idea: 'a habit tracker I use from the terminal' -> 'habit-tracker-terminal'."""
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]
    return "-".join(words[:3]) or "my-app"


def document(text: str) -> Path | None:
    """The file a typed or drag-and-dropped answer points at (quotes and escaped spaces allowed), else None."""
    t = text.strip().strip("'\"").replace("\\ ", " ")
    if not t or "\n" in t or len(t) > 1024:
        return None
    p = Path(t).expanduser()
    return p.resolve() if p.is_file() else None


def title(text: str) -> str:
    """The first Markdown heading, without words like 'specification'."""
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return re.sub(r"(?i)\b(spec(ification)?|requirements|prd)\b", "", m[1]) if m else ""


def free_dir(parent: Path, name: str) -> Path:
    """parent itself if it is empty, else parent/name (or name-2, name-3 … if taken)."""
    if parent.is_dir() and not any(p for p in parent.iterdir() if not p.name.startswith(".")):
        return parent
    d, n = parent / name, 2
    while d.exists() and any(d.iterdir()):
        d, n = parent / f"{name}-{n}", n + 1
    return d


ALIASES = {"python": "python", "py": "python", "typescript": "typescript", "ts": "typescript",
           "javascript": "javascript", "js": "javascript", "node": "javascript"}


def parse_languages(text: str) -> list[str]:
    """Languages named or numbered (1-based, in LANGUAGES order) anywhere in free text, in the order given."""
    names = list(LANGUAGES)
    found = []
    for w in re.findall(r"[a-z]+|\d+", text.lower()):
        close = difflib.get_close_matches(w, names, n=1, cutoff=0.75) if len(w) >= 4 else []
        lang = names[int(w) - 1] if w.isdigit() and 0 < int(w) <= len(names) else ALIASES.get(w) or (close or [None])[0]
        if lang and lang not in found:
            found.append(lang)
    return found


def ask_languages(available: dict[str, bool], suggest: list[str] | None = None) -> list[str]:
    """Numbered menu; accepts numbers or names in any phrasing, asks again until it understands."""
    if not sys.stdin.isatty():
        return suggest or (["python"] if available["python"] else [l for l, ok in available.items() if ok][:1])
    print("Which language(s) should it be written in? Pick one or more, e.g. `1` or `1, 2`.")
    for n, (l, ok) in enumerate(available.items(), 1):
        print(f"  {n}) {l}{'' if ok else '  (not installed on this computer)'}")
    while True:
        default = ", ".join(str(list(available).index(l) + 1) for l in suggest) if suggest else "1"
        langs = parse_languages(ask("Languages", default))
        if langs and ask(f"  → {' and '.join(langs)}. Right? [Y/n]").lower() in ("", "y", "yes"):
            return langs
        if langs:
            continue
        print(f"  I didn't catch a language. Type numbers (like 1, 2) or names ({', '.join(LANGUAGES)}).")


def detect() -> dict[str, bool]:
    return {lang: all(shutil.which(n) for n in spec["needs"]) for lang, spec in LANGUAGES.items()}


def init(root: Path, name: str | None, describe: str | None, langs: list[str], install: bool, force: bool,
         show_next: bool = True, suggest: list[str] | None = None) -> int:
    if (root / "friend.toml").exists() and not force:
        print(f"{root}/friend.toml already exists (use --force to overwrite the scaffold)", file=sys.stderr)
        return 3
    available = detect()
    name = name or ask("Project name", root.name) or root.name
    if describe is None:
        describe = ask("What are you building? (one or two sentences; used to draft the spec)", "")
    if not langs:
        langs = ask_languages(available, suggest)
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


def render_context(cfg, run=None) -> str:
    """CONTEXT.md: where everything is and why, for people and for new agent sessions opening the project.
    Written by friend from its own records after every run (deterministic; never by an agent)."""
    import specflow
    from config import load_plan
    from speclib import all_spec_files, parse_rules
    name = cfg.raw.get("project", {}).get("name") or cfg.root.name
    brief = cfg.path(cfg.spec_dir) / "BRIEF.md"
    idea = brief.read_text().split("\n", 2)[-1].strip().split("\n\n")[0][:600] if brief.exists() else ""
    out = [f"# Context: {name}", "", "Written by friend after every run from its own records. Do not edit; it is replaced.", ""]
    if idea:
        out += ["## What this is", "", idea, ""]
    rules = parse_rules(cfg.root, all_spec_files(cfg.root, cfg.spec_dir))
    out += ["## Where things are", "", "| Path | What | Who writes it |", "|---|---|---|",
            f"| `{cfg.spec_dir}/` | the specification: {len(rules)} rules; the source of truth | you (and friend's spec writer, with your approval) |",
            f"| `{cfg.spec_dir}/DONE.md` | the sprint contract: what done means | you |",
            f"| `{cfg['layout']['plan']}` | build targets | planner agent, approved by you |",
            f"| `{cfg.tests_dir}/` | the test suite, written from the spec without seeing code | tester agent, approved by you |"]
    plan = load_plan(cfg) or {"targets": {}}
    for t_name, t in plan["targets"].items():
        out.append(f"| `{t['dir']}/` | the `{t_name}` program (run: `friend run {t_name}`) | builder agent; never edit by hand |")
    out += [f"| `{cfg['layout']['approvals']}` | what you approved, by content hash | friend |",
            "| `.friend/` | run logs, proof of done, reports (not committed) | friend |", ""]
    for t_name, t in plan["targets"].items():
        try:
            trace = json.loads((cfg.root / t["dir"] / "TRACE.json").read_text())["rules"]
        except (OSError, ValueError, KeyError):
            continue
        files: dict[str, list[str]] = {}
        for rule, fs in trace.items():
            for f in fs if isinstance(fs, list) else []:
                files.setdefault(f, []).append(rule)
        out += [f"### `{t_name}`: which file implements which rules", ""]
        out += [f"- `{t['dir']}/{f}`: {', '.join(sorted(rs, key=lambda r: (r.split('-')[0], int(r.split('-')[1]) if r.split('-')[-1].isdigit() else 0)))}"
                for f, rs in sorted(files.items())] + [""]
    qs = specflow.load_questions(cfg)
    decided = [q for q in qs if q["status"] == "answered"]
    assumed = [q for q in qs if q["status"] == "assumed"]
    if decided or assumed:
        out += ["## Decisions", ""] + [f"- **{q['answer']}** ({', '.join(q['rules']) or 'general'}): {q['question'][:200]}" for q in decided]
        out += [f"- Assumed: {q['recommendation'][:200]} ({', '.join(q['rules']) or 'general'})" for q in assumed] + [""]
    if run:
        st = run.state
        audit = st["tasks"].get("audit", {})
        out += ["## Last build", "", f"- run `{st['run_id']}`: **{st['status']}**, spec `{st['spec_hash'][:12]}`, ${st['cost_usd']:.2f}"]
        u = st.get("update")
        if u:
            what = "; ".join(f"{k} {', '.join(u[k])}" for k in ("changed", "added", "removed") if u[k]) or "no rule changed"
            out.append(f"- edited the existing code for a spec change from `{u['base'][:12]}`: {what}")
        if "kill_rate" in audit:
            out.append(f"- mutation audit: tests caught {audit['kill_rate']:.0%} of deliberately broken copies")
        survived = [f"{m['target']} {m['rule']}" for res in (audit.get("results") or {}).values() for m in res if m["outcome"] == "survived"]
        if survived:
            out.append(f"- weak spots (a broken copy passed every test): {', '.join(survived)}")
        out.append("")
    out += ["## How to change it", "", "- Change behaviour: `friend spec refine \"...\"` (or edit `spec/`), `friend approve spec`, then "
            "`friend generate`. Existing tests and code are edited for the changed rules, not rebuilt.",
            "- Check it: `friend verify` (writes `.friend/proof.md`).", ""]
    return "\n".join(out)


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
