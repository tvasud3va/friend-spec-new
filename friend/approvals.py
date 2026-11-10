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
SPEC_HISTORY = 4   # rule snapshots of recently approved specs, the baselines for updating existing code


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
    if kind == "spec":
        remember(data, current, spec_rules(cfg))
    save(cfg, data)
    return rec


def spec_rules(cfg: Config) -> dict[str, dict]:
    """Every rule in the spec as it is now: {id: {level, text, file}}."""
    from speclib import all_spec_files, parse_rules
    return {r.id: {"level": r.level, "text": r.text, "file": r.file} for r in parse_rules(cfg.root, all_spec_files(cfg.root, cfg.spec_dir))}


def remember(data: dict, spec_hash: str, rules: dict) -> None:
    hist = [h for h in data.get("spec_history", []) if h["hash"] != spec_hash]
    data["spec_history"] = (hist + [{"hash": spec_hash, "rules": rules}])[-SPEC_HISTORY:]


def rules_at(cfg: Config, spec_hash: str) -> dict[str, dict] | None:
    """The rules of an earlier approved spec, or None if friend did not keep them."""
    return next((h["rules"] for h in load(cfg).get("spec_history", []) if h["hash"] == spec_hash), None)


def rule_diff(old: dict[str, dict], new: dict[str, dict]) -> dict[str, list[str]]:
    gone = lambda rules, i: i not in rules or rules[i]["level"] == "REMOVED"  # noqa: E731
    order = lambda i: (i.rsplit("-", 1)[0], int(i.rsplit("-", 1)[1]) if i.rsplit("-", 1)[-1].isdigit() else 0)  # noqa: E731
    return {"added": sorted((i for i in new if gone(old, i) and not gone(new, i)), key=order),
            "changed": sorted((i for i in new if not gone(old, i) and not gone(new, i)
                               and (old[i]["text"], old[i]["level"]) != (new[i]["text"], new[i]["level"])), key=order),
            "removed": sorted((i for i in old if not gone(old, i) and gone(new, i)), key=order)}


def revoke(cfg: Config, kind: str) -> None:
    data = load(cfg)
    if data.pop(kind, None) is not None:
        save(cfg, data)


def spec_hash(cfg: Config) -> str:
    st, why = status(cfg, "spec")
    if st != "approved":
        raise ApprovalError(f"spec is not approved: {why}. Review it, then run `friend approve spec`")
    data = load(cfg)
    if rules_at(cfg, data["spec"]["hash"]) is None:   # approved before friend kept rule snapshots: the files still match
        remember(data, data["spec"]["hash"], spec_rules(cfg))
        save(cfg, data)
    return data["spec"]["hash"]
