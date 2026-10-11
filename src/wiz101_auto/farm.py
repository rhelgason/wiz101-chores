"""One fight for seeds or gold, then back to the day plan.

The mob is the one named for that farm, by its template name or the name
on its plate. Savarstaad casts Empower, then Scarecrow, and passes while
the next of those is not ready. Deathblade is not used. A battle window
counts as a fight even when the duel phase still says it has not started.
If those creatures are not loaded, it moves through the pass until one is.
A pull walks onto the creature. Across the zone, one teleport lands a short
walk away and the same walk finishes it. A creature that does not fight is
skipped in place. The next pull walks to another. Camp jumps wait until the
zone is free and skip the Northguard gate, which loads the wizard out of the
pass. Each teleport and walk is logged before it runs.
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
import os
import re
import time

from loguru import logger

from . import blackbox

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
    "seeds": (("troubled", "splithoof barbarian"), ("splithoof", "ghboar")),
    "splithoof": (("splithoof", "barbarian"), ("ghboar",)),
    "halfang": (("halfang", "bristlecrown"),),
    "early-gold": (("lost soul", "rotten", "dark fairy", "ghoul"),),
}
# Compass landmarks in Grizzleheim/GH_Hero (Compass.xml). The Northguard
# gate (-6529.6, -4087.8, 406.5) is not one: standing on it loads Northguard,
# and the next teleport then runs during that zone change. That closed Wine.
# The client only loads the creatures near the wizard.
NORTHGUARD_GATE = (-6529.6, -4087.8, 406.5)
SAVARSTAAD_CAMPS = (
    (-29892.1, 17385.7, 516.9),
    (-21881.5, -1694.7, -38.1),
    (-7599.6, -432.4, 641.3),
    (-8122.4, -7139.8, 971.1),
    (-7491.8, -4317.8, 501.9),
    (6449.9, -6051.4, 1927.8),
    (23071.7, 19170.1, 2387.2),
    (11919.6, 19511.6, 1213.5),
    (7398.4, 37146.1, 1515.3),
    (-8256.4, 36869.1, 1661.1),
    (-11430.9, 23105.8, 880.1),
)
CAMPS = {"seeds": SAVARSTAAD_CAMPS, "splithoof": SAVARSTAAD_CAMPS}
_camp_at = {"seeds": 0, "splithoof": 0}
# The live zone path. "Savarstaad Pass" never appears in Grizzleheim/GH_Hero.
ZONE_PATHS = {
    "seeds": "Grizzleheim/GH_Hero",
    "splithoof": "Grizzleheim/GH_Hero",
}
# Wine dies if camp teleports follow each other. Mobs also stream in late.
CAMP_GAP = 8.0
CAMP_SETTLE = 6.0
LAP_WAIT = 15.0
RETURN_GAP = 1500.0
_last_camp = 0.0
_last_seen: dict[str, tuple[float, float, float]] = {}
_return_tried = {"seeds": False, "splithoof": False}
# One teleport when the creature is farther than this, then walk the rest.
FAR = 4000.0
# That jump lands here. The walk after it goes onto the creature.
ARRIVE = 800.0
# Another matching creature closer than this means walk, the jump can wait.
CLEAR_OF = 1000.0
# approach_point's default. The far jump passes ARRIVE on its own.
ENGAGE_GAP = ARRIVE
# A creature that did not start a fight, or just died, is left alone this long.
MISS_FOR = 20.0
# The duel phase flipped about 11s after the wizard stood on the boar.
# An 8s wait gave up, the next pull teleported, and Wine closed (23:34).
FIGHT_WAIT_POLLS = 35
# No teleport for this long after standing on a mob. The join locks movement,
# and a jump during that lock is what closed the client.
JOIN_HOLD = 15.0
_contact_at = 0.0
_leave_until: dict[int, float] = {}
_last_step = ""
ROUNDS = 30
HEAL_BELOW = 0.60  # cast a death drain while health is under this
PULL_BELOW = 0.35  # gather health before starting another fight
# Death drains. Cast on an enemy; the caster is the one who is healed.
DRAIN_NAMES = ("ghoul", "wraith", "vampire", "skeletal dragon")
DRAIN_EFFECTS = {"steal_health", "steal_over_time"}
# Savarstaad. Empower, then Scarecrow, then Scarecrow again if they live.
# Deathblade stays in the deck and is not cast on this farm.
COMBO = ("empower", "scarecrow")


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


BASE_SPEED = 580.0


def walk_seconds(distance: float, speed_bonus: float = 0.0) -> float:
    """Seconds to hold W. `speed_bonus` is the game's percent; 0 is normal speed."""
    factor = (speed_bonus / 100.0) + 1.0
    if factor <= 0.05:
        factor = 1.0
    if distance <= 1.0:
        return 0.0
    return distance / (BASE_SPEED * factor)


def walk_stuck(moved: float, attempted: float) -> bool:
    """The hold finished and the wizard is still where it started."""
    return attempted >= 80.0 and moved < 40.0


def mark_mob_contact(now: float | None = None) -> None:
    """The wizard is on a mob. A teleport in the next few seconds can close Wine."""
    global _contact_at
    _contact_at = time.monotonic() if now is None else now


def seconds_since_contact(now: float | None = None) -> float | None:
    if not _contact_at:
        return None
    current = time.monotonic() if now is None else now
    return current - _contact_at


def unstick_allowed(seconds_since: float | None, *, fighting: bool = False) -> bool:
    """False while a duel may be joining, or while one is already on screen.

    Standing on a boar, waiting 8s, then teleporting closed the client. The
    same fights showed up a couple of seconds after that wait gave up.
    """
    if fighting:
        return False
    if seconds_since is None:
        return True
    return seconds_since >= JOIN_HOLD


def already_there(here: str, target: str) -> bool:
    """True in the zone the farm wants. The path counts, not only the display name."""
    low = (here or "").lower()
    path = ZONE_PATHS.get(target, "")
    if path and path.lower() in low:
        return True
    return any(place.lower() in low for place in PLACES.get(target, ()))


def pull_plan(dist: float) -> str:
    """Walk onto a nearby creature. Teleport only when it is across the zone."""
    if dist > FAR:
        return "teleport"
    return "walk"


def pull_destination(
    me: tuple[float, float, float],
    mob: tuple[float, float, float],
) -> tuple[float, float, float]:
    """The mob itself, or one short-walk landing when the mob is across the zone."""
    dist = math.hypot(mob[0] - me[0], mob[1] - me[1])
    if pull_plan(dist) == "teleport":
        return approach_point(me, mob, ARRIVE)
    return mob


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


async def _here(client):
    from .walk import here

    return await here(client)


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
        me = await _here(client)
        fighting = await client.in_battle()
    except Exception as exc:
        logger.info(f"farm: {what} position unread ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return
    logger.info(f"farm: {what} at ({me.x:.0f},{me.y:.0f},{me.z:.0f}) in_battle={str(bool(fighting)).lower()}")


async def _teleport_to(
    client,
    dest: tuple[float, float, float],
    why: str,
    *,
    engage: bool = True,
) -> bool:
    """Teleport only when the hook is idle and a duel is not joining."""
    from wizwalker import XYZ

    try:
        fighting = await in_fight(client)
    except Exception as exc:
        if _client_closed(exc):
            raise
        fighting = False
    if not unstick_allowed(seconds_since_contact(), fighting=fighting):
        if fighting:
            logger.info(f"farm: skipping teleport ({why}); a fight is already on screen")
        else:
            logger.info(f"farm: skipping teleport ({why}); the last mob's fight may still be joining")
        return False
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


async def _speed_bonus(client) -> float:
    try:
        return float(await client.client_object.speed_multiplier())
    except Exception as exc:
        logger.info(f"farm: speed unread ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return 0.0


async def _face(client, yaw: float) -> None:
    """Turn the body goto was turning. That write is what spins the wizard."""
    await client.body.write_yaw(yaw)
    try:
        other = await client.client_object.actor_body()
        if other is not None:
            await other.write_yaw(yaw)
    except Exception as exc:
        logger.info(f"farm: second yaw unread ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise


async def _hold_forward(client, x: float, y: float) -> tuple[float, float]:
    """Face the point and hold W with PostMessage.

    client.goto holds W with SendMessageW. On this Wine build that key never
    arrives, so the wizard only turns. The dance arrows use PostMessageW.
    Returns (units moved, units attempted).
    """
    from wizwalker.utils import XYZ, calculate_perfect_yaw

    from .petdance import post_key

    me = await _here(client)
    attempted = math.hypot(x - me.x, y - me.y)
    if attempted < 20.0:
        logger.info(f"farm: already at the walk point ({attempted:.0f})")
        return 0.0, attempted
    seconds = walk_seconds(attempted, await _speed_bonus(client))
    yaw = calculate_perfect_yaw(XYZ(me.x, me.y, me.z), XYZ(x, y, me.z))
    await _face(client, yaw)
    logger.info(f"farm: posting W for {seconds:.1f}s yaw={yaw:.2f} toward ({x:.0f},{y:.0f})")
    await post_key(client.window_handle, "W", seconds)
    after = await _here(client)
    moved = math.hypot(after.x - me.x, after.y - me.y)
    logger.info(f"farm: walk moved {moved:.0f} of {attempted:.0f}")
    if walk_stuck(moved, attempted):
        # Posted W moves the dance game. This run's overworld walk moved 0,
        # so one SendMessage hold is tried before a teleport.
        from wizwalker.constants import Keycode
        from wizwalker.utils import timed_send_key

        logger.info("farm: posted W did not move; trying the other key path")
        await timed_send_key(client.window_handle, Keycode.W, min(max(seconds, 0.3), 2.0))
        after = await _here(client)
        moved = math.hypot(after.x - me.x, after.y - me.y)
        logger.info(f"farm: other key path moved {moved:.0f} of {attempted:.0f}")
    return moved, attempted


async def _walk_to(
    client,
    dest: tuple[float, float, float],
    why: str,
    *,
    teleport_if_stuck: bool = True,
) -> None:
    """Walk to this point. The log line is written first.

    teleport_if_stuck is off only when a jump from here is not wanted.
    """
    note_step(f"step=walk why={why} dest=({dest[0]:.0f},{dest[1]:.0f})")
    logger.info(f"farm: {last_step()}")
    moved, attempted = await _hold_forward(client, dest[0], dest[1])
    if walk_stuck(moved, attempted) and teleport_if_stuck:
        if os.environ.get("WINEPREFIX"):
            logger.warning(
                f"farm: W did not move the wizard ({moved:.0f} of {attempted:.0f}); "
                "skipping the unstick teleport in Wine"
            )
            await _log_where(client, f"after {why}")
            return
        logger.info(
            f"farm: W did not move the wizard ({moved:.0f} of {attempted:.0f}); teleporting to the same point"
        )
        await _teleport_to(client, dest, f"unstick {why}")
        return
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
    """Walk onto the creature. One teleport first when it is across the zone."""
    try:
        if await in_fight(client):
            logger.info(f"farm: already in a fight with {label or target} gid={gid or 0}")
            return True
    except Exception as exc:
        logger.info(f"farm: battle check failed ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
    if not gid:
        logger.info("farm: this creature has no id, so a miss will not be remembered")
    near_s = "none" if nearest is None else f"{nearest:.0f}"
    try:
        for attempt in (1, 2):
            if await in_fight(client):
                logger.info(f"farm: fight started with gid={gid or 0}")
                return True
            pos = await mob.location()
            me = await _here(client)
            dist = math.hypot(pos.x - me.x, pos.y - me.y)
            mob_xyz = (pos.x, pos.y, pos.z)
            _last_seen[target] = mob_xyz
            _return_tried[target] = False
            plan = pull_plan(dist)
            logger.info(
                f"farm: pulling {label or target} gid={gid or 0} plan={plan} "
                f"try={attempt} dist={dist:.0f} "
                f"me=({me.x:.0f},{me.y:.0f},{me.z:.0f}) "
                f"mob=({pos.x:.0f},{pos.y:.0f},{pos.z:.0f}) "
                f"nearest_other={near_s} known={str(known).lower()}"
            )
            jumped = plan == "teleport" and attempt == 1 and teleport_allowed(dist, nearest, known=known)
            if jumped:
                land = pull_destination((me.x, me.y, me.z), mob_xyz)
                if await _teleport_to(client, land, f"near gid={gid or 0}"):
                    continue
                logger.info("farm: teleport skipped; walking the whole way")
            if dist <= 80.0:
                logger.info(f"farm: reached gid={gid or 0} ({dist:.0f})")
                mark_mob_contact()
                break
            await _walk_to(client, mob_xyz, f"to gid={gid or 0} from {dist:.0f}")
    except Exception as exc:
        logger.info(f"farm: pull step failed ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        try:
            if await in_fight(client):
                logger.info(f"farm: fight started during the pull gid={gid or 0}")
                return True
        except Exception as check:
            logger.info(f"farm: battle check failed ({type(check).__name__}: {check})")
            if _client_closed(check):
                raise
        return False
    return await _wait_for_fight(client, gid)


def fight_visible(in_battle: bool | None, cards: int, windows: set[str]) -> bool:
    """A duel is on screen: the duel phase says so, or a battle-only window is up.

    Visible hand cards (SpellCheckBox) are not proof: this account shows 7 of
    them outside any duel, and that alone started phantom fights on 10-07
    10:59 (pet) and 10-10 19:36 (seeds, which then never moved). Combatant
    frames and the end-of-duel window still count when the phase lags.
    """
    if in_battle:
        return True
    found = {name.lower() for name in windows}
    return "combatantcontrol" in found or "donewindow" in found


def phase_disagrees(in_battle: bool | None, cards: int, windows: set[str]) -> str:
    """Why the window says there is a fight when the phase does not. Empty if they agree."""
    if in_battle or not fight_visible(in_battle, cards, windows):
        return ""
    parts = [f"cards={cards}"]
    if windows:
        parts.append(", ".join(sorted(windows)))
    return " ".join(parts)


async def _window_visible(window) -> bool:
    try:
        from wizwalker.memory import WindowFlags

        return WindowFlags.visible in await window.flags()
    except Exception:
        return False


async def _duel_signals(client) -> tuple[bool | None, int, set[str]]:
    """Phase flag, visible hand cards, and visible battle-window names.

    The window tree is walked only when the phase says there is no fight.
    A normal round does not pay for that walk.
    """
    try:
        phase: bool | None = bool(await client.in_battle())
    except Exception as exc:
        if _client_closed(exc):
            raise
        phase = None
    if phase:
        return True, 0, set()
    cards = 0
    names: set[str] = set()
    try:
        root = client.root_window
        for box in await root.get_windows_with_type("SpellCheckBox"):
            try:
                if await box.name() == "PetCard":
                    continue
                if await _window_visible(box):
                    cards += 1
            except Exception:
                continue
        for name in ("CombatantControl", "DoneWindow"):
            for window in await root.get_windows_with_name(name):
                try:
                    if await _window_visible(window):
                        names.add(name)
                except Exception:
                    continue
    except Exception as exc:
        if _client_closed(exc):
            raise
    return phase, cards, names


async def in_fight(client) -> bool:
    """True while a duel is on screen, including when the phase read says it ended."""
    phase, cards, names = await _duel_signals(client)
    return fight_visible(phase, cards, names)


async def play_current_fight(client, controller, *, combo: bool = True) -> None:
    """Play the duel that is already going. Does not walk or teleport."""
    logger.info("farm: a fight is already going; playing it")
    await _play(client, controller, combo=combo)
    await _note_potato(client)


async def _wait_for_fight(client, gid: int) -> bool:
    for _ in range(FIGHT_WAIT_POLLS):
        phase, cards, names = await _duel_signals(client)
        if fight_visible(phase, cards, names):
            missed = phase_disagrees(phase, cards, names)
            if missed:
                logger.info(
                    f"farm: fight started with gid={gid or 0}; the duel phase had not caught up ({missed})"
                )
            else:
                logger.info(f"farm: fight started with gid={gid or 0}")
            return True
        await asyncio.sleep(0.4)
    phase, cards, names = await _duel_signals(client)
    shown = ",".join(sorted(names)) or "none"
    leave_alone(gid)
    logger.info(
        f"farm: gid={gid or 0} did not start a fight "
        f"(phase_in_battle={phase}, cards={cards}, windows={shown}); trying another"
    )
    return False


async def _step_off(client, mob) -> bool:
    """False while the wizard is still beside the creature it just fought.

    The next pull walks to another creature from here.
    """
    try:
        pos = await mob.location()
        me = await _here(client)
    except Exception as exc:
        logger.info(f"farm: could not measure the corpse ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        return True
    dist = math.hypot(pos.x - me.x, pos.y - me.y)
    if dist >= CLEAR_OF:
        logger.info(f"farm: {dist:.0f} from the last fight")
        return True
    logger.info(f"farm: {dist:.0f} from the last fight; next pull walks to another")
    return False


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
        if await in_fight(client):
            logger.info("farm: still in a fight; playing it")
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
        logger.info(f"farm: the pull failed ({type(exc).__name__}: {exc}); gathering health and going back")
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
    here = await client.zone_name() or ""
    if already_there(here, target):
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
        me = await _here(client)
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
    labels = [label for _e, label, _d, _g in found if label]
    boars = [label for label in labels if "boar" in label.lower() or "troubled" in label.lower()]
    words = [label for label in labels if " " in label]
    pool = boars or words or labels
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


async def _camp_spot(client, target: str) -> tuple[float, float, float]:
    """Where to look next. The last mob, once, then the next compass point.

    A full lap waits before another. A quiet pass is not a teleport every
    few seconds: that loop is what closed the Wine client.
    """
    camps = CAMPS[target]
    seen = _last_seen.get(target)
    if seen is not None and not _return_tried.get(target):
        dist = None
        try:
            me = await _here(client)
            dist = math.hypot(seen[0] - me.x, seen[1] - me.y)
        except Exception as exc:
            logger.info(f"farm: position unread before a return ({type(exc).__name__}: {exc})")
            if _client_closed(exc):
                raise
        if dist is None or dist > RETURN_GAP:
            _return_tried[target] = True
            logger.info(f"farm: going back to the last mob {_pt(seen)}")
            return seen
    index = _camp_at.get(target, 0)
    if index > 0 and index % len(camps) == 0:
        logger.info("farm: one lap of the pass found no mobs; waiting before another")
        await asyncio.sleep(LAP_WAIT)
        _return_tried[target] = False
    spot = camps[index % len(camps)]
    _camp_at[target] = index + 1
    return spot


async def _settle_for_mob(client, groups):
    """Mobs stream in after a jump. Poll until one is named or the wait ends."""
    deadline = time.monotonic() + CAMP_SETTLE
    last: list = []
    while True:
        seen = await _nearby(client, groups)
        if seen is None:
            return None
        last = seen
        found = _ready(seen)
        pick = best_mob([(label, dist) for _e, label, dist, _g in found], groups)
        if pick is not None:
            return _picked(found, pick, seen, groups)
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(1.0)
    logger.info(f"farm: still no mob after the move ({_name_sample(last)})")
    return None


async def _acquire_mob(client, target: str):
    """The closest preferred mob that was not just fought. Moves on if none are loaded.

    Returns (entity, label, gid, nearest_other, known) or None. known is false only
    when a later caller is told the scan failed; a failed scan returns None itself.
    """
    global _last_camp
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
    resting_match = any(resting(gid) and mob_rank(label, groups) is not None for _e, label, _d, gid in seen)
    if resting_match:
        skipped = _resting_note(seen, groups)
        logger.info(f"farm: the nearby creatures just failed to fight; waiting ({skipped or 'no ids'})")
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
    from .world import is_free, wait_until_free

    if not await is_free(client):
        logger.info("farm: still loading or in a fight; not teleporting")
        await wait_until_free(client, 8.0)
        return None
    spot = await _camp_spot(client, target)
    wait = CAMP_GAP - (time.monotonic() - _last_camp) if _last_camp else 0.0
    if wait > 0:
        logger.info(f"farm: waiting {wait:.0f}s so the last teleport can finish")
        await asyncio.sleep(wait)
    logger.info(f"farm: no {target} mob loaded ({sample}); moving to another part of the zone")
    try:
        moved = await _teleport_to(client, spot, f"next camp {_pt(spot)}")
    except Exception as exc:
        logger.info(f"farm: could not move to the next camp ({type(exc).__name__}: {exc})")
        if _client_closed(exc):
            raise
        await asyncio.sleep(CAMP_GAP)
        return None
    if not moved:
        await asyncio.sleep(2.0)
        return None
    _last_camp = time.monotonic()
    return await _settle_for_mob(client, groups)


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
        moved = await _teleport_to(client, (pos.x, pos.y, pos.z), "health wisp", engage=False)
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
    # Letters only: an internal name like "Spell_Empower" has no word break before
    # "empower", which hid the card on 10-10. Empowerment is a different spell.
    if "empower" in flat and "empowerment" not in flat and "empowered" not in flat:
        return "empower"
    return None


def next_combo(done: list[str], castable: set[str], in_hand: set[str]) -> str | None:
    """The next Savarstaad card, or None to pass (kept for its tests; plan_round decides now)."""
    hand = [{"spell": sp, "castable": sp in castable} for sp in sorted(in_hand)]
    action = plan_round(hand, done)
    return hand[action[1]]["spell"] if action[0] == "cast" else None


def plan_round(hand: list[dict], done: list[str], heal: bool = False) -> tuple:
    """One round of the Savarstaad farm. `hand` is one dict per card:
    spell ('empower' | 'scarecrow' | None), castable, keep (a treasure or item
    card: never discarded), drain (a death drain).

    ('drain',) when health is low and Scarecrow cannot go; ('cast', i);
    ('discard', [i, ...]) when Scarecrow is not in hand at all, so the next
    draw can bring it; else ('pass',). Empower goes first, once per fight,
    whenever it is castable: it no longer holds Scarecrow back while it waits
    for pips, and Scarecrow no longer waits forever for a card never drawn.
    """
    scarecrow = [i for i, c in enumerate(hand) if c.get("spell") == "scarecrow" and c.get("castable")]
    empower_ready = "empower" not in done
    empower = [i for i, c in enumerate(hand) if c.get("spell") == "empower" and c.get("castable")]
    if heal and not scarecrow:
        return ("drain",)
    if empower and empower_ready:
        return ("cast", empower[0])
    if scarecrow:
        return ("cast", scarecrow[0])
    if not any(c.get("spell") == "scarecrow" for c in hand):
        junk = [
            i
            for i, c in enumerate(hand)
            if not c.get("keep")
            and not c.get("drain")
            and not (c.get("spell") == "empower" and empower_ready)
        ]
        if junk:
            return ("discard", junk)
    return ("pass",)


async def _planning_ready(handler) -> bool | None:
    """True on the planning phase. False when the phase says the fight ended.

    A memory miss is reported as ended by the client, which is how a visible
    fight was skipped. The caller keeps the battle window ahead of that.
    """
    from wizwalker.memory import DuelPhase

    try:
        phase = await handler.client.duel.duel_phase()
    except Exception as exc:
        if _client_closed(exc):
            raise
        logger.info(f"farm: duel phase unreadable ({type(exc).__name__}: {exc})")
        return None
    if phase == DuelPhase.planning:
        return True
    if phase == DuelPhase.ended:
        return False
    return None


async def _ready_to_cast(handler, phase: bool | None, cards: int) -> bool:
    """Hand cards can be played. The phase is used only when no card is visible."""
    if cards > 0:
        return True
    if phase is not True:
        return False
    return await _planning_ready(handler) is True


async def _play(client, controller, combo: bool = False):
    from wizwalker.combat import AoeHandler

    handler = AoeHandler(client)
    done: list[str] = []
    announced = False
    missing = 0
    idle = 0
    phantom = 0
    casts = 0
    while casts < ROUNDS:
        if controller:
            await controller.checkpoint()
        ratio = await _ratio(client)
        if ratio is not None and ratio <= 0:
            logger.info("farm: health is gone; leaving the fight")
            return
        phase, cards, names = await _duel_signals(client)
        if not fight_visible(phase, cards, names):
            missing += 1
            if missing >= 3:
                logger.info(f"farm: the fight window is gone (phase_in_battle={phase}, cards={cards})")
                return
            await asyncio.sleep(0.4)
            continue
        missing = 0
        disagreed = phase_disagrees(phase, cards, names)
        if disagreed and not announced:
            logger.info(f"farm: the duel phase says no fight, but the battle window is up ({disagreed})")
            announced = True
        try:
            ready = await _ready_to_cast(handler, phase, cards)
        except Exception as exc:
            if _client_closed(exc):
                raise
            logger.info(f"farm: could not read the round ({type(exc).__name__}: {exc})")
            ready = cards > 0
        if not ready:
            idle += 1
            if idle >= 75:
                logger.info("farm: the battle window stayed up but no card was ready")
                return
            await asyncio.sleep(0.4)
            continue
        idle = 0
        if phase is not True:
            # The phase says no duel and only windows say yes: a real hand settles it.
            try:
                hand = len(await handler.get_cards())
            except Exception as exc:
                if _client_closed(exc):
                    raise
                hand = 0
            if hand == 0:
                phantom += 1
                if phantom >= 3:
                    logger.info("farm: no duel after all (no duel phase, no hand); moving on")
                    blackbox.event("phantom_fight", windows=sorted(names), cards=cards)
                    return
                await asyncio.sleep(0.5)
                continue
        phantom = 0
        casts += 1
        try:
            if combo:
                await _cast_combo(handler, done, heal=needs_heal(ratio))
            else:
                await _cast_or_pass(handler, heal=needs_heal(ratio))
        except Exception as exc:
            logger.info(f"farm: round failed ({exc}); passing")
            blackbox.event("round_failed", error=f"{type(exc).__name__}: {exc}")
            try:
                await handler.pass_button()
            except Exception as pass_exc:
                logger.info(f"farm: pass failed ({pass_exc})")
                return
        await asyncio.sleep(0.4)
    logger.info("farm: stopped after the round limit")


async def _read_hand(handler) -> list[tuple[object, dict]]:
    out = []
    for card in await handler.get_cards():
        try:
            name = await _card_name(card)
            castable = await card.is_castable()
        except Exception as exc:
            if _client_closed(exc):
                raise
            continue
        keep = False
        for check in ("is_treasure_card", "is_item_card"):
            try:
                keep = keep or bool(await getattr(card, check)())
            except Exception:
                keep = True  # unread: do not throw it away
        info = {
            "name": name,
            "spell": combo_spell(name),
            "castable": castable,
            "keep": keep,
            "drain": is_death_heal(name),
        }
        out.append((card, info))
    return out


async def _cast_on_self(handler, card) -> None:
    """Self-target: the member's Health text, else its whole frame (the Health
    child was missing once on 10-10 and the round was lost)."""
    member = await handler.get_client_member()
    try:
        await card.cast(member)
    except ValueError:
        await handler.client.mouse_handler.click_window(member._combatant_control)


async def _cast_combo(handler, done: list[str], heal: bool = False) -> str:
    """Empower on the wizard, then Scarecrow; dig for Scarecrow by discarding."""
    hand = await _read_hand(handler)
    infos = [info for _card, info in hand]
    action = plan_round(infos, done, heal)
    if action[0] == "drain":
        drained = await _cast_drain(handler)
        if drained:
            return drained
        logger.info("farm: health is low and no death drain is in hand")
        action = plan_round(infos, done, False)
    if action[0] == "cast":
        card, info = hand[action[1]]
        step = info["spell"]
        if step == "scarecrow":
            await card.cast(None)
        else:
            await _cast_on_self(handler, card)
        done.append(step)
        logger.info(f"farm: casting {step}")
        blackbox.event("cast", spell=step)
        return step
    if action[0] == "discard":
        names = [hand[i][1]["name"] for i in action[1]]
        logger.info(f"farm: no Scarecrow in hand; discarding {', '.join(names)} to draw for it")
        blackbox.event("discard", cards=names)
        for i in sorted(action[1], reverse=True):  # from the right, so earlier windows stay put
            try:
                await hand[i][0].discard(sleep_time=0.4)
            except Exception as exc:
                if _client_closed(exc):
                    raise
                logger.info(f"farm: discard failed ({exc})")
                break
    await handler.pass_button()
    shown = ", ".join(f"{i['name']}{'' if i['castable'] else ' (not castable)'}" for i in infos) or "nothing"
    logger.info(f"farm: passing; hand: {shown}")
    blackbox.event(
        "cast",
        spell="pass",
        hand=[i["name"] for i in infos],
        castable=[i["name"] for i in infos if i["castable"]],
    )
    return "pass"


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
        blackbox.event("cast", spell=name or "death drain", why="low health")
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
