"""Adapter for the coding agent: Claude Code run headless (`claude -p`).

`invoke()` runs one role-scoped agent step and returns an AgentResult. This module owns the
exact CLI flags, the permission envelope, stream translation and failure classification.
It makes no workflow decisions.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from config import Config
from roles import Role


@dataclass
class AgentResult:
    ok: bool                        # a result event arrived and was not an error
    exit_code: int | None
    subtype: str = ""               # success | error_max_budget_usd | error_during_execution | timeout | crash
    output: dict | None = None      # structured output (role schema)
    text: str = ""
    session_id: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    turns: int = 0
    session_missing: bool = False
    stderr_tail: str = ""
    tools_used: dict = field(default_factory=dict)
    denials: int = 0


def build_command(cfg: Config, role: Role, run_dir: Path, session_id: str, resume: bool, system_prompt: str) -> list[str]:
    a = cfg["agent"]
    cmd = [a["command"], "-p",
           "--output-format", "stream-json", "--verbose",
           "--setting-sources", "",                 # ignore user/project settings: a reproducible envelope
           "--strict-mcp-config",                   # no MCP servers
           "--disable-slash-commands",
           "--tools", ",".join(role.tools),
           "--allowedTools", *role.allowed_tools(),
           "--permission-mode", "dontAsk",          # anything not allowed is denied, never prompted
           "--append-system-prompt", system_prompt,
           "--json-schema", json.dumps(role.schema),
           "--max-budget-usd", str(a["max_budget_usd"])]
    cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    if a.get("model"):
        cmd += ["--model", a["model"]]
    if a.get("effort"):
        cmd += ["--effort", a["effort"]]
    return cmd


def invoke(cfg: Config, role: Role, run_dir: Path, session_id: str, resume: bool, prompt: str, system_prompt: str,
           log_name: str, on_event: Callable[..., None]) -> AgentResult:
    cmd = build_command(cfg, role, run_dir, session_id, resume, system_prompt)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"))}
    env["FRIEND_ROLE"] = role.kind
    log_path = run_dir / "agent" / f"{log_name}.jsonl"
    proc = subprocess.Popen(cmd, cwd=cfg.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    proc.stdin.write(prompt)
    proc.stdin.close()
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
    timed_out = threading.Event()

    def on_timeout() -> None:
        timed_out.set()
        _kill(proc)
    timer = threading.Timer(cfg["agent"]["step_timeout_s"], on_timeout)
    timer.start()

    res = AgentResult(ok=False, exit_code=None, session_id=session_id)
    try:
        with open(log_path, "a") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _translate(ev, res, on_event)
        proc.wait()
    except BaseException:  # KeyboardInterrupt: stop the agent; the controller records "interrupted"
        _kill(proc)
        raise
    finally:
        timer.cancel()
    res.exit_code = proc.returncode
    res.stderr_tail = "".join(stderr)[-2000:]
    if timed_out.is_set():
        res.ok, res.subtype = False, "timeout"
    elif not res.subtype:
        res.subtype = "crash"
    res.session_missing = resume and not res.ok and "no conversation found" in (res.stderr_tail + res.text).lower()
    return res


def _translate(ev: dict, res: AgentResult, on_event: Callable[..., None]) -> None:
    kind = ev.get("type")
    if kind == "system" and ev.get("subtype") == "init":
        res.session_id = ev.get("session_id", res.session_id)
        res.model = ev.get("model")
        on_event("agent.session", session_id=res.session_id, model=res.model)
    elif kind == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                on_event("agent.text", text=block["text"].strip()[:500])
            elif block.get("type") == "tool_use" and block.get("name") != "StructuredOutput":
                inp = block.get("input", {})
                res.tools_used[block["name"]] = res.tools_used.get(block["name"], 0) + 1
                on_event("agent.tool", tool=block["name"],
                         input=str(inp.get("file_path") or inp.get("command") or inp.get("pattern") or inp.get("path") or "")[:200])
    elif kind == "result":
        res.subtype = ev.get("subtype", "")
        res.ok = not ev.get("is_error") and res.subtype == "success"
        res.text = (ev.get("result") or "") + " ".join(map(str, ev.get("errors") or []))
        res.output = ev.get("structured_output")
        if res.output is None and res.ok and res.text:
            try:
                res.output = json.loads(res.text)
            except json.JSONDecodeError:
                pass
        res.cost_usd = float(ev.get("total_cost_usd") or 0)
        res.turns = int(ev.get("num_turns") or 0)
        res.denials = len(ev.get("permission_denials") or [])
        res.session_id = ev.get("session_id", res.session_id)
        on_event("agent.result", subtype=res.subtype, turns=res.turns, cost_usd=round(res.cost_usd, 4))


def _kill(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(1)
