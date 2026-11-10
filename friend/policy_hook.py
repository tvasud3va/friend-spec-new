#!/usr/bin/env python3
"""PreToolUse hook: confines one agent role to the scope the controller computed for it.

  --write P   (repeatable) paths the role may write; writes anywhere else are denied
  --read P    (repeatable) paths the role may read with Read/Glob/Grep (write paths are readable too)
  --exec C    (repeatable) command prefixes the role may run as black boxes; the rest of the
              command line is still checked for forbidden paths
  --deny P    (repeatable) paths a Bash command may not mention (everything outside the role's scope)

Exit 2 blocks the tool call and shows the reason to the agent. Every denial is appended to the
run's event log. The hook fails closed: any internal error denies the call.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

INTERPRETERS = {"sh", "bash", "zsh", "fish", "dash", "env", "xargs", "eval", "exec", "deno", "bun",
                "npx", "npm", "pnpm", "yarn", "uv", "uvx", "pip", "pip3", "osascript", "awk", "sed", "find"}
from pathlib import Path

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Glob", "Grep", "LS"}
SHELL_OPS = re.compile(r"[;&|`<>]|\$\(|\n")
ALWAYS_DENIED = {"WebFetch", "WebSearch", "Task", "Agent"}


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def mentions(cmd: str, rel: str) -> bool:
    """True if `rel` appears in cmd as a path (not as part of a longer name)."""
    # must start a path token (not be a component inside another path, e.g. `friend` in `clients/x/friend/a.py`)
    return re.search(r"(?<![\w./-])" + re.escape(rel.rstrip("/")) + r"(?![\w-])", cmd) is not None


def summarize(inp: dict) -> str:
    for k in ("file_path", "path", "command", "pattern"):
        if inp.get(k):
            return str(inp[k])[:200]
    return json.dumps(inp)[:200]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--write", action="append", default=[])
    ap.add_argument("--read", action="append", default=[])
    ap.add_argument("--exec", action="append", default=[], dest="execs")
    ap.add_argument("--deny", action="append", default=[])
    args = ap.parse_args()
    root = Path(args.root).resolve()
    writes = [(root / w).resolve() for w in args.write]
    reads = writes + [(root / r).resolve() for r in args.read]

    call = json.load(sys.stdin)
    tool, inp = call.get("tool_name", ""), call.get("tool_input", {}) or {}

    def deny(reason: str) -> int:
        try:  # logging must never turn a deny into an allow
            with open(args.events, "a") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "policy.denied", "task": args.label,
                                    "tool": tool, "input": summarize(inp), "reason": reason}) + "\n")
        except OSError:
            pass
        print(f"policy: {reason}", file=sys.stderr)
        return 2

    def resolve(p: str) -> Path:
        return (Path(p) if os.path.isabs(p) else root / p).resolve()

    scope_txt = ", ".join(sorted({*args.write, *args.read})) or "(nothing)"
    if tool in ALWAYS_DENIED:
        return deny(f"{tool} is not available in this role")
    if tool in WRITE_TOOLS:
        p = inp.get("file_path") or inp.get("notebook_path") or ""
        if not p or not any(inside(resolve(p), w) for w in writes):
            return deny(f"writes are allowed only in: {', '.join(args.write) or '(nowhere: this role is read-only)'} (got {p!r})")
        return 0
    if tool in READ_TOOLS:
        p = inp.get("file_path") or inp.get("path") or ""
        if not p and tool == "Glob":  # a relative pattern's literal prefix is its effective root
            pat = inp.get("pattern") or ""
            lead = pat.split("*")[0].rsplit("/", 1)[0] if "/" in pat.split("*")[0] else ""
            p = lead if lead and not os.path.isabs(pat) and ".." not in pat else ""
        if not p:
            return deny(f"{tool} needs an explicit path inside: {scope_txt}")
        if tool == "Read" and own_task_output(p, args.root):
            return 0
        if not any(inside(resolve(p), r) for r in reads):
            return deny(f"{p!r} is outside this role's readable scope ({scope_txt}). "
                        "If information you need is missing from the spec, report it as a spec question.")
        return 0
    if tool == "Bash":
        cmd = (inp.get("command") or "").strip()
        if SHELL_OPS.search(cmd):
            return deny("run one command at a time, without shell operators (; & | > < ` $( ))")
        rest, matched = cmd, False
        for prefix in args.execs:
            if cmd.startswith(prefix) and (len(cmd) == len(prefix) or prefix.endswith("/") or cmd[len(prefix)] == " "):
                rest, matched = cmd[len(prefix):], True
                break
        program = os.path.basename(cmd.split()[0]) if cmd.split() else ""
        if not matched and (program in INTERPRETERS or re.match(r"(python|pypy|node|ruby|perl|php)[\d.]*$", program)):
            return deny(f"{program!r} may only run as one of this role's commands: "
                        + ("; ".join(args.execs) or "(none)") + ". Arbitrary code (e.g. -c/-e) is not allowed.")
        for d in args.deny:
            if mentions(rest, d):
                return deny(f"commands may not reference {d!r} (outside this role's scope)")
        if ".." in rest.split() or "/.." in rest or "../" in rest or "~" in rest:
            return deny("relative parent paths and ~ are not allowed; use repository-relative paths")
        for word in re.findall(r"(?<![\w.-])(/[^\s'\"]+)", rest):
            if not any(inside(Path(word).resolve(), r) for r in reads) and not word.startswith(("/tmp", "/private/tmp", "/dev/null")):
                return deny(f"absolute path {word!r} is outside this role's scope")
        return 0
    return 0


def own_task_output(p: str, root: str) -> bool:
    """Claude Code keeps the output of the agent's own background commands under
    <tmp>/claude-<uid>/<project slug>/; the agent may read that back (nothing else lives there)."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path(root).resolve()))
    real = str(Path(p).resolve())
    return re.match(r"^/(private/)?tmp/claude-\d+/" + re.escape(slug) + "/", real) is not None and ".." not in p


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:  # fail closed: exit 1 would be a non-blocking hook error (= allow)
        print(f"policy hook error, denying: {e}", file=sys.stderr)
        sys.exit(2)
