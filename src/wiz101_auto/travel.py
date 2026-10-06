"""Get from one zone to another, and back to Wizard City.

Gate positions come from WizSprinter's traversal data (`travel_data`). A
standard gate is walked into. A gate whose kind starts with "x" (a boat, a
rope, a door that asks) is used by pressing X. From a house or the dorm, Go
Home and the world gate reach Wizard City, then the same gates reach the
Pet Pavilion.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger
from wizwalker import XYZ, Keycode

from . import ui
from .travel_data import gate_kind, gate_toward, note_zone_jump, press_x_gate, ride_gate
from .world import is_free, wait_for_loading

DORM = "WizardCity/Interiors/WC_Housing_Dorm_Interior"
DORM_DOOR = XYZ(-205.0, 13.0, 0.0)
RAVENWOOD = "WizardCity/WC_Ravenwood"
DORM_BUTTON = "GotoDormButton"
HOUSE_FILE = Path("state") / "house.json"
MAX_HOPS = 8


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


async def spiral_to(client, world: str) -> bool:
    """Open the world gate here and choose `world` ("WizardCity") on the Spiral Map."""
    from .relog import _find_button

    label = "wizard city" if world == "WizardCity" else world.lower()
    spot = await entity_pos(client, ("universeteleport",))
    if spot is None:
        logger.warning("no world gate in this zone")
        return False
    await client.teleport(spot)
    await asyncio.sleep(0.8)
    await client.send_key(Keycode.X, 0.1)
    opened = False
    for _ in range(20):
        if await ui.is_visible(client, ui.SPIRAL_DOOR_TELEPORT):
            opened = True
            break
        await asyncio.sleep(0.3)
    if not opened:
        logger.warning("the world gate did not open the Spiral Map")
        return False
    button = await _find_button(client.root_window, (label,))
    for _ in range(6):
        if button is not None:
            break
        if not await ui.click(client, ui.SPIRAL_DOOR_NEXT):
            break
        await asyncio.sleep(0.4)
        button = await _find_button(client.root_window, (label,))
    if button is None:
        logger.warning(f"{label.title()} is not on the Spiral Map")
        await ui.click(client, ui.SPIRAL_DOOR_EXIT)
        return False
    logger.info(f"Spiral Map: {label.title()}")
    await ui.click_center(client, button)
    await asyncio.sleep(0.3)
    await ui.click(client, ui.SPIRAL_DOOR_TELEPORT)
    await wait_for_loading(client, appear_timeout=8.0)
    return (await client.zone_name() or "").split("/")[0] == world


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
            await client.send_key(Keycode.X, 0.1)
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
    """Walk the known gates to `dest`. True once there."""
    blocked: set[tuple[str, str]] = set()
    for _ in range(max_hops):
        zone = await client.zone_name() or ""
        if zone == dest:
            return True
        if not await is_free(client):
            logger.info(f"stopped on the way to {dest}: not free to move")
            return False
        try:
            gate = gate_toward(zone, dest, blocked)
        except Exception as exc:
            logger.warning(f"no gate data ({exc})")
            return False
        if not gate:
            logger.info(f"no gate path from {zone} to {dest}")
            return False
        pos, nxt = gate
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
