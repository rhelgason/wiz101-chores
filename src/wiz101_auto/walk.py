"""Walking by posted keys. Nothing here teleports or writes game code.

The wizard is turned by writing its yaw (heap data, the same write
client.goto makes), then W is held with PostMessageW. Each key message
carries a real scan code and repeat bits. WizWalker's taps send lParam 0;
on this Wine build those sometimes moved the wizard 0 units.

A walk re-aims every few tenths of a second, so a wandering boar is still
reached, and it stops as soon as a duel appears. A walk that does not move
sidesteps, then gives up and says so. Every walk is one line in the log and
one event in events.jsonl.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from . import blackbox

WM_KEYDOWN, WM_KEYUP = 0x100, 0x101
VK = {"W": 0x57, "A": 0x41, "S": 0x53, "D": 0x44, "X": 0x58, "B": 0x42}
# US set 1 scan codes, if MapVirtualKey fails
SCAN = {0x57: 0x11, 0x41: 0x1E, 0x53: 0x1F, 0x44: 0x20, 0x58: 0x2D, 0x42: 0x30}
# Getting unstuck. The game has no strafe key: A and D turn, Q is the quest
# list and E the emote wheel (Bin/config.xml; 10-12 the Q "strafes" opened the
# quest book). So each stall backs up (S), turns (A or D, alternating), and
# walks on (W) a little longer each time, then the aim comes back to the target.
UNSTICK = ((0.4, "A", 0.3, 0.6), (0.4, "D", 0.3, 0.8), (0.7, "A", 0.45, 1.1))
BASE_SPEED = 580.0  # WizWalker's WIZARD_SPEED, units per second at +0%
REPEAT = 0.05  # key repeat while held
AIM_EVERY = 0.3
PROGRESS_EVERY = 1.0
MIN_PROGRESS = 30.0  # less than this in PROGRESS_EVERY is a stall


def key_lparam(scan: int, *, up: bool, held: bool = False) -> int:
    """lParam for WM_KEYDOWN/WM_KEYUP: repeat 1, scan code, previous state, transition."""
    value = 1 | ((scan & 0xFF) << 16)
    if up or held:
        value |= 1 << 30
    if up:
        value |= 1 << 31
    return value


def yaw_to(me: tuple[float, float], target: tuple[float, float]) -> float:
    """WizWalker's calculate_perfect_yaw, without its divide-by-zero: 0 faces -y."""
    dx, dy = target[0] - me[0], target[1] - me[1]
    length = math.hypot(dx, dy)
    if length < 1.0:
        return 0.0
    angle = math.acos(max(-1.0, min(1.0, -dy / length)))
    return 2 * math.pi - angle if dx > 0 else angle


def walk_seconds(distance: float, speed_bonus: float = 0.0) -> float:
    """Seconds of W for this distance. `speed_bonus` is the game's percent."""
    factor = (speed_bonus / 100.0) + 1.0
    if factor <= 0.05:
        factor = 1.0
    if distance <= 1.0:
        return 0.0
    return distance / (BASE_SPEED * factor)


def stalled(moved: float) -> bool:
    return moved < MIN_PROGRESS


@dataclass
class WalkResult:
    outcome: str  # reached | fight | interrupted | stuck | lost | timeout
    start_dist: float
    end_dist: float
    moved: float
    seconds: float

    def __str__(self) -> str:
        return (
            f"{self.outcome}: {self.start_dist:.0f} -> {self.end_dist:.0f} away, "
            f"moved {self.moved:.0f} in {self.seconds:.1f}s"
        )


FOCUS_REQUEST = Path("state") / "focus.request"  # mac/crashwatch.py brings Wizard101 to the front
FOCUS_EVERY = 10.0
FOCUS_SETTLE = 1.2  # macOS takes a moment to switch apps
MOVEMENT = (0x57, 0x41, 0x53, 0x44)  # W A S D
_last_focus = [-1e9]
_focused_once = [False]


def request_focus(why: str) -> bool:
    """Ask the Mac side to make Wizard101 the active app. Rate-limited."""
    if time.monotonic() - _last_focus[0] < FOCUS_EVERY:
        return False
    _last_focus[0] = time.monotonic()
    try:
        FOCUS_REQUEST.parent.mkdir(exist_ok=True)
        FOCUS_REQUEST.write_text(why, encoding="utf-8")
    except OSError:
        return False
    blackbox.event("focus_request", why=why)
    logger.info(f"walk: Wizard101 is not the active window; asking the Mac to bring it forward ({why})")
    return True


class Keys:
    """Movement keys the way the game reads them.

    The game polls GetAsyncKeyState (its import table has it, and no
    DirectInput), so a held W has to be real key state. SendInput gives
    exactly that inside Wine. A posted WM_KEYDOWN only reaches the window's
    message queue: the dance arrows (single presses) work that way, walking
    (a held key) moved 0 on 10-10 19:39. Both are sent; a key is pressed once
    with SendInput and its WM_KEYDOWN repeats while held.

    A separate WinDLL keeps these argtypes away from WizWalker's calls.
    """

    def __init__(self, window_handle: int):
        import ctypes
        from ctypes import wintypes

        self.hwnd = window_handle
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._post = user32.PostMessageW
        self._post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._post.restype = wintypes.BOOL
        self._map = user32.MapVirtualKeyW
        self._map.argtypes = [wintypes.UINT, wintypes.UINT]
        self._map.restype = wintypes.UINT
        self._foreground = user32.GetForegroundWindow
        self._foreground.restype = wintypes.HWND

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class MOUSEINPUT(ctypes.Structure):  # only here so the union has the real INPUT size
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", UNION)]

        self._INPUT, self._KEYBDINPUT = INPUT, KEYBDINPUT
        self._send = user32.SendInput
        self._send.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self._send.restype = wintypes.UINT
        self._ctypes = ctypes
        self.down: set[int] = set()
        self.input_ok: bool | None = None

    def scan(self, vk: int) -> int:
        try:
            code = int(self._map(vk, 0))
        except Exception:
            code = 0
        return code or SCAN.get(vk, 0)

    def foreground(self) -> bool:
        try:
            return int(self._foreground() or 0) == int(self.hwnd)
        except Exception:
            return True  # unknown: do not ask for focus

    def _input(self, vk: int, up: bool) -> None:
        ki = self._KEYBDINPUT(vk, self.scan(vk), 0x0002 if up else 0, 0, 0)  # KEYEVENTF_KEYUP
        event = self._INPUT(1, self._INPUT._fields_[1][1](ki=ki))  # INPUT_KEYBOARD
        sent = self._send(1, self._ctypes.byref(event), self._ctypes.sizeof(self._INPUT))
        if self.input_ok is None or (not sent) == self.input_ok:
            self.input_ok = bool(sent)
            if not sent:
                logger.warning(f"walk: SendInput refused a key (error {self._ctypes.get_last_error()})")

    def press(self, vk: int, *, held: bool = False) -> None:
        if vk not in self.down:
            self._input(vk, up=False)
        self._post(self.hwnd, WM_KEYDOWN, vk, key_lparam(self.scan(vk), up=False, held=held))
        self.down.add(vk)

    def release(self, vk: int) -> None:
        self._input(vk, up=True)
        self._post(self.hwnd, WM_KEYUP, vk, key_lparam(self.scan(vk), up=True))
        self.down.discard(vk)

    def release_all(self) -> None:
        for vk in list(self.down):
            self.release(vk)

    def release_movement(self) -> None:
        """Key-up for W, A, S and D whether or not they look held (start and stop)."""
        for vk in MOVEMENT:
            self.release(vk)

    async def hold(self, vk: int, seconds: float) -> None:
        end = time.monotonic() + max(seconds, REPEAT)
        self.press(vk)
        try:
            while time.monotonic() < end:
                await asyncio.sleep(REPEAT)
                self.press(vk, held=True)
        finally:
            self.release(vk)

    async def tap(self, vk: int) -> None:
        await self.hold(vk, 0.1)


def keys_for(client) -> Keys:
    keys = getattr(client, "_posted_keys", None)
    if keys is None:
        keys = Keys(client.window_handle)
        client._posted_keys = keys
    return keys


async def position(client) -> tuple[float, float, float]:
    """Where the wizard is: the local client object's own body (what WizWalker's goto reads).

    Not `client.body`: that comes from the player hook, which keeps whichever
    object last looked like a player. After the first duel on 10-10 19:47 it
    stayed on the old duel spot, every walk read "moved 0", and the wizard
    only turned in place.
    """
    try:
        body = await client.client_object.actor_body()
        if body is not None:
            p = await body.position()
            return (p.x, p.y, p.z)
    except Exception as exc:
        if type(exc).__name__ == "ClientClosedError":
            raise
    p = await client.body.position()
    return (p.x, p.y, p.z)


async def here(client):
    """position() as an object with .x .y .z, for code written against WizWalker's XYZ."""
    from types import SimpleNamespace

    x, y, z = await position(client)
    return SimpleNamespace(x=x, y=y, z=z)


async def face(client, yaw: float) -> None:
    """Turn the body. The actor body is written too: that is the one goto turned."""
    await client.body.write_yaw(yaw)
    try:
        other = await client.client_object.actor_body()
        if other is not None:
            await other.write_yaw(yaw)
    except Exception as exc:
        if type(exc).__name__ == "ClientClosedError":
            raise


async def speed_bonus(client) -> float:
    try:
        return float(await client.client_object.speed_multiplier())
    except Exception as exc:
        if type(exc).__name__ == "ClientClosedError":
            raise
        return 0.0


Target = Callable[[], Awaitable[tuple[float, float, float] | None]]
Check = Callable[[], Awaitable[bool]]


async def walk_to(
    client,
    target: Target,
    *,
    why: str,
    stop_within: float = 90.0,
    max_seconds: float = 25.0,
    fight: Check | None = None,
    checkpoint: Callable[[], Awaitable[None]] | None = None,
    interrupt: Check | None = None,
    precise: bool = True,
    keep_moving: bool = False,
) -> WalkResult:
    """Hold W toward `target()` until within `stop_within`, a fight, or a stall.

    `target` is read again every AIM_EVERY seconds; None means it is gone.
    Close in, W is let go and held once for just the distance left: holding
    it through a re-aim overshoots a small target (the dance sigil) and
    circles it.
    """
    keys = keys_for(client)
    start = await position(client)
    goal = await target()
    if goal is None:
        return WalkResult("lost", 0.0, 0.0, 0.0, 0.0)
    # Wine calls the game "foreground" even while another Mac app is active,
    # and then held keys never reach it (10-10 19:43: 12 walks moved 0 with
    # foreground=true). So the Mac is asked to bring Wizard101 forward on the
    # first walk of a run, and again whenever a walk moves nothing.
    front = getattr(keys, "foreground", lambda: True)()
    if not _focused_once[0] and request_focus("first walk of this run"):
        _focused_once[0] = True
        await asyncio.sleep(FOCUS_SETTLE)
    speed = BASE_SPEED * max((await speed_bonus(client)) / 100.0 + 1.0, 0.05)
    near = speed * AIM_EVERY * 1.5  # closer than this, the next held tick would overshoot
    first = math.dist(start[:2], goal[:2])
    began = time.monotonic()
    blackbox.event(
        "walk_start",
        why=why,
        me=_r(start),
        to=_r(goal),
        dist=round(first),
        speed=round(speed),
        foreground=front,
    )
    logger.info(f"walk: {why}: {first:.0f} away")
    outcome = "timeout"
    here = start
    last_mark, last_pos = began, start
    stalls = 0
    side = 0
    w = VK["W"]
    try:
        while time.monotonic() - began < max_seconds:
            if checkpoint is not None:
                held = w in keys.down
                keys.release_all()
                await checkpoint()
                if held:
                    keys.press(w)
            try:
                here = await position(client)
                goal = await target()
            except Exception as exc:
                if type(exc).__name__ == "ClientClosedError":
                    raise
                outcome = "interrupted"  # memory moved under us: usually a zone loading
                break
            if goal is None:
                outcome = "lost"
                break
            dist = math.dist(here[:2], goal[:2])
            if dist <= stop_within:
                outcome = "reached"
                break
            if fight is not None and await fight():
                outcome = "fight"
                break
            if interrupt is not None and await interrupt():
                outcome = "interrupted"  # a zone change or a loading screen: the caller looks again
                break
            await face(client, yaw_to(here[:2], goal[:2]))
            if precise and dist < near:
                # Final approach: one measured hold, then look again.
                keys.release(w)
                hold = max(REPEAT, (dist - stop_within * 0.5) / speed)
                await keys.hold(w, hold)
                after = await position(client)
                if math.dist(after[:2], here[:2]) < min(MIN_PROGRESS, 0.3 * hold * speed):
                    stalls += 1  # a hold that moves nothing counts like any stall
                    if stalls >= 4:
                        outcome = "stuck"
                        break
                else:
                    stalls = 0
                last_mark, last_pos = time.monotonic(), after
                continue
            keys.press(w, held=w in keys.down)
            await asyncio.sleep(AIM_EVERY)
            now = time.monotonic()
            if now - last_mark >= PROGRESS_EVERY:
                moved = math.dist(here[:2], last_pos[:2])
                last_mark, last_pos = now, here
                if moved < 1.0 and math.dist(here[:2], start[:2]) < 1.0 and request_focus(f"{why}: moved 0"):
                    await asyncio.sleep(FOCUS_SETTLE)  # keys only reach the active Mac app
                    continue
                if stalled(moved):
                    stalls += 1
                    if stalls >= 4:
                        outcome = "stuck"
                        break
                    # Around whatever is in the way: back up, then strafe, alternating.
                    keys.release(w)
                    back, turn, turning, forward = UNSTICK[side % len(UNSTICK)]
                    side += 1
                    blackbox.event("walk_sidestep", why=why, at=_r(here), key=turn)
                    await keys.hold(VK["S"], back)
                    await keys.hold(VK[turn], turning)
                    await keys.hold(w, forward)
                else:
                    stalls = 0
    finally:
        if keep_moving and outcome == "reached":
            for vk in list(keys.down):
                if vk != w:
                    keys.release(vk)  # W stays down: the next leg carries straight on
        else:
            keys.release_all()
    try:
        end = await position(client)
        goal = await target()
    except Exception as exc:  # a new zone loading: the old target means nothing now
        if type(exc).__name__ == "ClientClosedError":
            raise
        end, goal = here, None
    result = WalkResult(
        outcome,
        first,
        math.dist(end[:2], goal[:2]) if goal else 0.0,
        math.dist(start[:2], end[:2]),
        time.monotonic() - began,
    )
    blackbox.event(
        "walk_end",
        why=why,
        outcome=outcome,
        me=_r(end),
        moved=round(result.moved),
        left=round(result.end_dist),
        seconds=round(result.seconds, 1),
    )
    logger.info(f"walk: {why}: {result}")
    return result


async def walk_point(client, point: tuple[float, float, float], **kwargs) -> WalkResult:
    async def fixed():
        return point

    return await walk_to(client, fixed, **kwargs)


def _r(p) -> list[int]:
    return [round(v) for v in p]
