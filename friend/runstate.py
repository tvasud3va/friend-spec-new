"""Durable run state and the event log. Everything a caller (CLI, Friend) observes comes from here."""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable

TERMINAL = {"passed", "failed", "blocked", "done", "skipped"}
RUN_TERMINAL = {"passed", "failed", "blocked"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def write_atomic(path: Path, data: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def new_task() -> dict:
    return {"status": "pending", "session_id": None, "session_started": False, "attempts": 0, "repairs": 0,
            "rounds": 0, "last_checks": None, "last_output": None, "questions": [], "error": None, "model": None}


class Run:
    """One generation request: .friend/runs/<id>/{state.json, events.jsonl, agent/*.jsonl, settings-*.json}."""

    def __init__(self, dir: Path, state: dict, sink: Callable[[dict], None] | None = None) -> None:
        self.dir, self.state, self.sink = dir, state, sink
        (dir / "agent").mkdir(parents=True, exist_ok=True)

    @classmethod
    def create(cls, mf_dir: Path, spec_hash: str, config_hash: str, versions: dict, sink=None) -> "Run":
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        state = {"run_id": run_id, "created": now(), "updated": now(), "status": "running",
                 "spec_hash": spec_hash, "config_hash": config_hash, "versions": versions, "cost_usd": 0.0,
                 "disputes_used": 0, "resumed": 0, "tasks": {"plan": new_task(), "tests": new_task()}}
        run = cls(mf_dir / "runs" / run_id, state, sink)
        run.save()
        write_atomic(mf_dir / "latest", run_id)
        return run

    @classmethod
    def load(cls, mf_dir: Path, run_id: str | None = None, sink=None) -> "Run | None":
        if run_id is None:
            latest = mf_dir / "latest"
            if not latest.exists():
                return None
            run_id = latest.read_text().strip()
        d = mf_dir / "runs" / run_id
        if not (d / "state.json").exists():
            return None
        return cls(d, json.loads((d / "state.json").read_text()), sink)

    def save(self) -> None:
        self.state["updated"] = now()
        write_atomic(self.dir / "state.json", json.dumps(self.state, indent=2))

    def t(self, task: str) -> dict:
        return self.state["tasks"].setdefault(task, new_task())

    def set(self, task: str, **fields) -> None:
        """Change task state, persist, and emit task.status when the status changes."""
        ts = self.t(task)
        old = ts["status"]
        ts.update(fields)
        self.save()
        if "status" in fields and fields["status"] != old:
            self.emit("task.status", task=task, status=fields["status"], previous=old,
                      **({"error": ts["error"]} if fields["status"] in ("failed", "blocked") and ts["error"] else {}))

    def emit(self, type: str, **fields) -> None:
        ev = {"ts": now(), "run": self.state["run_id"], "type": type, **fields}
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        if self.sink:
            self.sink(ev)

    def unfinished(self) -> bool:
        return self.state["status"] not in RUN_TERMINAL
