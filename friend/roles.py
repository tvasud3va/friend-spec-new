"""Agent roles. A role = tools + filesystem scope + allowed commands + output schema + system prompt.

The policy is the same for every project: scopes are computed from the layout in friend.toml and
the plan, so nothing here names a project-specific directory.

  planner   reads spec                 writes nothing (structured output)   → target plan
  tester    reads spec + testkit docs  writes the generated test suite      → tests
  builder   reads spec + its target    writes its target directory          → one implementation
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
    "builder": {"type": "object", "required": ["status", "summary", "spec_questions"], "properties": {
        "status": {"type": "string", "enum": ["complete", "blocked"]},
        "summary": {"type": "string"},
        "spec_questions": QUESTIONS}},
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


def planner(cfg: Config) -> Role:
    return Role("planner", "plan", READ_ONLY, read=[cfg.spec_dir])


def tester(cfg: Config) -> Role:
    suite = " ".join(cfg.suite_cmd())
    return Role("tester", "tests", WRITER, write=[cfg.tests_dir], read=[cfg.spec_dir, rel_or_abs(cfg.root, KIT / "README.md")],
                execs=[f"{suite} --list", f"{suite} --rules"])


def builder(cfg: Config, name: str, target: dict) -> Role:
    d = target["dir"]
    execs = [" ".join(cfg.suite_cmd() + ["--target", name])]
    if target.get("build"):
        execs.append(" ".join(expand(target["build"], d)))
    execs += [" ".join(expand(c, d)) for c in target.get("selfcheck") or []]
    launch = expand(target["launch"], d)
    execs.append(f"{launch[0]} {d}/" if not launch[0].startswith(d) else launch[0])
    return Role("builder", f"build:{name}", WRITER, write=[d], read=[cfg.spec_dir], execs=execs)

