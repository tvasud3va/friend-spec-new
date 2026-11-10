"""Spec loading, linting, hashing and approval. Deterministic; no agent involvement."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

RULE_RE = re.compile(r"^\s*-\s+\*\*([A-Z][A-Z0-9]*-\d+)\*\*\s+\((MUST|SHOULD|MAY|REMOVED)\)\s*(.*)$")
LOOSE_RULE_RE = re.compile(r"^\s*-\s+\*\*([A-Z][A-Z0-9]*-\d+)\*\*")
AMENDS_RE = re.compile(r"\bAmends ([A-Z][A-Z0-9]*-\d+)\b")
EXT_RE = re.compile(r"^(\d{3})-[a-z0-9-]+\.md$")


class SpecError(Exception):
    pass


@dataclass
class Rule:
    id: str
    level: str
    text: str
    file: str
    line: int


def all_spec_files(root: Path, spec_dir: str) -> list[Path]:
    """Every file that can hold rules. BRIEF.md is context, not requirements (it may quote an imported spec)."""
    return sorted(p for p in (root / spec_dir).rglob("*.md") if p.is_file() and p != root / spec_dir / "BRIEF.md")


def behaviour_spec_files(root: Path, spec_dir: str) -> list[Path]:
    """Files every target and the test suite share: README, core, extensions (numeric order)."""
    spec = root / spec_dir
    exts = sorted(p for p in (spec / "extensions").glob("*.md") if EXT_RE.match(p.name))
    return [spec / "README.md", *sorted((spec / "core").glob("*.md")), *exts]


def platform_spec_files(root: Path, spec_dir: str) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in (root / spec_dir / "platforms").glob("*.md"))


def target_spec_files(root: Path, spec_dir: str, platform_spec: str) -> list[Path]:
    """Exactly what one target is generated from: the shared files plus its own platform spec."""
    return [*behaviour_spec_files(root, spec_dir), root / platform_spec]


def spec_lines(f: Path):
    """(line_no, line) outside ``` fenced blocks: examples in fences are not rules."""
    fenced = False
    for n, line in enumerate(f.read_text().splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            yield n, line


def parse_rules(root: Path, files: list[Path]) -> list[Rule]:
    rules = []
    for f in files:
        for n, line in spec_lines(f):
            if m := RULE_RE.match(line):
                rules.append(Rule(m[1], m[2], m[3], str(f.relative_to(root)), n))
    return rules


def lint(root: Path, spec_dir: str) -> list[str]:
    problems = []
    files = all_spec_files(root, spec_dir)
    for f in files:
        for n, line in spec_lines(f):
            if LOOSE_RULE_RE.match(line) and not RULE_RE.match(line):
                problems.append(f"{f.relative_to(root)}:{n}: rule bullet without a valid (MUST|SHOULD|MAY|REMOVED) level")
    rules = parse_rules(root, files)
    seen: dict[str, Rule] = {}
    for r in rules:
        if r.id in seen:
            problems.append(f"{r.file}:{r.line}: duplicate rule id {r.id} (first at {seen[r.id].file}:{seen[r.id].line})")
        seen[r.id] = r
    for r in rules:
        for ref in AMENDS_RE.findall(r.text):
            if ref not in seen:
                problems.append(f"{r.file}:{r.line}: {r.id} amends unknown rule {ref}")
    spec = root / spec_dir
    for p in (spec / "extensions").glob("*.md"):
        if p.name != "README.md" and not EXT_RE.match(p.name):
            problems.append(f"{p.relative_to(root)}: extension files must be named NNN-name.md")
    if not (spec / "README.md").exists():
        problems.append(f"{spec_dir}/README.md missing")
    core = list((spec / "core").glob("*.md"))
    if not core:
        problems.append(f"{spec_dir}/core/ has no spec files")
    elif not any("interface" in p.stem for p in core):
        problems.append(f"{spec_dir}/core/ needs an interface file (e.g. interface.md) defining how programs are launched, driven and observed")
    if not platform_spec_files(root, spec_dir):
        problems.append(f"{spec_dir}/platforms/ has no platform specs: nothing to generate")
    import contract  # the sprint contract is part of the spec; its rule references must resolve
    problems += contract.lint(root, spec_dir, rules)
    return problems
