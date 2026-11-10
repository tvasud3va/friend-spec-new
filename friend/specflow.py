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
                "plain": q.get("plain") or {},
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


def show(q: dict, plain: dict | None) -> None:
    """A spec question, in plain words when the reviewer gave them, else the technical version with rule IDs."""
    if plain:
        print(f"\n{plain['question']}")
        opts, rec = plain["options"], plain.get("recommendation")
    else:
        print(f"\n{q['id']} [{', '.join(q['rules']) or '-'}] {q['question']}")
        if q.get("why"):
            print(f"   why: {q['why']}")
        opts, rec = q.get("options") or [], q.get("recommendation")
    for i, opt in enumerate(opts, 1):
        print(f"   {i}) {opt}")
    if rec:
        print(f"   recommended: {rec}")


def interactive_answers(qs: list[dict], read: Callable[[str], str]) -> dict[str, tuple[str, str | None]]:
    """Ask the user about each open question. Returns {id: (status, answer)} for answered/dismissed ones."""
    decided: dict[str, tuple[str, str | None]] = {}
    for q in qs:
        plain = q.get("plain") or {}
        simple = bool(plain.get("question")) and len(plain.get("options") or []) == len(q.get("options") or [])
        while True:
            show(q, plain if simple else None)
            ans = read("   > number or your own words (Enter = recommended" + (", ? = technical details" if simple else "")
                       + ", s = skip): ").strip()
            if ans == "?" and simple:
                simple = False
                continue
            break
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


# ── interview (friend new) ────────────────────────────────────────────────────

INTERVIEW_MAX_QUESTIONS = 4


def _ipath(cfg: Config) -> Path:
    return cfg.root / ".friend" / "interview.json"


def load_interview(cfg: Config) -> dict | None:
    p = _ipath(cfg)
    return json.loads(p.read_text()) if p.exists() else None


def save_interview(cfg: Config, state: dict) -> None:
    _ipath(cfg).parent.mkdir(exist_ok=True)
    write_atomic(_ipath(cfg), json.dumps(state, indent=2) + "\n")


def new_interview(idea: str) -> dict:
    return {"idea": idea, "rounds": [], "understanding": "", "ready": False, "status": "interviewing",
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}


def transcript(state: dict) -> list[tuple[str, str]]:
    return [(q["question"], q["answer"]) for r in state["rounds"] for q in r["questions"] if q.get("answer")]


def interview_round(cfg: Config, state: dict, languages: list[str], sink) -> bool:
    """Ask the interviewer for its understanding and the next questions. Each round is a fresh session with the
    full transcript, so an interview survives crashes and can continue on another day. Returns False on failure."""
    run, ctl = _session(cfg, sink)
    n = len(state["rounds"]) + 1
    run.set("interview", status="working", session_id=str(uuid.uuid4()), session_started=False, attempts=0)
    qa = "\n".join(f"- {q} → {a}" for q, a in transcript(state)) or "(none yet: this is the first round)"
    text = prompt("interview", round=n, idea=state["idea"], languages=", ".join(languages) or "(not chosen)",
                  transcript=qa, max=INTERVIEW_MAX_QUESTIONS)
    try:
        res = ctl.agent_step("interview", roles.interviewer(cfg), text, False, f"interview-{n}")
    except Stop:
        return False
    out = res.output or {}
    if not res.ok or "understanding" not in out:
        return False
    run.set("interview", status="done")
    qs = [] if out.get("ready") else (out.get("questions") or [])[:INTERVIEW_MAX_QUESTIONS]
    base = sum(len(r["questions"]) for r in state["rounds"])
    state["rounds"].append({"questions": [{"id": f"I{base + i}", "question": q["question"], "why": q.get("why", ""),
                                           "options": q.get("options", []), "recommendation": q.get("recommendation", ""),
                                           "answer": None} for i, q in enumerate(qs, 1)]})
    state["understanding"] = out["understanding"]
    state["reply"] = out.get("reply", "")
    state["ready"] = bool(out.get("ready")) or not qs
    save_interview(cfg, state)
    return True


def open_interview_questions(state: dict) -> list[dict]:
    return [q for q in (state["rounds"][-1]["questions"] if state["rounds"] else []) if q.get("answer") is None]


def answer_interview(state: dict, answers: dict[str, str]) -> None:
    for q in open_interview_questions(state):
        if q["id"] in answers:
            a = answers[q["id"]].strip()
            opts = q.get("options") or []
            if a.isdigit() and 1 <= int(a) <= len(opts):
                a = opts[int(a) - 1]
            q["answer"] = a or q.get("recommendation") or "(no preference)"


def ask_interview(qs: list[dict], read: Callable[[str], str]) -> tuple[dict[str, str], bool]:
    """Ask in the terminal. Returns (answers, stop) where stop means 'enough questions, write the spec'."""
    answers: dict[str, str] = {}
    for q in qs:
        print(f"\n{q['question']}")
        if not q.get("options"):
            a = read("  > ").strip()
        else:
            if q.get("why"):
                print(f"  why it matters: {q['why']}")
            for i, opt in enumerate(q["options"], 1):
                print(f"    {i}) {opt}")
            if q.get("recommendation"):
                print(f"  recommended: {q['recommendation']}")
            a = read("  > pick a number or say it your way (Enter = recommended): ").strip()
        if a.lower() in ("done", "enough", "q"):
            return answers, True
        answers[q["id"]] = a
    return answers, False


def write_brief(cfg: Config, name: str, state: dict) -> Path:
    p = cfg.path(cfg.spec_dir) / "BRIEF.md"
    qa = "\n".join(f"- **{q}**  \n  {a}" for q, a in transcript(state))
    p.write_text(f"# Brief: {name}\n\n{state['idea'].strip()}\n\n## What we agreed\n\n{state['understanding'].strip()}\n"
                 + (f"\n## Interview\n\n{qa}\n" if qa else ""))
    return p


def contract(cfg: Config, sink) -> tuple[bool, dict, list[str]]:
    return _author(cfg, prompt("spec_contract"), sink, "contract")
