"""Refuse a teleport that would land on an enemy.

The quest bot's version also snapped landings onto collision geometry and
walked instead of teleporting. Chores only need the part that keeps a reagent
or a gate from dropping the wizard into a fight.
"""

from __future__ import annotations

import math

from loguru import logger

CLEARANCE = 600.0  # an enemy this close to the landing: don't go


def teleport_aborted(client) -> bool:
    """The last teleport was refused (an enemy on the spot). Don't walk in."""
    return bool(getattr(client, "_teleport_aborted", False))


def install(client):
    """Wrap `client.teleport` once."""
    if getattr(client, "_safe_teleport", False):
        return
    raw = client.teleport

    async def teleport(xyz, *args, allow_engage=False, **kwargs):
        client._teleport_aborted = False
        if allow_engage:
            return await raw(xyz, *args, **kwargs)
        try:
            mobs = await client.get_mobs()
        except Exception:
            mobs = []
        for mob in mobs:
            try:
                pos = await mob.location()
            except Exception:
                continue
            if math.dist((pos.x, pos.y), (xyz.x, xyz.y)) < CLEARANCE:
                client._teleport_aborted = True
                logger.info("not teleporting: an enemy is on that spot")
                return None
        return await raw(xyz, *args, **kwargs)

    client.teleport = teleport
    client._safe_teleport = True
