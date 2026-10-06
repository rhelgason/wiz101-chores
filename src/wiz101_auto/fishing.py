"""Fish a quest with the lure the wizard already has, then reel on the bite.

The fishing window and the fish list are the game's own. A bite is the fish
object moving to the hooked state. Space is pressed once then. Nothing is
written into the fish, the clock, or the distance checks. A miss still spends
energy, so a cast only happens when a fish is in the pond and energy remains.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger

from . import ui

# FishStatusCode.unknown2. The fish has taken the lure.
BITE = 2
BITE_TIMEOUT = 25.0
MAX_CASTS = 6
LURE_ENERGY = 2  # stop before a cast that would empty a small globe
FISH_PLACES = ("Triton Avenue", "the Commons", "Cyclops Lane")
DUMP = Path("state") / "fishing_windows.txt"


def bite_ready(statuses) -> bool:
    """True when one fish in the pond has taken the lure."""
    for status in statuses:
        name = getattr(status, "name", None)
        if name == "unknown2" or status == BITE or status == "unknown2":
            return True
    return False


def can_cast(energy: int | None) -> bool:
    """A lure spends energy even when the reel misses. Unknown energy may try once."""
    return energy is None or energy >= LURE_ENERGY


async def fish_once(client, controller=None) -> int:
    """Catch up to a few fish. Returns how many catch windows were closed."""
    if controller:
        controller.allow_idle(180)
    caught = 0
    try:
        for _ in range(MAX_CASTS):
            if controller:
                await controller.checkpoint()
            energy = await _energy(client)
            if not can_cast(energy):
                logger.info(f"fishing: {energy} energy left; not casting another lure")
                break
            fishes = await _fish_list(client)
            if not fishes:
                if not await _go_to_water(client):
                    logger.info("fishing: no pond here, and the Commons pond was not reached")
                    break
                fishes = await _fish_list(client)
            if not fishes:
                logger.info("fishing: the pond has no fish; not casting")
                break
            if not await _cast_lure(client):
                await _dump(client, "the lure button was not on screen")
                break
            if not await _wait_bite(client):
                logger.info("fishing: the lure was not taken; leaving it rather than reeling a miss")
                break
            await client.send_key(_space(), 0.05)
            if await _dismiss_catch(client):
                caught += 1
                logger.success(f"fishing: catch {caught}")
            else:
                logger.info("fishing: the reel did not land a fish")
                break
        return caught
    finally:
        if controller:
            controller.end_idle()


def _space():
    from wizwalker import Keycode

    return Keycode.SPACEBAR


async def _energy(client) -> int | None:
    from .petdance import first_number

    try:
        return first_number(await ui.named_text(client, "textEnergy"))
    except Exception:
        return None


async def _manager(client):
    return await client.game_client.fishing_manager()


async def _fish_list(client) -> list:
    try:
        return list(await (await _manager(client)).fish_list())
    except Exception as exc:
        logger.debug(f"fishing: fish list unreadable ({exc})")
        return []


async def _go_to_water(client) -> bool:
    from .travel import go_to_zone
    from .travel_data import _data, named_zone

    here = (await client.zone_name() or "").lower()
    if any(place.lower() in here for place in FISH_PLACES):
        return True
    try:
        display = _data()[1]
    except Exception as exc:
        logger.info(f"fishing: no zone list ({exc})")
        return False
    for place in FISH_PLACES:
        dest = named_zone(place, display)
        if dest and await go_to_zone(client, dest):
            logger.info(f"fishing: at {place}")
            return True
    return False


async def _window(client, name: str):
    try:
        for window in await client.root_window.get_windows_with_name(name):
            if await window.is_visible():
                return window
    except Exception:
        return None
    return None


async def _open_fishing(client) -> bool:
    if await _window(client, "FishingWindow"):
        return True
    if not await ui.click_named(client, "OpenFishingButton"):
        return False
    for _ in range(20):
        if await _window(client, "FishingWindow"):
            return True
        await asyncio.sleep(0.25)
    return False


async def _cast_lure(client) -> bool:
    """The first spell on the fishing bar. That is the lure the wizard owns."""
    if not await _open_fishing(client):
        return False
    window = await _window(client, "FishingWindow")
    if window is None:
        return False
    try:
        sub = await window.get_child_by_name("FishingSubWindow")
        frame = await sub.get_child_by_name("BottomFrame")
        icon = await frame.get_child_by_name("Icon1")
    except Exception as exc:
        logger.info(f"fishing: lure slot missing ({exc})")
        return False
    await client.mouse_handler.click_window(icon)
    await asyncio.sleep(1.0)
    box = await ui.modal_box(client)
    if box is not None:
        text = (await ui.modal_text(box)).lower()
        logger.info(f"fishing: the lure was refused ({text or 'a message'}); not confirming it")
        await ui.modal_click(client, box, "rightButton")
        return False
    return True


async def _wait_bite(client) -> bool:
    deadline = time.monotonic() + BITE_TIMEOUT
    while time.monotonic() < deadline:
        fishes = await _fish_list(client)
        statuses = []
        for fish in fishes:
            try:
                statuses.append(await fish.status_code())
            except Exception:
                continue
        if bite_ready(statuses):
            return True
        await asyncio.sleep(0.2)
    return False


async def _dismiss_catch(client) -> bool:
    for _ in range(40):
        window = await _window(client, "CaughtFishModalWindow")
        if window is not None:
            try:
                caught = await window.get_child_by_name("CaughtFish")
                exit_button = await caught.get_child_by_name("exit")
                await client.mouse_handler.click_window(exit_button)
            except Exception as exc:
                logger.info(f"fishing: catch window would not close ({exc})")
                return False
            await asyncio.sleep(0.4)
            return True
        await asyncio.sleep(0.25)
    return False


async def _dump(client, why: str):
    texts = await ui.visible_texts(client)
    logger.info(f"fishing: {why}")
    try:
        DUMP.parent.mkdir(exist_ok=True)
        DUMP.write_text("\n".join(texts), encoding="utf-8")
    except OSError:
        pass
