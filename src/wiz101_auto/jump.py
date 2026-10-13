"""Data-only jumps: move the wizard without writing game code after connect.

WizWalker's teleport hooks the game's "set this object's position" call. On
every jump it also NOPs two jumps in the movement code from outside, and
its hook cave writes them back from inside the game: code changing while it
runs, on every teleport. Under Rosetta that is what the crashes followed
(PLAYBOOK.md, "Why the Mac client crashed").

PositionHook is the same hook without any of that. It goes in once at
connect, recorded and adopted like the other hooks (hooking.py). Its cave
only does this: when the call is for our client object and the flag is set,
replace the incoming position with ours and clear the flag. A jump is then
data writes into the bot's own buffer (x, y, z, the flag, the object) and a
short W: the game itself calls set-position as the wizard moves, the hook
swaps in the target, and the movement update carries it to the server.

Opt in with `safety.data_jumps: true`. Unverified live: that a plain walk
reaches the hooked call (WizWalker forced that path with its NOPs), and that
the server keeps the wizard where it landed. A jump that does not land turns
jumps off for the run, and walking carries on.

Helper layout (21 bytes): f32 x, f32 y, f32 z, u8 should_update, u64 target.
"""

from __future__ import annotations

import asyncio
import math
import struct
import time

from loguru import logger
from wizwalker.memory.hooks import MovementTeleportHook, SimpleHook

from . import blackbox

LAND_WITHIN = 150.0  # a jump that ends farther than this from the target did not land
MOB_CLEAR = 700.0  # never land this close to a creature
ARM_SECONDS = 2.5  # W taps until the hook takes the target
SETTLE = 1.2  # then the server has this long to put the wizard back, if it will
# Pacing. 10-12 22:46: 21 jumps landed, then the game closed (no dump) on the
# 4th jump of a chain fired 0.1s apart, before the last landing had settled.
# A landing now holds the next jump back: a few seconds, longer after a long
# jump (the new area streams in), and never while the game is loading.
QUIET_MIN = 5.0  # the gather pilot jumps slowly
QUIET_PER_UNIT = 1.0 / 4000.0  # +1s per 4000 units jumped
QUIET_MAX = 10.0
STILL_FOR = 0.6  # and the wizard has stood still this long
ARM_KEYS = ("W", "W", "S", "S", "A", "W", "D", "W")  # taps while a jump is armed
MAX_MISSES = 3  # jumps in a row that do not land before jumps are off for the run
# Keys reach the game only once Wizard101 is really the active Mac app. Each
# run started with ~40s where every walk moved 0 though focus said yes (10-12
# 22:52, 22:59; 10-11 01:08), and the jumps tried then all missed. So a jump
# first proves a tap moves the wizard, asking for focus until it does.
KEYS_WAIT = 90.0
KEYS_TAP = 0.25
MOVED = 5.0  # a tap that moved the wizard less than this moved nothing


def quiet_after(moved: float) -> float:
    """Seconds the next jump waits after one of `moved` units."""
    return min(QUIET_MIN + moved * QUIET_PER_UNIT, QUIET_MAX)


def position_cave(helper: int, original: bytes) -> bytes:
    """The hook body: swap in the target position for our object while the flag is set."""
    addr = struct.pack("<Q", helper)
    flag = struct.pack("<Q", helper + 12)
    z = struct.pack("<Q", helper + 8)
    target = struct.pack("<Q", helper + 13)
    swap = (
        b"\x50"  # push rax
        b"\x48\xa1" + addr  # mov rax, [x,y]
        + b"\x48\x89\x02"  # mov [rdx], rax
        b"\xa1" + z  # mov eax, [z]
        + b"\x89\x42\x08"  # mov [rdx+8], eax
        b"\x31\xc0"  # xor eax, eax
        b"\xa2" + flag  # mov [flag], al
        + b"\x58"  # pop rax
    )
    check_flag = (
        b"\x50"  # push rax
        b"\xa0" + flag  # mov al, [flag]
        + b"\x84\xc0"  # test al, al
        b"\x58"  # pop rax
        b"\x0f\x84" + struct.pack("<i", len(swap))  # je skip
    )
    check_object = (
        b"\x50"  # push rax
        b"\x48\xa1" + target  # mov rax, [target]
        + b"\x48\x39\xc1"  # cmp rcx, rax
        b"\x58"  # pop rax
        b"\x0f\x85" + struct.pack("<i", len(check_flag) + len(swap))  # jne skip
    )
    return check_object + check_flag + swap + bytes(original)


class PositionHook(SimpleHook):
    """MovementTeleportHook's spot, with no prehook (no collision NOPs, no page
    protection change) and no code writes in its cave."""

    pattern = MovementTeleportHook.pattern
    instruction_length = 5
    exports = [("jump_helper", 21)]
    noops = 0
    cave_size = 128  # the body is 88 bytes with its jump back (hooking._install reserves this much)

    async def bytecode_generator(self, packed_exports):
        (helper,) = struct.unpack("<Q", packed_exports[0][1])
        original = await self.read_bytes(self.jump_address, self.instruction_length)
        return position_cave(helper, original)


def landed(target, end, within: float = LAND_WITHIN) -> bool:
    return math.dist(target[:2], end[:2]) <= within


def clear_landing(target, mobs: list, distance: float = MOB_CLEAR) -> bool:
    return all(math.dist(target[:2], m[:2]) > distance for m in mobs)


class Jumper:
    """client._jump: `await jumper(xyz, why=...)` -> True when the wizard landed there."""

    def __init__(self, client):
        self.client = client
        self.enabled = True
        self.jumps = 0
        self.ready_at = 0.0  # monotonic time the next jump may go
        self.last_end = 0.0
        self.misses = 0  # jumps in a row that did not land
        self.keys_ok = False  # a tap has moved the wizard since the last miss

    async def keys_reach_game(self) -> bool:
        """Tap S then W until the wizard moves (asking the Mac for focus between taps)."""
        from .walk import VK, keys_for, position, request_focus

        if self.keys_ok:
            return True
        keys = keys_for(self.client)
        end_by = time.monotonic() + KEYS_WAIT
        told = False
        while time.monotonic() < end_by:
            before = await position(self.client)
            await keys.hold(VK["S"], KEYS_TAP)
            await keys.hold(VK["W"], KEYS_TAP)
            await asyncio.sleep(0.3)
            if math.dist(before[:3], (await position(self.client))[:3]) >= MOVED:
                self.keys_ok = True
                if told:
                    logger.info("jump: keys reach the game now")
                return True
            if not told:
                logger.info("jump: keys are not reaching the game yet; asking the Mac for focus")
                blackbox.event("jump_keys_wait")
                told = True
            request_focus("jump: a tap moved nothing")
            await asyncio.sleep(2.0)
        logger.warning(f"jump: keys did not reach the game in {KEYS_WAIT:.0f}s")
        blackbox.event("needs_player", why="keys are not reaching Wizard101 (click its window?)")
        return False

    async def _settled(self) -> None:
        """Wait out the pacing: the quiet time, no loading screen, the wizard standing still."""
        from .walk import position

        while time.monotonic() < self.ready_at:
            await asyncio.sleep(min(0.25, self.ready_at - time.monotonic()))
        end_by = time.monotonic() + 15.0
        before = await position(self.client)
        while time.monotonic() < end_by:
            try:
                loading = bool(await self.client.is_loading())
            except Exception as exc:
                if type(exc).__name__ == "ClientClosedError":
                    raise
                loading = False
            await asyncio.sleep(STILL_FOR)
            now = await position(self.client)
            if not loading and math.dist(before[:3], now[:3]) < 5.0:
                return
            before = now

    async def helper(self) -> int | None:
        return self.client.hook_handler._base_addrs.get("jump_helper")

    async def mobs(self) -> list:
        out = []
        try:
            for mob in await self.client.get_mobs():
                try:
                    p = await mob.location()
                    out.append((p.x, p.y, p.z))
                except Exception:
                    continue
        except Exception as exc:
            if type(exc).__name__ == "ClientClosedError":
                raise
        return out

    async def __call__(self, target, *, why: str = "") -> bool:
        from .walk import VK, keys_for, position

        helper = await self.helper()
        if not self.enabled or not helper:
            return False
        if not clear_landing(target, await self.mobs()):
            logger.info(f"jump: not landing by a creature at ({target[0]:.0f}, {target[1]:.0f}); walking")
            return False
        await self._settled()
        if not await self.keys_reach_game():
            return False  # not a jump that failed: no keys, no movement, no hooked call
        handler = self.client.hook_handler
        start = await position(self.client)
        obj = await self.client.client_object.read_base_address()
        x, y, z = (float(v) for v in target[:3])
        gap = round(time.monotonic() - self.last_end, 1) if self.last_end else None
        blackbox.event("jump_start", why=why, at=_r(start), to=_r(target), gap=gap)
        await handler.write_bytes(helper + 13, struct.pack("<Q", obj))
        await handler.write_bytes(helper, struct.pack("<fff", x, y, z + 5.0))
        await handler.write_bytes(helper + 12, b"\x01")
        keys = keys_for(self.client)
        end_by = time.monotonic() + ARM_SECONDS
        taken = False
        step = 0
        while time.monotonic() < end_by:
            # The hooked call runs when the wizard actually moves. Wedged against
            # something, W alone moved nothing (10-12 23:53), so back up and turn too.
            await keys.hold(VK[ARM_KEYS[step % len(ARM_KEYS)]], 0.1)
            step += 1
            if (await handler.read_bytes(helper + 12, 1)) == b"\x00":
                taken = True
                break
            await asyncio.sleep(0.05)
        if not taken:
            await handler.write_bytes(helper + 12, b"\x00")
            if math.dist(start[:3], (await position(self.client))[:3]) < MOVED:
                # Keys stopped arriving: prove them again before the next jump; not a miss.
                self.keys_ok = False
                logger.info("jump: the arming taps moved nothing (keys not reaching the game); later")
                blackbox.event("jump_end", why=why, landed=False, reason="keys", at=_r(start), to=_r(target))
                return False
            return self._off("the hooked call never ran though the wizard moved", why, start, target)
        await asyncio.sleep(SETTLE)
        end = await position(self.client)
        if not landed(target, end):
            return self._off(
                f"the wizard ended at ({end[0]:.0f}, {end[1]:.0f}), not the target", why, start, target
            )
        self.jumps += 1
        self.misses = 0
        moved = math.dist(start[:2], end[:2])
        self.last_end = time.monotonic()
        self.ready_at = self.last_end + quiet_after(moved)
        logger.info(f"jump: {why or 'jump'}: {moved:.0f} units, landed")
        blackbox.event("jump_end", why=why, landed=True, moved=round(moved), at=_r(end))
        return True

    def _off(self, reason: str, why: str, start, target) -> bool:
        blackbox.event("jump_end", why=why, landed=False, reason=reason, at=_r(start), to=_r(target))
        self.last_end = time.monotonic()
        self.ready_at = self.last_end + QUIET_MAX
        self.misses += 1
        if self.misses >= MAX_MISSES:
            # Several in a row (10-12 23:53 one miss ended jumps for the run; too soon).
            self.enabled = False
            logger.warning(f"jump: {reason}; {self.misses} misses in a row, jumps are off for this run")
        else:
            logger.warning(f"jump: {reason}; walking this one ({self.misses} miss(es) in a row)")
        return False


def _r(p) -> list[int]:
    return [round(v) for v in p[:3]]
