"""Get from one zone to another, including a change of world.

Gate positions come from WizSprinter's traversal data (`travel_data`). A
standard gate is walked into. A gate whose kind starts with "x" (a boat, a
rope, a door that asks) is used by pressing X. Another world is this world's
spiral door, in either direction. A house door opens onto any world. The
dorm is left on foot. A zone with no gate path goes Home once, then the
same route.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .travel_data import (
    door_spot_usable,
    gate_kind,
    note_zone_jump,
    press_x_gate,
    ride_gate,
    travel_step,
    zone_doors,
)
from .world import is_free, wait_for_loading

DORM = "WizardCity/Interiors/WC_Housing_Dorm_Interior"
DORM_DOOR = XYZ(-205.0, 13.0, 0.0)
RAVENWOOD = "WizardCity/WC_Ravenwood"
DORM_BUTTON = "GotoDormButton"
HOUSE_FILE = Path("state") / "house.json"
MAX_HOPS = 24  # a world door plus the longer gate chains, and a few retries


def is_house(zone: str | None) -> bool:
    """The player's house ("Housing_..."). The home button goes there once one is bought."""
    return bool(zone) and zone.startswith("Housing_")


def in_wizard_city_streets(zone: str | None) -> bool:
    """Wizard City, and not a dorm, house, or other interior."""
    if not zone or not zone.startswith("WizardCity/"):
        return False
    low = zone.lower()
    return "/interiors/" not in low and not low.startswith("wizardcity/interiors")


async def entity_pos(client, needles: tuple[str, ...]):
    """Location of the first entity whose template name contains one of `needles`."""
    try:
        entities = await client.get_base_entity_list()
    except Exception:
        return None
    for entity in entities:
        try:
            template = await entity.object_template()
            name = (await template.object_name() or "").lower() if template else ""
            if any(n in name for n in needles):
                return await entity.location()
        except Exception:
            continue
    return None


async def go_home(client) -> bool:
    """Press Go Home. Lands in the player's house, or the dorm. Already home counts."""
    zone = await client.zone_name()
    if is_house(zone):
        return True
    for _ in range(3):
        note_zone_jump()
        if not await ui.click_named(client, DORM_BUTTON):
            logger.warning("no home button to click")
            return False
        await asyncio.sleep(1.0)
        await ui.confirm_modal(client)
        await wait_for_loading(client, appear_timeout=6.0)
        zone = await client.zone_name()
        if is_house(zone):
            try:
                HOUSE_FILE.parent.mkdir(exist_ok=True)
                HOUSE_FILE.write_text(json.dumps({"zone": zone}), encoding="utf-8")
            except OSError:
                pass
            return True
        if zone == DORM:
            return True
        await asyncio.sleep(2.0)
    logger.warning(f"the home button landed in {zone}")
    return False


async def _leave_dorm(client) -> bool:
    await client.teleport(DORM_DOOR)
    await asyncio.sleep(0.5)
    await client.send_key(Keycode.W, 1.5)
    await wait_for_loading(client)
    return (await client.zone_name() or "") == RAVENWOOD


# The spiral door's world buttons (Deimos). The caption is what the map shows.
SPIRAL_WORLDS = {
    "WizardCity": ("wbtnWizardCity", "wizard city"),
    "Krokotopia": ("wbtnKrokotopia", "krokotopia"),
    "Marleybone": ("wbtnMarleybone", "marleybone"),
    "MooShu": ("wbtnMooShu", "mooshu"),
    "DragonSpire": ("wbtnDragonSpire", "dragonspyre"),
    "Grizzleheim": ("wbtnGrizzleheim", "grizzleheim"),
    "Celestia": ("wbtnCelestia", "celestia"),
    "Wysteria": ("wbtnWysteria", "wysteria"),
    "Zafaria": ("wbtnZafaria", "zafaria"),
    "Avalon": ("wbtnAvalon", "avalon"),
    "Azteca": ("wbtnAzteca", "azteca"),
    "Khrysalis": ("wbtnKhrysalis", "khrysalis"),
    "Polaris": ("wbtnPolaris", "polaris"),
    "Arcanum": ("wbtnArcanum", "arcanum"),
    "Mirage": ("wbtnMirage", "mirage"),
    "Empyrea": ("wbtnEmpyrea", "empyrea"),
    "Karamelle": ("wbtnKaramelle", "karamelle"),
    "Lemuria": ("wbtnLemuria", "lemuria"),
}
DOOR_NEEDLES = ("universeteleport", "worlddoor", "zonedoor", "spiraldoor", "worldteleport")


async def _page_world(client, button_name: str, label: str):
    """The world button on the spiral page that is open, if it is this one."""

    async def match(window, depth: int = 0):
        if depth > 4:
            return None
        try:
            children = await window.children()
        except Exception:
            return None
        for child in children:
            try:
                if not await child.is_visible():
                    continue
                name = await child.name() or ""
                text = ui._TAGS.sub("", await child.maybe_text() or "").strip().lower()
            except Exception:
                continue
            if name == button_name or text == label:
                return child
            found = await match(child, depth + 1)
            if found is not None:
                return found
        return None

    try:
        options = await client.root_window.get_windows_with_name("optionWindow")
    except Exception:
        return None
    for opt in options:
        try:
            if await opt.is_visible():
                found = await match(opt)
                if found is not None:
                    return found
        except Exception:
            continue
    return None


async def _pick_world(client, world: str) -> bool:
    """On an open spiral map, select `world` and press Teleport."""
    button_name, label = SPIRAL_WORLDS.get(world, ("", world.lower()))
    for _ in range(5):
        await ui.click(client, ui.SPIRAL_DOOR_PREV)
        await asyncio.sleep(0.15)
    for _ in range(8):
        button = await _page_world(client, button_name, label)
        if button is not None:
            await ui.click_center(client, button)
            break
        if not await ui.click(client, ui.SPIRAL_DOOR_NEXT):
            logger.warning(f"{label.title()} is not on the Spiral Map")
            await ui.click(client, ui.SPIRAL_DOOR_EXIT)
            return False
        await asyncio.sleep(0.35)
    else:
        logger.warning(f"{label.title()} is not on the Spiral Map")
        await ui.click(client, ui.SPIRAL_DOOR_EXIT)
        return False
    logger.info(f"Spiral Map: {label.title()}")
    await asyncio.sleep(0.3)
    if not await ui.click_named(client, "teleportButton"):
        await ui.click(client, ui.SPIRAL_DOOR_TELEPORT)
    await wait_for_loading(client, appear_timeout=8.0)
    return (await client.zone_name() or "").split("/")[0] == world


async def spiral_to(client, world: str, spot=None) -> bool:
    """Open the world door here and take it to `world` ("Grizzleheim")."""
    place = await entity_pos(client, DOOR_NEEDLES)
    if place is None and spot is not None and door_spot_usable(spot):
        place = spot
    if place is not None:
        await client.teleport(place)
        await asyncio.sleep(0.8)
    else:
        logger.info("no world door marker; trying the prompt in this room")
    for _ in range(12):
        if await ui.is_visible(client, ui.SPIRAL_DOOR_TELEPORT):
            return await _pick_world(client, world)
        await ui.press_interact(client)
        await asyncio.sleep(0.45)
    logger.warning("the world door did not open the Spiral Map")
    return False


async def reach_wizard_city(client) -> bool:
    """Stand somewhere in Wizard City the zone gates can leave (not a dorm or house)."""
    if in_wizard_city_streets(await client.zone_name()):
        return True
    logger.info("going home, then out into Wizard City")
    if not await go_home(client):
        return False
    zone = await client.zone_name() or ""
    if zone == DORM:
        if not await _leave_dorm(client):
            logger.warning("could not leave the dorm")
            return False
        zone = await client.zone_name() or ""
    if is_house(zone):
        if not await spiral_to(client, "WizardCity"):
            return False
    return in_wizard_city_streets(await client.zone_name()) or (
        (await client.zone_name() or "").startswith("WizardCity/")
    )


async def _use_x_gate(client, pos, nxt: str, ride: bool) -> bool:
    await client.teleport(pos)
    await asyncio.sleep(1.0)
    for _leg in range(2 if ride else 1):
        here = await client.zone_name()
        for _ in range(10):
            await ui.press_interact(client)
            await asyncio.sleep(0.5)
            if await client.zone_name() != here:
                break
        await wait_for_loading(client, appear_timeout=8.0)
        if await client.zone_name() == nxt:
            return True
    return await client.zone_name() == nxt


async def _walk_gate(client, pos, zone: str) -> bool:
    await client.teleport(pos)
    await asyncio.sleep(0.6)
    if await client.zone_name() != zone:
        return True
    await client.send_key(Keycode.W, 1.4)
    await asyncio.sleep(0.4)
    return await client.zone_name() != zone


async def go_to_zone(client, dest: str, max_hops: int = MAX_HOPS) -> bool:
    """Walk gates to `dest`. Another world is the world door, then its gates.

    A walk-only client (the Mac) goes on foot with navigate.py, on the
    game's own paths; the code below teleports to each gate (Windows).
    """
    if getattr(client, "_walk_only", False):
        from .navigate import go_to_zone as walk_to_zone

        return await walk_to_zone(
            client, dest, getattr(client, "_controller", None), getattr(client, "_on_fight", None)
        )
    from .travel_data import _data

    blocked: set[tuple[str, str]] = set()
    door_uses = 0
    escaped = False
    try:
        doors = zone_doors()
        gates = _data()[0]
    except Exception as exc:
        logger.warning(f"no gate data ({exc})")
        return False
    for _ in range(max_hops):
        zone = await client.zone_name() or ""
        if zone == dest:
            return True
        if not await is_free(client):
            logger.info(f"stopped on the way to {dest}: not free to move")
            return False
        # The dorm has no gate. A house's own door opens onto any world.
        if zone == DORM:
            if not await _leave_dorm(client):
                return False
            continue
        if is_house(zone):
            logger.info(f"taking the house world door to {dest.split('/')[0]}")
            if not await spiral_to(client, dest.split("/")[0]):
                return False
            continue
        step = travel_step(zone, dest, gates, doors, blocked)
        if step is None:
            if not escaped:
                escaped = True
                logger.info(f"no gate path from {zone}; going home, then the world door")
                if await go_home(client):
                    continue
            where = "the world door" if zone.split("/")[0] != dest.split("/")[0] else dest
            logger.info(f"no gate path from {zone} to {where}")
            return False
        if step[0] == "world":
            if door_uses >= 2:
                logger.info(f"the world door did not reach {dest}")
                return False
            door_uses += 1
            _door_zone, door_pos = step[1], step[2]
            logger.info(f"taking the world door to {dest.split('/')[0]}")
            spot = door_pos if door_spot_usable(door_pos) else None
            if not await spiral_to(client, dest.split("/")[0], spot):
                return False
            continue
        _kind, pos, nxt = step
        kind = gate_kind(zone, nxt)
        logger.info(f"heading to {dest}: gate to {nxt}")
        if press_x_gate(kind):
            moved = await _use_x_gate(client, pos, nxt, ride_gate(kind))
        else:
            moved = await _walk_gate(client, pos, zone)
        await wait_for_loading(client)
        if not moved and (await client.zone_name() or "") == zone:
            logger.warning(f"gate {zone} -> {nxt} did not work; trying another way")
            blocked.add((zone, nxt))
    return (await client.zone_name() or "") == dest
