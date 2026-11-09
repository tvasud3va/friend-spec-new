#!/usr/bin/env python3
"""friend: describe an app, approve a spec, get independently tested implementations.

Start here:
  friend spec lint               check the spec (spec/, next to friend.toml): rule format, unique IDs, layout

Global: --root DIR (default: nearest directory upwards with friend.toml).
Exit codes: 0 ok · 1 failed · 3 refused (config).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ConfigError, load  # noqa: E402
from speclib import lint  # noqa: E402


def find_root(start: Path) -> Path:
    """Nearest directory at or above `start` containing friend.toml (like git finds .git); else `start`."""
    for d in [start, *start.parents]:
        if (d / "friend.toml").exists():
            return d
    return start


def cmd_spec(args, cfg) -> int:
    if args.spec_cmd == "lint":
        problems = lint(cfg.root, cfg.spec_dir)
        print("\n".join(problems) or "ok")
        return 1 if problems else 0
    return 3


def main() -> int:
    ap = argparse.ArgumentParser(prog="friend", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="project root (default: nearest directory upwards containing friend.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spec")
    ssub = sp.add_subparsers(dest="spec_cmd", required=True)
    ssub.add_parser("lint")
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
    return {"spec": cmd_spec}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
