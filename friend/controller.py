"""The deterministic workflow. Agents write things; this module decides everything else.

  plan ─► [approval] ─► tests ─► [approval] ─► build:<target> (one per target, sequential) ─► interop ─► audit

  plan    planner proposes targets → deterministic validation (retries) → plan.json
  tests   tester writes the suite → gates: imports, rule coverage, untestable limit, vacuity (fix rounds)
  build   prepare → implement → checking (build, suite, static gates) → repairing … → passed|failed|blocked
          disputed failing tests → arbiter → test_wrong: tester fixes test, suite re-gated and re-approved,
          every target re-checked | code_wrong: back to repair | spec_ambiguous: blocked
  interop interop-tagged tests over every mixed assignment of targets to roles
  audit   saboteur makes rule-violating mutants of each passing target; the suite must catch them

Every transition is persisted before the next action, so `generate` can resume after any crash.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import uuid
from pathlib import Path
from string import Template

import agent
import approvals
import checks
import roles
from config import Config, expand, load_plan, probe_toolchain, validate_plan_targets
from runstate import TERMINAL, Run, write_atomic
from speclib import behaviour_spec_files, parse_rules, platform_spec_files, target_spec_files

HARNESS = Path(__file__).resolve().parent
TESTS_MANIFEST = "MANIFEST.json"


def config_hash(root: Path) -> str:
    h = hashlib.sha256((root / "friend.toml").read_bytes())
    for p in [*sorted((HARNESS / "prompts").glob("*.md")), HARNESS / "testkit" / "README.md"]:
        h.update(p.read_bytes())
    return h.hexdigest()


def prompt(name: str, **vals) -> str:
    return Template((HARNESS / "prompts" / f"{name}.md").read_text()).safe_substitute(**vals)


def system_prompt(kind: str) -> str:
    return prompt("common") + "\n\n" + prompt(f"role-{kind}")


def bullet(paths) -> str:
    return "\n".join(f"- `{p}`" for p in paths)


def format_failures(failures: list[dict], limit: int = 12) -> str:
    out = []
    for f in failures[:limit]:
        rules = f" (rules: {', '.join(f['rules'])})" if f.get("rules") else ""
        out.append(f"### {f['name']}: {f['status']}{rules}\n```\n{f.get('detail', '')[:1500]}\n```")
    if len(failures) > limit:
        out.append(f"... and {len(failures) - limit} more")
    return "\n\n".join(out)


class Rework(Exception):
    """The reviewer rejected an artifact; regenerate it with this feedback."""

    def __init__(self, feedback: str) -> None:
        self.feedback = feedback


class Stop(Exception):
    """A stage cannot continue (awaiting approval, blocked, failed). The run ends with `status`."""

    def __init__(self, status: str) -> None:
        self.status = status


class Controller:
    """reviewer(kind, summary, detail) -> ("approve" | "reject" | "quit", feedback): interactive approval, optional.
    budget_usd: total spend at which the run pauses (resumable), optional."""

    def __init__(self, cfg: Config, run: Run, auto_approve: bool, reviewer=None, budget_usd: float | None = None) -> None:
        self.cfg, self.root, self.run, self.auto = cfg, cfg.root, run, auto_approve
        self.reviewer, self.budget = reviewer, budget_usd

    # ── top level ──
    def execute(self) -> str:
        r = self.run
        r.emit("run.resumed" if r.state["resumed"] else "run.started", spec_hash=r.state["spec_hash"][:12],
               versions=r.state["versions"], auto_approve=self.auto)
        try:
            self.stage_plan()
            self.stage_tests()
            plan = load_plan(self.cfg)
            for name in plan["targets"]:
                r.t(f"build:{name}")
            r.save()
            self.stage_builds(plan)
            self.stage_interop(plan)
            self.stage_audit(plan)
            final = "passed"
        except Stop as s:
            final = s.status
        r.state["status"] = final
        r.save()
        r.emit("run.finished", status=final, cost_usd=round(r.state["cost_usd"], 2),
               tasks={k: v["status"] for k, v in r.state["tasks"].items()})
        return final

    # ── agent step shared by all roles ──
    def agent_step(self, task: str, role: roles.Role, text: str, resume: bool, log: str) -> agent.AgentResult:
        r, ts = self.run, self.run.t(task)
        guard = checks.protected_hash(self.root, role.write)
        before = {w: checks.snapshot(self.root / w) for w in role.write}
        r.set(task, session_started=True, attempts=ts["attempts"] + 1)
        r.emit("step.started", task=task, role=role.kind, session_id=ts["session_id"], resume=resume, attempt=ts["attempts"])
        emit = lambda type, **f: r.emit(type, task=task, **f)  # noqa: E731
        res = agent.invoke(self.cfg, role, r.dir, ts["session_id"], resume, text, system_prompt(role.kind), log, emit)
        if res.session_missing:  # the session to resume is gone: a new session, with a self-contained prompt
            r.emit("agent.session_lost", task=task, session_id=ts["session_id"])
            r.set(task, session_id=str(uuid.uuid4()))
            res = agent.invoke(self.cfg, role, r.dir, ts["session_id"], False, text, system_prompt(role.kind), log + "-new", emit)
        r.state["cost_usd"] += res.cost_usd
        ts["model"] = res.model or ts["model"]
        over_budget = self.budget is not None and r.state["cost_usd"] >= self.budget
        for w in role.write:
            r.emit("files.changed", task=task, path=w, **checks.diff(before[w], checks.snapshot(self.root / w)))
        r.emit("step.finished", task=task, role=role.kind, subtype=res.subtype, turns=res.turns,
               cost_usd=round(res.cost_usd, 4), tools=res.tools_used, denials=res.denials)
        if checks.protected_hash(self.root, role.write) != guard:
            r.set(task, status="failed", error=f"policy: files outside {role.write or 'the read-only scope'} changed during the {role.kind} step")
            raise Stop("failed")
        questions = (res.output or {}).get("spec_questions") or []
        if questions:
            ts["questions"] += questions
            r.emit("spec.questions", task=task, questions=questions)
        r.save()
        if over_budget:
            r.emit("budget.exceeded", spent=round(r.state["cost_usd"], 2), budget=self.budget)
            raise Stop("paused")
        return res

    def crashed(self, task: str, res: agent.AgentResult) -> bool:
        """True if the step produced no usable result and should be retried; raises when retries are exhausted."""
        if res.output is not None or res.subtype not in ("crash", "timeout"):
            return False
        ts = self.run.t(task)
        if ts["attempts"] <= self.cfg["agent"]["agent_retries"]:
            self.run.emit("agent.retry", task=task, reason=res.subtype, stderr=res.stderr_tail[-500:])
            return True
        self.run.set(task, status="failed", error=f"agent {res.subtype}: {res.stderr_tail[-300:]}")
        raise Stop("failed")

    def gate_approval(self, task: str, kind: str) -> None:
        st, why = approvals.status(self.cfg, kind)
        if st == "approved":
            return self.run.set(task, status="done")
        if self.auto:
            approvals.approve(self.cfg, kind, by="auto")
            self.run.emit("approval", task=task, artifact=kind, by="auto")
            return self.run.set(task, status="done")
        if self.reviewer:
            summary, detail = self.summarize(kind)
            decision, feedback = self.reviewer(kind, summary, detail)
            if decision == "approve":
                approvals.approve(self.cfg, kind, by="human")
                self.run.emit("approval", task=task, artifact=kind, by="human")
                return self.run.set(task, status="done")
            if decision == "reject":
                self.run.emit("approval.rejected", task=task, artifact=kind, feedback=feedback)
                raise Rework(feedback)
        self.run.set(task, status="awaiting_approval")
        self.run.emit("approval.required", task=task, artifact=kind, path=approvals.artifact_path(self.cfg, kind),
                      command=f"friend approve {kind}")
        raise Stop("awaiting_approval")

    # ── review summaries ──
    def summarize(self, kind: str) -> tuple[str, str]:
        if kind == "plan":
            plan = load_plan(self.cfg) or {"targets": {}}
            rows = [f"  {n:12} build: {' '.join(t['build']) if t.get('build') else '-'}\n  {'':12} run:   {' '.join(t['launch'])}"
                    + (f"\n  {'':12} note:  {t['notes'][:200]}" if t.get("notes") else "") for n, t in plan["targets"].items()]
            return "── Plan ──\n" + "\n".join(rows), json.dumps(plan, indent=2)
        m = self.tests_manifest() or {}
        rep = m.get("report", {})
        unt = "\n".join(f"    {u['rule']}: {u['reason'][:160]}" for u in m.get("untestable", []))
        summary = (f"── Tests ({self.cfg.tests_dir}/) ──\n  {rep.get('tests', '?')} tests · {len(rep.get('covered', []))}/{rep.get('must_rules', '?')} "
                   f"MUST/SHOULD rules covered · {len(m.get('untestable', []))} untestable · {len(rep.get('vacuous', []))} vacuous"
                   + (f"\n  untestable:\n{unt}" if unt else ""))
        tests, _ = checks.list_suite(self.cfg)
        detail = "\n".join(f"  {t['name']:45} {', '.join(t['rules'])}" + ("  [interop]" if t["interop"] else "") for t in tests or [])
        return summary, "rule coverage by test:\n" + detail

    # ── stage: plan ──
    def stage_plan(self) -> None:
        r, ts = self.run, self.run.t("plan")
        if ts["status"] == "done" and approvals.status(self.cfg, "plan")[0] == "approved":
            return
        st, _ = approvals.status(self.cfg, "plan")
        existing = load_plan(self.cfg)
        feedback = ""
        if st == "approved" or (existing and existing.get("spec_hash") == r.state["spec_hash"] and st == "unapproved"):
            r.emit("step.skipped", task="plan", reason="plan for this spec already exists")
            try:
                return self.gate_approval("plan", "plan")
            except Rework as rw:
                feedback = rw.feedback
        while True:
            self.generate_plan(feedback)
            try:
                return self.gate_approval("plan", "plan")
            except Rework as rw:
                feedback = rw.feedback

    def generate_plan(self, feedback: str) -> None:
        r = self.run
        platform = platform_spec_files(self.root, self.cfg.spec_dir)
        tools = "\n".join(f"- `{t['cmd']}` ({n}: {r.state['versions']['toolchain'].get(n, '?')})"
                          for n, t in self.cfg["toolchain"].items())
        base = prompt("planner", spec_hash=r.state["spec_hash"], spec_dir=self.cfg.spec_dir, platform_specs=bullet(platform),
                      generated_root=self.cfg.generated_root, toolchain=tools)
        if feedback:
            base += f"\n\n## The reviewer rejected the previous plan\n\n{feedback}"
        text, problems = base, []
        for attempt in range(self.cfg["workflow"]["max_test_rounds"] + 1):
            r.set("plan", status="working", session_id=str(uuid.uuid4()), attempts=0)
            res = self.agent_step("plan", roles.planner(self.cfg), text, False, f"plan-{attempt}")
            if self.crashed("plan", res):
                continue
            targets = (res.output or {}).get("targets") or []
            problems = validate_plan_targets(self.cfg, targets, platform)
            if not problems:
                break
            r.emit("gate.failed", task="plan", gate="gate:plan", detail="; ".join(problems))
            text = base + "\n\n## Your previous plan was rejected\n\n" + "\n".join(f"- {p}" for p in problems)
        if problems:
            r.set("plan", status="failed", error="planner output invalid: " + "; ".join(problems))
            raise Stop("failed")
        plan = {"spec_hash": r.state["spec_hash"], "run_id": r.state["run_id"], "targets": {}}
        for t in targets:
            d = f"{self.cfg.generated_root}/{t['name']}"
            plan["targets"][t["name"]] = {"dir": d, "platform_spec": t["platform_spec"], "comment": t["comment"],
                                          "header": f"{t['comment']} GENERATED by friend", "build": t.get("build") or None,
                                          "launch": t["launch"], "sources": t["sources"], "selfcheck": t.get("selfcheck") or [],
                                          "notes": t.get("notes", "")}
        write_atomic(self.cfg.path(self.cfg["layout"]["plan"]), json.dumps(plan, indent=2) + "\n")
        r.emit("plan.written", targets=list(plan["targets"]))

    # ── stage: tests ──
    def tests_vars(self) -> dict:
        suite = " ".join(self.cfg.suite_cmd())
        svc = "\n".join(f"- `{n}`: {s.get('describe', '')}" for n, s in self.cfg["services"].items()) or "- (none)"
        return {"spec_hash": self.run.state["spec_hash"], "tests_dir": self.cfg.tests_dir, "spec_dir": self.cfg.spec_dir,
                "spec_files": bullet(p.relative_to(self.root) for p in behaviour_spec_files(self.root, self.cfg.spec_dir)),
                "services": svc, "list_cmd": f"{suite} --list", "rules_cmd": f"{suite} --rules", "null_cmd": f"{suite} --null-target"}

    def stage_tests(self) -> None:
        r, ts = self.run, self.run.t("tests")
        if ts["status"] == "done" and approvals.status(self.cfg, "tests")[0] == "approved":
            return
        st, _ = approvals.status(self.cfg, "tests")
        manifest = self.tests_manifest()
        if st == "approved" or (manifest and manifest.get("spec_hash") == r.state["spec_hash"] and manifest.get("gates") == "passed"):
            r.emit("step.skipped", task="tests", reason="approved/validated suite for this spec already exists")
            return self.review_tests()
        d = self.cfg.path(self.cfg.tests_dir)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        r.set("tests", status="working", session_id=str(uuid.uuid4()), session_started=False, attempts=0, rounds=0)
        text, resume = prompt("tester", **self.tests_vars()), False
        while True:
            res = self.agent_step("tests", roles.tester(self.cfg), text, resume, f"tests-{ts['rounds']}-{ts['attempts']}")
            if self.crashed("tests", res):
                text, resume = prompt("tester", **self.tests_vars()), False
                r.set("tests", session_id=str(uuid.uuid4()))
                continue
            out = res.output or {}
            if out.get("status") == "blocked":
                r.set("tests", status="blocked", error="tester reported blocking spec questions")
                raise Stop("blocked")
            ts["last_output"] = out
            failures = self.check_tests(out.get("untestable") or [])
            if not failures:
                break
            if ts["rounds"] >= self.cfg["workflow"]["max_test_rounds"]:
                r.set("tests", status="failed", error=f"test gates still failing after {ts['rounds']} fix rounds")
                raise Stop("failed")
            r.set("tests", status="fixing", rounds=ts["rounds"] + 1, attempts=0)
            text, resume = prompt("tester_fix", **self.tests_vars(), round=ts["rounds"], problems=format_failures(failures)), True
        self.review_tests()

    def review_tests(self) -> None:
        """Approval gate for the suite; a rejection becomes a tester fix round, re-gated, and reviewed again."""
        r, ts = self.run, self.run.t("tests")
        while True:
            try:
                return self.gate_approval("tests", "tests")
            except Rework as rw:
                r.set("tests", status="fixing", rounds=ts["rounds"] + 1, attempts=0)
                if not ts["session_started"]:
                    r.set("tests", session_id=str(uuid.uuid4()))
                res = self.agent_step("tests", roles.tester(self.cfg),
                                      prompt("tester_fix", **self.tests_vars(), round=ts["rounds"], problems=f"### Reviewer feedback\n\n{rw.feedback}"),
                                      ts["session_started"], f"tests-review-{ts['rounds']}")
                failures = self.check_tests((res.output or {}).get("untestable") or (self.tests_manifest() or {}).get("untestable") or [])
                if failures:
                    r.set("tests", status="failed", error="suite revised after review fails its gates")
                    raise Stop("failed")

    def check_tests(self, untestable: list[dict]) -> list[dict]:
        r = self.run
        r.set("tests", status="checking")
        r.emit("step.started", task="tests", step="test gates")
        failures, report = checks.test_gates(self.cfg, untestable)
        for f in failures:
            r.emit("gate.failed", task="tests", gate=f["name"], detail=f["detail"][:600])
        r.emit("tests.report", task="tests", **{k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in report.items()})
        if not failures:
            manifest = {"spec_hash": r.state["spec_hash"], "run_id": r.state["run_id"], "gates": "passed",
                        "untestable": untestable, "report": report}
            write_atomic(self.cfg.path(self.cfg.tests_dir) / TESTS_MANIFEST, json.dumps(manifest, indent=2) + "\n")
        r.t("tests")["last_checks"] = {"failed": len(failures), "failures": failures}
        r.save()
        return failures

    def tests_manifest(self) -> dict | None:
        p = self.cfg.path(self.cfg.tests_dir) / TESTS_MANIFEST
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    # ── stage: builds ──
    def stage_builds(self, plan: dict) -> None:
        while True:
            pending = [n for n in plan["targets"] if self.run.t(f"build:{n}")["status"] not in TERMINAL]
            if not pending:
                break
            self.drive_build(pending[0], plan["targets"][pending[0]])
        statuses = {self.run.t(f"build:{n}")["status"] for n in plan["targets"]}
        if statuses != {"passed"}:
            raise Stop("blocked" if "blocked" in statuses else "failed")

    def build_vars(self, name: str, t: dict) -> dict:
        sh = self.run.state["spec_hash"]
        return {"target": name, "target_dir": t["dir"], "spec_hash": sh, "spec_hash12": sh[:12],
                "spec_files": bullet(p.relative_to(self.root) for p in target_spec_files(self.root, self.cfg.spec_dir, t["platform_spec"])),
                "launch": " ".join(expand(t["launch"], t["dir"])),
                "build": " ".join(expand(t["build"], t["dir"])) if t.get("build") else "(none)",
                "header": t["header"], "sources": ", ".join(t["sources"]),
                "test_cmd": " ".join(self.cfg.suite_cmd() + ["--target", name]),
                "max_rounds": self.cfg["workflow"]["max_repairs"]}

    def continue_prompt(self, name: str, t: dict) -> str:
        existing = checks.snapshot(self.root / t["dir"])
        last = self.run.t(f"build:{name}")["last_checks"]
        return prompt("continue", **self.build_vars(name, t), existing_files=bullet(existing) or "- (none)",
                      last_failures=("Most recent check failures:\n\n" + format_failures(last["failures"])) if last and last["failures"] else "")

    def drive_build(self, name: str, t: dict) -> None:
        r, task, w = self.run, f"build:{name}", self.cfg["workflow"]
        ts = r.t(task)
        role = roles.builder(self.cfg, name, t)
        while (st := ts["status"]) not in TERMINAL:
            if st == "pending":
                d = self.root / t["dir"]
                if d.exists():
                    shutil.rmtree(d)  # regeneration boundary: the whole target directory
                d.mkdir(parents=True)
                r.emit("step.finished", task=task, step="prepare", detail=f"cleaned {t['dir']}/")
                r.set(task, status="implementing", session_id=str(uuid.uuid4()), session_started=False, attempts=0)
            elif st in ("implementing", "repairing"):
                if ts["attempts"] > 0 or (st == "implementing" and ts["session_started"]):
                    text = self.continue_prompt(name, t)
                elif st == "implementing":
                    text = prompt("implement", **self.build_vars(name, t))
                else:
                    text = prompt("repair", **self.build_vars(name, t), round=ts["repairs"],
                                  failures=format_failures(ts["last_checks"]["failures"]))
                res = self.agent_step(task, role, text, ts["session_started"], f"{name}-{st}-{ts['repairs']}-{ts['attempts']}")
                if self.crashed(task, res):
                    continue
                out = res.output or {}
                ts["last_output"] = out
                if out.get("status") == "blocked":
                    r.set(task, status="blocked", error="builder reported blocking spec questions")
                    return
                r.set(task, status="checking", attempts=0)  # complete, out of budget, or no output: checks decide
            elif st == "checking":
                failures = self.check_target(name, t)
                if not failures:
                    self.finish_target(name, t)
                    r.set(task, status="passed")
                    continue
                disputed = self.disputes_for(name, failures)
                if disputed and r.state["disputes_used"] < w["max_disputes"]:
                    self.arbitrate(name, t, disputed, failures)
                elif ts["repairs"] < w["max_repairs"]:
                    r.set(task, status="repairing", repairs=ts["repairs"] + 1, attempts=0)
                else:
                    r.set(task, status="failed", error=f"checks still failing after {w['max_repairs']} repairs")

    def check_target(self, name: str, t: dict) -> list[dict]:
        r, task = self.run, f"build:{name}"
        r.emit("step.started", task=task, step="checks")
        failures = checks.build(self.cfg, t)
        if not failures:
            res = checks.run_suite(self.cfg, ["--target", name],
                                   lambda c: r.emit("test.case", task=task, **{k: c[k] for k in ("name", "status", "rules", "duration_s", "detail")}))
            failures = res["failures"] + checks.static_gates(self.cfg, name, t, r.state["spec_hash"])
            passed = res["passed"]
        else:
            passed = 0
        for f in failures:
            if f["name"].startswith("gate:"):
                r.emit("gate.failed", task=task, gate=f["name"], detail=f["detail"][:600])
        r.t(task)["last_checks"] = {"passed": passed, "failed": len(failures), "failures": failures}
        r.save()
        r.emit("test.summary", task=task, passed=passed, failed=len(failures))
        return failures

    def finish_target(self, name: str, t: dict) -> None:
        ts, st = self.run.t(f"build:{name}"), self.run.state
        checks.write_manifest(self.cfg, name, t, {
            "run_id": st["run_id"], "spec_hash": st["spec_hash"], "plan_hash": approvals.load(self.cfg)["plan"]["hash"],
            "tests_hash": approvals.load(self.cfg)["tests"]["hash"], "session_id": ts["session_id"], "model": ts["model"],
            "agent_cli": st["versions"]["agent_cli"], "toolchain": st["versions"]["toolchain"], "generated_at": st["updated"]})

    def disputes_for(self, name: str, failures: list[dict]) -> list[dict]:
        out = self.run.t(f"build:{name}").get("last_output") or {}
        failing = {f.get("test", f["name"]) for f in failures} | {f["name"] for f in failures}
        return [d for d in out.get("test_disputes") or [] if d.get("test") in failing]

    # ── arbitration ──
    def arbitrate(self, name: str, t: dict, disputes: list[dict], failures: list[dict]) -> None:
        r, task = self.run, f"build:{name}"
        r.state["disputes_used"] += 1
        r.emit("dispute.opened", task=task, tests=[d["test"] for d in disputes])
        details = []
        for d in disputes:
            f = next((f for f in failures if f.get("test", f["name"]) == d["test"] or f["name"] == d["test"]), {})
            details.append(f"### {d['test']} (rules: {', '.join(d.get('rules', []))})\nImplementer's reason: {d['reason']}\n"
                           f"Failure output:\n```\n{f.get('detail', '')[:1200]}\n```")
        r.set("arbitrate", status="working", session_id=str(uuid.uuid4()), session_started=False, attempts=0)
        text = prompt("arbiter", spec_hash=r.state["spec_hash"], tests_dir=self.cfg.tests_dir, target=name,
                      spec_files=", ".join(f"`{p.relative_to(self.root)}`" for p in behaviour_spec_files(self.root, self.cfg.spec_dir)),
                      disputes="\n\n".join(details))
        res = self.agent_step("arbitrate", roles.arbiter(self.cfg), text, False, f"arbitrate-{r.state['disputes_used']}")
        verdicts = (res.output or {}).get("verdicts") or []
        r.set("arbitrate", status="done")
        r.emit("dispute.verdicts", task=task, verdicts=verdicts)
        wrong_tests = [v for v in verdicts if v["verdict"] == "test_wrong"]
        ambiguous = [v for v in verdicts if v["verdict"] == "spec_ambiguous"]
        if ambiguous:
            q = [{"rule": ",".join(v.get("rules", [])), "question": v.get("fix") or v["explanation"], "blocking": True} for v in ambiguous]
            r.t(task)["questions"] += q
            r.emit("spec.questions", task=task, questions=q)
            r.set(task, status="blocked", error="arbiter found the spec ambiguous for disputed tests")
            return
        if wrong_tests:
            self.amend_tests(wrong_tests)
            return
        # every dispute was rejected: the code must change; give the builder the arbiter's reasoning
        ts = r.t(task)
        for v in verdicts:
            for f in ts["last_checks"]["failures"]:
                if f.get("test", f["name"]) == v["test"]:
                    f["detail"] += f"\n\nARBITER (test is correct): {v['explanation']}"
        ts["last_output"] = {**(ts["last_output"] or {}), "test_disputes": []}
        if ts["repairs"] < self.cfg["workflow"]["max_repairs"]:
            r.set(task, status="repairing", repairs=ts["repairs"] + 1, attempts=0)
        else:
            r.set(task, status="failed", error="disputes rejected and repairs exhausted")

    def amend_tests(self, verdicts: list[dict]) -> None:
        r, ts = self.run, self.run.t("tests")
        problems = "\n\n".join(f"### {v['test']} is wrong (rules: {', '.join(v.get('rules', []))})\n{v['explanation']}\n\nRequired change: {v.get('fix', '')}"
                               for v in verdicts)
        r.set("tests", status="fixing", rounds=ts["rounds"] + 1, attempts=0)
        resume = bool(ts["session_id"]) and ts["session_started"]
        if not resume:
            r.set("tests", session_id=str(uuid.uuid4()))
        res = self.agent_step("tests", roles.tester(self.cfg), prompt("tester_fix", **self.tests_vars(), round=ts["rounds"], problems=problems),
                              resume, f"tests-amend-{r.state['disputes_used']}")
        out = res.output or {}
        failures = self.check_tests(out.get("untestable") or (self.tests_manifest() or {}).get("untestable") or [])
        if failures:
            r.set("tests", status="failed", error="amended test suite fails its gates")
            raise Stop("failed")
        approvals.approve(self.cfg, "tests", by="arbitration", note="; ".join(v["test"] for v in verdicts))
        r.set("tests", status="done")
        r.emit("tests.amended", tests=[v["test"] for v in verdicts])
        for task, s in r.state["tasks"].items():   # every target is re-judged by the amended suite
            if task.startswith("build:") and s["status"] in ("passed", "checking"):
                r.set(task, status="checking")

    # ── stage: interop ──
    def stage_interop(self, plan: dict) -> None:
        r = self.run
        if len(plan["targets"]) < 2:
            return r.set("interop", status="skipped")
        if r.t("interop")["status"] == "passed":
            return
        tests, _ = checks.list_suite(self.cfg)
        if not any(t["interop"] for t in tests or []):
            r.emit("step.skipped", task="interop", reason="the suite has no interop tests")
            return r.set("interop", status="skipped")
        r.set("interop", status="checking")
        res = checks.run_suite(self.cfg, ["--interop", ",".join(plan["targets"])],
                               lambda c: r.emit("test.case", task="interop", **{k: c[k] for k in ("name", "status", "rules", "duration_s", "detail")}))
        r.emit("test.summary", task="interop", passed=res["passed"], failed=res["failed"])
        r.t("interop")["last_checks"] = {"passed": res["passed"], "failed": res["failed"], "failures": res["failures"]}
        if res["failed"] or not res["passed"]:
            r.set("interop", status="failed", error=f"{res['failed']} interop tests failed" if res["passed"] else "no interop tests ran")
            raise Stop("failed")
        r.set("interop", status="passed")

    # ── stage: audit (mutation testing of the suite) ──
    def stage_audit(self, plan: dict) -> None:
        r, n = self.run, self.cfg["workflow"]["audit_mutants"]
        if n <= 0:
            return r.set("audit", status="skipped")
        if r.t("audit")["status"] == "passed":
            return
        r.set("audit", status="checking")
        covered = (self.tests_manifest() or {}).get("report", {}).get("covered", [])
        results = r.t("audit").setdefault("results", {})
        for name, t in plan["targets"].items():
            if name in results:
                continue
            results[name] = self.audit_target(name, t, covered, n)
            r.save()
        valid = [m for res in results.values() for m in res if m["outcome"] in ("killed", "survived")]
        killed = sum(m["outcome"] == "killed" for m in valid)
        rate = killed / len(valid) if valid else 0.0
        r.emit("audit.summary", killed=killed, valid=len(valid), kill_rate=round(rate, 2),
               survived=[f"{m['target']}:{m['rule']}" for m in valid if m["outcome"] == "survived"])
        r.t("audit")["kill_rate"] = rate
        if rate < self.cfg["workflow"]["audit_min_kill_rate"]:
            r.set("audit", status="failed", error=f"kill rate {rate:.0%} below {self.cfg['workflow']['audit_min_kill_rate']:.0%}")
            raise Stop("failed")
        r.set("audit", status="passed")

    def audit_target(self, name: str, t: dict, covered: list[str], n: int) -> list[dict]:
        r, task = self.run, f"audit:{name}"
        rng = random.Random(f"{r.state['run_id']}:{name}")
        try:  # only rules this target implements itself (TRACE maps them to files, not "n/a")
            trace = json.loads((self.root / t["dir"] / "TRACE.json").read_text())["rules"]
        except (OSError, ValueError, KeyError):
            trace = {}
        rules_all = [x.id for x in parse_rules(self.root, behaviour_spec_files(self.root, self.cfg.spec_dir))
                     if x.level == "MUST" and x.id in covered and isinstance(trace.get(x.id), list) and trace[x.id]]
        chosen = rng.sample(rules_all, min(n, len(rules_all)))
        dirs = [f"{self.cfg.generated_root}/.mut-{name}-{k}" for k in range(len(chosen))]
        for d in dirs:
            p = self.root / d
            if p.exists():
                shutil.rmtree(p)
            shutil.copytree(self.root / t["dir"], p, ignore=shutil.ignore_patterns("__pycache__", checks.MANIFEST))
        originals = {d: checks.snapshot(self.root / d) for d in dirs}
        try:
            r.set(task, status="working", session_id=str(uuid.uuid4()), session_started=False, attempts=0)
            text = prompt("saboteur", target=name, spec_hash=r.state["spec_hash"],
                          spec_files=", ".join(f"`{p.relative_to(self.root)}`" for p in target_spec_files(self.root, self.cfg.spec_dir, t["platform_spec"])),
                          assignments="\n".join(f"- `{d}/`: violate **{rule}**" for d, rule in zip(dirs, chosen)))
            self.agent_step(task, roles.saboteur(self.cfg, name, dirs), text, False, f"audit-{name}")
            out = []
            for d, rule in zip(dirs, chosen):
                if not any(checks.diff(originals[d], checks.snapshot(self.root / d)).values()):
                    outcome = "unchanged"
                elif checks.build(self.cfg, t, d):
                    outcome = "stillborn"
                else:
                    res = checks.run_suite(self.cfg, ["--target", name, "--dir-override", f"{name}={d}"])
                    outcome = "killed" if res["failed"] else "survived"
                    killers = sorted({c["test"] for c in res["cases"] if c["status"] != "pass"})
                r.emit("audit.mutant", task=task, rule=rule, outcome=outcome, **({"killed_by": killers} if outcome == "killed" else {}))
                out.append({"target": name, "rule": rule, "outcome": outcome})
            r.set(task, status="done")
            return out
        finally:
            for d in dirs:
                shutil.rmtree(self.root / d, ignore_errors=True)


def preflight(cfg: Config) -> tuple[dict, list[str]]:
    """Versions of everything that influences generation, and blocking problems."""
    from config import agent_cli_version
    versions, problems = probe_toolchain(cfg)
    cli = agent_cli_version(cfg)
    if not cli:
        problems.append(f"agent CLI {cfg['agent']['command']!r} not found on PATH")
    for name, svc in cfg["services"].items():
        for arg in svc["launch"][1:]:
            if "/" in arg and not (cfg.root / arg).exists():
                problems.append(f"service {name}: {arg} not found")
    return {"agent_cli": cli, "model": cfg["agent"]["model"] or "(CLI default)", "toolchain": versions}, problems
