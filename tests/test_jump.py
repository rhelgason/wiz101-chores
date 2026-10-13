"""Data-only jumps (jump.py): the hook body, and a jump against a fake game."""

import asyncio
import struct
import sys
import types
from types import SimpleNamespace

import pytest


@pytest.fixture
def jump(monkeypatch):
    hooks = types.ModuleType("wizwalker.memory.hooks")

    class SimpleHook:
        def __init__(self, handler=None):
            self.hook_handler = handler

    hooks.SimpleHook = SimpleHook
    hooks.MovementTeleportHook = SimpleNamespace(pattern=b"\x57\x48\x83\xec")
    monkeypatch.setitem(sys.modules, "wizwalker", types.ModuleType("wizwalker"))
    monkeypatch.setitem(sys.modules, "wizwalker.memory", types.ModuleType("wizwalker.memory"))
    monkeypatch.setitem(sys.modules, "wizwalker.memory.hooks", hooks)
    sys.modules.pop("wiz101_auto.jump", None)
    from wiz101_auto import jump as module

    yield module
    sys.modules.pop("wiz101_auto.jump", None)


HELPER = 0x7FF000001000
ORIGINAL = b"\x57\x48\x83\xec\x20"  # push rdi; sub rsp, 20


def test_the_hook_body_skips_to_the_original_bytes_and_writes_only_the_helper(jump):
    body = jump.position_cave(HELPER, ORIGINAL)
    assert body.endswith(ORIGINAL)
    skip = len(body) - len(ORIGINAL)
    # jne (not our object) and je (flag clear) both land on the original bytes.
    jne = body.index(b"\x0f\x85")
    assert jne + 6 + struct.unpack_from("<i", body, jne + 2)[0] == skip
    je = body.index(b"\x0f\x84")
    assert je + 6 + struct.unpack_from("<i", body, je + 2)[0] == skip
    # Every absolute address in it is inside the 21-byte helper: no game code is touched.
    for op in (b"\x48\xa1", b"\xa0", b"\xa1", b"\xa2"):
        at = body.find(op)
        while at >= 0:
            (addr,) = struct.unpack_from("<Q", body, at + len(op))
            if addr >> 32 == HELPER >> 32:
                assert HELPER <= addr < HELPER + 21
            at = body.find(op, at + 1)
    assert b"\x48\xa3" not in body  # no 64-bit store anywhere (WizWalker's cave rewrote code with it)


class Game:
    """A helper buffer, and a wizard the hook moves when the flag is set and W is held."""

    def __init__(self, takes=True, lands=True, mobs=(), keys_arrive=True):
        self.mem = {}
        self.pos = (0.0, 0.0, 0.0)
        self.takes, self.lands, self.mobs = takes, lands, list(mobs)
        self.keys_arrive = keys_arrive
        game = self

        class Handler:
            _base_addrs = {"jump_helper": HELPER}

            async def write_bytes(self, addr, data):
                for i, b in enumerate(data):
                    game.mem[addr + i] = b

            async def read_bytes(self, addr, n):
                return bytes(game.mem.get(addr + i, 0) for i in range(n))

        class Keys:
            async def hold(self, vk, seconds):
                if not game.keys_arrive:
                    return
                game.pos = (game.pos[0], game.pos[1] + 10.0, game.pos[2])  # a tap moves the wizard
                if game.takes and game.mem.get(HELPER + 12) == 1:
                    x, y, z = struct.unpack("<fff", bytes(game.mem[HELPER + i] for i in range(12)))
                    game.mem[HELPER + 12] = 0
                    if game.lands:
                        game.pos = (x, y, z)

        self.keys = Keys()

        async def base():
            return 0x1234

        async def mobs():
            return [SimpleNamespace(location=_loc(m)) for m in game.mobs]

        self.client = SimpleNamespace(
            hook_handler=Handler(),
            client_object=SimpleNamespace(read_base_address=base),
            get_mobs=mobs,
        )


def _loc(p):
    async def location():
        return SimpleNamespace(x=p[0], y=p[1], z=p[2])

    return location


@pytest.fixture
def fake(jump, monkeypatch):
    from wiz101_auto import walk

    def make(**kwargs):
        game = Game(**kwargs)

        async def position(client):
            return game.pos

        monkeypatch.setattr(walk, "position", position)
        monkeypatch.setattr(walk, "keys_for", lambda client: game.keys)
        monkeypatch.setattr(jump, "SETTLE", 0.0)
        monkeypatch.setattr(jump, "ARM_SECONDS", 0.3)
        monkeypatch.setattr(jump, "STILL_FOR", 0.01)
        monkeypatch.setattr(jump, "KEYS_WAIT", 0.5)
        monkeypatch.setattr(walk, "request_focus", lambda why: False)
        monkeypatch.setattr(jump, "KEYS_TAP", 0.0)
        monkeypatch.setattr(jump, "QUIET_MAX", 0.01)
        monkeypatch.setattr(jump, "QUIET_MIN", 0.01)
        monkeypatch.setattr(jump, "ARM_SECONDS", 0.6)
        return game

    return make


def test_a_jump_lands_and_names_our_object(jump, fake):
    game = fake()
    jumper = jump.Jumper(game.client)
    assert asyncio.run(jumper((3000.0, 400.0, 10.0), why="test")) is True
    assert game.pos[:2] == (3000.0, 400.0)
    assert struct.unpack("<Q", bytes(game.mem[HELPER + 13 + i] for i in range(8)))[0] == 0x1234
    assert jumper.enabled and jumper.jumps == 1


def test_a_hook_that_never_runs_turns_jumps_off_after_three_in_a_row(jump, fake):
    """10-12 23:53: one miss (a wedged wizard) ended jumps for the run; now it takes three."""
    game = fake(takes=False)
    jumper = jump.Jumper(game.client)
    assert asyncio.run(jumper((3000.0, 0.0, 0.0))) is False
    assert game.mem[HELPER + 12] == 0  # disarmed
    assert jumper.enabled
    for _ in range(jump.MAX_MISSES - 1):
        asyncio.run(jumper((3000.0, 0.0, 0.0)))
    assert not jumper.enabled


def test_a_landing_resets_the_miss_count(jump, fake):
    game = fake(takes=False)
    jumper = jump.Jumper(game.client)
    asyncio.run(jumper((3000.0, 0.0, 0.0)))
    game.takes = True
    assert asyncio.run(jumper((3000.0, 0.0, 0.0)))
    assert jumper.misses == 0 and jumper.enabled


def test_arming_backs_up_and_turns_when_w_moves_nothing(jump, fake):
    game = fake(takes=False)
    pressed = []
    real = game.keys.hold

    async def hold(vk, seconds):
        pressed.append(vk)
        await real(vk, seconds)

    game.keys.hold = hold
    asyncio.run(jump.Jumper(game.client)((3000.0, 0.0, 0.0)))
    from wiz101_auto.walk import VK

    assert {VK["W"], VK["S"], VK["A"], VK["D"]} <= set(pressed)


def test_a_jump_the_server_undoes_turns_jumps_off_after_three(jump, fake):
    game = fake(lands=False)
    jumper = jump.Jumper(game.client)
    for _ in range(jump.MAX_MISSES):
        assert asyncio.run(jumper((3000.0, 0.0, 0.0))) is False
    assert not jumper.enabled


def test_no_landing_beside_a_creature(jump, fake):
    game = fake(mobs=[(3100.0, 0.0, 0.0)])
    jumper = jump.Jumper(game.client)
    assert asyncio.run(jumper((3000.0, 0.0, 0.0))) is False
    assert game.pos == (0.0, 0.0, 0.0)
    assert jumper.enabled  # not a failure of jumping, just a bad place


def test_the_body_fits_the_cave_it_reserves(jump):
    body = jump.position_cave(HELPER, ORIGINAL) + b"\xe9\0\0\0\0"  # SimpleHook adds the jump back
    assert len(body) <= jump.PositionHook.cave_size


def test_jumps_are_paced_longer_after_a_long_one(jump):
    assert jump.quiet_after(0) == jump.QUIET_MIN
    assert jump.quiet_after(4000) == pytest.approx(jump.QUIET_MIN + 1.0)
    assert jump.quiet_after(43000) == jump.QUIET_MAX


def test_the_next_jump_waits_for_the_last_landing_to_settle(jump, fake, monkeypatch):
    """10-12: the game closed on the 4th jump of a chain fired 0.1s apart."""
    import time

    game = fake()
    monkeypatch.setattr(jump, "QUIET_MIN", 0.3)
    monkeypatch.setattr(jump, "QUIET_MAX", 0.3)
    monkeypatch.setattr(jump, "QUIET_PER_UNIT", 0.0)
    jumper = jump.Jumper(game.client)
    assert asyncio.run(jumper((3000.0, 0.0, 0.0)))
    began = time.monotonic()
    assert asyncio.run(jumper((6000.0, 0.0, 0.0)))
    assert time.monotonic() - began >= 0.3


def test_keys_that_do_not_reach_the_game_are_not_a_jump_miss(jump, fake):
    """10-12: each run began with ~40s of taps moving nothing; three jumps then
    "missed" and turned jumps off. Now the jump waits for keys and does not count it."""
    game = fake(keys_arrive=False)
    jumper = jump.Jumper(game.client)
    for _ in range(jump.MAX_MISSES + 1):
        assert asyncio.run(jumper((3000.0, 0.0, 0.0))) is False
    assert jumper.enabled and jumper.misses == 0
    assert game.mem.get(HELPER + 12, 0) == 0  # never armed
    game.keys_arrive = True
    assert asyncio.run(jumper((3000.0, 0.0, 0.0)))
