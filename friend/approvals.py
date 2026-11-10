"""Approval records for the three reviewed artifacts: spec → plan, spec → tests.

An approval pins an artifact's content hash and the spec hash it was approved against, so
changing the spec invalidates the approved plan and tests, and editing an approved artifact
invalidates its own approval. Stored in approvals.json (committed).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from config import Config
from runstate import write_atomic

KINDS = ("spec", "plan", "tests")


class ApprovalError(Exception):
    pass


def tree_hash(root: Path, rel: str) -> str | None:
    """Content hash of a file or directory (None if missing). __pycache__ and manifests are ignored."""
    p = root / rel
    if not p.exists():
        return None
    files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts)
    h = hashlib.sha256()
    for f in files:
        h.update(str(f.relative_to(root)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def artifact_path(cfg: Config, kind: str) -> str:
    return {"spec": cfg.spec_dir, "plan": cfg["layout"]["plan"], "tests": cfg.tests_dir}[kind]


def load(cfg: Config) -> dict:
    p = cfg.path(cfg["layout"]["approvals"])
    return json.loads(p.read_text()) if p.exists() else {}


def save(cfg: Config, data: dict) -> None:
    write_atomic(cfg.path(cfg["layout"]["approvals"]), json.dumps(data, indent=2) + "\n")


def status(cfg: Config, kind: str) -> tuple[str, str]:
    """('approved'|'unapproved'|'stale'|'missing', reason)."""
    current = tree_hash(cfg.root, artifact_path(cfg, kind))
    if current is None:
        return "missing", f"{artifact_path(cfg, kind)} does not exist"
    rec = load(cfg).get(kind)
    if not rec:
        return "unapproved", f"{kind} has never been approved"
    if rec["hash"] != current:
        return ("stale" if kind == "spec" else "unapproved"), f"{artifact_path(cfg, kind)} changed since it was approved"
    if kind != "spec":
        spec = load(cfg).get("spec")
        if not spec or rec.get("spec_hash") != spec["hash"]:
            return "stale", f"{kind} was approved against a different spec"
    return "approved", ""


def approve(cfg: Config, kind: str, by: str, note: str = "") -> dict:
    if kind not in KINDS:
        raise ApprovalError(f"unknown artifact {kind!r}")
    current = tree_hash(cfg.root, artifact_path(cfg, kind))
    if current is None:
        raise ApprovalError(f"nothing to approve: {artifact_path(cfg, kind)} does not exist")
    data = load(cfg)
    if kind != "spec":
        st, why = status(cfg, "spec")
        if st != "approved":
            raise ApprovalError(f"approve the spec first ({why})")
    rec = {"hash": current, "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": by}
    if note:
        rec["note"] = note
    if kind != "spec":
        rec["spec_hash"] = data["spec"]["hash"]
        if kind == "tests" and by == "arbitration" and data.get("tests"):
            rec["amendments"] = data["tests"].get("amendments", []) + [{"at": rec["approved_at"], "note": note}]
    data[kind] = rec
    save(cfg, data)
    return rec


def revoke(cfg: Config, kind: str) -> None:
    data = load(cfg)
    if data.pop(kind, None) is not None:
        save(cfg, data)


def spec_hash(cfg: Config) -> str:
    st, why = status(cfg, "spec")
    if st != "approved":
        raise ApprovalError(f"spec is not approved: {why}. Review it, then run `friend approve spec`")
    return load(cfg)["spec"]["hash"]
