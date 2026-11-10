"""Agent roles. A role = tools + filesystem scope + allowed commands + output schema + system prompt.

The policy is the same for every project: scopes are computed from the layout in friend.toml and
the plan, so nothing here names a project-specific directory.

  planner   reads spec                 writes nothing (structured output)   → target plan
  tester    reads spec + testkit docs  writes the generated test suite      → tests
  builder   reads spec + its target    writes its target directory          → one implementation
  arbiter   reads spec + tests         writes nothing                       → verdicts on test disputes
  saboteur  reads spec + mutant copies writes mutant copies                 → deliberately broken variants
  author    reads spec + testkit docs  writes spec/                         → drafts and edits the spec
  reviewer  reads spec + testkit docs  writes nothing                       → ambiguity questions
  interviewer reads spec               writes nothing                       → questions about the idea
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config import KIT, Config, expand, rel_or_abs

READ_ONLY = ["Read", "Glob", "Grep"]
WRITER = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]

QUESTIONS = {"type": "array", "items": {"type": "object", "properties": {
    "rule": {"type": "string", "description": "rule id(s) concerned, if any"},
    "question": {"type": "string"},
    "assumption": {"type": "string", "description": "what you assumed, if you proceeded"},
    "blocking": {"type": "boolean"}}, "required": ["question", "blocking"]}}

SCHEMAS = {
    "planner": {"type": "object", "required": ["targets", "spec_questions"], "properties": {
        "targets": {"type": "array", "items": {"type": "object",
            "required": ["name", "platform_spec", "comment", "launch", "sources"], "properties": {
                "name": {"type": "string", "description": "short lowercase identifier, e.g. 'python'"},
                "platform_spec": {"type": "string", "description": "path of the platform spec this target implements"},
                "comment": {"type": "string", "description": "line-comment token of the language, e.g. '#' or '//'"},
                "build": {"type": "array", "items": {"type": "string"}, "description": "argv run from the repo root before testing; [] if no build step"},
                "launch": {"type": "array", "items": {"type": "string"}, "description": "argv that starts the program, from the repo root"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "globs (relative to the target dir) matching hand-authored source files"},
                "selfcheck": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "optional fast syntax/type-check argv lists"},
                "notes": {"type": "string"}}}},
        "spec_questions": QUESTIONS}},
    "tester": {"type": "object", "required": ["status", "summary", "untestable", "spec_questions"], "properties": {
        "status": {"type": "string", "enum": ["complete", "blocked"]},
        "summary": {"type": "string"},
        "untestable": {"type": "array", "items": {"type": "object", "required": ["rule", "reason"], "properties": {
            "rule": {"type": "string"}, "reason": {"type": "string"}}}},
        "spec_questions": QUESTIONS}},
    "builder": {"type": "object", "required": ["status", "summary", "spec_questions", "test_disputes"], "properties": {
        "status": {"type": "string", "enum": ["complete", "blocked"]},
        "summary": {"type": "string"},
        "spec_questions": QUESTIONS,
        "test_disputes": {"type": "array", "description": "failing tests you believe contradict the spec", "items": {
            "type": "object", "required": ["test", "rules", "reason"], "properties": {
                "test": {"type": "string"}, "rules": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string", "description": "which rule text the test contradicts, quoted"}}}}}},
    "arbiter": {"type": "object", "required": ["verdicts"], "properties": {
        "verdicts": {"type": "array", "items": {"type": "object", "required": ["test", "verdict", "explanation"], "properties": {
            "test": {"type": "string"},
            "verdict": {"type": "string", "enum": ["test_wrong", "code_wrong", "spec_ambiguous"]},
            "rules": {"type": "array", "items": {"type": "string"}},
            "explanation": {"type": "string"},
            "fix": {"type": "string", "description": "for test_wrong: exactly what the test must assert instead; for spec_ambiguous: the question"}}}}}},
    "author": {"type": "object", "required": ["summary", "files_written", "spec_questions"], "properties": {
        "summary": {"type": "string"},
        "files_written": {"type": "array", "items": {"type": "string"}},
        "spec_questions": QUESTIONS}},
    "reviewer": {"type": "object", "required": ["questions"], "properties": {
        "questions": {"type": "array", "items": {"type": "object", "required": ["rules", "question", "why", "options", "recommendation"], "properties": {
            "rules": {"type": "array", "items": {"type": "string"}},
            "question": {"type": "string"},
            "why": {"type": "string", "description": "what could differ between implementations or tests"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "empty for an open question"},
            "recommendation": {"type": "string"}}}}}},
    "interviewer": {"type": "object", "required": ["ready", "understanding", "questions"], "properties": {
        "ready": {"type": "boolean", "description": "true when the remaining unknowns can be settled by unsurprising defaults"},
        "understanding": {"type": "string", "description": "the product as agreed so far, as markdown bullets"},
        "questions": {"type": "array", "items": {"type": "object", "required": ["question", "why", "options", "recommendation"], "properties": {
            "question": {"type": "string"},
            "why": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "recommendation": {"type": "string"}}}}}},
    "saboteur": {"type": "object", "required": ["mutants"], "properties": {
        "mutants": {"type": "array", "items": {"type": "object", "required": ["dir", "rule", "change"], "properties": {
            "dir": {"type": "string"}, "rule": {"type": "string"}, "change": {"type": "string"}}}}}},
}


@dataclass
class Role:
    kind: str
    label: str
    tools: list[str]
    write: list[str] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    execs: list[str] = field(default_factory=list)

    @property
    def schema(self) -> dict:
        return SCHEMAS[self.kind]

    def allowed_tools(self) -> list[str]:
        allowed = [t for t in READ_ONLY if t in self.tools]
        for w in self.write:
            allowed += [f"Write({w}/**)", f"Edit({w}/**)"]
        if "Bash" in self.tools:
            allowed += [f"Bash({e}*)" for e in self.execs] + ["Bash(ls *)", "Bash(mkdir -p *)"]
        return allowed

    def deny_paths(self, root: Path) -> list[str]:
        """Everything in the repository outside this role's read/write scope."""
        scope = [(root / p).resolve() for p in self.read + self.write]
        out: list[str] = []

        def walk(d: Path) -> None:
            for child in sorted(d.iterdir()):
                c = child.resolve()
                if any(c == s or s in c.parents for s in scope):
                    continue                       # inside scope
                if any(c in s.parents for s in scope):
                    walk(child)                    # ancestor of something in scope
                elif child.name not in (".", ".."):
                    out.append(str(child.relative_to(root)))
        walk(root)
        return out


def planner(cfg: Config) -> Role:
    return Role("planner", "plan", READ_ONLY, read=[cfg.spec_dir])


def tester(cfg: Config) -> Role:
    suite = " ".join(cfg.suite_cmd())
    return Role("tester", "tests", WRITER, write=[cfg.tests_dir], read=[cfg.spec_dir, rel_or_abs(cfg.root, KIT / "README.md")],
                execs=[f"{suite} --list", f"{suite} --rules", f"{suite} --null-target"])


def builder(cfg: Config, name: str, target: dict) -> Role:
    d = target["dir"]
    execs = [" ".join(cfg.suite_cmd() + ["--target", name])]
    if target.get("build"):
        execs.append(" ".join(expand(target["build"], d)))
    execs += [" ".join(expand(c, d)) for c in target.get("selfcheck") or []]
    launch = expand(target["launch"], d)
    execs.append(f"{launch[0]} {d}/" if not launch[0].startswith(d) else launch[0])
    return Role("builder", f"build:{name}", WRITER, write=[d], read=[cfg.spec_dir], execs=execs)


def arbiter(cfg: Config) -> Role:
    return Role("arbiter", "arbitrate", READ_ONLY, read=[cfg.spec_dir, cfg.tests_dir])


def author(cfg: Config) -> Role:
    return Role("author", "spec", ["Read", "Write", "Edit", "Glob", "Grep"], write=[cfg.spec_dir],
                read=[rel_or_abs(cfg.root, KIT / "README.md")])


def reviewer(cfg: Config) -> Role:
    return Role("reviewer", "spec-review", READ_ONLY, read=[cfg.spec_dir, rel_or_abs(cfg.root, KIT / "README.md")])


def interviewer(cfg: Config) -> Role:
    return Role("interviewer", "interview", READ_ONLY, read=[cfg.spec_dir])


def saboteur(cfg: Config, name: str, mutant_dirs: list[str]) -> Role:
    return Role("saboteur", f"audit:{name}", ["Read", "Write", "Edit", "Glob", "Grep"], write=list(mutant_dirs),
                read=[cfg.spec_dir])
