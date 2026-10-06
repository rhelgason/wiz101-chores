"""Read the wizard and carry out one decision from `dayplan`.

Dance games and a named reagent sweep actually run. Hatching, garden spells,
fishing casts, and gold fights do not: those screens are not mapped, and a
guessed click spends energy or gold on a failure. The decision is still made
and logged, and the loop comes back to it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from . import ui
from .dayplan import FARMS, Decision, Snapshot, choose, fishing_quest, places_for, reagent_from_quest
from .petdance import load_pet

GARDEN_FILE = Path("state") / "garden.json"
REAGENT_REQUEST = Path("state") / "reagent.request"
FISH_REQUEST = Path("state") / "fish.request"
PAUSE_BETWEEN = 20.0  # seconds between tries that cannot click the screen yet


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def garden_state() -> dict:
    """Rank, barn, and seeds. Missing file means a new gardener on boom shrooms."""
    base = {
        "rank": 1,
        "red_barn": False,
        "couch_potato_seeds": 0,
        "boom_shroom_seeds": 0,
        "plants_need_care": False,
    }
    try:
        data = json.loads(GARDEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if isinstance(data, dict):
        base.update({k: data[k] for k in base if k in data})
    return base


async def _stat(client, *names: str) -> int | None:
    stats = getattr(client, "stats", None)
    if stats is None:
        return None
    for name in names:
        fn = getattr(stats, name, None)
        if fn is None:
            continue
        try:
            value = fn()
            if asyncio.iscoroutine(value):
                value = await value
            return int(value)
        except Exception:
            continue
    return None


async def look(client, cfg, dancer) -> Snapshot:
    """Energy, gold, the tracked quest, and the saved pet and garden."""
    energy = energy_max = None
    try:
        energy, energy_max = await dancer.energy()
    except Exception:
        pass
    if energy_max is None:
        energy_max = await _stat(client, "energy_max", "max_energy")
    goal = ""
    try:
        goal = await ui.quest_goal(client) or ""
    except Exception:
        pass
    asked = _read(REAGENT_REQUEST)
    pet = load_pet()
    garden = garden_state()
    stats = pet.get("stats") if isinstance(pet.get("stats"), dict) else None
    talents = tuple(pet.get("talents") or ())
    return Snapshot(
        energy=energy,
        energy_max=energy_max,
        gold=await _stat(client, "current_gold", "gold"),
        level=await _stat(client, "reference_level", "level"),
        pet_kind=pet.get("kind"),
        pet_stage=pet.get("stage"),
        manifested=talents,
        stats=stats,
        confirm_mega=bool(pet.get("confirm_mega")),
        gardening_rank=int(garden["rank"]),
        red_barn=bool(garden["red_barn"]),
        couch_potato_seeds=int(garden["couch_potato_seeds"]),
        boom_shroom_seeds=int(garden["boom_shroom_seeds"]),
        plants_need_care=bool(garden["plants_need_care"]),
        reagent=asked if asked else (reagent_from_quest(goal) if cfg.reagents.enabled else None),
        fishing=_read(FISH_REQUEST) is not None or fishing_quest(goal),
        pet_training=cfg.pet.auto,
        mode=cfg.mode,
    )


async def carry_out(decision: Decision, client, dancer, gatherer) -> None:
    """Do `decision`. Returns when that attempt is over (a dance trip, one sweep, or a wait)."""
    if decision.action == "dance":
        dancer.cfg.goals["ghulture"] = decision.train_until or "adult"
        dancer.cfg.default_goal = decision.train_until or "adult"
        await dancer.trip(0, energy_floor=decision.energy_floor)
        return
    if decision.action == "reagent":
        got = await gatherer.tick(only=decision.target or None, places=places_for(decision.target))
        if not got:
            await asyncio.sleep(PAUSE_BETWEEN)
        return
    if decision.action == "garden":
        await _tend_from_home(client)
    elif decision.action == "fish":
        logger.info(
            "fishing: the quest is tracked, and the minigame is not mapped. "
            "A cast would spend the energy and fail the reel, so none is cast"
        )
    elif decision.action == "hatch":
        logger.info(
            "hatch: take the ghulture to the Pet Pavilion kiosk and hatch with a perfect "
            "ghulture (death-dealer, death-giver, pain-giver, mighty, armor breaker; "
            "255/250/260/260/250). The hatch window is not mapped, so no gold is spent"
        )
    elif decision.action == "buy_barn":
        logger.info(
            "garden: buy the Red Barn Farm for 125,000 gold (it is a couch-potato like). "
            "The housing shop is not mapped, so the gold is not spent"
        )
    elif decision.action == "gold":
        logger.info(
            f"gold: {FARMS.get(decision.target, decision.target)}. "
            "Combat is not in this tree, so no fight is started"
        )
    await asyncio.sleep(PAUSE_BETWEEN)


async def _tend_from_home(client):
    """Stand in the house when plants need care. Spells are not cast blind."""
    from .travel import go_home, is_house

    try:
        zone = await client.zone_name() or ""
    except Exception:
        zone = ""
    if not is_house(zone):
        logger.info("garden: going home so the plants can be tended")
        try:
            await go_home(client)
        except Exception as exc:
            logger.info(f"garden: could not go home ({exc})")
    logger.info(
        "garden: cast a spell only when a plant is showing that need. "
        "Pests on these plants are rank 1. Likes are placed, not cast: "
        "Litter, King Parsley, a pixie, Sandwich Station, Tropical Garden Gnome, "
        "and the Red Barn itself. No spell path is mapped, so nothing is cast"
    )


def decide(snap: Snapshot) -> Decision:
    return choose(snap)
