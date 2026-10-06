"""One fight for seeds or gold, then back to the day plan.

The mob is the one named for that farm. A healthy round casts an area hit
from the deck that is already equipped, or a single hit, or it passes. Below
about 60% health the round casts a death drain on an enemy first (Ghoul,
Wraith, Vampire, Skeletal Dragon, or a card that steals health). Sacrifice
is not used. A defeat, or health that is already low, takes a potion or a
health wisp and returns. The day plan sends the wizard back. Nothing here
stops the session.
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
HEAL_BELOW = 0.60  # cast a death drain while health is under this
PULL_BELOW = 0.35  # gather health before starting another fight
# Death drains. Cast on an enemy; the caster is the one who is healed.
DRAIN_NAMES = ("ghoul", "wraith", "vampire", "skeletal dragon")
DRAIN_EFFECTS = {"steal_health", "steal_over_time"}


def mob_matches(name: str, needles: tuple[str, ...]) -> bool:
    low = (name or "").lower()
    return any(needle in low for needle in needles)


def needs_heal(ratio: float | None) -> bool:
    return ratio is not None and ratio < HEAL_BELOW


def too_hurt_to_pull(ratio: float | None) -> bool:
    """Unread health does not skip the pull. Very low health does."""
    return ratio is not None and ratio < PULL_BELOW


def is_death_heal(name: str, effects: list[str] | None = None) -> bool:
    """A death drain that heals the caster. Sacrifice spends health, so it is not one."""
    low = (name or "").lower()
    if "sacrifice" in low:
        return False
    if any(needle in low for needle in DRAIN_NAMES):
        return True
    kinds = {str(effect).split(".")[-1].lower() for effect in (effects or [])}
    return bool(kinds & DRAIN_EFFECTS)


async def fight_one(client, target: str, controller=None) -> bool:
    """Travel, pull one matching mob, and play the duel. False if no fight started.

    A defeat or a failed pull is a retry. The controller is not stopped.
    """
    from .safety import BotStopped

    if controller:
        controller.allow_idle(240)
    try:
        await _recover(client, controller)
        if too_hurt_to_pull(await _ratio(client)):
            logger.info("farm: health is still low; gathering it again before the next pull")
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
        await _note_potato(client)
        zone = ""
        try:
            zone = await client.zone_name() or ""
        except Exception:
            pass
        logger.info(f"farm: the fight in {zone.split('/')[-1] or 'this zone'} is over")
        await _recover(client, controller)
        return True
    except BotStopped:
        raise
    except Exception as exc:
        logger.info(f"farm: the pull failed ({exc}); gathering health and going back")
        try:
            await _recover(client, controller)
        except BotStopped:
            raise
        except Exception:
            pass
        return False
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


async def _ratio(client) -> float | None:
    try:
        current = int(await client.stats.current_hitpoints())
        maximum = int(await client.stats.max_hitpoints())
    except Exception:
        return None
    if maximum <= 0:
        return None
    return current / maximum


async def _recover(client, controller=None) -> None:
    """Potion, then a health wisp, when health is low or the wizard is down."""
    ratio = await _ratio(client)
    if ratio is not None and ratio <= 0:
        zone = ""
        try:
            zone = await client.zone_name() or ""
        except Exception:
            pass
        if controller:
            controller.record_death(zone)
        logger.info("farm: defeated; gathering health and going back")
        await asyncio.sleep(3.0)
        ratio = await _ratio(client)
    if not needs_heal(ratio):
        return
    if await _potion(client):
        ratio = await _ratio(client)
    if needs_heal(ratio):
        await _wisp(client)


async def _potion(client) -> bool:
    try:
        charge = float(await client.stats.potion_charge())
    except Exception:
        return False
    if charge < 1.0:
        return False
    use = getattr(client, "use_potion", None)
    if use is None:
        return False
    try:
        used = await use()
    except Exception as exc:
        logger.info(f"farm: potion failed ({exc})")
        return False
    if used:
        logger.info("farm: used a potion")
        await asyncio.sleep(0.6)
    return bool(used)


async def _wisp(client) -> bool:
    finder = getattr(client, "find_closest_health_wisp", None)
    if finder is None:
        logger.info("farm: this client cannot look for a health wisp")
        return False
    try:
        wisp = await finder(only_safe=False)
    except Exception as exc:
        logger.info(f"farm: health wisp lookup failed ({exc})")
        return False
    if wisp is None:
        logger.info("farm: no health wisp in this zone")
        return False
    try:
        pos = await wisp.location()
        await client.teleport(pos)
    except Exception as exc:
        logger.info(f"farm: could not reach a health wisp ({exc})")
        return False
    from .safe_teleport import teleport_aborted

    if teleport_aborted(client):
        logger.info("farm: the health wisp is on an enemy; trying again next pass")
        return False
    await asyncio.sleep(0.8)
    logger.info("farm: stood on a health wisp")
    return True


async def _note_potato(client) -> None:
    """Count a seed the reward line named when the backpack read did not grow."""
    from . import ui
    from .garden import garden_state, loot_mentioned_potato, note_live, save_garden

    before = int(garden_state().get("couch_potatoes") or 0)
    try:
        await note_live(client)
    except Exception as exc:
        logger.debug(f"farm: backpack unread after the fight ({exc})")
    after = int(garden_state().get("couch_potatoes") or 0)
    try:
        texts = await ui.visible_texts(client)
    except Exception:
        texts = []
    if loot_mentioned_potato(texts) and after <= before:
        state = garden_state()
        state["couch_potatoes"] = after + 1
        save_garden(state)
        logger.info(f"farm: couch potatoes {state['couch_potatoes']}")


async def _play(client, controller):
    from wizwalker.combat import AoeHandler

    handler = AoeHandler(client)
    for _ in range(ROUNDS):
        if controller:
            await controller.checkpoint()
        ratio = await _ratio(client)
        if ratio is not None and ratio <= 0:
            return
        try:
            if not await handler.in_combat():
                return
        except Exception:
            return
        try:
            await handler.wait_for_planning_phase()
        except Exception:
            return
        try:
            await _cast_or_pass(handler, heal=needs_heal(ratio))
        except Exception as exc:
            logger.info(f"farm: round failed ({exc}); passing")
            try:
                await handler.pass_button()
            except Exception:
                return
        await asyncio.sleep(0.4)


async def _card_name(card) -> str:
    for reader in (card.display_name, card.name):
        try:
            text = await reader()
        except Exception:
            continue
        if text:
            return text
    return ""


async def _effect_names(card) -> list[str]:
    try:
        effects = await card.get_spell_effects()
    except Exception:
        return []
    names = []
    for effect in effects or []:
        try:
            kind = await effect.effect_type()
        except Exception:
            continue
        names.append(getattr(kind, "name", None) or str(kind))
    return names


async def _living_enemies(handler) -> list:
    enemies = []
    for member in await handler.get_members():
        try:
            if await member.is_monster() and not await member.is_dead():
                enemies.append(member)
        except Exception:
            continue
    return enemies


async def _cast_or_pass(handler, heal: bool = False) -> str:
    enemies = await _living_enemies(handler)
    if heal and enemies:
        for card in await handler.get_cards():
            try:
                if not await card.is_castable():
                    continue
                name = await _card_name(card)
                effects = await _effect_names(card)
            except Exception:
                continue
            if not is_death_heal(name, effects):
                continue
            await card.cast(enemies[0])
            logger.info(f"farm: health is low; casting {name or 'a death drain'}")
            return "heal"
        logger.info("farm: health is low and no death drain is in hand")
    aoes = await handler.get_damaging_aoes(check_enchanted=False)
    if aoes and await aoes[0].is_castable():
        await aoes[0].cast(None)
        return "aoe"
    for card in await handler.get_cards():
        try:
            if not await card.is_castable():
                continue
            kind = (await card.type_name() or "").lower()
            name = await _card_name(card)
            effects = await _effect_names(card)
        except Exception:
            continue
        if kind in ("enchantment", "heal", "healing", "ward", "charm", "global"):
            continue
        if is_death_heal(name, effects):
            continue
        if enemies:
            await card.cast(enemies[0])
            return "single"
    await handler.pass_button()
    return "pass"
