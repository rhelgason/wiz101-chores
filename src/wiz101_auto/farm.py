"""One fight for seeds or gold, then back to the day plan.

The mob is the one named for that farm, by its template name or the name
on its plate. Savarstaad casts Deathblade, then Empower, then Scarecrow,
and passes while the next of those is not ready. If those creatures are
not loaded, it moves through the pass until one is.
A pull walks to the aggro edge. It teleports only when the creature is far
and the wizard is clear of every other one, and that jump lands well outside
the creature. Standing on one means walking away. Each teleport and walk is
logged before it runs.
Any other farm casts an area hit from the deck that is already equipped, or
a single hit, or it passes. Below about 60% health, and only when that next
card cannot be cast, the round casts a death drain on an enemy first (Ghoul,
Wraith, Vampire, Skeletal Dragon, or a card that steals health). Sacrifice
is not used. A defeat, or health that is already low, takes a potion or a
health wisp and returns. Nothing here stops the session.
"""

from __future__ import annotations

import asyncio
import math
import re
import time

from loguru import logger

PLACES = {
    "splithoof": ("Savarstaad Pass", "Boar Camp"),
    "seeds": ("Savarstaad Pass",),
    "halfang": ("Vestrilund",),
    "early-gold": ("Triton Avenue", "Cyclops Lane"),
}
NEEDLES = {
    "splithoof": ("splithoof", "barbarian"),
    "seeds": ("troubled", "splithoof barbarian"),
    "halfang": ("halfang", "bristlecrown"),
    "early-gold": ("lost soul", "rotten", "dark fairy", "ghoul"),
}
# First group wins over a closer creature from a later group.
GROUPS = {
    "seeds": (("troubled", "splithoof barbarian"), ("splithoof",)),
    "splithoof": (("splithoof", "barbarian"),),
    "halfang": (("halfang", "bristlecrown"),),
    "early-gold": (("lost soul", "rotten", "dark fairy", "ghoul"),),
}
# Compass landmarks in Grizzleheim/GH_Hero (Compass.xml), plus the gate
# back to Northguard. The camp is one of these; the client only loads the
# creatures near the wizard.
SAVARSTAAD_CAMPS = (
    (-29892.1, 17385.7, 516.9),
    (-21881.5, -1694.7, -38.1),
    (-7599.6, -432.4, 641.3),
    (-8122.4, -7139.8, 971.1),
    (-7491.8, -4317.8, 501.9),
    (-6529.6, -4087.8, 406.5),
    (6449.9, -6051.4, 1927.8),
    (22112.1, 1220.5, 601.3),
    (23071.7, 19170.1, 2387.2),
    (11919.6, 19511.6, 1213.5),
    (7398.4, 37146.1, 1515.3),
    (-8256.4, 36869.1, 1661.1),
    (-11430.9, 23105.8, 880.1),
)
CAMPS = {"seeds": SAVARSTAAD_CAMPS, "splithoof": SAVARSTAAD_CAMPS}
_camp_at = {"seeds": 0, "splithoof": 0}
# Closer than this, the wizard is on the creature. Walk away. Never teleport.
TOO_CLOSE = 400.0
# Walk to here: inside the 600 aggro radius, outside the creature's body.
INSIDE_EDGE = 520.0
# A far teleport lands here, then the wizard walks in. Also the walk-away point.
OUTSIDE = 1800.0
# Teleport only when the chosen creature is farther than this.
FAR = 4000.0
# And only when every other matching creature is at least this far.
CLEAR_OF = 1000.0
# approach_point's default. Pulls pass an explicit gap.
ENGAGE_GAP = INSIDE_EDGE
# A creature that did not start a fight, or just died, is left alone this long.
MISS_FOR = 20.0
# After walking off a creature, leave it long enough to pick a different one.
WALK_OUT_FOR = 8.0
_leave_until: dict[int, float] = {}
_last_step = ""
ROUNDS = 30
HEAL_BELOW = 0.60  # cast a death drain while health is under this
PULL_BELOW = 0.35  # gather health before starting another fight
# Death drains. Cast on an enemy; the caster is the one who is healed.
DRAIN_NAMES = ("ghoul", "wraith", "vampire", "skeletal dragon")
DRAIN_EFFECTS = {"steal_health", "steal_over_time"}
# Savarstaad. Each is cast once, in order, then Scarecrow again if they live.
COMBO = ("deathblade", "empower", "scarecrow")


def _flat(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def mob_rank(name: str, groups: tuple[tuple[str, ...], ...]) -> int | None:
    """Which name group matches, starting at 0. An earlier group is preferred."""
    flat = _flat(name)
    if not flat:
        return None
    for rank, group in enumerate(groups):
        if any(_flat(needle) in flat for needle in group if _flat(needle)):
            return rank
    return None


def mob_matches(name: str, needles: tuple[str, ...]) -> bool:
    return mob_rank(name, (needles,)) is not None


def approach_point(
    me: tuple[float, float, float],
    mob: tuple[float, float, float],
    gap: float = ENGAGE_GAP,
) -> tuple[float, float, float]:
    """A point `gap` from the creature, on the wizard's side of it."""
    dx = mob[0] - me[0]
    dy = mob[1] - me[1]
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        return mob[0] + gap, mob[1], mob[2]
    reach = gap / dist if dist < gap else (dist - gap) / dist
    if dist < gap:
        return mob[0] - dx * reach, mob[1] - dy * reach, mob[2]
    return me[0] + dx * reach, me[1] + dy * reach, mob[2]


def pull_plan(dist: float) -> str:
    """walk_out on the creature, teleport only when it is far, otherwise walk_in."""
    if dist < TOO_CLOSE:
        return "walk_out"
    if dist > FAR:
        return "teleport"
    return "walk_in"


def teleport_allowed(dist: float, nearest: float | None, *, known: bool) -> bool:
    """A jump is safe only from clear ground, toward a creature that is far away.

    `nearest` is the closest other matching creature. None means none was loaded.
    `known` is false when that scan failed; an unread zone is not jumped.
    """
    if not known or pull_plan(dist) != "teleport":
        return False
    if nearest is None:
        return True
    return nearest >= CLEAR_OF


def nearest_other(
    rows: list[tuple[object, str, float, int]],
    gid: int,
    groups: tuple[tuple[str, ...], ...],
) -> float | None:
    """Distance to the closest other named creature. A gid of 0 matches nothing."""
    best = None
    for _entity, label, dist, other in rows:
        if mob_rank(label, groups) is None:
            continue
        if gid and other == gid:
            continue
        if best is None or dist < best:
            best = dist
    return best


def note_step(text: str) -> None:
    """Remember the teleport or walk that is about to run, for the crash line."""
    global _last_step
    _last_step = text


def last_step() -> str:
    return _last_step


def closed_detail() -> str:
    """Suffix for the client-closed line, naming the step that was in progress."""
    step = last_step()
    return f" during {step}" if step else ""


def leave_alone(gid: int, seconds: float = MISS_FOR) -> None:
    if gid:
        _leave_until[gid] = time.monotonic() + seconds


def resting(gid: int, now: float | None = None) -> bool:
    if not gid:
        return False
    return (time.monotonic() if now is None else now) < _leave_until.get(gid, 0.0)


def _client_closed(exc: BaseException) -> bool:
    return type(exc).__name__ == "ClientClosedError"


def best_mob(labels: list[tuple[str, float]], groups: tuple[tuple[str, ...], ...]) -> int | None:
    """Index of the preferred named mob. Closer wins inside one group."""
    chosen = None
    key = None
    for index, (label, dist) in enumerate(labels):
        rank = mob_rank(label, groups)
        if rank is None:
            continue
        candidate = (rank, dist)
        if key is None or candidate < key:
            key = candidate
            chosen = index
    return chosen


def needs_heal(ratio: float | None) -> bool:
    return ratio is not None and ratio < HEAL_BELOW


def too_hurt_to_pull(ratio: float | None) -> bool:
    """Unread health does not skip the pull. Very low health does."""
    return ratio is not None and ratio < PULL_BELOW


def is_death_heal(name: str, effects: list[str] | None = None) -> bool:
    """A death drain that heals the caster. Sacrifice spends health, so it is not one."""
    low = (name or "").lower()
    if "sacrifice" in low:
        return False
    if any(needle in low for needle in DRAIN_NAMES):
        return True
    kinds = {str(effect).split(".")[-1].lower() for effect in (effects or [])}
    return bool(kinds & DRAIN_EFFECTS)


def _pt(point: tuple[float, float, float]) -> str:
    return f"({point[0]:.0f},{point[1]:.0f},{point[2]:.0f})"


async def _hook_busy(client) -> bool | None:
    """True while a teleport is still writing. None when the hook cannot be read."""
    helper = getattr(client, "_teleport_helper", None)
    read = getattr(helper, "should_update", None) if helper is not None else None
    if read is None:
        return None
    try:
        return bool(await read())
    except Exception as exc:
        logger.info(f"farm: teleport hook unreadable ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return None


async def _log_where(client, what: str) -> None:
    try:
        me = await client.body.position()
        fighting = await client.in_battle()
    except Exception as exc:
        logger.info(f"farm: {what} position unread ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return
    logger.info(
        f"farm: {what} at ({me.x:.0f},{me.y:.0f},{me.z:.0f}) "
        f"in_battle={str(bool(fighting)).lower()}"
    )


async def _teleport_to(
    client,
    dest: tuple[float, float, float],
    why: str,
    *,
    engage: bool = True,
) -> bool:
    """Teleport only when the hook is idle. The log line is written first."""
    from wizwalker import XYZ

    busy = await _hook_busy(client)
    if busy:
        logger.info(f"farm: teleport hook is busy before {why}; waiting")
        await asyncio.sleep(2.0)
        busy = await _hook_busy(client)
        if busy:
            logger.info(f"farm: teleport hook still busy; skipping {why}")
            return False
    shown = "unknown" if busy is None else "false"
    note_step(f"step=teleport why={why} hook_busy={shown} dest={_pt(dest)} engage={engage}")
    logger.info(f"farm: {last_step()}")
    await client.teleport(XYZ(dest[0], dest[1], dest[2]), allow_engage=engage)
    from .safe_teleport import teleport_aborted

    if teleport_aborted(client):
        logger.info(f"farm: teleport refused ({why}); an enemy is on that spot")
        return False
    await _log_where(client, f"landed {why}")
    return True


async def _walk_to(client, dest: tuple[float, float, float], why: str) -> None:
    """Walk. Never to the creature's own coordinates. The log line is written first."""
    note_step(f"step=goto why={why} dest=({dest[0]:.0f},{dest[1]:.0f})")
    logger.info(f"farm: {last_step()}")
    await client.goto(dest[0], dest[1])
    await _log_where(client, f"after {why}")


async def _pull(
    client,
    mob,
    label: str,
    gid: int,
    target: str,
    nearest: float | None,
    known: bool,
) -> bool:
    """Start the fight. Never teleport while on a creature, and never walk onto its origin."""
    try:
        if await client.in_battle():
            logger.info(f"farm: already in a fight with {label or target} gid={gid or 0}")
            return True
    except Exception as exc:
        logger.info(f"farm: battle check failed ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return False
    try:
        pos = await mob.location()
        me = await client.body.position()
    except Exception as exc:
        logger.info(f"farm: pull positions unread ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return False
    dist = math.hypot(pos.x - me.x, pos.y - me.y)
    plan = pull_plan(dist)
    near_s = "none" if nearest is None else f"{nearest:.0f}"
    logger.info(
        f"farm: pulling {label or target} gid={gid or 0} plan={plan} dist={dist:.0f} "
        f"me=({me.x:.0f},{me.y:.0f},{me.z:.0f}) mob=({pos.x:.0f},{pos.y:.0f},{pos.z:.0f}) "
        f"nearest_other={near_s} known={str(known).lower()}"
    )
    if not gid:
        logger.info("farm: this creature has no id, so a miss will not be remembered")
    mob_xyz = (pos.x, pos.y, pos.z)
    try:
        if plan == "walk_out":
            away = approach_point((me.x, me.y, me.z), mob_xyz, OUTSIDE)
            await _walk_to(client, away, f"walk out, gid={gid or 0} was {dist:.0f} away")
            if await client.in_battle():
                logger.info(f"farm: fight started while walking out gid={gid or 0}")
                return True
            leave_alone(gid, WALK_OUT_FOR)
            logger.info(f"farm: walked clear of gid={gid or 0}; leaving it for {WALK_OUT_FOR:.0f}s")
            return False
        if plan == "teleport" and not teleport_allowed(dist, nearest, known=known):
            logger.info(f"farm: not teleporting; nearest other creature is {near_s}. Walking in")
            plan = "walk_in"
        if plan == "teleport":
            land = approach_point((me.x, me.y, me.z), mob_xyz, OUTSIDE)
            why = f"outside gid={gid or 0} nearest_other={near_s}"
            if not await _teleport_to(client, land, why):
                logger.info("farm: teleport skipped; walking in from here")
                plan = "walk_in"
        if plan in {"walk_in", "teleport"}:
            pos = await mob.location()
            me = await client.body.position()
            dist = math.hypot(pos.x - me.x, pos.y - me.y)
            if dist > INSIDE_EDGE:
                edge = approach_point((me.x, me.y, me.z), (pos.x, pos.y, pos.z), INSIDE_EDGE)
                await _walk_to(client, edge, f"aggro edge gid={gid or 0} from {dist:.0f}")
            else:
                logger.info(f"farm: already {dist:.0f} from gid={gid or 0}; not walking any closer")
    except Exception as exc:
        logger.info(f"farm: pull step failed ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        try:
            if await client.in_battle():
                logger.info(f"farm: fight started during the pull gid={gid or 0}")
                return True
        except Exception as check:
            logger.info(f"farm: battle check failed ({type(check).__name__}: {check})")
            if _client_closed(check):
                raise
        return False
    return await _wait_for_fight(client, mob, gid)


async def _wait_for_fight(client, mob, gid: int) -> bool:
    for _ in range(10):
        try:
            if await client.in_battle():
                logger.info(f"farm: fight started with gid={gid or 0}")
                return True
        except Exception as exc:
            logger.info(f"farm: battle check failed ({type(exc).__name__}: {exc})")
            if _client_closed(exc):
                raise
            return False
        await asyncio.sleep(0.4)
    logger.info(f"farm: gid={gid or 0} did not start a fight; walking clear")
    try:
        pos = await mob.location()
        me = await client.body.position()
        away = approach_point((me.x, me.y, me.z), (pos.x, pos.y, pos.z), OUTSIDE)
        await _walk_to(client, away, f"clear after a miss gid={gid or 0}")
        if await client.in_battle():
            logger.info(f"farm: fight started while walking clear gid={gid or 0}")
            return True
    except Exception as exc:
        logger.info(f"farm: walk clear failed ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        try:
            if await client.in_battle():
                logger.info(f"farm: fight started while walking clear gid={gid or 0}")
                return True
        except Exception as check:
            logger.info(f"farm: battle check failed ({type(check).__name__}: {check})")
            if _client_closed(check):
                raise
    leave_alone(gid)
    logger.info(f"farm: leaving gid={gid or 0} alone for {MISS_FOR:.0f}s")
    return False


async def _step_off(client, mob) -> bool:
    """Walk clear of a corpse before the next teleport. False when still on it."""
    try:
        pos = await mob.location()
        me = await client.body.position()
    except Exception as exc:
        logger.info(f"farm: could not measure the corpse ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return False
    dist = math.hypot(pos.x - me.x, pos.y - me.y)
    if dist >= CLEAR_OF:
        logger.info(f"farm: already {dist:.0f} from the fight; no need to walk clear")
        return True
    try:
        away = approach_point((me.x, me.y, me.z), (pos.x, pos.y, pos.z), OUTSIDE)
        await _walk_to(client, away, f"clear of the fight ({dist:.0f})")
    except Exception as exc:
        logger.info(f"farm: step off failed ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return False
    try:
        if await client.in_battle():
            logger.info("farm: a fight started while walking off the last one")
            return False
    except Exception as exc:
        logger.info(f"farm: battle check failed ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return False
    return True


async def fight_one(client, target: str, controller=None) -> bool:
    """Travel, pull one matching mob, and play the duel. False if no fight started.

    A defeat or a failed pull is a retry. The controller is not stopped.
    """
    from .safety import BotStopped

    if controller:
        controller.allow_idle(240)
    try:
        await _recover(client, controller)
        if too_hurt_to_pull(await _ratio(client)):
            logger.info("farm: health is still low; gathering it again before the next pull")
            return False
        if not await _go(client, target):
            return False
        picked = await _acquire_mob(client, target)
        if picked is None:
            return False
        mob, label, gid, nearest, known = picked
        if not await _pull(client, mob, label, gid, target, nearest, known):
            return False
        await _play(client, controller, combo=target == "seeds")
        await _note_potato(client)
        zone = ""
        try:
            zone = await client.zone_name() or ""
        except Exception:
            pass
        logger.info(f"farm: the fight in {zone.split('/')[-1] or 'this zone'} is over")
        leave_alone(gid, 12.0)
        clear = await _step_off(client, mob)
        if await client.in_battle():
            logger.info("farm: playing the fight that started while walking clear")
            await _play(client, controller, combo=target == "seeds")
            await _note_potato(client)
            clear = False
        await _recover(client, controller, allow_teleport=clear)
        await asyncio.sleep(1.5)
        return True
    except BotStopped:
        raise
    except Exception as exc:
        if _client_closed(exc):
            raise
        logger.info(
            f"farm: the pull failed ({type(exc).__name__}: {exc}); gathering health and going back"
        )
        try:
            await _recover(client, controller)
        except BotStopped:
            raise
        except Exception:
            pass
        return False
    finally:
        if controller:
            controller.end_idle()


async def _go(client, target: str) -> bool:
    from .travel import go_to_zone
    from .travel_data import _data, named_zone

    places = PLACES.get(target, ())
    here = (await client.zone_name() or "").lower()
    if any(place.lower() in here for place in places):
        return True
    try:
        display = _data()[1]
    except Exception as exc:
        logger.info(f"farm: no zone list ({exc})")
        return False
    for place in places:
        dest = named_zone(place, display)
        if dest and await go_to_zone(client, dest):
            logger.info(f"farm: at {place}")
            return True
    logger.info(f"farm: could not reach {', '.join(places) or target}")
    return False


def _groups(target: str) -> tuple[tuple[str, ...], ...]:
    return GROUPS.get(target) or ((NEEDLES.get(target, ()),),)


async def _template_names(entity) -> tuple[str, str]:
    template = await entity.object_template()
    if not template:
        return "", ""
    object_name = (await template.object_name() or "").replace("_", " ")
    code = (await template.display_name() or "").strip()
    return object_name, code


async def _plate(client, object_name: str, code: str) -> str:
    """The template name plus the name floating over the creature."""
    shown = ""
    if code and "_" in code:
        from .names import lang_name

        shown = await lang_name(client, code)
    elif code:
        shown = code
    return f"{object_name} {shown}".strip()


async def _is_npc(entity) -> bool:
    try:
        for behavior in await entity.inactive_behaviors():
            kind = await behavior.read_type_name()
            named = await behavior.behavior_name()
            if kind == "NPCBehavior" or named == "NPCBehavior":
                return True
    except Exception:
        return False
    return False


async def _entity_gid(entity) -> int:
    """Prefer the full id. A failed read is 0, and 0 cannot be left alone."""
    for method in ("global_id_full", "global_id"):
        read = getattr(entity, method, None)
        if read is None:
            continue
        try:
            gid = int(await read() or 0)
        except Exception:
            continue
        if gid:
            return gid
    return 0


async def _nearby(client, groups: tuple[tuple[str, ...], ...]) -> list[tuple[object, str, float, int]] | None:
    """Loaded entities. None when the list itself could not be read.

    Plate names are read only when the template name missed.
    """
    try:
        entities = await client.get_base_entity_list()
    except Exception as exc:
        logger.info(f"farm: creatures unreadable ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return None
    try:
        me = await client.body.position()
    except Exception as exc:
        logger.info(f"farm: wizard position unread ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return None
    raw = []
    for entity in entities or []:
        try:
            object_name, code = await _template_names(entity)
            if not object_name and not code:
                continue
            pos = await entity.location()
            dist = math.hypot(pos.x - me.x, pos.y - me.y)
        except Exception:
            continue
        raw.append((entity, object_name, code, dist, await _entity_gid(entity)))
    named = [(entity, object_name, dist, gid) for entity, object_name, _code, dist, gid in raw]
    ready = [row for row in named if not resting(row[3])]
    if best_mob([(label, dist) for _e, label, dist, _g in ready], groups) is not None:
        return named
    found = []
    for entity, object_name, code, dist, gid in raw:
        label = object_name
        try:
            if code and await _is_npc(entity):
                label = await _plate(client, object_name, code)
        except Exception:
            label = object_name
        if label:
            found.append((entity, label, dist, gid))
    return found


def _ready(found: list[tuple[object, str, float, int]]) -> list[tuple[object, str, float, int]]:
    now = time.monotonic()
    return [row for row in found if not resting(row[3], now)]


def _name_sample(found: list[tuple[object, str, float, int]]) -> str:
    words = [label for _e, label, _d, _g in found if " " in label]
    pool = words or [label for _e, label, _d, _g in found if label]
    return ", ".join(pool[:8]) or "nothing named"


def _resting_note(seen: list[tuple[object, str, float, int]], groups: tuple[tuple[str, ...], ...]) -> str:
    now = time.monotonic()
    parts = []
    for _entity, label, _dist, gid in seen:
        if mob_rank(label, groups) is None:
            continue
        if not gid:
            parts.append(f"gid=0 {label} (cannot skip)")
            continue
        left = _leave_until.get(gid, 0.0) - now
        if left > 0:
            parts.append(f"gid={gid} {label} {left:.0f}s")
    return ", ".join(parts[:6])


def _picked(found, pick: int, seen, groups):
    entity, label, dist, gid = found[pick]
    logger.info(f"farm: chose {label} gid={gid or 0} dist={dist:.0f}")
    skipped = _resting_note(seen, groups)
    if skipped:
        logger.info(f"farm: skipped {skipped}")
    return entity, label, gid, nearest_other(seen, gid, groups), True


async def _acquire_mob(client, target: str):
    """The closest preferred mob that was not just fought. Moves on if none are loaded.

    Returns (entity, label, gid, nearest_other, known) or None. known is false only
    when a later caller is told the scan failed; a failed scan returns None itself.
    """
    groups = _groups(target)
    seen = await _nearby(client, groups)
    if seen is None:
        logger.info("farm: creature list failed; not teleporting")
        await asyncio.sleep(2.0)
        return None
    found = _ready(seen)
    pick = best_mob([(label, dist) for _e, label, dist, _g in found], groups)
    if pick is not None:
        return _picked(found, pick, seen, groups)
    resting_match = any(
        resting(gid) and mob_rank(label, groups) is not None for _e, label, _d, gid in seen
    )
    if resting_match:
        skipped = _resting_note(seen, groups)
        logger.info(
            "farm: the nearby creatures just failed to fight; waiting "
            f"({skipped or 'no ids'})"
        )
        await asyncio.sleep(4.0)
        return None
    camps = CAMPS.get(target)
    sample = _name_sample(seen)
    if not camps:
        logger.info(f"farm: no {target} mob in this zone ({sample})")
        await asyncio.sleep(3.0)
        return None
    blocked = nearest_other(seen, 0, groups)
    if blocked is not None and blocked < CLEAR_OF:
        logger.info(f"farm: not teleporting to the next camp; a creature is {blocked:.0f} away")
        await asyncio.sleep(2.0)
        return None
    spot = camps[_camp_at[target] % len(camps)]
    _camp_at[target] += 1
    logger.info(f"farm: no {target} mob loaded ({sample}); moving to another part of the zone")
    try:
        moved = await _teleport_to(client, spot, f"next camp {_pt(spot)}")
    except Exception as exc:
        logger.info(f"farm: could not move to the next camp ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        await asyncio.sleep(2.0)
        return None
    if not moved:
        await asyncio.sleep(2.0)
        return None
    await asyncio.sleep(1.2)
    seen = await _nearby(client, groups)
    if not seen:
        logger.info("farm: still no mob after the move")
        return None
    found = _ready(seen)
    pick = best_mob([(label, dist) for _e, label, dist, _g in found], groups)
    if pick is None:
        skipped = _resting_note(seen, groups)
        logger.info(f"farm: still no mob after the move ({skipped or sample})")
        return None
    return _picked(found, pick, seen, groups)


async def _ratio(client) -> float | None:
    try:
        current = int(await client.stats.current_hitpoints())
        maximum = int(await client.stats.max_hitpoints())
    except Exception:
        return None
    if maximum <= 0:
        return None
    return current / maximum


async def _recover(client, controller=None, *, allow_teleport: bool = True) -> None:
    """Potion, then a health wisp, when health is low or the wizard is down."""
    ratio = await _ratio(client)
    if ratio is not None and ratio <= 0:
        zone = ""
        try:
            zone = await client.zone_name() or ""
        except Exception:
            pass
        if controller:
            controller.record_death(zone)
        logger.info("farm: defeated; gathering health and going back")
        await asyncio.sleep(3.0)
        ratio = await _ratio(client)
    if not needs_heal(ratio):
        return
    if await _potion(client):
        ratio = await _ratio(client)
    if needs_heal(ratio):
        await _wisp(client, allow_teleport=allow_teleport)


async def _potion(client) -> bool:
    try:
        charge = float(await client.stats.potion_charge())
    except Exception:
        return False
    if charge < 1.0:
        return False
    use = getattr(client, "use_potion", None)
    if use is None:
        return False
    try:
        used = await use()
    except Exception as exc:
        logger.info(f"farm: potion failed ({exc})")
        return False
    if used:
        logger.info("farm: used a potion")
        await asyncio.sleep(0.6)
    return bool(used)


async def _wisp(client, *, allow_teleport: bool = True) -> bool:
    finder = getattr(client, "find_closest_health_wisp", None)
    if finder is None:
        logger.info("farm: this client cannot look for a health wisp")
        return False
    if not allow_teleport:
        logger.info("farm: not teleporting to a health wisp while still on a creature")
        return False
    try:
        wisp = await finder(only_safe=False)
    except Exception as exc:
        logger.info(f"farm: health wisp lookup failed ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return False
    if wisp is None:
        logger.info("farm: no health wisp in this zone")
        return False
    try:
        pos = await wisp.location()
        moved = await _teleport_to(
            client, (pos.x, pos.y, pos.z), "health wisp", engage=False
        )
    except Exception as exc:
        logger.info(f"farm: could not reach a health wisp ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return False
    if not moved:
        return False
    await asyncio.sleep(0.8)
    logger.info("farm: stood on a health wisp")
    return True


async def _note_potato(client) -> None:
    """Count a seed the reward line named when the backpack read did not grow."""
    from . import ui
    from .garden import garden_state, loot_mentioned_potato, note_live, save_garden

    before = int(garden_state().get("couch_potatoes") or 0)
    try:
        await note_live(client)
    except Exception as exc:
        logger.debug(f"farm: backpack unread after the fight ({exc})")
    after = int(garden_state().get("couch_potatoes") or 0)
    try:
        texts = await ui.visible_texts(client)
    except Exception:
        texts = []
    if loot_mentioned_potato(texts) and after <= before:
        state = garden_state()
        state["couch_potatoes"] = after + 1
        save_garden(state)
        logger.info(f"farm: couch potatoes {state['couch_potatoes']}")


def combo_spell(name: str) -> str | None:
    """'Death Blade' -> deathblade. 'Empowered' is not Empower."""
    low = (name or "").lower()
    flat = re.sub(r"[^a-z]", "", low)
    if "deathblade" in flat:
        return "deathblade"
    if "scarecrow" in flat:
        return "scarecrow"
    if re.search(r"\bempower\b", low):
        return "empower"
    return None


def next_combo(done: list[str], castable: set[str], in_hand: set[str]) -> str | None:
    """The next Savarstaad card, or None to pass.

    A card that is in hand but not castable yet is waited on. A card that is
    not in hand is skipped, so a missing blade still reaches Scarecrow.
    """
    finished = set(done)
    for step in COMBO:
        if step in finished:
            continue
        if step in castable:
            return step
        if step in in_hand:
            return None
    if "scarecrow" in castable:
        return "scarecrow"
    return None


async def _play(client, controller, combo: bool = False):
    from wizwalker.combat import AoeHandler

    handler = AoeHandler(client)
    done: list[str] = []
    for _ in range(ROUNDS):
        if controller:
            await controller.checkpoint()
        ratio = await _ratio(client)
        if ratio is not None and ratio <= 0:
            return
        try:
            if not await handler.in_combat():
                return
        except Exception:
            return
        try:
            await handler.wait_for_planning_phase()
        except Exception:
            return
        try:
            if combo:
                await _cast_combo(handler, done, heal=needs_heal(ratio))
            else:
                await _cast_or_pass(handler, heal=needs_heal(ratio))
        except Exception as exc:
            logger.info(f"farm: round failed ({exc}); passing")
            try:
                await handler.pass_button()
            except Exception:
                return
        await asyncio.sleep(0.4)


async def _cast_combo(handler, done: list[str], heal: bool = False) -> str:
    """Deathblade on the wizard, Empower on the wizard, Scarecrow on the fight."""
    cards = []
    for card in await handler.get_cards():
        try:
            castable = await card.is_castable()
            name = await _card_name(card)
        except Exception:
            continue
        spell = combo_spell(name)
        if spell:
            cards.append((card, spell, castable))
    in_hand = {spell for _card, spell, _ok in cards}
    castable = {spell for _card, spell, ok in cards if ok}
    step = next_combo(done, castable, in_hand)
    if step is None and heal:
        drained = await _cast_drain(handler)
        if drained:
            return drained
        logger.info("farm: health is low and no death drain is in hand")
    if step is None:
        await handler.pass_button()
        logger.info("farm: passing until Deathblade, Empower, or Scarecrow is ready")
        return "pass"
    card = next(card for card, spell, ok in cards if spell == step and ok)
    if step == "scarecrow":
        await card.cast(None)
    else:
        await card.cast(await handler.get_client_member())
    done.append(step)
    logger.info(f"farm: casting {step}")
    return step


async def _card_name(card) -> str:
    for reader in (card.display_name, card.name):
        try:
            text = await reader()
        except Exception:
            continue
        if text:
            return text
    return ""


async def _effect_names(card) -> list[str]:
    try:
        effects = await card.get_spell_effects()
    except Exception:
        return []
    names = []
    for effect in effects or []:
        try:
            kind = await effect.effect_type()
        except Exception:
            continue
        names.append(getattr(kind, "name", None) or str(kind))
    return names


async def _living_enemies(handler) -> list:
    enemies = []
    for member in await handler.get_members():
        try:
            if await member.is_monster() and not await member.is_dead():
                enemies.append(member)
        except Exception:
            continue
    return enemies


async def _cast_drain(handler) -> str | None:
    """A death drain on one living enemy, or None when none can be cast."""
    enemies = await _living_enemies(handler)
    if not enemies:
        return None
    for card in await handler.get_cards():
        try:
            if not await card.is_castable():
                continue
            name = await _card_name(card)
            effects = await _effect_names(card)
        except Exception:
            continue
        if not is_death_heal(name, effects):
            continue
        await card.cast(enemies[0])
        logger.info(f"farm: health is low; casting {name or 'a death drain'}")
        return "heal"
    return None


async def _cast_or_pass(handler, heal: bool = False) -> str:
    if heal:
        drained = await _cast_drain(handler)
        if drained:
            return drained
        logger.info("farm: health is low and no death drain is in hand")
    enemies = await _living_enemies(handler)
    aoes = await handler.get_damaging_aoes(check_enchanted=False)
    if aoes and await aoes[0].is_castable():
        await aoes[0].cast(None)
        return "aoe"
    for card in await handler.get_cards():
        try:
            if not await card.is_castable():
                continue
            kind = (await card.type_name() or "").lower()
            name = await _card_name(card)
            effects = await _effect_names(card)
        except Exception:
            continue
        if kind in ("enchantment", "heal", "healing", "ward", "charm", "global"):
            continue
        if is_death_heal(name, effects):
            continue
        if enemies:
            await card.cast(enemies[0])
            return "single"
    await handler.pass_button()
    return "pass"
