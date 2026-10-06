"""One fight for seeds or gold, then back to the day plan.

The mob is the one named for that farm. The round casts an area hit from the
deck that is already equipped, or a single hit on one enemy, or it passes.
Energy is checked again by the day plan after the fight, so a full globe is
not left inside a pull.
"""

from __future__ import annotations

import asyncio

from loguru import logger

PLACES = {
    "splithoof": ("Savarstaad Pass", "Boar Camp"),
    "halfang": ("Vestrilund",),
    "early-gold": ("Triton Avenue", "Cyclops Lane"),
}
NEEDLES = {
    "splithoof": ("splithoof", "barbarian"),
    "halfang": ("halfang", "bristlecrown"),
    "early-gold": ("lost soul", "rotten", "dark fairy", "ghoul"),
}
ROUNDS = 30


def mob_matches(name: str, needles: tuple[str, ...]) -> bool:
    low = (name or "").lower()
    return any(needle in low for needle in needles)


async def fight_one(client, target: str, controller=None) -> bool:
    """Travel, pull one matching mob, and play the duel. False if no fight started."""
    if controller:
        controller.allow_idle(240)
    try:
        if await _hurt(client):
            logger.info("farm: health is low; not pulling")
            return False
        if not await _go(client, target):
            return False
        mob = await _find_mob(client, NEEDLES.get(target, ()))
        if mob is None:
            logger.info(f"farm: no {target} mob in this zone")
            return False
        try:
            pos = await mob.location()
            await client.teleport(pos, allow_engage=True)
        except Exception as exc:
            logger.info(f"farm: could not reach the mob ({exc})")
            return False
        for _ in range(16):
            if await client.in_battle():
                break
            await asyncio.sleep(0.5)
        if not await client.in_battle():
            logger.info("farm: the mob did not start a fight")
            return False
        await _play(client, controller)
        zone = await client.zone_name() or ""
        logger.info(f"farm: the fight in {zone.split('/')[-1] or 'this zone'} is over")
        return True
    finally:
        if controller:
            controller.end_idle()


async def _go(client, target: str) -> bool:
    from .travel import go_to_zone
    from .travel_data import _data, named_zone

    places = PLACES.get(target, ())
    here = (await client.zone_name() or "").lower()
    if any(place.lower() in here for place in places):
        return True
    try:
        display = _data()[1]
    except Exception as exc:
        logger.info(f"farm: no zone list ({exc})")
        return False
    for place in places:
        dest = named_zone(place, display)
        if dest and await go_to_zone(client, dest):
            logger.info(f"farm: at {place}")
            return True
    logger.info(f"farm: could not reach {', '.join(places) or target}")
    return False


async def _find_mob(client, needles: tuple[str, ...]):
    if not needles:
        return None
    try:
        mobs = await client.get_mobs()
    except Exception as exc:
        logger.info(f"farm: mobs unreadable ({exc})")
        return None
    for mob in mobs:
        try:
            template = await mob.object_template()
            name = (await template.object_name() or "") if template else ""
        except Exception:
            continue
        if mob_matches(name, needles):
            return mob
    return None


async def _hurt(client) -> bool:
    try:
        current = int(await client.stats.current_hitpoints())
        maximum = int(await client.stats.max_hitpoints())
    except Exception:
        return False
    return maximum > 0 and current / maximum < 0.35


async def _play(client, controller):
    from wizwalker.combat import AoeHandler

    handler = AoeHandler(client)
    for _ in range(ROUNDS):
        if controller:
            await controller.checkpoint()
        if not await handler.in_combat():
            return
        try:
            await handler.wait_for_planning_phase()
        except Exception:
            return
        try:
            await _cast_or_pass(handler)
        except Exception as exc:
            logger.info(f"farm: round failed ({exc}); passing")
            try:
                await handler.pass_button()
            except Exception:
                return
        await asyncio.sleep(0.4)


async def _cast_or_pass(handler) -> str:
    aoes = await handler.get_damaging_aoes(check_enchanted=False)
    if aoes and await aoes[0].is_castable():
        await aoes[0].cast(None)
        return "aoe"
    enemies = []
    for member in await handler.get_members():
        try:
            if await member.is_monster() and not await member.is_dead():
                enemies.append(member)
        except Exception:
            continue
    for card in await handler.get_cards():
        try:
            if not await card.is_castable():
                continue
            kind = (await card.type_name() or "").lower()
        except Exception:
            continue
        if kind in ("enchantment", "heal", "healing", "ward", "charm", "global"):
            continue
        if enemies:
            await card.cast(enemies[0])
            return "single"
    await handler.pass_button()
    return "pass"
