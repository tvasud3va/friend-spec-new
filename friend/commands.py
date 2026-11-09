#!/usr/bin/env python3
"""friend: describe an app, approve a spec, get independently tested implementations.

Start here:
  friend spec approve            lint the spec (spec/, next to friend.toml) and record what you approved

More:
  friend spec lint | approve
  friend status · friend approve spec|plan|tests

Global: --root DIR (default: nearest directory upwards with friend.toml), --json (machine output).
Exit codes: 0 ok · 1 failed · 3 refused (config, approval).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import approvals  # noqa: E402
from config import ConfigError, load  # noqa: E402
from speclib import lint  # noqa: E402


def find_root(start: Path) -> Path:
    """Nearest directory at or above `start` containing friend.toml (like git finds .git); else `start`."""
    for d in [start, *start.parents]:
        if (d / "friend.toml").exists():
            return d
    return start


def cmd_approve(args, cfg) -> int:
    if args.artifact == "spec":
        problems = lint(cfg.root, cfg.spec_dir)
        if problems:
            print("spec lint failed:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 3
    try:
        rec = approvals.approve(cfg, args.artifact, by="human")
    except approvals.ApprovalError as e:
        print(str(e), file=sys.stderr)
        return 3
    print(json.dumps(rec) if args.json else f"approved {args.artifact} {rec['hash'][:12]}")
    return 0


def cmd_status(args, cfg) -> int:
    arts = {k: approvals.status(cfg, k) for k in approvals.KINDS}
    if args.json:
        print(json.dumps({"artifacts": {k: {"status": s, "reason": w} for k, (s, w) in arts.items()}}, indent=2))
        return 0
    for k, (s, why) in arts.items():
        print(f"{k:6} {s}" + (f"  ({why})" if why else ""))
    return 0


def cmd_spec(args, cfg) -> int:
    if args.spec_cmd == "lint":
        problems = lint(cfg.root, cfg.spec_dir)
        print("\n".join(problems) or "ok")
        return 1 if problems else 0
    if args.spec_cmd == "approve":
        args.artifact = "spec"
        return cmd_approve(args, cfg)
    return 3


def main() -> int:
    ap = argparse.ArgumentParser(prog="friend", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="project root (default: nearest directory upwards containing friend.toml)")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spec")
    ssub = sp.add_subparsers(dest="spec_cmd", required=True)
    sl = ssub.add_parser("lint")
    sa = ssub.add_parser("approve")
    a = sub.add_parser("approve")
    a.add_argument("artifact", nargs="?", default="spec", choices=approvals.KINDS)
    s = sub.add_parser("status")
    for p in (a, s, sl, sa):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else find_root(Path.cwd())
    if not (root / "friend.toml").exists():
        print(f"no friend.toml in {root} or any parent directory", file=sys.stderr)
        return 3
    try:
        cfg = load(root)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 3
    return {"approve": cmd_approve, "status": cmd_status, "spec": cmd_spec}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
