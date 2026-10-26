"""Whether the wizard can be moved, and waiting out a loading screen."""

from __future__ import annotations

import asyncio

from loguru import logger

from . import blackbox, ui

LOADING_TELL = 300.0  # a loading screen this long: tell the player (it does not end by waiting)


async def is_free(client) -> bool:
    """Not loading, not fighting, and not in a dialogue."""
    try:
        return not (
            await client.is_loading()
            or await client.in_battle()
            or await ui.is_visible(client, ui.ADVANCE_DIALOG)
        )
    except Exception as exc:
        # A closed client is not "busy". Callers retry, then ask for a fresh game.
        if type(exc).__name__ == "ClientClosedError":
            raise
        return False


async def wait_until_free(client, timeout: float = 60.0) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await is_free(client):
            return True
        await asyncio.sleep(0.25)
    return False


async def wait_for_loading(client, appear_timeout: float = 2.0):
    """Give a loading screen a moment to appear, then wait for it to finish."""
    loop = asyncio.get_running_loop()
    end = loop.time() + appear_timeout
    while loop.time() < end and not await client.is_loading():
        await asyncio.sleep(0.1)
    started = loop.time()
    told = False
    while await client.is_loading():
        if not told and loop.time() - started >= LOADING_TELL:
            # 10-26 08:05 (run 20261025-231646-seeds): a flee's load to Northguard
            # logged ConnectComplete, AppCloseConnection state 5, then nothing;
            # the bot waited here silently for 74 min until the game closed.
            told = True
            minutes = round(LOADING_TELL / 60)
            logger.warning(f"world: a loading screen has not ended in {minutes} min; the game may be frozen")
            blackbox.event(
                "needs_player",
                why=f"a loading screen has not ended in {minutes} min (restart the game, then the bot)",
            )
        await asyncio.sleep(0.2)
