"""Read the wizard and carry out one decision from `dayplan`.

The live plan dances while energy can pay, feeding a snack after each win,
then farms. Garden, fishing, and hatch still run if a decision names them.
The day plan does not name them. The barn shop is still not clicked.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from . import ui
from .dayplan import FARMS, Decision, Snapshot, choose, fishing_quest, places_for, reagent_from_quest
from .garden import garden_state, note_live
from .petdance import load_pet, save_pet
from .petplan import note_equipped

REAGENT_REQUEST = Path("state") / "reagent.request"
FISH_REQUEST = Path("state") / "fish.request"
PAUSE_BETWEEN = 20.0  # seconds before repeating a step that could not act


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


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
    try:
        await note_live(client)
    except Exception as exc:
        logger.debug(f"garden: live read failed ({exc})")
    pet = load_pet()
    updated = note_equipped(pet)
    if updated != pet:
        save_pet(updated)
        pet = updated
    garden = garden_state()
    stats = pet.get("stats") if isinstance(pet.get("stats"), dict) else None
    talents = tuple(pet.get("talents") or ())
    base = pet.get("base") if isinstance(pet.get("base"), dict) else {}
    base_stats = base.get("stats") if isinstance(base.get("stats"), dict) else None
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
        couch_potatoes=int(garden.get("couch_potatoes") or 0),
        boom_shroom_seeds=int(garden["boom_shroom_seeds"]),
        plants_need_care=bool(garden["plants_need_care"]),
        reagent=asked if asked else (reagent_from_quest(goal) if cfg.reagents.enabled else None),
        fishing=_read(FISH_REQUEST) is not None or fishing_quest(goal),
        pet_training=cfg.pet.auto,
        mode=cfg.mode,
        base_kind=base.get("kind"),
        base_stage=base.get("stage"),
        base_talents=tuple(base.get("talents") or ()),
        base_stats=base_stats,
    )


async def carry_out(decision: Decision, client, dancer, gatherer) -> None:
    """Do `decision`. Returns when that attempt is over (a dance trip, one sweep, or a wait)."""
    if decision.action == "dance":
        # The live loop feeds after every win and keeps playing past a miss.
        if decision.spend:
            dancer.feed = True
        elif decision.games <= 0:
            dancer.cfg.goals["ghulture"] = decision.train_until or "adult"
            dancer.cfg.default_goal = decision.train_until or "adult"
        await dancer.trip(
            decision.games,
            energy_floor=decision.energy_floor,
            past_goal=decision.spend or decision.games > 0,
            keep_on_miss=decision.spend,
        )
        return
    if decision.action == "reagent":
        got = await gatherer.tick(only=decision.target or None, places=places_for(decision.target))
        if not got:
            await asyncio.sleep(PAUSE_BETWEEN)
        return
    acted = False
    if decision.action == "garden":
        from .garden import tend

        acted = await tend(client, dancer.controller, decision.why)
    elif decision.action == "fish":
        from .fishing import fish_once

        acted = await fish_once(client, dancer.controller) > 0
    elif decision.action == "hatch":
        from .hatch import hatch_once

        result = await hatch_once(client, dancer, dancer.controller)
        acted = result == "hatched"
        if result != "hatched":
            logger.info(f"hatch: {decision.why}")
    elif decision.action == "buy_barn":
        logger.info(
            "garden: buy the Red Barn Farm for 125,000 gold (it is a couch-potato like). "
            "The housing shop is not mapped, so the gold is not spent"
        )
    elif decision.action == "gold":
        from .farm import fight_one

        logger.info(f"gold: {FARMS.get(decision.target, decision.target)}")
        acted = await fight_one(client, decision.target, dancer.controller)
    if not acted:
        await asyncio.sleep(PAUSE_BETWEEN)


def decide(snap: Snapshot) -> Decision:
    return choose(snap)
