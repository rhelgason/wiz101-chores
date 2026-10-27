"""Bot configuration, loaded from YAML (see config.example.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

MODES = ("chores", "pet", "reagents", "seeds", "gather", "tracker")  # chores is the day plan in PLAYBOOK.md


@dataclass
class SafetyConfig:
    stop_key: str = "ctrl+shift+q"
    pause_key: str = "ctrl+shift+p"
    max_hours: float = 0.0  # 0 = no limit: run until stopped
    mouseless: bool = True  # clicks through a memory hook so the real mouse stays free
    # Paused, take the mouse hook out so the player can click. Each pause is
    # then two more writes to game code; off, a pause only holds the bot.
    pause_releases_mouse: bool = False
    # Experimental: jump the wizard with data writes only (jump.py). One more
    # hook goes in at connect; no game code is written after it. Off: walk.
    data_jumps: bool = False


@dataclass
class PetConfig:
    """Pet dance game. `auto` lets the day plan train the equipped ghulture.
    It stops at `goals` (by pet kind) or `default_goal`. The plan sets the
    ghulture goal itself: adult, or mega for the one confirm pet."""

    auto: bool = True
    goals: dict = field(default_factory=dict)
    default_goal: str = "adult"
    feed: bool = True  # feed the first snack offered after each win
    # Dance stage: "lowest" (the stage that trains the stats furthest below
    # their caps; rotates until the stat block is read), "rotate", "random",
    # or a stage name ("Wizard City", "Krokotopia", ...).
    stage: str = "lowest"


@dataclass
class ReagentConfig:
    """Reagents are gathered when a quest or `reagent "name"` asks. `enabled`
    follows the tracked quest. There is no all-day sweep of every node.
    `hop_zones` is how far to walk once the named zones are empty."""

    enabled: bool = True
    hop_zones: int = 1


@dataclass
class SeedsConfig:
    """`mode: seeds`. Home is where the wizard stands when the run starts (Boar
    Camp). Only creatures within `leash` of home are pulled."""

    leash: float = 4500.0
    rest_minutes: float = 4.0  # stand still this long for health or mana before fighting anyway
    northguard_trips: bool = True  # low and nothing nearby: Mark, walk to Northguard for wisps, Recall
    northguard_full: float = 0.98  # a trip fills the globe this far (then tops up the other one)
    northguard_minutes: float = 10.0  # ... or stays this long at most
    sell: bool = True  # bag nearly full: backpack Quick Sell of this run's loot, in place
    # Other creatures instead of the boars, in turn; the run stops after the
    # last. Each: a name, or {creature, count (30), zone, refill}. Empty: seeds.
    hunts: list = field(default_factory=list)


@dataclass
class GatherConfig:
    """`mode: gather`: walk a reagent's spawn spots (read from the zone files),
    pick up what has spawned, keep away from creatures, flee any duel."""

    reagent: str = "Black Lotus"
    world: str = ""  # empty: the reagent's usual world (gather.REAGENT_WORLDS)
    zones: list[str] = field(default_factory=list)  # display names or zone paths; empty: the best few
    zone_count: int = 3  # how many of the best zones to tour in turn
    # With safety.data_jumps (the pilot): jump only, never walk, in one zone
    # (the one the wizard is in, if it lists spots; else the best). Rounds wait
    # this long between them for respawns.
    respawn_minutes: float = 4.0


@dataclass
class Config:
    mode: str = "chores"  # the day plan | pet only | a reagent sweep | Savarstaad seeds
    log_file: str = "wiz101-auto.log"
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    pet: PetConfig = field(default_factory=PetConfig)
    reagents: ReagentConfig = field(default_factory=ReagentConfig)
    seeds: SeedsConfig = field(default_factory=SeedsConfig)
    gather: GatherConfig = field(default_factory=GatherConfig)


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


SECTIONS = ("seeds", "gather", "pet", "safety", "reagents")


def load_config(path: str | Path | None, overrides: dict[str, Any] | None = None) -> Config:
    """Defaults, then the YAML file (optional), then `overrides` ({"seeds.leash": 4000, ...},
    from the command line), then validation."""
    cfg = Config()
    if path:
        p = Path(path)
        if p.exists():
            _merge(cfg, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
        else:
            raise FileNotFoundError(p)
    for key, value in (overrides or {}).items():
        section, _, name = key.rpartition(".")
        target = getattr(cfg, section) if section else cfg
        _merge(target, {name: value}, f"{section}." if section else "")
    return validate(cfg)


def parse_hunt(text: str) -> dict[str, Any]:
    """`--hunt` text: 'NAME', 'NAME:COUNT', 'NAME@World/Zone' or 'NAME:COUNT@World/Zone'."""
    spec, _, zone = text.partition("@")
    name, _, count = spec.rpartition(":") if ":" in spec else (spec, "", "")
    hunt: dict[str, Any] = {"creature": name.strip()}
    if count.strip():
        if not count.strip().isdigit():
            raise ValueError(f"--hunt {text!r}: the count after ':' must be a number")
        hunt["count"] = int(count)
    if zone.strip():
        hunt["zone"] = zone.strip()
    _check_hunt(hunt)
    return hunt


def validate(cfg: Config) -> Config:
    from .safety import parse_hotkey

    parse_hotkey(cfg.safety.stop_key)
    parse_hotkey(cfg.safety.pause_key)
    if cfg.mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, not {cfg.mode!r}")
    if cfg.reagents.hop_zones < 0:
        raise ValueError("reagents.hop_zones must be 0 or more")
    from .petplan import DANCE_STAGES, STAGE_CHOICES

    stages = [*STAGE_CHOICES, *(name.lower() for name, _gains in DANCE_STAGES)]
    if cfg.pet.stage.strip().lower() not in stages:
        raise ValueError(f"pet.stage must be one of {', '.join(stages)}, not {cfg.pet.stage!r}")
    if not cfg.gather.reagent.strip():
        raise ValueError("gather.reagent must name a reagent")
    if cfg.gather.zone_count < 1:
        raise ValueError("gather.zone_count must be 1 or more")
    if cfg.seeds.leash < 500:
        raise ValueError("seeds.leash must be at least 500")
    for item in cfg.seeds.hunts:
        _check_hunt(item)
    return cfg


HUNT_KEYS = {"creature": str, "count": int, "zone": str, "refill": str}


def _check_hunt(item: Any) -> None:
    if isinstance(item, str):
        item = {"creature": item}
    if not isinstance(item, dict):
        raise ValueError(f"seeds.hunts: each hunt is a creature name or a mapping, not {item!r}")
    for key, value in item.items():
        if key not in HUNT_KEYS:
            raise ValueError(f"seeds.hunts: unknown key {key!r} (known: {', '.join(HUNT_KEYS)})")
        if not isinstance(value, HUNT_KEYS[key]) or isinstance(value, bool):
            raise ValueError(f"seeds.hunts: {key} should be {HUNT_KEYS[key].__name__}, got {value!r}")
    if not str(item.get("creature", "")).strip():
        raise ValueError("seeds.hunts: every hunt needs a creature")
    if item.get("count", 30) < 1:
        raise ValueError("seeds.hunts: count must be 1 or more")
