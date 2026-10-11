"""Savarstaad seed farm, without a single teleport.

Start the run anywhere. Outside Savarstaad Pass (Grizzleheim/GH_Hero) the
wizard walks there, world door and all (navigate.py), then on to Boar Camp,
which becomes home. Started inside the pass near the camp, where it stands
is home. The bot walks onto the nearest Troubled Warrior or
Splithoof Barbarian within `seeds.leash` of home, plays the duel (Empower,
then Scarecrow; a death drain when health is low and the next card cannot
be cast), and looks again. With nothing in reach it walks back toward home
and waits for the camp to respawn.

Nothing teleports: on the Mac, teleports closed the client. Out of the pass
(a defeat, a stray gate) it walks back. It does not rest in Northguard or
sell at the Bazaar. Low health or mana: a potion
if one is charged, a wisp within reach, else it stands still for a while. A
backpack two slots from full stops the run, so a dropped seed is not lost.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass

from loguru import logger

from . import blackbox

SAVARSTAAD = "Grizzleheim/GH_Hero"
NORTHGUARD = "Grizzleheim/GH_MainHub"
MANA_LOW = 0.30
MANA_READY = 0.55
HEALTH_LOW = 0.40  # out of a fight; in a fight the death drain covers 60%
HEALTH_READY = 0.65
BAG_FREE = 2
BAG_EVERY = 60.0  # seconds between backpack reads
FIGHT_WAIT = 14.0  # the duel showed up about 11s after contact (23:34 session)
SKIP_AFTER_MISS = 30.0
WISP_REACH = 2500.0
HOME_SLACK = 700.0  # nearer than this to home counts as home
REST_COOLDOWN = 600.0  # a rest that did not help is not repeated for this long
SPAWN_WAIT = 3.0
PULL_REACH = 15.0  # walk into the creature, not up to it: contact starts the duel
CONTACT = 150.0  # a walk that ended farther than this from it cannot have started a duel
AFTER_FIGHT = 0.6
BOAR_CAMP = (-23200.0, 14300.0, 502.0)  # Splithoof Barbarians; on the zone's walking graph
CAMP_NEAR = 6000.0  # a start this close to the camp keeps the wizard's own spot as home
TRAVEL_RETRY = 120.0


def in_savarstaad(zone: str | None) -> bool:
    return SAVARSTAAD.lower() in (zone or "").lower()


def mana_is_low(ratio: float | None) -> bool:
    """Unread mana does not stop the farm."""
    return ratio is not None and ratio < MANA_LOW


def mana_is_ready(ratio: float | None) -> bool:
    return ratio is None or ratio >= MANA_READY


def health_is_low(ratio: float | None) -> bool:
    return ratio is not None and ratio < HEALTH_LOW


def health_is_ready(ratio: float | None) -> bool:
    return ratio is None or ratio >= HEALTH_READY


def bag_is_full(used: int | None, capacity: int | None) -> bool:
    """True when only a couple of slots remain. Unread space is not full."""
    if used is None or capacity is None or capacity <= 0:
        return False
    return used >= max(capacity - BAG_FREE, 1)


def low_on(health: float | None, mana: float | None) -> str:
    """'health', 'mana', or '' (health first: a defeat costs more than a slow fight)."""
    if health_is_low(health):
        return "health"
    if mana_is_low(mana):
        return "mana"
    return ""


def recovered(what: str, health: float | None, mana: float | None) -> bool:
    return health_is_ready(health) if what == "health" else mana_is_ready(mana)


@dataclass(frozen=True)
class Seen:
    label: str
    pos: tuple[float, float, float]
    dist: float  # from the wizard
    gid: int
    resting: bool = False


def pick_target(
    seen: list[Seen],
    home: tuple[float, float, float],
    leash: float,
    groups: tuple[tuple[str, ...], ...],
) -> int | None:
    """The creature to pull: a farm name, within the leash of home, not skipped.

    An earlier name group wins over a nearer creature from a later one; inside
    one group the nearest to the wizard wins.
    """
    from .farm import mob_rank

    best, key = None, None
    for index, row in enumerate(seen):
        if row.resting:
            continue
        rank = mob_rank(row.label, groups)
        if rank is None:
            continue
        if math.dist(row.pos[:2], home[:2]) > leash:
            continue
        candidate = (rank, row.dist)
        if key is None or candidate < key:
            best, key = index, candidate
    return best


class SeedFarm:
    def __init__(self, client, controller, task: dict, cfg):
        self.client = client
        self.controller = controller
        self.task = task
        self.cfg = cfg
        self.home: tuple[float, float, float] | None = None
        self.fights = 0
        self.seeds_at_start: int | None = None
        self.next_bag = 0.0
        self.rest_after: dict[str, float] = {}
        self.said = ""
        self.quiet_since = 0.0
        self.home_fails = 0
        self.next_trip = 0.0
        self.topped_up = False
        from .selling import LootBook

        self.book = LootBook()

    def say_once(self, key: str, text: str) -> None:
        if self.said != key:
            logger.info(text)
            self.said = key

    async def ratios(self) -> tuple[float | None, float | None]:
        stats = self.client.stats
        health = mana = None
        try:
            hp, top = int(await stats.current_hitpoints()), int(await stats.max_hitpoints())
            health = hp / top if top > 0 else None
        except Exception as exc:
            _reraise_closed(exc)
        try:
            mp, top = int(await stats.current_mana()), int(await stats.max_mana())
            mana = mp / top if top > 0 else None
        except Exception as exc:
            _reraise_closed(exc)
        return health, mana

    async def where(self) -> tuple[float, float, float]:
        from .walk import position

        return await position(self.client)

    async def step(self) -> None:
        from .farm import in_fight
        from .world import is_free

        client = self.client
        if await in_fight(client):
            self.task["name"] = "fight"
            await self.play()
            return
        if not await is_free(client):
            self.task["name"] = "waiting (dialogue or loading)"
            self.say_once("busy", "seeds: in a dialogue or a loading screen; waiting")
            await asyncio.sleep(0.5)
            return
        zone = await client.zone_name() or ""
        if not in_savarstaad(zone):
            await self.travel(zone)
            return
        me = await self.where()
        if self.home is None:
            await self.settle(me)
            return
        if await self.bag_full():
            return
        health, mana = await self.ratios()
        if not self.topped_up:
            # Once, on arriving at the camp: start the farm with a fuller globe.
            self.topped_up = True
            if not mana_is_ready(mana) or not health_is_ready(health):
                logger.info(f"seeds: topping up before farming (health {_pct(health)}, mana {_pct(mana)})")
                await self.recover("mana" if not mana_is_ready(mana) else "health")
                return
        if health is not None and health <= 0:
            self.controller.record_death(zone)
            await asyncio.sleep(5.0)
            return
        need = low_on(health, mana)
        if need and time.monotonic() >= self.rest_after.get(need, 0.0):
            await self.recover(need)
            return
        await self.hunt(me)

    async def travel(self, zone: str) -> None:
        """Walk to Savarstaad Pass from wherever the wizard is; retry later if that fails."""
        from .travel import go_to_zone

        if time.monotonic() < self.next_trip:
            self.task["name"] = "waiting to retry the walk to Savarstaad Pass"
            await asyncio.sleep(5.0)
            return
        self.task["name"] = "walking to Savarstaad Pass"
        self.said = ""
        if await go_to_zone(self.client, SAVARSTAAD):
            return
        self.next_trip = time.monotonic() + TRAVEL_RETRY
        blackbox.event("needs_player", why="could not walk to Savarstaad Pass", zone=zone)
        self.say_once(
            f"away:{zone}",
            f"seeds: could not walk from {zone or 'here'} to Savarstaad Pass; trying again in "
            f"{TRAVEL_RETRY / 60:.0f} min (or walk it there and the farm carries on).",
        )

    async def settle(self, me) -> None:
        """Pick home: the wizard's own spot near the camp, else walk to Boar Camp first."""
        from .navigate import walk_in_zone

        if math.dist(me[:2], BOAR_CAMP[:2]) > CAMP_NEAR:
            self.task["name"] = "walking to Boar Camp"
            outcome = await walk_in_zone(
                self.client,
                BOAR_CAMP,
                why="to Boar Camp",
                controller=self.controller,
                on_fight=self.play,
                reach=HOME_SLACK / 2,
            )
            if outcome == "left zone":
                return
            me = await self.where()
            if outcome != "reached":
                logger.warning(f"seeds: the walk to Boar Camp ended {outcome}; farming from here")
        self.home = me
        logger.info(
            f"seeds: home is ({me[0]:.0f}, {me[1]:.0f}); pulling creatures within {self.cfg.leash:.0f} of it"
        )
        blackbox.event("seeds_home", home=[round(v) for v in me], leash=self.cfg.leash)
        if self.seeds_at_start is None:
            self.seeds_at_start = await self.count_seeds()
        if self.book.baseline is None:
            from .selling import _backpack

            items = await _backpack(self.client)
            if items is not None:
                self.book.see(items)  # what was owned before this run is never sold
                logger.info(f"seeds: {len(items)} item(s) in the bag at the start; only newer loot is sold")

    async def hunt(self, me: tuple[float, float, float]) -> None:
        from .farm import GROUPS, leave_alone

        self.task["name"] = "seeds"
        looked = await self.look()
        if looked is None:
            await asyncio.sleep(2.0)
            return
        seen, names = looked
        pick = pick_target([row for row, _e in seen], self.home, self.cfg.leash, GROUPS["seeds"])
        if pick is None:
            await self.idle(me, seen, names)
            return
        self.quiet_since = 0.0
        row, entity = seen[pick]
        self.said = ""
        logger.info(f"seeds: pulling {row.label} gid={row.gid} {row.dist:.0f} away")
        blackbox.event(
            "pull",
            label=row.label,
            gid=str(row.gid),
            dist=round(row.dist),
            mob=[round(v) for v in row.pos],
            me=[round(v) for v in me],
        )
        if await self.pull(entity, row):
            await self.play()
            leave_alone(row.gid, 12.0)
        else:
            leave_alone(row.gid, SKIP_AFTER_MISS)

    async def look(self) -> tuple[list[tuple[Seen, object]], list[str]] | None:
        """Loaded farm creatures and where they are, plus every loaded name (for the log)."""
        from .farm import GROUPS, _nearby, mob_rank, resting

        groups = GROUPS["seeds"]
        rows = await _nearby(self.client, groups)
        if rows is None:
            return None
        out = []
        for entity, label, dist, gid in rows:
            if mob_rank(label, groups) is None:
                continue
            try:
                p = await entity.location()
            except Exception as exc:
                _reraise_closed(exc)
                continue
            out.append((Seen(label, (p.x, p.y, p.z), dist, gid, resting(gid)), entity))
        return out, [label for _e, label, _d, _g in rows if label]

    async def idle(self, me, seen, names: list[str]) -> None:
        """Nothing to pull: back toward home, then wait for the camp to respawn."""
        from .navigate import walk_in_zone

        away = math.dist(me[:2], self.home[:2])
        if away > HOME_SLACK:
            self.task["name"] = "walking home"
            outcome = await walk_in_zone(
                self.client,
                self.home,
                why="home to Boar Camp",
                controller=self.controller,
                on_fight=self.play,
                reach=HOME_SLACK / 2,
            )
            if outcome == "stuck":
                self.home_fails += 1
                if self.home_fails >= 3:
                    self.home = me
                    self.home_fails = 0
                    logger.warning(
                        "seeds: could not walk back to home three times; "
                        f"home is now here ({me[0]:.0f}, {me[1]:.0f})"
                    )
                    blackbox.event("seeds_home", home=[round(v) for v in me], why="walk home kept sticking")
            else:
                self.home_fails = 0
            return
        if not self.quiet_since:
            self.quiet_since = time.monotonic()
        self.task["name"] = "waiting for creatures"
        farm = sorted({row.label for row, _e in seen})[:4]
        other = sorted(set(names) - set(farm))[:4]
        self.say_once(
            "quiet",
            "seeds: nothing to pull within the leash; waiting for respawns "
            f"(farm creatures loaded but skipped or out of reach: {', '.join(farm) or 'none'}; "
            f"other names: {', '.join(other) or 'none'})",
        )
        await asyncio.sleep(SPAWN_WAIT)

    async def _fight_now(self) -> bool:
        try:
            return bool(await self.client.in_battle())
        except Exception as exc:
            _reraise_closed(exc)
            return False

    async def pull(self, entity, row: Seen) -> bool:
        """Walk onto the creature, then wait for the duel. True when one started."""
        from .farm import in_fight, mark_mob_contact
        from .walk import walk_to

        async def target():
            try:
                p = await entity.location()
            except Exception as exc:
                _reraise_closed(exc)
                return None
            return (p.x, p.y, p.z)

        self.task["name"] = "pulling"
        result = await walk_to(
            self.client,
            target,
            why=f"to {row.label} gid={row.gid}",
            stop_within=PULL_REACH,
            max_seconds=min(30.0, 4.0 + row.dist / 350.0),
            fight=self._fight_now,
            checkpoint=self.controller.checkpoint,
        )
        if result.outcome in ("lost",):
            return await in_fight(self.client)
        # Only a walk that ended on the creature can still turn into a duel.
        touched = result.outcome in ("reached", "fight") or result.end_dist <= CONTACT
        if touched:
            mark_mob_contact()
        end = time.monotonic() + (FIGHT_WAIT if touched else 1.0)
        while time.monotonic() < end:
            if await in_fight(self.client):
                return True
            await asyncio.sleep(0.25)
        logger.info(
            f"seeds: {row.label} gid={row.gid} did not start a fight ({result.outcome}); trying another"
        )
        blackbox.event("pull_missed", gid=str(row.gid), walk=result.outcome)
        return False

    async def play(self) -> None:
        from .farm import play_current_fight

        self.task["name"] = "fight"
        started = time.monotonic()
        blackbox.event("fight_start")
        await play_current_fight(self.client, self.controller)
        self.fights += 1
        seeds = await self.count_seeds()
        gained = (
            ""
            if seeds is None or self.seeds_at_start is None
            else f"; couch potato seeds {seeds} (+{seeds - self.seeds_at_start} this run)"
        )
        logger.info(f"seeds: fight {self.fights} over in {time.monotonic() - started:.0f}s{gained}")
        blackbox.event("fight_end", n=self.fights, seconds=round(time.monotonic() - started), seeds=seeds)
        await asyncio.sleep(AFTER_FIGHT)

    async def count_seeds(self) -> int | None:
        from .garden import garden_state

        try:
            return int(garden_state().get("couch_potatoes") or 0)
        except Exception:
            return None

    async def bag_full(self) -> bool:
        if time.monotonic() < self.next_bag:
            return False
        self.next_bag = time.monotonic() + BAG_EVERY
        try:
            used, capacity = await self.client.backpack_space()
        except Exception as exc:
            _reraise_closed(exc)
            return False
        if not bag_is_full(int(used), int(capacity)):
            return False
        if self.cfg.sell and self.book.selling:
            from .selling import sell_trip

            self.task["name"] = "selling at the Bazaar"
            logger.info(
                f"seeds: the backpack is at {used}/{capacity}; marking the camp and walking to the Bazaar"
            )
            if await sell_trip(self.client, self.controller, self.book, SAVARSTAAD):
                logger.info("seeds: sold; back to the boars")
                return False
            logger.warning("seeds: selling did not make room")
        logger.warning(
            f"seeds: the backpack is at {used}/{capacity}. Stopping so no couch potato seed is lost. "
            "Sell or discard some loot, then start the bot again "
            "(the hooks stay; it reconnects without writing code)."
        )
        blackbox.event("needs_player", why="backpack full", used=int(used), capacity=int(capacity))
        self.controller.stop("the backpack is full")
        return True

    async def recover(self, need: str) -> None:
        """A potion, a wisp in reach, or standing still, until `need` is back."""
        self.task["name"] = f"recovering {need}"
        health, mana = await self.ratios()
        logger.info(f"seeds: {need} is low (health {_pct(health)}, mana {_pct(mana)})")
        blackbox.event("recover", need=need, health=health, mana=mana)
        if await self.potion():
            health, mana = await self.ratios()
            if recovered(need, health, mana):
                return
        if await self.wisp(need):
            health, mana = await self.ratios()
            if recovered(need, health, mana):
                return
        if self.cfg.northguard_trips and await self.northguard_trip(need):
            return
        await self.rest(need)

    async def northguard_trip(self, need: str) -> bool:
        """Mark the camp, walk to Northguard, take wisps or rest there, Recall back.

        True when the trip ran (whatever the globe did); False when it could
        not leave, and the caller rests in place instead.
        """
        from .navigate import trip

        self.task["name"] = f"to Northguard for {need}"
        logger.info(
            f"seeds: {need} is low and nothing nearby refills it; marking the camp and walking to Northguard"
        )
        blackbox.event("northguard_trip", need=need)

        async def errand() -> None:
            await self.gather(need)

        went = await trip(
            self.client,
            NORTHGUARD,
            SAVARSTAAD,
            errand,
            controller=self.controller,
            on_fight=getattr(self.client, "_on_fight", None),
        )
        if not went:
            logger.info("seeds: could not reach Northguard; resting here instead")
        return went

    async def gather(self, need: str) -> None:
        """In Northguard: walk onto each wisp of the kind needed, else stand still, until it is back."""
        from .navigate import walk_in_zone

        limit = time.monotonic() + self.cfg.rest_minutes * 60.0
        self.controller.allow_idle(self.cfg.rest_minutes * 60.0 + 60.0)
        taken: set[int] = set()
        next_log = 0.0
        try:
            while time.monotonic() < limit:
                await self.controller.checkpoint()
                health, mana = await self.ratios()
                if recovered(need, health, mana):
                    logger.info(
                        f"seeds: {need} is back in Northguard (health {_pct(health)}, mana {_pct(mana)})"
                    )
                    return
                spot = await self.wisp_spot(need, taken)
                if spot is not None:
                    gid, place = spot
                    taken.add(gid)
                    await walk_in_zone(
                        self.client, place, why=f"{need} wisp", controller=self.controller, reach=25.0
                    )
                    await asyncio.sleep(0.8)
                    continue
                if time.monotonic() >= next_log:
                    next_log = time.monotonic() + 30.0
                    logger.info(
                        f"seeds: no {need} wisp in Northguard; standing "
                        f"(health {_pct(health)}, mana {_pct(mana)})"
                    )
                await asyncio.sleep(3.0)
            logger.info(
                f"seeds: {need} still low after {self.cfg.rest_minutes:.0f} min in Northguard; "
                "going back anyway"
            )
        finally:
            self.controller.end_idle()

    async def wisp_spot(self, need: str, taken: set[int]):
        """(id, position) of the nearest wisp of that kind not walked to yet, or None."""
        from .farm import _entity_gid

        finder = getattr(self.client, f"get_{need}_wisps", None)
        if finder is None:
            return None
        try:
            wisps = await finder()
            me = await self.where()
            best = None
            for w in wisps or []:
                gid = await _entity_gid(w)
                if gid in taken:
                    continue
                p = await w.location()
                d = math.dist(me[:2], (p.x, p.y))
                if best is None or d < best[0]:
                    best = (d, gid, (p.x, p.y, p.z))
        except Exception as exc:
            _reraise_closed(exc)
            return None
        return None if best is None else (best[1], best[2])

    async def potion(self) -> bool:
        try:
            if float(await self.client.stats.potion_charge()) < 1.0:
                return False
            used = await self.client.use_potion()
        except Exception as exc:
            _reraise_closed(exc)
            logger.info(f"seeds: potion failed ({exc})")
            return False
        if used:
            logger.info("seeds: used a potion")
            blackbox.event("potion")
            await asyncio.sleep(1.0)
        return bool(used)

    async def wisp(self, need: str) -> bool:
        from .walk import walk_point

        finder = getattr(self.client, f"find_closest_{need}_wisp", None)
        if finder is None:
            return False
        try:
            found = await finder(only_safe=False)
            if found is None:
                return False
            p = await found.location()
        except Exception as exc:
            _reraise_closed(exc)
            return False
        me = await self.where()
        spot = (p.x, p.y, p.z)
        if math.dist(me[:2], spot[:2]) > WISP_REACH or math.dist(spot[:2], self.home[:2]) > self.cfg.leash:
            return False
        result = await walk_point(
            self.client,
            spot,
            why=f"{need} wisp",
            stop_within=25.0,
            max_seconds=12.0,
            fight=self._fight_now,
            checkpoint=self.controller.checkpoint,
        )
        await asyncio.sleep(1.0)
        return result.outcome == "reached"

    async def rest(self, need: str) -> None:
        """Stand still. Whether the globe refills out of combat is logged as it goes."""
        from .farm import in_fight

        limit = time.monotonic() + self.cfg.rest_minutes * 60.0
        health, mana = await self.ratios()
        first = health if need == "health" else mana
        logger.info(f"seeds: standing still for {need} (up to {self.cfg.rest_minutes:.0f} min)")
        self.controller.allow_idle(self.cfg.rest_minutes * 60.0 + 30.0)
        try:
            next_log = time.monotonic() + 30.0
            while time.monotonic() < limit:
                await self.controller.checkpoint()
                if await in_fight(self.client):
                    return
                health, mana = await self.ratios()
                if recovered(need, health, mana):
                    logger.info(f"seeds: {need} is back (health {_pct(health)}, mana {_pct(mana)})")
                    return
                if time.monotonic() >= next_log:
                    next_log = time.monotonic() + 30.0
                    logger.info(f"seeds: resting: health {_pct(health)}, mana {_pct(mana)}")
                await asyncio.sleep(3.0)
        finally:
            self.controller.end_idle()
        last = health if need == "health" else mana
        logger.info(
            f"seeds: {need} went {_pct(first)} -> {_pct(last)} while resting; fighting anyway, "
            f"no rest for {need} again for {REST_COOLDOWN / 60:.0f} min"
        )
        self.rest_after[need] = time.monotonic() + REST_COOLDOWN

    def summary(self) -> str:
        return f"{self.fights} fight(s)"


def _pct(ratio: float | None) -> str:
    return "?" if ratio is None else f"{ratio * 100:.0f}%"


def _reraise_closed(exc: BaseException) -> None:
    if type(exc).__name__ == "ClientClosedError":
        raise exc


async def seed_loop(client, controller, task: dict, cfg) -> None:
    """Farm Savarstaad until stopped."""
    from wizwalker.errors import ClientClosedError

    from .bot import game_gone
    from .safety import BotStopped

    farm = SeedFarm(client, controller, task, cfg)
    logger.info(
        "seeds: Savarstaad Pass, Troubled Warriors and Splithoof Barbarians, by walking only. "
        "Start it standing in Boar Camp."
    )
    closed = 0
    errors = 0
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            await farm.step()
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
            logger.opt(exception=exc).error(f"seeds: step failed: {type(exc).__name__}: {exc}")
            blackbox.event("step_error", error=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(min(30.0, 1.0 * errors))
    logger.info(f"seeds: {farm.summary()} this run")
