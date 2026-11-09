"""Load and validate friend.toml. Every path the harness uses comes from here."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


HARNESS = Path(__file__).resolve().parent
KIT = HARNESS / "testkit"


def rel_or_abs(root: Path, p: Path) -> str:
    """Path relative to the project root when inside it, else absolute."""
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(p.resolve())


class ConfigError(Exception):
    pass


DEFAULTS = {
    "layout": {"spec": "spec", "generated": "clients", "tests": "tests/generated", "plan": "plan.json",
               "approvals": "approvals.json", "holdout": []},
    "agent": {"command": "claude", "model": "", "effort": "", "max_budget_usd": 20.0, "step_timeout_s": 2400,
              "agent_retries": 1},
    "workflow": {"max_test_rounds": 3, "auto_approve": False},
    "toolchain": {},
    "services": {},
}


@dataclass
class Config:
    root: Path
    raw: dict

    def __getitem__(self, k: str) -> dict:
        return self.raw[k]

    # layout helpers (all relative to root)
    @property
    def spec_dir(self) -> str:
        return self.raw["layout"]["spec"]

    @property
    def tests_dir(self) -> str:
        return self.raw["layout"]["tests"]

    @property
    def generated_root(self) -> str:
        return self.raw["layout"]["generated"]

    def path(self, rel: str) -> Path:
        return self.root / rel

    def runner(self) -> list[str]:
        """The test runner command, rooted at this project (the harness may live elsewhere)."""
        return ["python3", rel_or_abs(self.root, KIT / "runner.py"), "--root", "."]

    def suite_cmd(self, suite: str | None = None) -> list[str]:
        return self.runner() + ["--suite", suite or self.tests_dir]


def load(root: Path) -> Config:
    path = root / "friend.toml"
    if not path.exists():
        raise ConfigError(f"{path} not found")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"friend.toml: {e}")
    unknown = set(raw) - set(DEFAULTS) - {"project"}
    if unknown:
        raise ConfigError(f"friend.toml: unknown sections {sorted(unknown)}")
    for section, defaults in DEFAULTS.items():
        given = raw.get(section, {})
        if isinstance(defaults, dict) and section not in ("toolchain", "services"):
            bad = set(given) - set(defaults)
            if bad:
                raise ConfigError(f"friend.toml [{section}]: unknown keys {sorted(bad)}")
            for k, v in given.items():
                if not isinstance(v, type(defaults[k])) and not (isinstance(defaults[k], float) and isinstance(v, int)):
                    raise ConfigError(f"friend.toml [{section}].{k}: expected {type(defaults[k]).__name__}, got {v!r}")
        raw[section] = {**defaults, **given} if isinstance(defaults, dict) else given
    for name, tool in raw["toolchain"].items():
        if not isinstance(tool, dict) or not isinstance(tool.get("cmd"), str):
            raise ConfigError(f"friend.toml [toolchain.{name}]: needs cmd = \"...\"")
    for name, svc in raw["services"].items():
        if not isinstance(svc.get("launch"), list) or not isinstance(svc.get("ready"), str):
            raise ConfigError(f"friend.toml [services.{name}]: needs launch = [...] and ready = \"regex\"")
    return Config(root, raw)


def expand(argv: list[str] | None, d: str) -> list[str] | None:
    return None if argv is None else [a.replace("{dir}", d) for a in argv]
