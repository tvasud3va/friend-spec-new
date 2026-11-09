"""Load and validate friend.toml. Every path the harness uses comes from here."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


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
