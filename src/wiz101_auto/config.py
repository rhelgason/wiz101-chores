"""Bot configuration, loaded from YAML (see config.example.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

MODES = ("chores", "pet", "reagents")  # chores is the day plan in PLAYBOOK.md


@dataclass
class SafetyConfig:
    stop_key: str = "ctrl+shift+q"
    pause_key: str = "ctrl+shift+p"
    max_hours: float = 0.0  # 0 = no limit: run until stopped
    mouseless: bool = True  # clicks through a memory hook so the real mouse stays free


@dataclass
class PetConfig:
    """Pet dance game. `auto` lets the day plan train the equipped ghulture.
    It stops at `goals` (by pet kind) or `default_goal`. The plan sets the
    ghulture goal itself: adult, or mega for the one confirm pet."""

    auto: bool = True
    goals: dict = field(default_factory=dict)
    default_goal: str = "adult"
    feed: bool = True  # feed the first snack offered after each win


@dataclass
class ReagentConfig:
    """Reagents are gathered when a quest or `reagent "name"` asks. `enabled`
    follows the tracked quest. There is no all-day sweep of every node.
    `hop_zones` is how far to walk once the named zones are empty."""

    enabled: bool = True
    hop_zones: int = 1


@dataclass
class Config:
    mode: str = "chores"  # the day plan | pet only | a reagent sweep only
    log_file: str = "wiz101-auto.log"
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    pet: PetConfig = field(default_factory=PetConfig)
    reagents: ReagentConfig = field(default_factory=ReagentConfig)


def _merge(obj: Any, data: dict[str, Any], path: str = "") -> Any:
    known = {f.name: f for f in fields(obj)}
    for key, value in (data or {}).items():
        if key not in known:
            raise ValueError(f"unknown config key: {path}{key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"{path}{key} must be a mapping")
            _merge(current, value, f"{path}{key}.")
        else:
            if isinstance(current, float) and isinstance(value, int):
                value = float(value)
            if current is not None and not isinstance(value, type(current)):
                raise ValueError(f"{path}{key} should be {type(current).__name__}, got {value!r}")
            setattr(obj, key, value)
    return obj


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    if path:
        p = Path(path)
        if p.exists():
            _merge(cfg, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
        else:
            raise FileNotFoundError(p)
    from .safety import parse_hotkey

    parse_hotkey(cfg.safety.stop_key)
    parse_hotkey(cfg.safety.pause_key)
    if cfg.mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, not {cfg.mode!r}")
    if cfg.reagents.hop_zones < 0:
        raise ValueError("reagents.hop_zones must be 0 or more")
    return cfg
