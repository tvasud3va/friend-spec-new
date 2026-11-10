"""The sprint contract (spec/DONE.md) and the proof of done.

DONE.md is the human-readable definition of done, approved together with the spec (it lives in spec/, so it
is part of the spec hash). Each item is a plain-language outcome tied to the rules that make it precise:

    - [ ] **D-1** Messages sent while offline are delivered after reconnecting. (rules: C-7, C-8)

`friend verify` proves the contract: every item is checked against the test results for its rules on every
target, and a fixed list of proof checks (approvals, coverage, vacuity, builds, suites, interop, holdout,
hand edits, mutation audit) is evaluated. The evidence is written to .friend/proof.md. Deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from speclib import Rule

CONTRACT_FILE = "DONE.md"
ITEM_RE = re.compile(r"^\s*-\s+\[[ xX]\]\s+\*\*(D-\d+)\*\*\s+(.*?)\s*\(rules?:\s*([^)]*)\)\s*$")
LOOSE_ITEM_RE = re.compile(r"^\s*-\s+\[[ xX]\]")
RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


@dataclass
class Item:
    id: str
    text: str
    rules: list[str]
    line: int


@dataclass
class Check:
    name: str
    status: str                 # pass | fail | unproven | skipped
    detail: str = ""
    evidence: list[str] = field(default_factory=list)


def path(root: Path, spec_dir: str) -> Path:
    return root / spec_dir / CONTRACT_FILE


def parse(root: Path, spec_dir: str) -> tuple[list[Item], list[str]]:
    """(items, format problems). A missing contract is not a problem here; callers decide."""
    p = path(root, spec_dir)
    if not p.exists():
        return [], []
    items, problems = [], []
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if m := ITEM_RE.match(line):
            refs = [r.strip() for r in m[3].split(",") if r.strip()]
            bad = [r for r in refs if not RULE_ID_RE.match(r)]
            if bad or not refs:
                problems.append(f"{spec_dir}/{CONTRACT_FILE}:{n}: {m[1]} needs `(rules: ID, ...)` with valid rule IDs")
            items.append(Item(m[1], m[2], refs, n))
        elif LOOSE_ITEM_RE.match(line):
            problems.append(f"{spec_dir}/{CONTRACT_FILE}:{n}: contract items look like "
                            "`- [ ] **D-1** outcome in plain language. (rules: C-1, C-2)`")
    return items, problems


def lint(root: Path, spec_dir: str, rules: list[Rule]) -> list[str]:
    items, problems = parse(root, spec_dir)
    if not path(root, spec_dir).exists():
        return []
    if not items and not problems:
        problems.append(f"{spec_dir}/{CONTRACT_FILE} has no items")
    known = {r.id: r for r in rules}
    seen: set[str] = set()
    for it in items:
        if it.id in seen:
            problems.append(f"{spec_dir}/{CONTRACT_FILE}:{it.line}: duplicate item {it.id}")
        seen.add(it.id)
        for ref in it.rules:
            if ref not in known:
                problems.append(f"{spec_dir}/{CONTRACT_FILE}:{it.line}: {it.id} cites unknown rule {ref}")
            elif known[ref].level == "REMOVED":
                problems.append(f"{spec_dir}/{CONTRACT_FILE}:{it.line}: {it.id} cites removed rule {ref}")
    return problems


def judge_items(items: list[Item], cases_by_target: dict[str, list[dict]], untestable: dict[str, str]) -> list[Check]:
    """An item is proven when every one of its rules has at least one test and every test citing them passed,
    on every target (and in interop). Rules declared untestable leave the item unproven, never passed."""
    out = []
    for it in items:
        failing, untested, ran = [], [], 0
        for rule in it.rules:
            hits = [(t, c) for t, cases in cases_by_target.items() for c in cases if rule in c.get("rules", [])]
            if not hits:
                untested.append(rule + (f" (untestable: {untestable[rule][:80]})" if rule in untestable else ""))
            ran += len(hits)
            failing += [f"{c['test']} on {t}" for t, c in hits if c["status"] != "pass"]
        if failing:
            out.append(Check(f"{it.id} {it.text}", "fail", f"failing: {', '.join(sorted(set(failing))[:6])}"))
        elif untested:
            out.append(Check(f"{it.id} {it.text}", "unproven", f"no test for {', '.join(untested)}"))
        else:
            out.append(Check(f"{it.id} {it.text}", "pass", f"{ran} test runs across {len(cases_by_target)} suite(s)/target(s)"))
    return out


MARK = {"pass": "✓", "fail": "✗", "unproven": "?", "skipped": "-"}


def render(project: str, header: list[str], items: list[Check], proof: list[Check], done: bool) -> str:
    out = [f"# Proof of done: {project}", "", *header, "",
           f"**Verdict: {'DONE' if done else 'NOT DONE'}**", ""]
    if items:
        out += ["## Contract (spec/DONE.md)", ""]
        out += [f"- {MARK[c.status]} {c.name}" + (f"  \n  {c.detail}" if c.detail else "") for c in items]
        out.append("")
    out += ["## Proof checks", ""]
    out += [f"- {MARK[c.status]} **{c.name}**: {c.detail}" for c in proof]
    out += ["", "Legend: ✓ proven · ✗ failing · ? not proven by any test · - not applicable", ""]
    return "\n".join(out)
