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
(a defeat, a stray gate) it walks back. Low health or mana: a potion if
one is charged, a wisp within reach, else Mark, a walk to Northguard's
wisps, and Recall (or it stands still for a while). A fight is fled when
the wizard runs dry of mana or is about to lose (farm.flee_reason); that
need, or a defeat (waking in Northguard), is then refilled before the next
pull, and an unread globe counts as empty until the full rest is stood. A
backpack two slots from full is quick sold in place (selling.quick_sell:
only this run's loot, never couch potatoes); if that cannot make room the
run stops, so a dropped seed is not lost.

`seeds.hunts` swaps the boars for other jobs, done in turn: defeat a
creature (a quest's "Defeat 30 Charmed Slaves") in its zone until the quest
tracker says the goal is met, or, when the tracker never shows it, until
that many duels with it were won. Then the next hunt; after the last the
run stops. Everything else (refills, fleeing, selling) is the same.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, replace

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
SEED_COUNT_EVERY = 5  # fights between couch potato counts (a read of the whole bag)
FIGHT_WAIT = 14.0  # the duel showed up about 11s after contact (23:34 session)
SKIP_AFTER_MISS = 30.0
WISP_REACH = 2500.0
HOME_SLACK = 700.0  # nearer than this to home counts as home
REST_COOLDOWN = 600.0  # a rest that did not help is not repeated for this long
WISP_RETRY = 20.0  # a wisp walked onto is not walked to again for this long (it may have respawned)
SPAWN_WAIT = 3.0
PULL_REACH = 15.0  # walk into the creature, not up to it: contact starts the duel
CONTACT = 150.0  # a walk that ended farther than this from it cannot have started a duel
AFTER_FIGHT = 0.6
BOAR_CAMP = (-23200.0, 14300.0, 502.0)  # Splithoof Barbarians; on the zone's walking graph
CAMP_NEAR = 6000.0  # a start this close to the camp keeps the wizard's own spot as home
TRAVEL_RETRY = 120.0
SAME_ERROR_STOP = 20  # one error this many steps running stops the run
STUCK_BUSY = 300.0  # a loading screen or dialogue this long: tell the player (10-12: 2 h on one)
DEFEAT_WINDOW = 90.0  # in Northguard this soon after a fight: it was lost


def in_savarstaad(zone: str | None) -> bool:
    return in_zone(zone, SAVARSTAAD)


def in_zone(zone: str | None, wanted: str) -> bool:
    return bool(wanted) and wanted.lower() in (zone or "").lower()


@dataclass(frozen=True)
class Hunt:
    """One job for the seeds loop: which creatures, where, and how many."""

    name: str  # for the log
    groups: tuple[tuple[str, ...], ...]  # name needles (farm.mob_rank); an earlier group is pulled first
    zone: str = ""  # "": the zone the wizard is in when the hunt starts
    camp: tuple[float, float, float] | None = None  # walked to first; None: home is where it stands
    refill: str = ""  # a zone with wisps for Mark/Recall trips; "": wisps in reach, then rest
    count: int = 0  # duels to win; 0: until stopped
    seeds: bool = False  # count couch potatoes in the bag


SEEDS = Hunt(
    "couch potato seeds",
    (("troubled", "splithoof barbarian"), ("splithoof", "ghboar")),
    SAVARSTAAD,
    BOAR_CAMP,
    NORTHGUARD,
    seeds=True,
)
SEED_WORDS = {"seed", "seeds", "boar", "couchpotato", "couchpotatoseed"}  # --hunt seeds: the boar farm
# Where a named creature is farmed, when known: (zone, camp, refill zone).
# Unknown: walk the wizard there and start the run; it hunts that zone.
# Zones from the game files (10-13): the Chamber of Fire map labels a
# "Chamber of the Charmed"; Knight's Court holds an "OlearyProwler" stand-in.
# No camp: the hunt roams the zone (roam_points). Unverified live.
KNOWN_HUNTS: dict[str, tuple[str, tuple[float, float, float] | None, str]] = {
    "charmedslave": ("Krokotopia/KT_Pyramid/KT_Chamber", None, ""),
    "olearyprowler": ("Marleybone/MB_ScotlandYard/MB_KnightsCourt", None, ""),
}
ROAM_SPACING = 3500.0  # one roam point per square this wide (the client loads creatures nearby only)
ROAM_AFTER = 8.0  # seconds of nothing to pull before walking to the next roam point


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def creature_needle(creature: str) -> str:
    """'Charmed Slaves' and "O'Leary Prowler" -> 'charmedslave', 'olearyprowler'."""
    words = [flat(w) for w in creature.split()]
    words = [w for w in words if w]
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return "".join(words)


def hunts_from(items: list) -> list[Hunt]:
    """`seeds.hunts` (strings or {creature, count, zone, refill}) -> hunts; empty: the seed farm."""
    out = []
    for item in items or []:
        spec = {"creature": item} if isinstance(item, str) else dict(item)
        creature = str(spec["creature"]).strip()
        needle = creature_needle(creature)
        if needle in SEED_WORDS:
            # The boar seed farm as a step in the list; until stopped unless given a count.
            out.append(replace(SEEDS, count=int(spec.get("count", 0))))
            continue
        zone, camp, refill = KNOWN_HUNTS.get(needle, ("", None, ""))
        out.append(
            Hunt(
                creature,
                ((needle,),),
                zone=str(spec.get("zone") or zone),
                camp=camp if not spec.get("zone") else None,
                refill=str(spec.get("refill") or refill),
                count=int(spec.get("count", 30)),
            )
        )
    return out or [SEEDS]


def roam_points(graph, start, spacing: float = ROAM_SPACING) -> list[tuple[float, float, float]]:
    """Spots spread over the walking graph part the wizard stands on, one per
    `spacing` square, in a nearest-next tour from `start`."""
    if graph is None or not graph.nodes:
        return []
    here = graph.nearest(start)[0]
    part = next((c for c in graph.components() if here in c), set(graph.nodes))
    cells: dict[tuple[int, int], int] = {}
    for node in part:
        x, y, _z = graph.nodes[node]
        cell = (int(x // spacing), int(y // spacing))
        middle = ((cell[0] + 0.5) * spacing, (cell[1] + 0.5) * spacing)
        best = cells.get(cell)
        if best is None or math.dist((x, y), middle) < math.dist(graph.nodes[best][:2], middle):
            cells[cell] = node
    left = [graph.nodes[n] for n in cells.values()]
    tour, at = [], start
    while left:
        nxt = min(left, key=lambda p: math.dist(p[:2], at[:2]))
        left.remove(nxt)
        tour.append(nxt)
        at = nxt
    return tour


def kills_in(foes: list[str], hunt: Hunt) -> int:
    """How many of a won duel's enemies were the hunt's creature."""
    from .farm import mob_rank

    return sum(1 for name in foes if mob_rank(name, hunt.groups) is not None)


def hunt_done(hunt: Hunt, kills: int) -> bool:
    """The count given on the command line is the goal (the quest tracker is not read:
    the Storm quest does not reliably show these goals)."""
    return hunt.count > 0 and kills >= hunt.count


def mana_is_low(ratio: float | None) -> bool:
    """Unread mana does not stop the farm."""
    return ratio is not None and ratio < MANA_LOW


def mana_is_ready(ratio: float | None) -> bool:
    return ratio is None or ratio >= MANA_READY


def health_is_low(ratio: float | None) -> bool:
    return ratio is not None and ratio < HEALTH_LOW


def health_is_ready(ratio: float | None) -> bool:
    return ratio is None or ratio >= HEALTH_READY


def is_full(ratio: float | None, full: float, strict: bool = False) -> bool:
    """A Northguard fill is done. Unread counts as full, unless the duel proved it empty."""
    if ratio is None:
        return not strict
    return ratio >= full


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


def recovered(what: str, health: float | None, mana: float | None, strict: bool = False) -> bool:
    """`strict` (a need the duel proved): an unread globe is not back."""
    ratio = health if what == "health" else mana
    if strict and ratio is None:
        return False
    return health_is_ready(health) if what == "health" else mana_is_ready(mana)


def hud_ratio(text: str, top: int | None) -> float | None:
    """A HUD globe's text ("2744", or "2744/2744") over its maximum. None if unreadable."""
    import re

    numbers = [int(n) for n in re.findall(r"\d+", (text or "").replace(",", ""))]
    if not numbers:
        return None
    if len(numbers) >= 2 and numbers[1] > 0:
        return min(numbers[0] / numbers[1], 1.0)
    if not top or top <= 0:
        return None
    return min(numbers[0] / top, 1.0)


def stats_contradict(fled: str, health: float | None, mana: float | None) -> bool:
    """client.stats says the globe the duel just ran out of is fine: it is misread."""
    ratio = mana if fled == "mana" else health if fled == "health" else None
    ready = mana_is_ready if fled == "mana" else health_is_ready
    return ratio is not None and ready(ratio)


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
        self.hunts = hunts_from(getattr(cfg, "hunts", None) or [])
        self.hunt = self.hunts[0]
        self.zone = self.hunt.zone  # "" until the first look at where the wizard is
        self.kills = 0  # the hunt's creatures defeated this hunt
        self.last_kills = 0  # from the last duel, taken back if it turns out lost
        self.roam: list[tuple[float, float, float]] = []  # roam points left this lap
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
        self.owed: list[str] = []  # needs a duel proved (fled, or defeated): refilled before the next pull
        self.trust_stats = True
        self.last_fight_end = 0.0
        self.last_fled = ""  # fleeing a Savarstaad duel lands in Northguard too: not a defeat
        self.busy_since = 0.0
        self.busy_told = False
        from .selling import LootBook

        self.book = LootBook()

    def say_once(self, key: str, text: str) -> None:
        if self.said != key:
            logger.info(text)
            self.said = key

    async def ratios(self) -> tuple[float | None, float | None]:
        """Health and mana, 0..1: client.stats, else the HUD globes' numbers
        over the maxima last read in a duel (client.stats read nothing on 10-11)."""
        health, mana = await self.stat_ratios() if self.trust_stats else (None, None)
        if health is None or mana is None:
            hud_health, hud_mana = await self.hud_ratios()
            health = hud_health if health is None else health
            mana = hud_mana if mana is None else mana
        return health, mana

    async def hud_ratios(self) -> tuple[float | None, float | None]:
        from . import ui
        from .farm import last_vitals

        out = []
        for name, top in (("textHealth", "max_health"), ("textMana", "max_mana")):
            text = ui._TAGS.sub("", await ui.named_text(self.client, name))
            out.append(hud_ratio(text, last_vitals.get(top)))
        return out[0], out[1]

    async def stat_ratios(self) -> tuple[float | None, float | None]:
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
            await self.note_busy()
            await asyncio.sleep(0.5)
            return
        self.busy_since, self.busy_told = 0.0, False
        zone = await client.zone_name() or ""
        if not self.zone and zone:
            self.zone = zone
            logger.info(f"seeds: hunting {self.hunt.name} in {zone} (where the wizard is)")
        if not in_zone(zone, self.zone):
            if not self.last_fled and time.monotonic() - self.last_fight_end < DEFEAT_WINDOW:
                self.defeated(zone)
            if self.hunt.refill and zone == self.hunt.refill and self.owed:
                # Already where the refill trip goes.
                while self.owed:
                    await self.gather(self.owed.pop(0), strict=True)
                return
            await self.travel(zone)
            return
        me = await self.where()
        if self.home is None:
            await self.settle(me)
            return
        if await self.bag_full():
            return
        if self.owed:
            await self.recover(self.owed.pop(0), strict=True)
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
        await self.hunt_round(me)

    async def note_busy(self) -> None:
        """A loading screen that never ends (10-12 10:19: the flee's, for two hours,
        until the player restarted the game) gets a warning and a Mac notification."""
        now = time.monotonic()
        if not self.busy_since:
            self.busy_since = now
        if self.busy_told or now - self.busy_since < STUCK_BUSY:
            return
        self.busy_told = True
        try:
            loading = bool(await self.client.is_loading())
        except Exception as exc:
            _reraise_closed(exc)
            loading = None
        what = "a loading screen" if loading else "a dialogue or loading screen"
        minutes = (now - self.busy_since) / 60.0
        logger.warning(
            f"seeds: stuck on {what} for {minutes:.0f} min; the game may have lost its connection. "
            "Still waiting; restart the game if it does not clear."
        )
        zone = await self.client.zone_name()
        blackbox.event("needs_player", why=f"stuck on {what} for {minutes:.0f} min", zone=zone)

    async def travel(self, zone: str) -> None:
        """Walk to the hunt's zone from wherever the wizard is; retry later if that fails."""
        from .travel import go_to_zone

        place = zone_title(self.zone)
        if time.monotonic() < self.next_trip:
            self.task["name"] = f"waiting to retry the walk to {place}"
            await asyncio.sleep(5.0)
            return
        self.task["name"] = f"walking to {place}"
        self.said = ""
        if await go_to_zone(self.client, self.zone):
            return
        self.next_trip = time.monotonic() + TRAVEL_RETRY
        blackbox.event("needs_player", why=f"could not walk to {place}", zone=zone)
        self.say_once(
            f"away:{zone}",
            f"seeds: could not walk from {zone or 'here'} to {place}; trying again in "
            f"{TRAVEL_RETRY / 60:.0f} min (or walk it there and the farm carries on).",
        )

    async def settle(self, me) -> None:
        """Pick home: the wizard's own spot near the camp, else walk to the camp first."""
        from .navigate import walk_in_zone

        camp = self.hunt.camp
        if camp is not None and math.dist(me[:2], camp[:2]) > CAMP_NEAR:
            self.task["name"] = "walking to the camp"
            outcome = await walk_in_zone(
                self.client,
                camp,
                why="to the camp",
                controller=self.controller,
                on_fight=self.play,
                reach=HOME_SLACK / 2,
            )
            if outcome == "left zone":
                return
            me = await self.where()
            if outcome != "reached":
                logger.warning(f"seeds: the walk to the camp ended {outcome}; farming from here")
        self.home = me
        logger.info(
            f"seeds: home is ({me[0]:.0f}, {me[1]:.0f}); pulling creatures within {self.cfg.leash:.0f} of it"
        )
        blackbox.event("seeds_home", home=[round(v) for v in me], leash=self.cfg.leash)
        if self.hunt.seeds and self.seeds_at_start is None:
            self.seeds_at_start = await self.count_seeds()
        if self.book.baseline is None:
            from .selling import _backpack

            items = await _backpack(self.client)
            if items is not None:
                self.book.see(items)  # what was owned before this run is never sold
                logger.info(f"seeds: {len(items)} item(s) in the bag at the start; only newer loot is sold")

    async def hunt_round(self, me: tuple[float, float, float]) -> None:
        from .farm import leave_alone

        self.task["name"] = self.hunt.name
        looked = await self.look()
        if looked is None:
            await asyncio.sleep(2.0)
            return
        seen, names = looked
        if self.roams:
            self.home = me  # a roaming hunt has no camp: pull what is near the wizard
        pick = pick_target([row for row, _e in seen], self.home, self.cfg.leash, self.hunt.groups)
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
            await self.play(won_over=row.label)
            leave_alone(row.gid, 12.0)
        else:
            leave_alone(row.gid, SKIP_AFTER_MISS)

    async def look(self) -> tuple[list[tuple[Seen, object]], list[str]] | None:
        """Loaded farm creatures and where they are, plus every loaded name (for the log)."""
        from .farm import _nearby, mob_rank, resting

        groups = self.hunt.groups
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

    @property
    def roams(self) -> bool:
        return not self.hunt.seeds and self.hunt.camp is None

    async def wander(self, me) -> None:
        """A roaming hunt with nothing near: walk to the next spot of the zone tour."""
        from .navigate import walk_in_zone
        from .navmesh import zone_graph

        if not self.roam:
            self.roam = roam_points(zone_graph(self.zone), me)
            if not self.roam:
                self.say_once("noroam", f"seeds: no walking graph for {self.zone}; waiting here")
                await asyncio.sleep(SPAWN_WAIT)
                return
            logger.info(f"seeds: looking for {self.hunt.name}s around {self.zone} ({len(self.roam)} spots)")
        spot = self.roam.pop(0)
        self.task["name"] = f"looking for {self.hunt.name}s"
        self.said = ""
        await walk_in_zone(
            self.client,
            spot,
            why=f"looking for {self.hunt.name}s",
            controller=self.controller,
            on_fight=self.play,
            reach=HOME_SLACK / 2,
        )

    async def idle(self, me, seen, names: list[str]) -> None:
        """Nothing to pull: back toward home, then wait for the camp to respawn.
        A roaming hunt (no camp) walks on through the zone instead."""
        from .navigate import walk_in_zone

        if self.roams:
            if not self.quiet_since:
                self.quiet_since = time.monotonic()
            if time.monotonic() - self.quiet_since >= ROAM_AFTER:
                self.quiet_since = 0.0
                await self.wander(me)
            else:
                await asyncio.sleep(SPAWN_WAIT)
            return

        away = math.dist(me[:2], self.home[:2])
        if away > HOME_SLACK:
            self.task["name"] = "walking home"
            outcome = await walk_in_zone(
                self.client,
                self.home,
                why="home to the camp",
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

    async def play(self, won_over: str = "") -> None:
        """Play the duel that is up and count the hunt's creatures in it."""
        from . import farm as fights
        from .farm import play_current_fight

        self.task["name"] = "fight"
        started = time.monotonic()
        blackbox.event("fight_start")
        fled = await play_current_fight(self.client, self.controller)
        self.fights += 1
        self.last_fight_end = time.monotonic()
        self.last_fled = fled or ""
        foes = list(fights.last_foes.values())
        self.last_kills = 0
        if fled:
            await self.owe(fled, f"fled fight {self.fights}, out of {fled}")
        else:
            # Every enemy in a duel that ended without a flee was defeated (a lost duel
            # shows up as a zone change and is taken back in defeated()).
            self.last_kills = kills_in(foes, self.hunt) or (1 if won_over and not foes else 0)
            self.kills += self.last_kills
        counting = self.hunt.seeds and self.fights % SEED_COUNT_EVERY == 0
        seeds = await self.count_seeds() if counting else None
        gained = (
            ""
            if seeds is None or self.seeds_at_start is None
            else f"; couch potato seeds {seeds} (+{seeds - self.seeds_at_start} this run)"
        )
        tally = (
            ""
            if self.hunt.seeds and not self.hunt.count
            else f"; {self.hunt.name} +{self.last_kills}, {self.kills} of {self.hunt.count or '?'}"
        )
        health, mana = await self.ratios()
        logger.info(
            f"seeds: fight {self.fights} over in {time.monotonic() - started:.0f}s{gained}{tally} "
            f"(enemies: {', '.join(foes) or '?'}; stats: health {_pct(health)}, mana {_pct(mana)})"
        )
        blackbox.event(
            "fight_end",
            n=self.fights,
            seconds=round(time.monotonic() - started),
            seeds=seeds,
            fled=fled,
            foes=foes,
            kills=self.last_kills,
            health=health,
            mana=mana,
        )
        await asyncio.sleep(AFTER_FIGHT)
        await self.check_hunt()

    async def check_hunt(self) -> None:
        """Has this hunt defeated its count? Then the next hunt, or stop."""
        if not hunt_done(self.hunt, self.kills):
            return
        logger.success(f"seeds: {self.hunt.name} is done ({self.kills} defeated)")
        blackbox.event("hunt_done", hunt=self.hunt.name, kills=self.kills)
        done = self.hunts.pop(0).name
        if not self.hunts:
            blackbox.event("needs_player", why="done with every hunt")
            self.controller.stop("every hunt is done")
            return
        self.hunt = self.hunts[0]
        if not self.hunt.zone:
            # Its zone is not known, and the creatures just finished live elsewhere.
            why = f"done with {done}; walk to the {self.hunt.name}s and start the bot again"
            logger.warning(f"seeds: {why} (or give the hunt a zone: --hunt \"NAME:COUNT@World/Zone\")")
            blackbox.event("needs_player", why=why)
            self.controller.stop(f"{done} is done; {self.hunt.name} needs a zone")
            return
        self.zone, self.home, self.topped_up, self.roam = self.hunt.zone, None, False, []
        self.kills = self.last_kills = 0
        wanted = f"{self.hunt.count} wanted" if self.hunt.count else "until stopped"
        logger.info(f"seeds: next, {self.hunt.name} ({wanted}), in {zone_title(self.zone)}")

    async def owe(self, need: str, why: str) -> None:
        """Refill `need` before the next pull. If client.stats calls it full, stop trusting it."""
        if need not in self.owed:
            self.owed.append(need)
        health, mana = await self.ratios()
        logger.warning(
            f"seeds: {why}; refilling {need} before the next pull "
            f"(stats say health {_pct(health)}, mana {_pct(mana)})"
        )
        blackbox.event("owed", need=need, why=why, health=health, mana=mana)
        if self.trust_stats and stats_contradict(need, health, mana):
            self.trust_stats = False
            logger.warning(
                f"seeds: the game's stats say {need} is fine right after the duel ran out of it; "
                "not trusting them this run (every rest now runs its full length)"
            )
            blackbox.event("stats_untrusted", need=need, health=health, mana=mana)

    def defeated(self, zone: str = NORTHGUARD) -> None:
        self.last_fight_end = 0.0
        self.kills = max(0, self.kills - self.last_kills)  # that duel was lost, not won
        self.last_kills = 0
        logger.warning(f"seeds: defeated (woke in {zone} after fight {self.fights}); refilling first")
        blackbox.event("defeat", fight=self.fights, zone=zone)
        self.controller.record_death(zone)
        for need in ("health", "mana"):
            if need not in self.owed:
                self.owed.append(need)

    async def count_seeds(self) -> int | None:
        """Couch potatoes in the bag, read from the bag itself (the drops hook is not in)."""
        from .garden import _item_stacks, count_potatoes

        stacks = await _item_stacks(self.client)
        return None if stacks is None else count_potatoes(stacks)

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
            from .selling import quick_sell

            self.task["name"] = "quick selling"
            logger.info(f"seeds: the backpack is at {used}/{capacity}; quick selling this run's loot")
            if await quick_sell(self.client, self.controller, self.book):
                logger.info("seeds: sold; back to the hunt")
                return False
            if await self._fight_now():
                return False  # a mob walked in; the fight comes first, then selling again
            logger.warning("seeds: selling did not make room")
        logger.warning(
            f"seeds: the backpack is at {used}/{capacity}. Stopping so no drop is lost. "
            "Sell or discard some loot, then start the bot again "
            "(the hooks stay; it reconnects without writing code)."
        )
        blackbox.event("needs_player", why="backpack full", used=int(used), capacity=int(capacity))
        self.controller.stop("the backpack is full")
        return True

    async def recover(self, need: str, strict: bool = False) -> None:
        """A potion, a wisp in reach, or standing still, until `need` is back."""
        self.task["name"] = f"recovering {need}"
        health, mana = await self.ratios()
        logger.info(f"seeds: {need} is low (health {_pct(health)}, mana {_pct(mana)})")
        blackbox.event("recover", need=need, health=health, mana=mana)
        if await self.potion():
            if strict and not self.trust_stats:
                return  # a potion fills both globes
            health, mana = await self.ratios()
            if recovered(need, health, mana, strict):
                return
        if await self.wisp(need):
            health, mana = await self.ratios()
            if recovered(need, health, mana, strict):
                return
        if self.cfg.northguard_trips and self.hunt.refill and await self.northguard_trip(need, strict):
            return
        await self.rest(need, strict)

    async def northguard_trip(self, need: str, strict: bool = False) -> bool:
        """Mark the camp, walk to the refill zone (Northguard), take wisps there, Recall back.

        True when the trip ran (whatever the globe did); False when it could
        not leave, and the caller rests in place instead.
        """
        from .navigate import trip

        place = zone_title(self.hunt.refill)
        self.task["name"] = f"to {place} for {need}"
        logger.info(f"seeds: {need} is low and nothing near refills it; marking the camp, walking to {place}")
        blackbox.event("northguard_trip", need=need)

        async def errand() -> None:
            await self.gather(need, strict)

        went = await trip(
            self.client,
            self.hunt.refill,
            self.zone,
            errand,
            controller=self.controller,
            on_fight=getattr(self.client, "_on_fight", None),
        )
        if not went:
            logger.info(f"seeds: could not reach {place}; resting here instead")
        return went

    async def gather(self, need: str, strict: bool = False) -> None:
        """In Northguard: fill `need` to full on its wisps, then top up the other globe.

        It used to leave at the camp's "ready" mark (55% mana): with 120 max
        mana that was ~6 min of fighting per trip (10-12 night). The wisps
        respawn fast, so a trip now fills the globe (`seeds.northguard_full`),
        up to `seeds.northguard_minutes`.
        """
        minutes = self.cfg.northguard_minutes
        limit = time.monotonic() + minutes * 60.0
        self.controller.allow_idle(minutes * 60.0 + 60.0)
        try:
            for kind in (need, "mana" if need == "health" else "health"):
                if not await self.fill(kind, limit, strict=strict and kind == need):
                    return
        finally:
            self.controller.end_idle()

    async def fill(self, kind: str, limit: float, *, strict: bool = False) -> bool:
        """Walk onto `kind` wisps (or stand) until it is full. False when the trip should end."""
        from .navigate import walk_in_zone

        full = self.cfg.northguard_full
        taken: dict[int, float] = {}
        next_log = 0.0
        while time.monotonic() < limit:
            await self.controller.checkpoint()
            if (await self.client.zone_name() or "") != self.hunt.refill:
                return False
            health, mana = await self.ratios()
            ratio = health if kind == "health" else mana
            if is_full(ratio, full, strict):
                logger.info(
                    f"seeds: {kind} is full in {zone_title(self.hunt.refill)} "
                    f"(health {_pct(health)}, mana {_pct(mana)})"
                )
                return True
            now = time.monotonic()
            spot = await self.wisp_spot(kind, {g for g, t in taken.items() if now - t < WISP_RETRY})
            if spot is not None:
                gid, place = spot
                taken[gid] = now
                await walk_in_zone(
                    self.client, place, why=f"{kind} wisp", controller=self.controller, reach=25.0
                )
                await asyncio.sleep(0.8)
                continue
            if now >= next_log:
                next_log = now + 30.0
                logger.info(
                    f"seeds: waiting for {kind} wisps to respawn in {zone_title(self.hunt.refill)} "
                    f"(health {_pct(health)}, mana {_pct(mana)})"
                )
            await asyncio.sleep(2.0)
        health, mana = await self.ratios()
        logger.info(
            f"seeds: {self.cfg.northguard_minutes:.0f} min in {zone_title(self.hunt.refill)} is up "
            f"(health {_pct(health)}, mana {_pct(mana)}); going back"
        )
        return False

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

    async def rest(self, need: str, strict: bool = False) -> None:
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
                if recovered(need, health, mana, strict):
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


def zone_title(zone: str) -> str:
    """'Grizzleheim/GH_Hero' -> 'Savarstaad Pass' for the two the seed farm knows, else the path."""
    return {SAVARSTAAD: "Savarstaad Pass", NORTHGUARD: "Northguard"}.get(zone, zone or "the hunt's zone")


def _pct(ratio: float | None) -> str:
    return "?" if ratio is None else f"{ratio * 100:.0f}%"


def _reraise_closed(exc: BaseException) -> None:
    if type(exc).__name__ == "ClientClosedError":
        raise exc


async def seed_loop(client, controller, task: dict, cfg) -> None:
    """Farm Savarstaad until stopped."""
    farm = SeedFarm(client, controller, task, cfg)
    if farm.hunt.seeds:
        logger.info(
            "seeds: Savarstaad Pass, Troubled Warriors and Splithoof Barbarians, by walking only. "
            "Start it standing in Boar Camp."
        )
    else:
        plan = "; then ".join(
            f"{h.name} x{h.count or 'until stopped'} in {zone_title(h.zone)}" for h in farm.hunts
        )
        logger.info(f"seeds: hunts, in turn: {plan}. By walking only; the run stops after the last.")
    await run_steps(farm, controller)


async def run_steps(farm, controller) -> None:
    """Step until stopped; one error repeating SAME_ERROR_STOP times stops the run."""
    from .safety import BotStopped

    closed = errors = same = 0
    last_error = ""
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            await farm.step()
            closed = errors = same = 0
            last_error = ""
        except BotStopped:
            break
        except Exception as exc:
            if type(exc).__name__ == "ClientClosedError":
                from .bot import game_gone

                closed += 1
                if game_gone(controller, closed):
                    break
                await asyncio.sleep(2.0)
                continue
            closed = 0
            errors += 1
            error = f"{type(exc).__name__}: {exc}"
            same = same + 1 if error == last_error else 1
            last_error = error
            logger.opt(exception=exc).error(f"seeds: step failed: {error}")
            blackbox.event("step_error", error=error)
            if same >= SAME_ERROR_STOP:
                # 10-14: a bug failed every step for ten hours, retried every 13s.
                why = f"the same error {same} times in a row ({error})"
                logger.error(f"seeds: stopping: {why}")
                blackbox.event("needs_player", why=f"bot error: {error}")
                controller.stop(f"seeds kept failing: {error}")
                break
            await asyncio.sleep(min(30.0, 1.0 * errors))
    logger.info(f"seeds: {farm.summary()} this run")
