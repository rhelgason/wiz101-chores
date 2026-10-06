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

    async def tick(self, only: str | None = None, places: tuple[str, ...] = ()) -> int:
        """Sweep for `only` (a reagent name; empty picks up every free reagent).

        `places` are display names to travel to first ("Chelsea Court"). When
        those are not given, a dry zone hops to its neighbours.
        """
        if places:
            await self._go_to_a_place(places)
        zone = await self.client.zone_name() or ""
        got = await self._sweep(only)
        if got or places or self.hop_zones <= 0 or not zone:
            if not got and only:
                logger.info(f"reagents: no {only} in {zone or 'this zone'}")
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
            return await self._sweep(only)
        logger.info(f"reagents: nothing to pick up within {self.hop_zones} gate(s) of here")
        return 0

    async def _go_to_a_place(self, places: tuple[str, ...]):
        here = (await self.client.zone_name() or "").lower()
        if any(p.lower() in here for p in places):
            return
        try:
            from .travel_data import _data, named_zone
        except Exception as exc:
            logger.debug(f"reagents: no zone list ({exc})")
            return
        try:
            display = _data()[1]
        except Exception as exc:
            logger.info(f"reagents: could not read the zone list ({exc})")
            return
        for place in places:
            dest = named_zone(place, display)
            if not dest:
                continue
            logger.info(f"reagents: going to {place}")
            if await go_to_zone(self.client, dest):
                return
        logger.info(f"reagents: could not reach {', '.join(places)}; sweeping here")

    async def _sweep(self, only: str | None = None) -> int:
        async def press():
            await self.client.send_key(Keycode.X, 0.1)

        zone = await self.client.zone_name() or ""
        n = 0
        while n < ZONE_CAP and await self.collector.collect_nearby(press, max_range=1e9, only=only):
            n += 1
            self.picked += 1
            await self.controller.checkpoint()
            if (await self.client.zone_name() or "") != zone:
                break
        if n:
            logger.success(f"reagents: picked up {n} in {zone or '?'}")
        return n
