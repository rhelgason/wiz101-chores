"""Pick up reagents and unlocked chests lying in the zone.

The friend's bot only grabbed these while it was questing, and only when one
was already close. This walks the whole zone (then neighbouring zones, a few
gates out) and picks up every free reagent and chest it can reach without
stepping on an enemy.
"""

from __future__ import annotations

from loguru import logger
from wizwalker import Keycode

from .collect import Collector
from .travel import go_to_zone
from .travel_data import zones_near

ZONE_CAP = 40  # pickups in one zone before the next look (a full field, then on)


class Gatherer:
    def __init__(self, client, hop_zones: int, controller):
        self.client = client
        self.hop_zones = hop_zones
        self.controller = controller
        self.collector = Collector(client)
        self.picked = 0

    async def tick(self) -> int:
        """Sweep where we are. If nothing is left and hopping is on, try the
        next neighbouring zone. Returns how many pickups this call made."""
        zone = await self.client.zone_name() or ""
        got = await self._sweep()
        if got or self.hop_zones <= 0 or not zone:
            return got
        try:
            nearby = [z for z in zones_near(zone, self.hop_zones) if z != zone]
        except Exception as exc:
            logger.debug(f"reagents: no neighbour zones ({exc})")
            return 0
        for dest in nearby:
            logger.info(f"reagents: nothing left in {zone.split('/')[-1]}; trying {dest.split('/')[-1]}")
            if not await go_to_zone(self.client, dest):
                continue
            return await self._sweep()
        logger.info(f"reagents: nothing to pick up within {self.hop_zones} gate(s) of here")
        return 0

    async def _sweep(self) -> int:
        async def press():
            await self.client.send_key(Keycode.X, 0.1)

        zone = await self.client.zone_name() or ""
        n = 0
        while n < ZONE_CAP and await self.collector.collect_nearby(press, max_range=1e9):
            n += 1
            self.picked += 1
            await self.controller.checkpoint()
            if (await self.client.zone_name() or "") != zone:
                break
        if n:
            logger.success(f"reagents: picked up {n} in {zone or '?'}")
        return n
