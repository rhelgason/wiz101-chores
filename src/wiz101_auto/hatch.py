"""Hatch the base with a kiosk ghulture that is already showing the perfect talents.

The kiosk is in the Pet Pavilion. A hatch button is clicked only when the
open page itself shows a ghulture and all five talents. A button that spends
crowns, skips the timer, or hatches faster is never clicked. One hatch, then
stop: the egg timer is left alone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from . import ui
from .petplan import TARGET

DUMP = Path("state") / "hatch_windows.txt"
BROWSE = ("browse pets", "find a pet", "search pets")
CONFIRM = ("hatch", "hatch pet")
FORBIDDEN = (
    "crown",
    "elixir",
    "hatch now",
    "faster",
    "speed",
    "instant",
    "skip the timer",
    "free hatch",
)


def _bad(text: str) -> bool:
    low = text.lower()
    return any(word in low for word in FORBIDDEN)


def shows_perfect_ghulture(texts) -> bool:
    """The open page names a ghulture and every talent we are hatching for."""
    blob = " ".join(texts).lower()

    def has(phrase: str) -> bool:
        return phrase in blob or phrase.replace("-", " ") in blob

    if not has("ghulture"):
        return False
    return all(has(talent) for talent in TARGET)


def next_hatch_click(texts) -> str | None:
    """The caption to click, or None when clicking would spend gold on a guess.

    Hatch is returned only for a page that already shows the perfect ghulture.
    Browse is returned only to open the list. Speed and crowns captions are skipped.
    """
    if shows_perfect_ghulture(texts):
        for text in texts:
            if _bad(text):
                continue
            if text.strip().lower() in CONFIRM:
                return text.strip()
        return None
    for text in texts:
        if _bad(text):
            continue
        if text.strip().lower() in BROWSE:
            return text.strip()
    return None


async def hatch_once(client, dancer, controller=None) -> str:
    """'hatched', 'looked', or 'skipped'. Gold is spent only on 'hatched'."""
    if controller:
        controller.allow_idle(180)
    try:
        if not await dancer._go_to_pavilion():
            logger.info("hatch: the Pet Pavilion was not reached")
            return "skipped"
        pos = await _kiosk(client)
        if pos is None:
            logger.info("hatch: the kiosk is not in view")
            return "skipped"
        await client.teleport(pos)
        await asyncio.sleep(0.5)
        await client.send_key(_x(), 0.1)
        await asyncio.sleep(1.2)
        for _ in range(6):
            if controller:
                await controller.checkpoint()
            texts = await ui.visible_texts(client, limit=120)
            click = next_hatch_click(texts)
            if click is None:
                await _dump(texts)
                logger.info(
                    "hatch: no page is showing the perfect ghulture yet, so no gold is spent"
                )
                return "looked"
            if not await ui.click_text(client, click):
                await _dump(texts)
                return "looked"
            logger.info(f"hatch: clicked {click}")
            await asyncio.sleep(1.0)
            if click.strip().lower() in CONFIRM:
                logger.success("hatch: hatched with the kiosk ghulture. The egg timer is left as it is")
                return "hatched"
        return "looked"
    finally:
        if controller:
            controller.end_idle()


def _x():
    from wizwalker import Keycode

    return Keycode.X


async def _kiosk(client):
    from .travel import entity_pos

    return await entity_pos(client, ("kiosk", "hatchmake", "hatch making", "stan"))


async def _dump(texts: list[str]):
    try:
        DUMP.parent.mkdir(exist_ok=True)
        DUMP.write_text("\n".join(texts), encoding="utf-8")
    except OSError:
        pass
