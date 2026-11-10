"""Spec authoring: draft, refine, review, and the open-questions store.

These run before generation, through the same audited agent-step machinery (scoped role, policy hook,
integrity check, events) but in their own run records under .friend/spec/. Spec changes are never
approved automatically: the user reviews the diff and runs `friend spec approve`.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Callable

import roles
from config import KIT, Config, rel_or_abs
from controller import Controller, Stop, prompt
from runstate import Run, write_atomic
from speclib import lint

LINT_ROUNDS = 2


# ── questions store (.friend/questions.json) ──────────────────────────────────────

def _qpath(cfg: Config) -> Path:
    return cfg.root / ".friend" / "questions.json"


def load_questions(cfg: Config) -> list[dict]:
    p = _qpath(cfg)
    return json.loads(p.read_text()) if p.exists() else []


def save_questions(cfg: Config, qs: list[dict]) -> None:
    _qpath(cfg).parent.mkdir(exist_ok=True)
    write_atomic(_qpath(cfg), json.dumps(qs, indent=2) + "\n")


def add_questions(cfg: Config, new: list[dict], source: str, status: str = "open") -> list[dict]:
    """Add questions (deduplicated by text); returns the ones actually added. status="assumed" records a
    non-blocking assumption the agent already wrote into the spec: listed by `friend questions --all`, not asked."""
    qs = load_questions(cfg)
    known = {q["question"].strip().lower() for q in qs}
    added = []
    n = max([int(q["id"][1:]) for q in qs] or [0])
    for q in new:
        text = q.get("question", "").strip()
        if not text or text.lower() in known:
            continue
        n += 1
        rules = q.get("rules") or ([r.strip() for r in q["rule"].split(",")] if q.get("rule") else [])
        item = {"id": f"Q{n}", "source": source, "rules": rules, "question": text, "why": q.get("why", ""),
                "options": q.get("options", []), "recommendation": q.get("recommendation") or q.get("assumption", ""),
                "blocking": bool(q.get("blocking")),
                "status": "open" if q.get("blocking") else status, "answer": None,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
        qs.append(item)
        known.add(text.lower())
        added.append(item)
    save_questions(cfg, qs)
    return added


def set_status(cfg: Config, ids_answers: dict[str, tuple[str, str | None]]) -> None:
    qs = load_questions(cfg)
    for q in qs:
        if q["id"] in ids_answers:
            q["status"], q["answer"] = ids_answers[q["id"]]
    save_questions(cfg, qs)


# ── agent sessions ────────────────────────────────────────────────────────────

def _session(cfg: Config, sink) -> tuple[Run, Controller]:
    run = Run.create(cfg.root / ".friend" / "spec", "-", "-", {"model": cfg["agent"]["model"], "agent_cli": None, "toolchain": {}}, sink)
    run.state["tasks"] = {}
    return run, Controller(cfg, run, auto_approve=False)


def _author(cfg: Config, text: str, sink, label: str) -> tuple[bool, dict, list[str]]:
    """Run the author with `text`, then lint, with fix rounds. Returns (ok, output, lint problems)."""
    run, ctl = _session(cfg, sink)
    run.set("spec", status="working", session_id=str(uuid.uuid4()), session_started=False, attempts=0)
    role = roles.author(cfg)
    out: dict = {}
    try:
        resume = False
        for rnd in range(LINT_ROUNDS + 1):
            res = ctl.agent_step("spec", role, text, resume, f"{label}-{rnd}")
            if ctl.crashed("spec", res):
                continue
            out = res.output or out
            problems = lint(cfg.root, cfg.spec_dir)
            if not problems:
                run.set("spec", status="done")
                return True, out, []
            run.emit("gate.failed", task="spec", gate="gate:lint", detail="; ".join(problems))
            text, resume = prompt("lint_fix", problems="\n".join(f"- {p}" for p in problems)), True
        run.set("spec", status="failed", error="spec still fails lint")
        return False, out, problems
    except Stop:
        return False, out, [run.t("spec").get("error") or "agent step failed"]


def draft(cfg: Config, sink) -> tuple[bool, dict, list[str]]:
    return _author(cfg, prompt("spec_draft", kit_readme=rel_or_abs(cfg.root, KIT / "README.md")), sink, "draft")


def refine(cfg: Config, instruction: str, sink) -> tuple[bool, dict, list[str]]:
    return _author(cfg, prompt("spec_refine", instruction=instruction), sink, "refine")


def apply_answers(cfg: Config, answered: list[dict], sink) -> tuple[bool, dict, list[str]]:
    lines = []
    for q in answered:
        lines.append(f"### {q['id']} (rules: {', '.join(q['rules']) or 'n/a'})\nQuestion: {q['question']}\nDecision: {q['answer']}")
    return _author(cfg, prompt("spec_answer", answers="\n\n".join(lines)), sink, "answer")


def review(cfg: Config, sink, max_questions: int = 8) -> tuple[bool, list[dict]]:
    run, ctl = _session(cfg, sink)
    run.set("spec-review", status="working", session_id=str(uuid.uuid4()), session_started=False, attempts=0)
    try:
        res = ctl.agent_step("spec-review", roles.reviewer(cfg),
                             prompt("spec_review", kit_readme=rel_or_abs(cfg.root, KIT / "README.md"), max=max_questions),
                             False, "review")
    except Stop:
        return False, []
    run.set("spec-review", status="done")
    return True, (res.output or {}).get("questions") or []


def interactive_answers(qs: list[dict], read: Callable[[str], str]) -> dict[str, tuple[str, str | None]]:
    """Ask the user about each open question. Returns {id: (status, answer)} for answered/dismissed ones."""
    decided: dict[str, tuple[str, str | None]] = {}
    for q in qs:
        print(f"\n{q['id']} [{', '.join(q['rules']) or '-'}] {q['question']}")
        if q.get("why"):
            print(f"   why: {q['why']}")
        for i, opt in enumerate(q.get("options") or [], 1):
            print(f"   {i}) {opt}")
        if q.get("recommendation"):
            print(f"   recommended: {q['recommendation']}")
        ans = read("   > number or your own words (Enter = recommended, s = skip): ").strip()
        if ans.lower() == "s":
            continue
        if ans.lower() == "d":
            decided[q["id"]] = ("dismissed", None)
            continue
        if not ans:
            ans = q.get("recommendation") or ""
            if not ans:
                continue
        elif ans.isdigit() and 1 <= int(ans) <= len(q.get("options") or []):
            ans = q["options"][int(ans) - 1]
        decided[q["id"]] = ("answered", ans)
    return decided


def contract(cfg: Config, sink) -> tuple[bool, dict, list[str]]:
    return _author(cfg, prompt("spec_contract"), sink, "contract")
