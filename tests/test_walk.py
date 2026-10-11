"""Walking by posted keys: key messages, aiming, and what a walk reports."""

import asyncio
import math
from types import SimpleNamespace

import pytest

from wiz101_auto import walk


def perfect_yaw(cx, cy, tx, ty):
    """WizWalker's calculate_perfect_yaw, copied from wizwalker/utils.py."""
    target_line = math.dist((cx, cy), (tx, ty))
    origin_line = math.dist((cx, cy), (cx, cy - 1))
    target_to_origin = math.dist((tx, ty), (cx, cy - 1))
    if 1.0 - abs(origin_line) > 0.0 or 1.0 - abs(target_line) > 0.0:
        return 0
    angle = math.acos(
        (target_line**2 + origin_line**2 - target_to_origin**2) / (2 * origin_line * target_line)
    )
    if tx > cx:
        return math.radians(360 - math.degrees(angle))
    return angle


def test_key_messages_carry_the_scan_code_and_state_bits():
    assert walk.key_lparam(0x11, up=False) == 0x00110001
    assert walk.key_lparam(0x11, up=False, held=True) == 0x40110001
    assert walk.key_lparam(0x11, up=True) == 0xC0110001


@pytest.mark.parametrize("target", [(100, 0), (-100, 0), (0, 100), (0, -100), (30, -400), (-250, 77)])
def test_yaw_matches_wizwalker(target):
    assert walk.yaw_to((0, 0), target) == pytest.approx(perfect_yaw(0, 0, *target))


def test_yaw_on_the_spot_is_zero():
    assert walk.yaw_to((5, 5), (5.2, 5.1)) == 0.0


class FakeKeys:
    def __init__(self):
        self.down = set()
        self.log = []

    def press(self, vk, *, held=False):
        self.down.add(vk)

    def release(self, vk):
        self.down.discard(vk)

    def release_all(self):
        self.down.clear()

    async def hold(self, vk, seconds):
        self.log.append(chr(vk))


class World:
    """A wizard that moves `step` toward its yaw for each position read while W is down."""

    def __init__(self, step=200.0, start=(0.0, 0.0)):
        self.pos = list(start)
        self.yaw = 0.0
        self.step = step
        self.keys = FakeKeys()

    def client(self):
        world = self

        async def position():
            if walk.VK["W"] in world.keys.down and world.step:
                # yaw 0 faces -y; the wizard turns clockwise from there.
                world.pos[0] += -math.sin(world.yaw) * world.step
                world.pos[1] += -math.cos(world.yaw) * world.step
            return SimpleNamespace(x=world.pos[0], y=world.pos[1], z=0.0)

        async def write_yaw(yaw):
            world.yaw = yaw

        async def actor_body():
            return None

        return SimpleNamespace(
            body=SimpleNamespace(position=position, write_yaw=write_yaw),
            client_object=SimpleNamespace(actor_body=actor_body),
            _posted_keys=self.keys,
            window_handle=1,
        )


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(walk, "AIM_EVERY", 0.001)
    monkeypatch.setattr(walk, "PROGRESS_EVERY", 0.004)


def test_a_walk_reaches_a_point_and_lets_go_of_w():
    world = World()
    result = asyncio.run(walk.walk_point(world.client(), (1000.0, 600.0, 0.0), why="test", stop_within=150))
    assert result.outcome == "reached"
    assert result.end_dist <= 150 + 1
    assert not world.keys.down


def test_a_walk_stops_when_the_duel_starts():
    world = World()

    async def fight():
        return world.pos[0] > 300

    result = asyncio.run(walk.walk_point(world.client(), (2000.0, 0.0, 0.0), why="test", fight=fight))
    assert result.outcome == "fight"
    assert not world.keys.down


def test_a_wizard_that_does_not_move_sidesteps_then_reports_stuck():
    world = World(step=0.0)
    result = asyncio.run(walk.walk_point(world.client(), (2000.0, 0.0, 0.0), why="test", max_seconds=5))
    assert result.outcome == "stuck"
    assert world.keys.log[:3] == ["A", "D", "A"]
    assert not world.keys.down


def test_a_target_that_vanishes_is_lost():
    world = World()

    async def gone():
        return None

    result = asyncio.run(walk.walk_to(world.client(), gone, why="test"))
    assert result.outcome == "lost"


class TimedWorld:
    """Movement is speed x the time W is held, like the game: a re-aim tick can overshoot."""

    def __init__(self, speed):
        import time

        self.time = time
        self.pos = [0.0, 0.0]
        self.yaw = 0.0
        self.speed = speed
        self.since = None
        world = self

        class Keys(FakeKeys):
            def press(self, vk, *, held=False):
                world.advance()
                if vk == walk.VK["W"] and world.since is None:
                    world.since = world.time.monotonic()
                super().press(vk, held=held)

            def release(self, vk):
                world.advance()
                if vk == walk.VK["W"]:
                    world.since = None
                super().release(vk)

            def release_all(self):
                self.release(walk.VK["W"])
                super().release_all()

            async def hold(self, vk, seconds):
                self.press(vk)
                await asyncio.sleep(seconds)
                self.release(vk)

        self.keys = Keys()

    def advance(self):
        if self.since is None:
            return
        now = self.time.monotonic()
        step = (now - self.since) * self.speed
        self.pos[0] += -math.sin(self.yaw) * step
        self.pos[1] += -math.cos(self.yaw) * step
        self.since = now

    def client(self):
        world = self

        async def position():
            world.advance()
            return SimpleNamespace(x=world.pos[0], y=world.pos[1], z=0.0)

        async def write_yaw(yaw):
            world.advance()
            world.yaw = yaw

        async def actor_body():
            return None

        return SimpleNamespace(
            body=SimpleNamespace(position=position, write_yaw=write_yaw),
            client_object=SimpleNamespace(actor_body=actor_body),
            _posted_keys=self.keys,
            window_handle=1,
        )


def test_a_small_target_is_reached_without_circling_it(monkeypatch):
    monkeypatch.setattr(walk, "BASE_SPEED", 5800.0)
    monkeypatch.setattr(walk, "AIM_EVERY", 0.03)
    monkeypatch.setattr(walk, "PROGRESS_EVERY", 0.12)
    monkeypatch.setattr(walk, "REPEAT", 0.005)  # 50ms at 580/s, scaled like the speed
    world = TimedWorld(5800.0)
    result = asyncio.run(walk.walk_point(world.client(), (500.0, 400.0, 0.0), why="sigil", stop_within=25))
    assert result.outcome == "reached", result
    assert result.end_dist <= 25
    assert result.moved < 640 + 120  # one straight line, not loops around the sigil


def test_focus_is_asked_for_at_most_every_30s(tmp_path, monkeypatch):
    monkeypatch.setattr(walk, "FOCUS_REQUEST", tmp_path / "focus.request")
    monkeypatch.setattr(walk, "_last_focus", [-1e9])
    assert walk.request_focus("walk 1") is True
    (tmp_path / "focus.request").unlink()  # the Mac side took it
    assert walk.request_focus("walk 2") is False
    walk._last_focus[0] -= 31
    assert walk.request_focus("walk 3") is True
    assert (tmp_path / "focus.request").read_text() == "walk 3"


def test_a_short_final_hold_that_moves_is_not_a_stall(monkeypatch):
    monkeypatch.setattr(walk, "BASE_SPEED", 5800.0)
    monkeypatch.setattr(walk, "AIM_EVERY", 0.03)
    monkeypatch.setattr(walk, "PROGRESS_EVERY", 0.12)
    monkeypatch.setattr(walk, "REPEAT", 0.005)
    world = TimedWorld(5800.0)
    result = asyncio.run(walk.walk_point(world.client(), (200.0, 0.0, 0.0), why="short leg", stop_within=25))
    assert result.outcome == "reached", result


def test_a_final_approach_that_never_moves_is_stuck_not_a_timeout(monkeypatch):
    monkeypatch.setattr(walk, "BASE_SPEED", 5800.0)
    monkeypatch.setattr(walk, "AIM_EVERY", 0.03)
    monkeypatch.setattr(walk, "REPEAT", 0.005)
    world = TimedWorld(0.0)  # the 10-10 19:39 run: 236 away, moved 0, every leg ran out its time
    result = asyncio.run(walk.walk_point(world.client(), (236.0, 0.0, 0.0), why="leg 1", max_seconds=20))
    assert result.outcome == "stuck", result
    assert result.seconds < 5


def test_w_stays_down_between_legs_and_comes_up_at_the_end():
    world = World()
    client = world.client()
    first = asyncio.run(
        walk.walk_point(
            client, (600.0, 0.0, 0.0), why="leg 1", stop_within=150, precise=False, keep_moving=True
        )
    )
    assert first.outcome == "reached" and walk.VK["W"] in world.keys.down
    last = asyncio.run(walk.walk_point(client, (1200.0, 600.0, 0.0), why="leg 2", stop_within=150))
    assert last.outcome == "reached" and not world.keys.down
