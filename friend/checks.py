"""Deterministic checks: builds, test-suite runs, gates on generated tests and code, manifests."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from config import Config, expand
from speclib import behaviour_spec_files, parse_rules, target_spec_files

MANIFEST = ".generated.json"
NO_COMMENT_SUFFIXES = {".json"}  # formats with no comment syntax cannot carry the generated-file header
SKIP_PARTS = {".friend", "__pycache__", "node_modules", ".git"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snapshot(d: Path) -> dict[str, str]:
    if not d.exists():
        return {}
    return {str(p.relative_to(d)): sha(p) for p in sorted(d.rglob("*"))
            if p.is_file() and not SKIP_PARTS & set(p.parts) and p.name != MANIFEST}


def diff(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    return {"added": sorted(set(after) - set(before)),
            "modified": sorted(k for k in set(before) & set(after) if before[k] != after[k]),
            "deleted": sorted(set(before) - set(after))}


def protected_hash(root: Path, writable: list[str]) -> str:
    """Hash of every repository file outside `writable` (the step's own write scope)."""
    allowed = [(root / w).resolve() for w in writable]
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or SKIP_PARTS & set(p.relative_to(root).parts):
            continue
        rp = p.resolve()
        if any(a == rp or a in rp.parents for a in allowed):
            continue
        h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def failure(name: str, detail: str, rules: list[str] | None = None) -> dict:
    return {"name": name, "status": "fail", "rules": rules or [], "detail": detail}


# ── builds and suite runs ────────────────────────────────────────────────────

def build(cfg: Config, target: dict) -> list[dict]:
    """Run the target's build command (if any). [] on success, else one failure record."""
    cmd = expand(target.get("build"), target["dir"])
    if not cmd:
        return []
    try:
        proc = subprocess.run(cmd, cwd=cfg.root, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [failure("gate:build", f"`{' '.join(cmd)}` could not run: {e}")]
    if proc.returncode == 0:
        return []
    return [failure("gate:build", f"`{' '.join(cmd)}` exited {proc.returncode}:\n{(proc.stdout + proc.stderr)[-3000:]}")]


def run_suite(cfg: Config, args: list[str], on_case: Callable[[dict], None] | None = None,
              suite: str | None = None, env: dict | None = None, timeout: float = 1800) -> dict:
    cmd = [sys.executable, *cfg.suite_cmd(suite)[1:], *args, "--json"]  # same runner, current interpreter
    proc = subprocess.Popen(cmd, cwd=cfg.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, **(env or {})})
    cases, summary, suite_error = [], None, None
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "test.case":
            cases.append(ev)
            if on_case:
                on_case(ev)
        elif ev.get("type") == "test.summary":
            summary = ev
        elif ev.get("type") == "suite.error":
            suite_error = "\n".join(ev["errors"])
    err = proc.stderr.read()
    proc.wait(timeout)
    failures = [c for c in cases if c["status"] != "pass"]
    if suite_error:
        failures.append(failure("suite:import", suite_error[-3000:]))
    elif summary is None:
        failures.append({"name": "suite:runner", "status": "error", "rules": [], "detail": err[-1500:] or "runner crashed"})
    return {"passed": sum(c["status"] == "pass" for c in cases), "failed": len(failures), "failures": failures, "cases": cases}


def list_suite(cfg: Config, suite: str | None = None) -> tuple[list[dict] | None, str]:
    proc = subprocess.run([sys.executable, *cfg.suite_cmd(suite)[1:], "--list"], cwd=cfg.root, capture_output=True, text=True)
    if proc.returncode != 0:
        return None, (proc.stdout + proc.stderr)[-3000:]
    return json.loads(proc.stdout), ""


# ── gates on the generated test suite ─────────────────────────────────────────

def test_gates(cfg: Config, untestable: list[dict]) -> tuple[list[dict], dict]:
    """Checks a test suite must pass before it may judge any implementation. Returns (failures, coverage report)."""
    tests, err = list_suite(cfg)
    if tests is None:
        return [failure("gate:suite-imports", err)], {}
    if not tests:
        return [failure("gate:suite-empty", f"no tests found in {cfg.tests_dir}/test_*.py")], {}
    rules = {r.id: r for r in parse_rules(cfg.root, behaviour_spec_files(cfg.root, cfg.spec_dir))}
    must = [r for r in rules.values() if r.level in ("MUST", "SHOULD")]
    covered = {rid for t in tests for rid in t["rules"]}
    excused = {u["rule"]: u["reason"] for u in untestable}
    uncovered = [r.id for r in must if r.id not in covered and r.id not in excused]
    report = {"tests": len(tests), "must_rules": len(must), "covered": sorted(covered & {r.id for r in must}),
              "untestable": excused, "uncovered": uncovered}
    return [], report


# ── gates on generated code ───────────────────────────────────────────────────

def static_gates(cfg: Config, name: str, target: dict, spec_hash: str) -> list[dict]:
    d = cfg.root / target["dir"]
    fails: list[dict] = []
    sources = sorted({p for g in target["sources"] for p in d.glob(g) if p.is_file()})
    if not sources:
        return [failure("gate:sources", f"no source files matching {target['sources']} in {target['dir']}")]
    for p in sources:
        if p.suffix in NO_COMMENT_SUFFIXES:
            continue  # still covered by the manifest's hand-edit detection
        head = "\n".join(p.read_text(errors="replace").splitlines()[:3])
        if target["header"] not in head or spec_hash[:12] not in head:
            fails.append(failure("gate:header", f"{p.relative_to(cfg.root)}: first lines must contain "
                                 f"'{target['header']} from spec {spec_hash[:12]}'"))
    rules = [r for r in parse_rules(cfg.root, target_spec_files(cfg.root, cfg.spec_dir, target["platform_spec"]))
             if r.level != "REMOVED"]
    try:
        trace = json.loads((d / "TRACE.json").read_text())["rules"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return fails + [failure("gate:trace", f"{target['dir']}/TRACE.json missing or invalid: {e}")]
    missing = [r.id for r in rules if r.id not in trace]
    if missing:
        fails.append(failure("gate:trace", f"TRACE.json has no entry for: {', '.join(missing)}", missing))
    for rid, where in trace.items():
        if isinstance(where, list):
            ghosts = [w for w in where if not (d / w).exists()]
            if ghosts:
                fails.append(failure("gate:trace", f"TRACE.json {rid} cites missing files {ghosts}", [rid]))
    return fails


def write_manifest(cfg: Config, name: str, target: dict, meta: dict) -> dict:
    d = cfg.root / target["dir"]
    m = {"generator": "friend", "target": name, **meta, "files": snapshot(d)}
    (d / MANIFEST).write_text(json.dumps(m, indent=2) + "\n")
    return m

