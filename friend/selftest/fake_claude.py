#!/usr/bin/env python3
"""A scripted stand-in for the `claude` CLI, used by the harness self-tests.

Speaks the same interface the harness uses (`-p`, stream-json events, --session-id/--resume,
--settings with a PreToolUse hook, structured output) and follows a scenario file:

  FRIEND_FAKE_SCENARIO=path.json   {"<role>": [step, step, ...], ...}   (role = $FRIEND_ROLE)
Each invocation of a role consumes its next step (the last one repeats). Step keys:
  write:   {relpath: content}  written through the real policy hook ({spec12} → spec hash prefix)
  bypass:  {relpath: content}  written WITHOUT asking the hook (simulates a policy escape)
  bash:    [command, ...]      offered to the hook only (records allow/deny)
  mutate:  [old, new, file]    applied to every mutant dir named in the prompt
  output:  structured output to return (default: a "complete" result for the role)
  crash:   true                exit 1 without a result
  hang:    seconds             sleep before doing anything (for interrupt/timeout tests)
  missing_session: true        if resumed, fail like the real CLI does for an unknown session
Every call is logged to <scenario>.calls (JSON lines).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

DEFAULTS = {
    "builder": {"status": "complete", "summary": "done", "spec_questions": [], "test_disputes": []},
    "tester": {"status": "complete", "summary": "done", "untestable": [], "spec_questions": []},
    "arbiter": {"verdicts": []},
    "saboteur": {"mutants": []},
    "planner": {"targets": [], "spec_questions": []},
    "author": {"summary": "wrote spec", "files_written": [], "spec_questions": []},
    "reviewer": {"questions": []},
    "interviewer": {"ready": True, "understanding": "- an echo tool", "questions": []},
}


def arg(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("0.0.0 (fake-claude)")
        return 0
    prompt = sys.stdin.read()
    role = os.environ.get("FRIEND_ROLE", "?")
    scenario_path = Path(os.environ["FRIEND_FAKE_SCENARIO"])
    scenario = json.loads(scenario_path.read_text())
    state_path = scenario_path.with_suffix(".state")
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    steps = scenario.get(role, [{}])
    idx = state.get(role, 0)
    step = steps[min(idx, len(steps) - 1)]
    state[role] = idx + 1
    state_path.write_text(json.dumps(state))
    session = arg(argv, "--resume") or arg(argv, "--session-id")
    resume = "--resume" in argv
    with open(scenario_path.with_suffix(".calls"), "a") as f:
        f.write(json.dumps({"role": role, "step": idx, "session": session, "resume": resume, "prompt": prompt}) + "\n")

    if step.get("hang"):
        time.sleep(step["hang"])
    if step.get("missing_session") and resume:
        print(f"No conversation found with session ID: {session}", file=sys.stderr)
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 0, "session_id": session,
              "total_cost_usd": 0, "errors": [f"No conversation found with session ID: {session}"]})
        return 1
    emit({"type": "system", "subtype": "init", "session_id": session, "model": "fake-model"})
    m = re.search(r"[Ss]pec hash:? `([0-9a-f]{12,})`", prompt)
    spec12 = m.group(1)[:12] if m else "????????????"

    settings = json.loads(Path(arg(argv, "--settings")).read_text())
    hook_cmd = shlex.split(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def allowed(tool: str, tool_input: dict) -> bool:
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": tool, "input": tool_input}]}})
        r = subprocess.run(hook_cmd, input=json.dumps({"tool_name": tool, "tool_input": tool_input}), capture_output=True, text=True)
        return r.returncode == 0

    denials = []
    for rel, content in (step.get("write") or {}).items():
        path = Path.cwd() / rel
        if allowed("Write", {"file_path": str(path), "content": "..."}):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.replace("{spec12}", spec12))
        else:
            denials.append(rel)
    for rel, content in (step.get("bypass") or {}).items():
        path = Path.cwd() / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for cmd in step.get("bash") or []:
        if not allowed("Bash", {"command": cmd}):
            denials.append(cmd)
    if step.get("mutate"):
        old, new, fname = step["mutate"]
        for d in re.findall(r"`([^`]+)/`: violate", prompt):
            p = Path.cwd() / d / fname
            if allowed("Edit", {"file_path": str(p)}):
                p.write_text(p.read_text().replace(old, new))
    if step.get("crash"):
        print("fake crash", file=sys.stderr)
        return 1
    output = step.get("output", DEFAULTS.get(role, {}))
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "session_id": session,
          "total_cost_usd": 0.01, "structured_output": output, "result": json.dumps(output),
          "permission_denials": [{"tool_name": "x", "tool_input": {"d": d}} for d in denials]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
