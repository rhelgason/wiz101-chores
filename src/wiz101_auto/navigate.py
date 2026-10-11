"""Travel between zones and worlds on foot. Nothing here teleports.

Three layers:
- the route: which gates and which world door, from WizSprinter's gate list
  and world-door list (`travel_data.travel_step`);
- the walk inside a zone: the game's own walking graph (`navmesh`, from
  each zone's zone.nav), A* to the gate, then waypoint to waypoint with
  `walk.walk_point`; a stuck leg marks that edge blocked and plans again;
- the doorways: a standard gate is walked into until the zone changes; an
  "x" gate (a boat, a rope, a door that asks) is walked to and X is pressed;
  a world door is found by its object name, walked to, opened with X, and
  the Spiral Map is clicked (a server-side zone change, not a memory write).

A fight that starts on the way is handed to `on_fight`, then travel goes on.
Every leg is logged and recorded in events.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from . import blackbox, navmesh

DOORS_FILE = Path("state") / "world_doors.json"  # door spots found live: {zone: [x, y, z]}
WAYPOINT_REACH = 110.0
GATE_REACH = 35.0
DOOR_REACH = 140.0
PUSH_SECONDS = 1.6  # W held through a gate that the walk did not cross yet
MAX_REPLANS = 3
MAX_STEPS = 24
DORM = "WizardCity/Interiors/WC_Housing_Dorm_Interior"
DORM_DOOR = (-205.0, 13.0, 0.0)

Fight = Callable[[], Awaitable[None]]


def leg_seconds(dist: float) -> float:
    """Time allowed for one leg: base speed, doubled, plus slack for turning."""
    return min(90.0, 6.0 + 2.0 * dist / 580.0)


def blocked_edge(graph, a, b) -> set[tuple[int, int]]:
    """Both directions of the graph edge nearest the leg from `a` to `b`."""
    x, y = graph.nearest(a)[0], graph.nearest(b)[0]
    return {(x, y), (y, x)} if x != y else set()


def load_doors() -> dict[str, tuple[float, float, float]]:
    try:
        data = json.loads(DOORS_FILE.read_text(encoding="utf-8"))
        return {k: tuple(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError):
        return {}


def remember_door(zone: str, spot) -> None:
    doors = load_doors()
    doors[zone] = tuple(round(v, 1) for v in spot[:3])
    try:
        DOORS_FILE.parent.mkdir(exist_ok=True)
        DOORS_FILE.write_text(json.dumps(doors, indent=1), encoding="utf-8")
    except OSError:
        pass


class Navigator:
    def __init__(self, client, controller=None, on_fight: Fight | None = None):
        self.client = client
        self.controller = controller
        self.on_fight = on_fight

    async def checkpoint(self) -> None:
        if self.controller is not None:
            await self.controller.checkpoint()

    async def zone(self) -> str:
        return await self.client.zone_name() or ""

    async def here(self):
        from .walk import position

        return await position(self.client)

    async def _busy(self) -> bool:
        """A fight, a loading screen, or a dialogue in the way."""
        from .farm import in_fight
        from .world import is_free

        if await in_fight(self.client):
            if self.on_fight is not None:
                logger.info("travel: a fight started on the way; playing it")
                blackbox.event("travel_fight")
                await self.on_fight()
            else:
                await asyncio.sleep(1.0)
            return True
        if not await is_free(self.client):
            await asyncio.sleep(0.5)
            return True
        return False

    async def use(self) -> None:
        """Use what is in front of the wizard: click the Press X prompt, and press X.

        X goes as real key state (SendInput), the way the game reads keys;
        the posted X alone did not open the world door on 10-10 19:44.
        """
        from . import ui
        from .walk import VK, keys_for, request_focus

        clicked = await ui.press_interact(self.client)
        keys = keys_for(self.client)
        await keys.tap(VK["X"])
        blackbox.event("use", prompt_clicked=clicked)
        if not clicked:
            request_focus("pressing X")

    async def walk_in_zone(self, goal, *, why: str, reach: float = WAYPOINT_REACH) -> str:
        """Walk to `goal` in this zone on the game's paths.

        'reached', 'left zone' (a gate or a load took the wizard elsewhere),
        'fight' (one started and was played; call again), or 'stuck'.
        """
        from .walk import walk_point

        zone = await self.zone()
        graph = navmesh.zone_graph(zone)
        blocked: set[tuple[int, int]] = set()
        start_zone = zone

        async def interrupt() -> bool:
            try:
                return await self.client.is_loading() or (await self.zone()) != start_zone
            except Exception as exc:
                if type(exc).__name__ == "ClientClosedError":
                    raise
                return True

        async def fight() -> bool:
            try:
                return bool(await self.client.in_battle())
            except Exception as exc:
                if type(exc).__name__ == "ClientClosedError":
                    raise
                return False

        for plan in range(MAX_REPLANS + 1):
            me = await self.here()
            path = graph.route(me, goal, blocked) if graph is not None else None
            if path is None:
                if graph is not None:
                    logger.info(f"travel: no walking path in {zone} to {_pt(goal)}; walking straight")
                path = [tuple(goal)]
            blackbox.event("route", zone=zone, why=why, plan=plan, legs=len(path), to=_r(goal))
            logger.info(f"travel: {why} in {zone.split('/')[-1]}: {len(path)} leg(s)")
            prev = me
            failed = False
            for i, point in enumerate(path):
                last = i == len(path) - 1
                result = await walk_point(
                    self.client,
                    point,
                    why=f"{why} {i + 1}/{len(path)}",
                    stop_within=reach if last else WAYPOINT_REACH,
                    max_seconds=leg_seconds(math.dist(prev[:2], point[:2])),
                    fight=fight,
                    interrupt=interrupt,
                    checkpoint=self.checkpoint,
                    precise=last,
                    keep_moving=not last,
                )
                if result.outcome == "interrupted":
                    if await interrupt():
                        return "left zone"
                    failed = True
                    break
                if result.outcome == "fight":
                    await self._busy()
                    return "fight"
                if result.outcome in ("stuck", "timeout"):
                    if graph is not None:
                        blocked |= blocked_edge(graph, prev, point)
                    logger.info(f"travel: leg {i + 1} {result.outcome}; planning around it")
                    failed = True
                    break
                prev = point
            if not failed:
                return "reached"
        return "stuck"

    async def through_gate(self, gate, nxt: str, kind: str) -> bool:
        """Walk into a gate (or to it, and press X) until the zone is `nxt`."""
        from .travel_data import press_x_gate, ride_gate
        from .walk import VK, keys_for
        from .world import wait_for_loading

        here = await self.zone()
        x_gate = press_x_gate(kind)
        for attempt in range(3):
            outcome = await self.walk_in_zone(
                gate, why=f"to the gate to {nxt.split('/')[-1]}", reach=DOOR_REACH if x_gate else GATE_REACH
            )
            if outcome == "fight":
                continue
            if outcome == "left zone":
                await wait_for_loading(self.client, appear_timeout=4.0)
                return await self.zone() != here
            if x_gate:
                for _leg in range(2 if ride_gate(kind) else 1):
                    leg_zone = await self.zone()
                    for _ in range(8):
                        await self.use()
                        await asyncio.sleep(0.6)
                        if await self.client.is_loading() or await self.zone() != leg_zone:
                            break
                    await wait_for_loading(self.client, appear_timeout=8.0)
            else:
                # On the gate's spot but still here: keep walking the same way through it.
                keys = keys_for(self.client)
                await keys.hold(VK["W"], PUSH_SECONDS)
                await wait_for_loading(self.client, appear_timeout=3.0)
            if await self.zone() != here:
                return True
            logger.info(f"travel: the gate to {nxt} did not load (try {attempt + 1})")
        return False

    async def world_door(self, world: str, known_spot=None) -> bool:
        """Walk to this zone's world door, open the Spiral Map, and pick `world`."""
        from . import ui
        from .travel import DOOR_NEEDLES, _pick_world, entity_pos
        from .travel_data import door_spot_usable

        zone = await self.zone()
        spot = None
        found = await entity_pos(self.client, DOOR_NEEDLES)
        if found is not None:
            spot = (found.x, found.y, found.z)
            remember_door(zone, spot)
        elif zone in load_doors():
            spot = load_doors()[zone]
        elif known_spot is not None and door_spot_usable(known_spot):
            spot = (known_spot.x, known_spot.y, known_spot.z)
        if spot is None:
            logger.warning(f"travel: no world door found in {zone}")
            blackbox.event("travel_failed", why="no world door", zone=zone)
            await self._dump_entities(zone)
            return False
        logger.info(f"travel: world door at {_pt(spot)}; walking to it")
        for _ in range(3):
            outcome = await self.walk_in_zone(spot, why="to the world door", reach=DOOR_REACH)
            if outcome != "fight":
                break
        for _ in range(12):
            if await ui.is_visible(self.client, ui.SPIRAL_DOOR_TELEPORT):
                blackbox.event("spiral_map", world=world)
                return await _pick_world(self.client, world)
            await self.use()
            await asyncio.sleep(0.7)
        await self._dump_windows("spiral_door_windows.txt")
        await self._dump_entities(zone)
        logger.warning("travel: the world door did not open the Spiral Map")
        blackbox.event("travel_failed", why="spiral map did not open", zone=zone)
        return False

    async def leave_dorm(self) -> bool:
        from .travel import RAVENWOOD
        from .walk import VK, keys_for
        from .world import wait_for_loading

        await self.walk_in_zone(DORM_DOOR, why="to the dorm door", reach=GATE_REACH)
        if await self.zone() == DORM:
            await keys_for(self.client).hold(VK["W"], PUSH_SECONDS)
        await wait_for_loading(self.client, appear_timeout=3.0)
        return await self.zone() == RAVENWOOD

    async def go_to_zone(self, dest: str, max_steps: int = MAX_STEPS) -> bool:
        """Walk zone to zone, world to world, until the wizard is in `dest`."""
        from .travel import go_home, is_house
        from .travel_data import _data, gate_kind, travel_step, zone_doors

        gates, doors = _data()[0], zone_doors()
        blocked: set[tuple[str, str]] = set()
        door_uses = 0
        went_home = False
        began = time.monotonic()
        start = await self.zone()
        blackbox.event("travel_start", start=start, dest=dest)
        logger.info(f"travel: {start} -> {dest}, on foot")
        for _ in range(max_steps):
            await self.checkpoint()
            zone = await self.zone()
            if zone == dest:
                logger.success(f"travel: arrived in {dest} after {time.monotonic() - began:.0f}s")
                blackbox.event("travel_done", dest=dest, seconds=round(time.monotonic() - began))
                return True
            if await self._busy():
                continue
            if zone == DORM:
                if not await self.leave_dorm():
                    return self._fail(dest, "could not walk out of the dorm")
                continue
            if is_house(zone):
                if not await self.world_door(dest.split("/")[0]):
                    return self._fail(dest, "the house's world door did not work")
                continue
            step = travel_step(zone, dest, gates, doors, blocked)
            if step is None:
                if not went_home:
                    went_home = True
                    logger.info(
                        f"travel: no known way from {zone}; pressing Go Home, then walking from there"
                    )
                    if await go_home(self.client):
                        continue
                return self._fail(dest, f"no known way from {zone}")
            if step[0] == "world":
                if door_uses >= 2:
                    return self._fail(dest, "the world door did not take the wizard there")
                door_uses += 1
                if not await self.world_door(dest.split("/")[0], step[2]):
                    return self._fail(dest, "the world door did not work")
                continue
            _kind, pos, nxt = step
            kind = gate_kind(zone, nxt)
            logger.info(f"travel: {zone.split('/')[-1]} -> {nxt.split('/')[-1]} ({kind} gate)")
            blackbox.event("travel_gate", frm=zone, to=nxt, gate=kind, at=_r((pos.x, pos.y, pos.z)))
            if not await self.through_gate((pos.x, pos.y, pos.z), nxt, kind):
                logger.warning(f"travel: gate {zone} -> {nxt} did not work; trying another way")
                blocked.add((zone, nxt))
        return self._fail(dest, "too many steps")

    def _fail(self, dest: str, why: str) -> bool:
        logger.warning(f"travel: could not reach {dest}: {why}")
        blackbox.event("travel_failed", dest=dest, why=why)
        return False

    async def _dump_entities(self, zone: str) -> None:
        """Every loaded object's template name and spot, to find a door by hand."""
        folder = blackbox.folder()
        if folder is None:
            return
        lines = []
        try:
            for entity in await self.client.get_base_entity_list():
                try:
                    template = await entity.object_template()
                    name = await template.object_name() if template else ""
                    p = await entity.location()
                    lines.append(f"{name}\t{p.x:.0f}\t{p.y:.0f}\t{p.z:.0f}")
                except Exception:
                    continue
            safe = zone.replace("/", "-")
            (folder / f"entities-{safe}.txt").write_text("\n".join(sorted(lines)), encoding="utf-8")
        except Exception:
            pass

    async def _dump_windows(self, name: str) -> None:
        from . import ui

        folder = blackbox.folder()
        if folder is None:
            return
        try:
            lines = await ui.dump_tree(self.client.root_window, max_depth=6)
            (folder / name).write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass


def _pt(p) -> str:
    return f"({p[0]:.0f}, {p[1]:.0f})"


def _r(p) -> list[int]:
    return [round(v) for v in p[:3]]


async def go_to_zone(client, dest: str, controller=None, on_fight: Fight | None = None) -> bool:
    return await Navigator(client, controller, on_fight).go_to_zone(dest)


async def walk_in_zone(
    client, goal, *, why: str, controller=None, on_fight: Fight | None = None, reach: float = WAYPOINT_REACH
) -> str:
    nav = Navigator(client, controller, on_fight)
    outcome = "fight"
    for _ in range(4):
        outcome = await nav.walk_in_zone(goal, why=why, reach=reach)
        if outcome != "fight":
            break
    return outcome


async def mark_here(client) -> bool:
    """The HUD's Mark Location (MarkButton in GUI/HUDWindow.gui): a spot Recall returns to."""
    from . import ui

    zone = await client.zone_name() or ""
    if not await ui.click_named(client, "MarkButton"):
        logger.info("travel: no Mark Location button on screen; the way back will be walked")
        blackbox.event("mark_failed", zone=zone)
        return False
    await asyncio.sleep(0.8)
    await ui.confirm_modal(client)
    logger.info(f"travel: marked this spot in {zone}")
    blackbox.event("mark", zone=zone)
    return True


async def recall(client, expect_zone: str) -> bool:
    """The HUD's Recall Location: back to the mark (the game moves the wizard, no memory write)."""
    from . import ui
    from .world import wait_for_loading

    if not await ui.click_named(client, "RecallButton"):
        logger.info("travel: no Recall button on screen (cooling down or hidden); walking back")
        blackbox.event("recall_failed", why="no button")
        return False
    await asyncio.sleep(1.0)
    await ui.confirm_modal(client)
    await wait_for_loading(client, appear_timeout=8.0)
    arrived = (await client.zone_name() or "") == expect_zone
    blackbox.event("recall", arrived=arrived)
    logger.info("travel: recalled to the mark" if arrived else "travel: Recall missed the mark; walking back")
    return arrived


async def trip(client, dest: str, home_zone: str, errand, *, controller=None, on_fight=None) -> bool:
    """Mark here, walk to `dest`, run `errand()`, then Recall (or walk) back to `home_zone`."""
    marked = await mark_here(client)
    if not await go_to_zone(client, dest, controller, on_fight):
        return False
    try:
        await errand()
    finally:
        back = marked and await recall(client, home_zone)
        if not back:
            await go_to_zone(client, home_zone, controller, on_fight)
    return True
