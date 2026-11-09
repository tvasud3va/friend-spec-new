#!/usr/bin/env python3
"""friendkit runner: discovers and runs a black-box test suite against generated targets.

  runner.py --suite DIR --target NAME            every test, all roles played by NAME
  runner.py --suite DIR --interop A,B[,C]        interop tests, every mixed assignment of targets to roles
  runner.py --suite DIR --null-target            every test against a program that does nothing (vacuity check)
  runner.py --suite DIR --list                   import the suite, print its tests (JSON)
  runner.py --suite DIR --rules                  rule -> tests coverage map (JSON)
Options: --only a,b  --json  --dir-override NAME=PATH (run a target from another directory, e.g. a mutant)
Targets come from plan.json (generated) or friend.toml [targets]; services from friend.toml [services].
Exit code 0 iff every selected test passed (or, for --list/--rules, the suite imported cleanly).
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import os
import sys
import time
import tomllib
import traceback
from pathlib import Path

KIT = Path(__file__).resolve().parent
ROOT = KIT.parents[1]  # default project root; --root overrides
sys.path.insert(0, str(KIT))

import friendkit  # noqa: E402
from friendkit import TESTS, Ctx, TestFailure  # noqa: E402

NULL_IMPL = [sys.executable, str(KIT / "null_impl.py")]


def load_targets(overrides: dict[str, str]) -> tuple[dict[str, dict], dict[str, dict]]:
    with open(ROOT / "friend.toml", "rb") as f:
        cfg = tomllib.load(f)
    plan_path = ROOT / cfg.get("layout", {}).get("plan", "plan.json")
    targets = json.loads(plan_path.read_text())["targets"] if plan_path.exists() else cfg.get("targets", {})
    for name, t in targets.items():
        d = overrides.get(name, t["dir"])
        t["launch_argv"] = [resolve(a.replace("{dir}", d)) for a in t["launch"]]
    return targets, cfg.get("services", {})


def resolve(arg: str) -> str:
    """Make repo-relative paths absolute so programs can be launched from any working directory."""
    p = ROOT / arg
    return str(p) if ("/" in arg and (p.exists() or p.parent.exists())) else arg


def discover(suite: Path) -> list[str]:
    errors = []
    sys.path.insert(0, str(suite))
    for path in sorted(suite.glob("test_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            errors.append(f"{path.name}: {traceback.format_exc(limit=3)}")
    return errors


def run_one(test: dict, roles: dict[str, str], targets: dict, services: dict, null: bool) -> dict:
    ctx = Ctx(ROOT, roles, targets, services, launch_override=NULL_IMPL if null else None)
    t0 = time.monotonic()
    try:
        test["fn"](ctx)
        status, detail = "pass", ""
    except TestFailure as e:
        status, detail = "fail", str(e)
    except Exception:
        status, detail = "error", traceback.format_exc(limit=4)
    finally:
        ctx.close()
    mixed = {k: v for k, v in roles.items() if k != "default"}
    label = test["name"] + (f"[{','.join(f'{k}={v}' for k, v in mixed.items())}]" if len(set(mixed.values())) > 1 else "")
    return {"type": "test.case", "name": label, "test": test["name"], "status": status, "rules": test["rules"],
            "detail": detail, "duration_s": round(time.monotonic() - t0, 2), "targets": sorted(set(roles.values()))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="project root (default: the repository containing this kit)")
    ap.add_argument("--suite", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--target")
    mode.add_argument("--interop")
    mode.add_argument("--null-target", action="store_true")
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--rules", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dir-override", action="append", default=[])
    args = ap.parse_args()
    global ROOT
    if args.root:
        ROOT = Path(args.root).resolve()
    suite = (ROOT / args.suite).resolve()
    if not suite.is_dir():
        ap.error(f"suite {args.suite} not found")
    errors = discover(suite)
    if errors:
        print(json.dumps({"type": "suite.error", "errors": errors}) if args.json else "\n".join(errors), flush=True)
        return 1
    if args.list:
        print(json.dumps([{k: t[k] for k in ("name", "rules", "roles", "interop", "module", "doc")} for t in TESTS], indent=1))
        return 0
    if args.rules:
        cov: dict[str, list[str]] = {}
        for t in TESTS:
            for r in t["rules"]:
                cov.setdefault(r, []).append(t["name"])
        print(json.dumps(cov, indent=1, sort_keys=True))
        return 0

    targets, services = load_targets(dict(o.split("=", 1) for o in args.dir_override))
    selected = [t for t in TESTS if not args.only or t["name"] in args.only.split(",")]
    if args.null_target:
        plan = [(t, {"default": "null", **{r: "null" for r in t["roles"]}}) for t in selected]
    elif args.target:
        if args.target not in targets:
            ap.error(f"unknown target {args.target!r}; known: {sorted(targets)}")
        plan = [(t, {"default": args.target, **{r: args.target for r in t["roles"]}}) for t in selected]
    else:
        names = args.interop.split(",")
        for n in names:
            if n not in targets:
                ap.error(f"unknown target {n!r}")
        plan = []
        for t in (t for t in selected if t["interop"]):
            for combo in itertools.product(names, repeat=len(t["roles"])):
                if len(set(combo)) > 1:
                    plan.append((t, {"default": combo[0], **dict(zip(t["roles"], combo))}))
    if args.null_target:
        targets = {"null": {"launch_argv": NULL_IMPL}}
        friendkit.TIMEOUT_CAP = min(float(os.environ.get("FRIEND_TEST_TIMEOUT", "2")), 2.0)

    results = []
    for test, roles in plan:
        res = run_one(test, roles, targets, services, args.null_target)
        results.append(res)
        if args.json:
            print(json.dumps(res), flush=True)
        else:
            print(f"{res['status'].upper():5} {res['name']} ({res['duration_s']}s)", flush=True)
            if res["detail"]:
                print("      " + res["detail"].replace("\n", "\n      "), flush=True)
    passed = sum(r["status"] == "pass" for r in results)
    summary = {"type": "test.summary", "passed": passed, "failed": len(results) - passed, "total": len(results)}
    print(json.dumps(summary) if args.json else f"\n{passed}/{len(results)} passed", flush=True)
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
