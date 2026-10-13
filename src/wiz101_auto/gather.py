"""Reagent farming on foot (`mode: gather`), any reagent the zone files name.

Where to look comes from the game itself: each zone archive lists the spots
a reagent can spawn on (reagent_spots.py; "Path-BlackLotus" in MooShu's
zones). The server fills a few of those at random, so the bot walks a tour
of the spots, looks at what has loaded around it, and walks onto each
reagent it sees and presses X. Zones are ranked offline by how many spots a
short walk covers; the best few are toured in turn, so each has time to
respawn before the next visit.

Creatures are walked around: routes keep their graph edges 600 away from
every loaded creature, and a spot with one standing on it waits until later
in the tour. A duel that starts anyway is fled (client._on_fight; played
out only if Flee fails twice). Nothing teleports.

Every sighting is kept in state/gather.json (zone, spot, when), so which
spots really spawn, and how often, is learned across runs.

Black Lotus (MooShu, 10-12 files): Shirataki Temple 15 spots on a ~66k
walk, Ancient Burial Grounds 13 / ~150k, Kishibe Village 16 / ~174k, Village
of Sorrow 12 / ~186k, Crimson Fields 9, Tatakai Outpost 15, Cave of Solitude
16, Tree of Life 20 (longer walks); Jade Palace has one.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

from loguru import logger

from . import blackbox
from .reagent_spots import flat, known_spots, reagent_spots

STATE_FILE = Path("state") / "gather.json"
# A reagent's usual world, when the config names none. More go here as they are farmed.
REAGENT_WORLDS = {"blacklotus": "MooShu"}
SPOT_REACH = 300.0  # close enough to a spot to see what spawned on it
LOOK_RANGE = 2500.0  # reagents this close are worth walking to
PICK_REACH = 40.0
MOB_CLEAR = 600.0  # a spot or reagent with a creature this close waits
SPOT_KEY = 50.0  # spots are keyed on a 50-unit grid in the state file
WALK_SPEED = 580.0


def tour_order(start, spots: list, distance=None) -> list:
    """Spots nearest-first from `start`, each next one nearest to the last.

    `distance(a, b)` defaults to a straight line; the tour passes the walk
    over the zone graph (10-12: Shirataki's "nearest" spot was 59 legs away).
    """
    measure = distance or (lambda a, b: math.dist(a[:2], b[:2]))
    left = list(spots)
    out = []
    here = start
    while left:
        nxt = min(left, key=lambda p: measure(here, p))
        left.remove(nxt)
        out.append(nxt)
        here = nxt
    return out


def walk_distance(graph):
    """distance(a, b) along the zone graph (straight when it does not join them), cached."""
    cache: dict = {}

    def measure(a, b) -> float:
        key = (tuple(round(v) for v in a[:2]), tuple(round(v) for v in b[:2]))
        if key not in cache:
            path = graph.route(a, b) if graph is not None else None
            points = [a, *(path or [b])]
            cache[key] = sum(math.dist(p[:2], q[:2]) for p, q in zip(points, points[1:], strict=False))
        return cache[key]

    return measure


def tour_length(spots: list, graph=None) -> float:
    """Length of the nearest-first tour, over the zone graph when there is one."""
    if not spots:
        return 0.0
    order = tour_order(spots[0], spots)
    total = 0.0
    for a, b in zip(order, order[1:], strict=False):
        path = graph.route(a, b) if graph is not None else None
        points = [a, *(path or [b])]
        total += sum(math.dist(p[:2], q[:2]) for p, q in zip(points, points[1:], strict=False))
    return total


def area(zone: str) -> str:
    """'MooShu/MS_Plague/MS_Plague_Zone3_CliffsidePalace' -> 'MooShu/MS_Plague' (one hub's zones)."""
    parts = zone.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def rank_zones(table: dict[str, list], lengths: dict[str, float]) -> list[str]:
    """Zones by spots per minute of walking (more spots break ties), the best
    zone's own area first (its neighbours are a short walk, not a trip through
    the world's hub). One- and two-spot zones last."""

    def score(zone: str) -> tuple:
        spots = len(table[zone])
        minutes = max(lengths.get(zone, 0.0) / WALK_SPEED / 60.0, 0.5)
        return (spots < 3, -spots / minutes, -spots)

    ranked = sorted(table, key=score)
    if not ranked:
        return ranked
    home = area(ranked[0])
    return sorted(ranked, key=lambda z: (len(table[z]) < 3, area(z) != home, score(z)))


def plan_zones(reagent: str, world: str = "", count: int = 3, data_dir=None) -> list[str]:
    """The `count` best zones for `reagent`, read from the game's files."""
    from . import navmesh

    world = world or REAGENT_WORLDS.get(flat(reagent), "")
    table = reagent_spots(reagent, world, data_dir)
    lengths = {}
    for zone, spots in table.items():
        graph = navmesh.zone_graph(zone, data_dir)
        lengths[zone] = tour_length(spots, graph)
    return rank_zones(table, lengths)[:count]


def is_reagent(object_name: str, reagent: str) -> bool:
    """'BlackLotus', 'MS_BlackLotus_01' -> a Black Lotus. Not a spawner or a path."""
    name = flat(object_name)
    want = flat(reagent)
    return bool(want) and want in name and "spawn" not in name and "path" not in name


def spot_key(spot) -> str:
    return f"{round(spot[0] / SPOT_KEY) * int(SPOT_KEY)},{round(spot[1] / SPOT_KEY) * int(SPOT_KEY)}"


def nearest_spot(point, spots: list, within: float = 400.0):
    """The listed spot this sighting belongs to, or None."""
    best = min(spots, key=lambda s: math.dist(s[:2], point[:2]), default=None)
    if best is None or math.dist(best[:2], point[:2]) > within:
        return None
    return best


def clear_of(point, mobs: list, distance: float = MOB_CLEAR) -> bool:
    return all(math.dist(point[:2], m[:2]) > distance for m in mobs)


class Ledger:
    """state/gather.json: {reagent: {zone: {spot: {"visits", "seen", "picked", "last_seen"}}}}."""

    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def _row(self, reagent: str, zone: str, spot) -> dict:
        return (
            self.data.setdefault(flat(reagent), {})
            .setdefault(zone, {})
            .setdefault(spot_key(spot), {"visits": 0, "seen": 0, "picked": 0, "last_seen": 0})
        )

    def visit(self, reagent: str, zone: str, spot) -> None:
        self._row(reagent, zone, spot)["visits"] += 1

    def seen(self, reagent: str, zone: str, spot, picked: bool, now: float | None = None) -> None:
        row = self._row(reagent, zone, spot)
        row["seen"] += 1
        row["picked"] += int(picked)
        row["last_seen"] = round(time.time() if now is None else now)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            pass


class Gatherer:
    def __init__(self, client, controller, task: dict, cfg):
        self.client = client
        self.controller = controller
        self.task = task
        self.cfg = cfg
        self.reagent = cfg.reagent
        self.zones: list[str] = []
        self.next_zone = 0
        self.picked = 0
        self.tours = 0
        self.ledger = Ledger()
        self.tried: dict[int, float] = {}  # reagent entity id -> when a pickup was tried
        self.pilot_zone = ""  # the jump pilot's one zone

    # -- what is around -------------------------------------------------

    async def mob_spots(self) -> list[tuple[float, float, float]]:
        out = []
        try:
            for mob in await self.client.get_mobs():
                try:
                    p = await mob.location()
                    out.append((p.x, p.y, p.z))
                except Exception:
                    continue
        except Exception as exc:
            _reraise_closed(exc)
        return out

    async def reagents_here(self) -> list[tuple[int, tuple[float, float, float]]]:
        """(entity id, spot) of every loaded `reagent`."""
        out = []
        try:
            entities = await self.client.get_base_entity_list()
        except Exception as exc:
            _reraise_closed(exc)
            return out
        for entity in entities:
            try:
                template = await entity.object_template()
                if not template or not is_reagent(await template.object_name() or "", self.reagent):
                    continue
                p = await entity.location()
                out.append((int(await entity.global_id_full()), (p.x, p.y, p.z)))
            except Exception as exc:
                _reraise_closed(exc)
                continue
        return out

    async def where(self):
        from .walk import position

        return await position(self.client)

    # -- the loop -------------------------------------------------------

    async def resolve_zones(self) -> list[str]:
        if self.cfg.zones:
            from .travel_data import _data, named_zone

            display = _data()[1]
            out = []
            for name in self.cfg.zones:
                zone = name if "/" in name else named_zone(name, display)
                if zone:
                    out.append(zone)
                else:
                    logger.warning(f"gather: no zone called {name!r}; skipped")
            return out
        return plan_zones(self.reagent, self.cfg.world, self.cfg.zone_count)

    async def step(self) -> None:
        from .farm import in_fight
        from .world import is_free

        if not self.zones:
            self.zones = await self.resolve_zones()
            if not self.zones:
                logger.warning(
                    f"gather: no zone lists spawn spots for {self.reagent!r} "
                    "(is that world downloaded?); stopping"
                )
                self.controller.stop(f"no spots for {self.reagent}")
                return
            names = ", ".join(z.split("/")[-1] for z in self.zones)
            logger.info(f"gather: {self.reagent} in {names}, in that order")
            blackbox.event("gather_plan", reagent=self.reagent, zones=self.zones)
        if await in_fight(self.client):
            await self.fight()
            return
        if not await is_free(self.client):
            self.task["name"] = "waiting (dialogue or loading)"
            await asyncio.sleep(0.5)
            return
        jumper = getattr(self.client, "_jump", None)
        if jumper is not None:
            await self.pilot_step(jumper)
            return
        zone = await self.client.zone_name() or ""
        target = self.zones[self.next_zone % len(self.zones)]
        if zone != target:
            if zone in self.zones:
                self.next_zone = self.zones.index(zone)  # already in one of them: start here
            else:
                await self.travel(target)
                return
        await self.tour(zone)
        self.next_zone = (self.zones.index(zone) + 1) % len(self.zones)

    # -- the jump pilot (safety.data_jumps): jump only, one zone, slow --------

    async def pilot_step(self, jumper) -> None:
        zone = await self.client.zone_name() or ""
        if not self.pilot_zone:
            here_has_spots = bool(known_spots(zone, self.reagent))
            self.pilot_zone = zone if here_has_spots else self.zones[0]
            logger.info(
                f"gather: jump pilot in {self.pilot_zone.split('/')[-1]}: jumps only, "
                f"no walking in the zone, a round every {self.cfg.respawn_minutes:.0f} min"
            )
            blackbox.event("gather_pilot", zone=self.pilot_zone)
        if not jumper.enabled:
            logger.warning("gather: jumps stopped landing; the pilot does not walk, so it stops here")
            blackbox.event("needs_player", why="jumps stopped landing", zone=zone)
            self.controller.stop("jumps stopped landing")
            return
        if zone != self.pilot_zone:
            # A flee (MooShu sends it to Jade Palace) or a stray gate: the way back has gates.
            name = self.pilot_zone.split("/")[-1]
            logger.info(f"gather: back to {name} (gates, jumping the long stretches)")
            await self.travel(self.pilot_zone)
            return
        await self.jump_tour(zone, jumper)
        await self.wait_for_respawn()

    async def jump_tour(self, zone: str, jumper) -> None:
        """Jump to each spot (or a clear point beside it), and onto each reagent seen."""
        from . import navmesh
        from .farm import in_fight
        from .navigate import landing_spot

        spots = known_spots(zone, self.reagent)
        graph = navmesh.zone_graph(zone)
        order = tour_order(await self.where(), spots)
        got = 0
        started = time.monotonic()
        self.task["name"] = f"jumping to {self.reagent} spots in {zone.split('/')[-1]}"
        logger.info(f"gather: jumping to {len(order)} {self.reagent} spots in {zone.split('/')[-1]}")
        blackbox.event("gather_tour", zone=zone, spots=len(order), jumps_only=True)
        for spot in order:
            await self.controller.checkpoint()
            if (await self.client.zone_name() or "") != zone or await in_fight(self.client):
                return  # the next step deals with it
            got += await self.jump_pick_up(zone, spots, jumper)
            if not jumper.enabled:
                return
            if math.dist((await self.where())[:2], spot[:2]) > SPOT_REACH:
                landing = landing_spot(spot, await self.mob_spots(), graph)
                if landing is None:
                    logger.info(f"gather: creatures all around {_pt(spot)}; skipping it this round")
                    continue
                if not await jumper(landing, why=f"{self.reagent} spot"):
                    continue
            self.ledger.visit(self.reagent, zone, spot)
        got += await self.jump_pick_up(zone, spots, jumper)
        self.tours += 1
        self.ledger.save()
        minutes = (time.monotonic() - started) / 60.0
        logger.info(
            f"gather: {zone.split('/')[-1]} jumped in {minutes:.1f} min: {got} {self.reagent} "
            f"({self.picked} this run, {jumper.jumps} jumps)"
        )
        blackbox.event("gather_tour_done", zone=zone, got=got, total=self.picked, minutes=round(minutes, 1))

    async def jump_pick_up(self, zone: str, spots: list, jumper) -> int:
        """Jump onto each loaded reagent in reach that no creature guards, and press X."""
        from .navigate import Navigator

        got = 0
        for _ in range(6):
            now = time.monotonic()
            me = await self.where()
            found = [
                (gid, p)
                for gid, p in await self.reagents_here()
                if now - self.tried.get(gid, -1e9) > 120.0 and math.dist(p[:2], me[:2]) <= LOOK_RANGE
            ]
            if not found:
                return got
            gid, spot = min(found, key=lambda f: math.dist(f[1][:2], me[:2]))
            self.tried[gid] = now
            listed = nearest_spot(spot, spots)
            if not clear_of(spot, await self.mob_spots()):
                logger.info(f"gather: {self.reagent} at {_pt(spot)} has a creature beside it; leaving it")
                self.ledger.seen(self.reagent, zone, listed or spot, picked=False)
                continue
            logger.info(f"gather: {self.reagent} at {_pt(spot)} ({math.dist(spot[:2], me[:2]):.0f} away)")
            if math.dist(me[:2], spot[:2]) > PICK_REACH and not await jumper(spot, why=self.reagent):
                continue
            nav = Navigator(self.client, self.controller)
            for _press in range(3):
                await nav.use()
                await asyncio.sleep(1.2)
                if gid not in {g for g, _p in await self.reagents_here()}:
                    break
            picked = gid not in {g for g, _p in await self.reagents_here()}
            self.ledger.seen(self.reagent, zone, listed or spot, picked=picked)
            if picked:
                got += 1
                self.picked += 1
                logger.success(f"gather: picked up {self.reagent} ({self.picked} this run)")
                blackbox.event("gather_pick", reagent=self.reagent, zone=zone, at=[round(v) for v in spot])
            else:
                logger.info(f"gather: the {self.reagent} at {_pt(spot)} is still there; leaving it")
                blackbox.event("gather_missed", reagent=self.reagent, zone=zone, at=[round(v) for v in spot])
        return got

    async def wait_for_respawn(self) -> None:
        """Stand still between rounds (a duel that starts ends the wait)."""
        from .farm import in_fight

        seconds = self.cfg.respawn_minutes * 60.0
        self.task["name"] = f"waiting {self.cfg.respawn_minutes:.0f} min for {self.reagent} to respawn"
        logger.info(f"gather: next round in {self.cfg.respawn_minutes:.0f} min")
        self.controller.allow_idle(seconds + 60.0)
        try:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                await self.controller.checkpoint()
                if await in_fight(self.client):
                    return
                await asyncio.sleep(2.0)
        finally:
            self.controller.end_idle()

    async def travel(self, zone: str) -> None:
        from .travel import go_to_zone

        self.task["name"] = f"walking to {zone.split('/')[-1]}"
        logger.info(f"gather: walking to {zone}")
        if not await go_to_zone(self.client, zone):
            blackbox.event("needs_player", why=f"could not walk to {zone.split('/')[-1]}", zone=zone)
            logger.warning(f"gather: could not walk to {zone}; trying again in 2 min")
            await asyncio.sleep(120.0)

    async def fight(self) -> None:
        on_fight = getattr(self.client, "_on_fight", None)
        self.task["name"] = "fight"
        if on_fight is not None:
            await on_fight()  # flee, played out only if Flee fails twice
        else:
            from .farm import play_current_fight

            await play_current_fight(self.client, self.controller, combo=False)

    async def tour(self, zone: str) -> None:
        """Walk every spot in this zone once, picking up what is there."""
        from .navigate import walk_in_zone

        spots = known_spots(zone, self.reagent)
        if not spots:
            logger.info(f"gather: {zone} lists no {self.reagent} spots; moving on")
            return
        from . import navmesh

        me = await self.where()
        order = tour_order(me, spots, walk_distance(navmesh.zone_graph(zone)))
        waited: list = []
        got = 0
        started = time.monotonic()
        logger.info(f"gather: touring {len(order)} {self.reagent} spots in {zone.split('/')[-1]}")
        blackbox.event("gather_tour", zone=zone, spots=len(order))
        while order:
            await self.controller.checkpoint()
            if (await self.client.zone_name() or "") != zone:
                return  # a defeat or a stray gate; the step after this walks back
            got += await self.pick_up(zone, spots)
            spot = order.pop(0)
            if not clear_of(spot, await self.mob_spots()):
                if spot not in waited:
                    waited.append(spot)
                    order.append(spot)  # a creature on it: later in the tour
                    continue
                logger.info(f"gather: a creature still guards {_pt(spot)}; skipping it this tour")
                continue
            self.task["name"] = f"gathering {self.reagent} in {zone.split('/')[-1]}"
            outcome = await walk_in_zone(
                self.client,
                spot,
                why=f"{self.reagent} spot",
                controller=self.controller,
                on_fight=self.fight,
                reach=SPOT_REACH,
                avoid=self.mob_spots,
            )
            if outcome == "left zone":
                return
            self.ledger.visit(self.reagent, zone, spot)
        got += await self.pick_up(zone, spots)
        self.tours += 1
        self.ledger.save()
        minutes = (time.monotonic() - started) / 60.0
        logger.info(
            f"gather: {zone.split('/')[-1]} toured in {minutes:.1f} min: {got} {self.reagent} "
            f"({self.picked} this run)"
        )
        blackbox.event("gather_tour_done", zone=zone, got=got, total=self.picked, minutes=round(minutes, 1))

    async def pick_up(self, zone: str, spots: list) -> int:
        """Walk onto every loaded reagent in reach that no creature guards; X on each."""
        from .navigate import Navigator, walk_in_zone

        got = 0
        for _ in range(6):
            now = time.monotonic()
            me = await self.where()
            mobs = await self.mob_spots()
            found = [
                (gid, p)
                for gid, p in await self.reagents_here()
                if now - self.tried.get(gid, -1e9) > 120.0 and math.dist(p[:2], me[:2]) <= LOOK_RANGE
            ]
            if not found:
                return got
            gid, spot = min(found, key=lambda f: math.dist(f[1][:2], me[:2]))
            self.tried[gid] = now
            listed = nearest_spot(spot, spots)
            if not clear_of(spot, mobs):
                logger.info(f"gather: {self.reagent} at {_pt(spot)} has a creature beside it; leaving it")
                self.ledger.seen(self.reagent, zone, listed or spot, picked=False)
                continue
            logger.info(f"gather: {self.reagent} at {_pt(spot)} ({math.dist(spot[:2], me[:2]):.0f} away)")
            outcome = await walk_in_zone(
                self.client,
                spot,
                why=self.reagent,
                controller=self.controller,
                on_fight=self.fight,
                reach=PICK_REACH,
                avoid=self.mob_spots,
            )
            if outcome == "left zone":
                return got
            nav = Navigator(self.client, self.controller)
            for _press in range(3):
                await nav.use()
                await asyncio.sleep(1.2)
                if gid not in {g for g, _p in await self.reagents_here()}:
                    break
            picked = gid not in {g for g, _p in await self.reagents_here()}
            self.ledger.seen(self.reagent, zone, listed or spot, picked=picked)
            if listed is None:
                logger.info(f"gather: that one was not on a listed spot ({_pt(spot)})")
            if picked:
                got += 1
                self.picked += 1
                logger.success(f"gather: picked up {self.reagent} ({self.picked} this run)")
                blackbox.event("gather_pick", reagent=self.reagent, zone=zone, at=[round(v) for v in spot])
            else:
                logger.info(f"gather: the {self.reagent} at {_pt(spot)} is still there; leaving it")
                blackbox.event("gather_missed", reagent=self.reagent, zone=zone, at=[round(v) for v in spot])
        return got

    def summary(self) -> str:
        return f"{self.picked} {self.reagent} in {self.tours} tour(s)"


def _pt(p) -> str:
    return f"({p[0]:.0f}, {p[1]:.0f})"


def _reraise_closed(exc: BaseException) -> None:
    if type(exc).__name__ == "ClientClosedError":
        raise exc


async def gather_loop(client, controller, task: dict, cfg) -> None:
    """Farm `cfg.reagent` until stopped."""
    from wizwalker.errors import ClientClosedError

    from .bot import game_gone
    from .safety import BotStopped

    gatherer = Gatherer(client, controller, task, cfg)
    logger.info(f"gather: {cfg.reagent}, on foot, around creatures; fleeing any duel")
    closed = errors = 0
    try:
        while not controller.stopped.is_set():
            await controller.checkpoint()
            try:
                await gatherer.step()
                closed = errors = 0
            except BotStopped:
                break
            except ClientClosedError:
                closed += 1
                if game_gone(controller, closed):
                    break
                await asyncio.sleep(2.0)
            except Exception as exc:
                closed = 0
                errors += 1
                logger.opt(exception=exc).error(f"gather: step failed: {type(exc).__name__}: {exc}")
                blackbox.event("step_error", error=f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(min(30.0, 1.0 * errors))
    finally:
        gatherer.ledger.save()
    logger.info(f"gather: {gatherer.summary()} this run")

