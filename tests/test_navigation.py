"""Walking between zones and worlds: the game's nav data, A*, and the navigator."""

import asyncio
import math
import struct
import sys
import types
import zlib
from types import SimpleNamespace

import pytest

from wiz101_auto import navigate, navmesh, travel_data, walk


def nav_bytes(nodes, edges) -> bytes:
    """A zone.nav in the game's layout (see navmesh.py)."""
    out = struct.pack("<HI", max(nodes), len(nodes))
    for node, (x, y, z) in nodes.items():
        out += struct.pack("<fffH", x, y, z, node)
    pairs = [(a, b) for a, b in edges] + [(b, a) for a, b in edges]
    out += struct.pack("<I", len(pairs))
    for a, b in pairs:
        out += struct.pack("<HH", a, b)
    return out


def grid(width, height, step=480.0, holes=()):
    """Nodes on a grid, joined to their four neighbours, with `holes` left out (a wall)."""
    ids, nodes, edges = {}, {}, []
    for gy in range(height):
        for gx in range(width):
            if (gx, gy) in holes:
                continue
            ids[gx, gy] = len(ids)
            nodes[ids[gx, gy]] = (gx * step, gy * step, 0.0)
    for (gx, gy), node in ids.items():
        for nx, ny in ((gx + 1, gy), (gx, gy + 1)):
            if (nx, ny) in ids:
                edges.append((node, ids[nx, ny]))
    return nodes, edges


def test_zone_nav_round_trips():
    nodes, edges = grid(3, 2)
    graph = navmesh.NavGraph.parse(nav_bytes(nodes, edges))
    assert graph.nodes == nodes
    assert graph.edges[0] == {1, 3}
    assert len(graph.components()) == 1


def kiwad(files) -> bytes:
    """A version-2 KIWAD archive: header, file table, then the (maybe zlib'd) bodies."""
    header = b"KIWAD" + struct.pack("<ll", 2, len(files)) + b"\x01"
    names = [name.encode() + b"\0" for name, _d, _z in files]
    table_size = sum(21 + len(n) for n in names)
    offset = len(header) + table_size
    table, body = b"", b""
    for (_name, data, zipped), raw_name in zip(files, names, strict=True):
        stored = zlib.compress(data) if zipped else data
        table += struct.pack("<lll?ll", offset, len(data), len(stored), zipped, 0, len(raw_name)) + raw_name
        body += stored
        offset += len(stored)
    return header + table + body


def test_kiwad_reader(tmp_path):
    path = tmp_path / "World-Zone.wad"
    path.write_bytes(kiwad([("zone.nav", b"zone.nav bytes", True), ("other.xml", b"abc", False)]))
    assert set(navmesh.wad_entries(path)) == {"zone.nav", "other.xml"}
    assert navmesh.wad_read(path, "zone.nav") == b"zone.nav bytes"
    assert navmesh.wad_read(path, "other.xml") == b"abc"
    assert navmesh.wad_read(path, "missing") is None
    assert navmesh.wad_name("Grizzleheim/GH_Hero") == "Grizzleheim-GH_Hero.wad"


def test_a_zone_graph_is_read_from_its_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(navmesh, "_graphs", {})
    nodes, edges = grid(2, 2)
    (tmp_path / "Alpha-A_Hub.wad").write_bytes(kiwad([("zone.nav", nav_bytes(nodes, edges), True)]))
    graph = navmesh.zone_graph("Alpha/A_Hub", tmp_path)
    assert graph is not None and len(graph.nodes) == 4
    assert navmesh.zone_graph("Alpha/Missing", tmp_path) is None


def test_a_route_goes_around_a_wall():
    wall = {(2, y) for y in range(0, 4)}  # x=2 is blocked except at the top row
    nodes, edges = grid(5, 5, holes=wall)
    graph = navmesh.NavGraph.parse(nav_bytes(nodes, edges))
    path = graph.route((0.0, 0.0, 0.0), (1920.0, 0.0, 0.0))
    assert path is not None
    assert max(p[1] for p in path) >= 4 * 480.0  # over the top of the wall
    assert all(not (abs(p[0] - 960.0) < 1 and p[1] < 4 * 480.0 - 1) for p in path)
    assert path[-1] == (1920.0, 0.0, 0.0)


def test_straight_runs_collapse_to_their_corners():
    pts = [(0, 0, 0), (480, 0, 0), (960, 0, 0), (960, 480, 0), (960, 960, 0)]
    assert navmesh.straighten(pts) == [(0, 0, 0), (960, 0, 0), (960, 960, 0)]


def test_points_off_the_graph_have_no_route():
    nodes, edges = grid(2, 2)
    graph = navmesh.NavGraph.parse(nav_bytes(nodes, edges))
    assert graph.route((0, 0, 0), (50000.0, 0, 0)) is None


@pytest.mark.skipif(navmesh.game_data_dir() is None, reason="Wizard101 is not installed here")
def test_the_real_savarstaad_graph_reaches_boar_camp_from_the_northguard_gate():
    graph = navmesh.zone_graph("Grizzleheim/GH_Hero")
    assert graph is not None
    path = graph.route((-6529.6, -4087.8, 406.5), (-23200.0, 14300.0, 502.0))
    assert path is not None and len(path) > 5


# ---- the navigator, against a small simulated game -------------------------------


class Game:
    """Zones joined by gates; a wizard that moves while W is held; a world door."""

    STEP = 150.0

    def __init__(self):
        self.zone = "Alpha/A_Hub"
        self.pos = [0.0, 0.0]
        self.yaw = 0.0
        self.down = set()
        self.log = []
        # zone -> [(gate position, next zone, where the wizard appears there)]
        self.gates = {
            "Alpha/A_Hub": [((1920.0, 0.0), "Alpha/A_Door", (100.0, 100.0))],
            "Alpha/A_Door": [((100.0, -300.0), "Alpha/A_Hub", (1700.0, 0.0))],
        }
        self.door = ("Alpha/A_Door", (600.0, 600.0))
        self.spiral_open = False

    def move(self):
        if walk.VK["W"] not in self.down:
            return
        self.pos[0] += -math.sin(self.yaw) * self.STEP
        self.pos[1] += -math.cos(self.yaw) * self.STEP
        for spot, nxt, arrive in self.gates.get(self.zone, []):
            if math.dist(self.pos, spot) < 60:
                self.log.append(f"gate {self.zone} -> {nxt}")
                self.zone, self.pos = nxt, list(arrive)
                self.down.clear()
                return

    def client(self):
        game = self

        class Keys:
            down = game.down

            def press(self, vk, *, held=False):
                game.down.add(vk)

            def release(self, vk):
                game.down.discard(vk)

            def release_all(self):
                game.down.clear()

            async def hold(self, vk, seconds):
                game.down.add(vk)
                game.move()
                game.down.discard(vk)

        async def position():
            game.move()
            return SimpleNamespace(x=game.pos[0], y=game.pos[1], z=0.0)

        async def write_yaw(yaw):
            game.yaw = yaw

        async def none(*_a):
            return None

        async def empty(*_a):
            return []

        async def zone_name():
            return game.zone

        async def false():
            return False

        root = SimpleNamespace(children=empty, get_windows_with_type=empty, get_windows_with_name=empty)
        return SimpleNamespace(
            body=SimpleNamespace(position=position, write_yaw=write_yaw),
            client_object=SimpleNamespace(actor_body=none),
            _posted_keys=Keys(),
            window_handle=1,
            zone_name=zone_name,
            is_loading=false,
            in_battle=false,
            root_window=root,
        )


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(walk, "AIM_EVERY", 0.0005)
    monkeypatch.setattr(walk, "PROGRESS_EVERY", 0.002)
    monkeypatch.setattr(navigate, "PUSH_SECONDS", 0.01)
    monkeypatch.setattr(navigate, "_trouble", {})
    from wiz101_auto import world as world_mod

    async def no_load(*_a, **_k):
        return None

    monkeypatch.setattr(world_mod, "wait_for_loading", no_load)
    wall = {(2, y) for y in range(0, 4)}
    hub_nodes, hub_edges = grid(6, 5, holes=wall)
    door_nodes, door_edges = grid(3, 3)
    monkeypatch.setattr(
        navmesh,
        "_graphs",
        {
            "Alpha/A_Hub": navmesh.NavGraph.parse(nav_bytes(hub_nodes, hub_edges)),
            "Alpha/A_Door": navmesh.NavGraph.parse(nav_bytes(door_nodes, door_edges)),
            "Beta/B_Hub": None,
        },
    )
    XYZ = travel_data.XYZ
    gates = {
        "Alpha/A_Hub": [(XYZ(1920, 0, 0), "Alpha/A_Door")],
        "Alpha/A_Door": [(XYZ(100, -300, 0), "Alpha/A_Hub")],
    }
    doors = {"Alpha": ("Alpha/A_Door", XYZ(100, 100, 100)), "Beta": ("Beta/B_Hub", XYZ(0, 0, 0))}
    monkeypatch.setattr(travel_data, "_data", lambda: (gates, [], {}))
    monkeypatch.setattr(travel_data, "zone_doors", lambda: doors)
    monkeypatch.setattr(travel_data, "gate_kind", lambda a, b: "standard")
    game = Game()

    async def entity_pos(client, needles):
        if game.zone == game.door[0]:
            return SimpleNamespace(x=game.door[1][0], y=game.door[1][1], z=0.0)
        return None

    async def pick_world(client, name):
        game.log.append(f"spiral -> {name}")
        game.zone, game.pos = "Beta/B_Hub", [0.0, 0.0]
        return True

    async def never(*_a, **_k):
        return False

    fake_travel = types.ModuleType("wiz101_auto.travel")
    fake_travel.go_home = never
    fake_travel.is_house = lambda zone: False
    fake_travel.DOOR_NEEDLES = ("worlddoor",)
    fake_travel.entity_pos = entity_pos
    fake_travel._pick_world = pick_world
    fake_travel.RAVENWOOD = "WizardCity/WC_Ravenwood"
    monkeypatch.setitem(sys.modules, "wiz101_auto.travel", fake_travel)

    from wiz101_auto import ui

    async def spiral_visible(client, path):
        return path == ui.SPIRAL_DOOR_TELEPORT and game.zone == game.door[0] and (
            math.dist(game.pos, game.door[1]) < 200
        )

    monkeypatch.setattr(ui, "is_visible", spiral_visible)
    monkeypatch.setattr(navigate, "DOORS_FILE", __import__("pathlib").Path("/nonexistent/doors.json"))
    monkeypatch.setattr(navigate, "remember_door", lambda zone, spot: game.log.append(f"door {zone}"))
    return game


def test_the_wizard_walks_around_a_wall_through_a_gate_and_through_the_world_door(world):
    client = world.client()
    assert asyncio.run(navigate.go_to_zone(client, "Beta/B_Hub")) is True
    assert world.log == ["gate Alpha/A_Hub -> Alpha/A_Door", "door Alpha/A_Door", "spiral -> Beta"]
    assert not world.down


def test_a_stuck_leg_is_planned_around(world, monkeypatch):
    """A leg that does not move marks that edge blocked; the next plan avoids it."""
    client = world.client()
    calls = []
    real = walk.walk_point

    async def flaky(c, point, **kwargs):
        calls.append(tuple(point[:2]))
        if len(calls) == 1:
            return walk.WalkResult("stuck", 500, 500, 0, 1)
        return await real(c, point, **kwargs)

    monkeypatch.setattr(walk, "walk_point", flaky)
    nav = navigate.Navigator(client)
    assert asyncio.run(nav.walk_in_zone((1920.0, 960.0, 0.0), why="test")) in ("reached", "left zone")
    assert len(calls) > 1


def test_leg_time_grows_with_distance():
    assert navigate.leg_seconds(0) == 6.0
    assert navigate.leg_seconds(5800) == pytest.approx(26.0)
    assert navigate.leg_seconds(10**6) == 90.0


def test_a_trip_marks_runs_the_errand_and_recalls(monkeypatch):
    steps = []

    async def mark(client):
        steps.append("mark")
        return True

    async def go(client, dest, *a):
        steps.append(f"walk {dest}")
        return True

    async def recall(client, zone):
        steps.append("recall")
        return recall_works

    async def errand():
        steps.append("errand")

    monkeypatch.setattr(navigate, "mark_here", mark)
    monkeypatch.setattr(navigate, "go_to_zone", go)
    monkeypatch.setattr(navigate, "recall", recall)
    recall_works = True
    assert asyncio.run(navigate.trip(None, "Grizzleheim/GH_MainHub", "Grizzleheim/GH_Hero", errand)) is True
    assert steps == ["mark", "walk Grizzleheim/GH_MainHub", "errand", "recall"]
    steps.clear()
    recall_works = False  # cooling down: walk back instead
    asyncio.run(navigate.trip(None, "Grizzleheim/GH_MainHub", "Grizzleheim/GH_Hero", errand))
    assert steps[-2:] == ["recall", "walk Grizzleheim/GH_Hero"]


def test_position_comes_from_the_client_objects_body_not_the_player_hook():
    """10-10 19:47: the player hook stayed on the old duel spot; the client body did not."""

    async def body_pos():
        return SimpleNamespace(x=1.0, y=2.0, z=3.0)

    async def stale():
        return SimpleNamespace(x=-21242.0, y=12543.0, z=503.0)

    async def actor_body():
        return SimpleNamespace(position=body_pos)

    client = SimpleNamespace(
        client_object=SimpleNamespace(actor_body=actor_body), body=SimpleNamespace(position=stale)
    )
    assert asyncio.run(walk.position(client)) == (1.0, 2.0, 3.0)


def test_a_gate_that_will_not_load_does_not_send_the_wizard_home(world, monkeypatch):
    """10-12 01:51: no key reached the game, the gate 'failed', and Go Home
    left the wizard stuck in the house. A failed route is now just a failure."""
    went_home = []

    async def home(*_a, **_k):
        went_home.append(True)
        return True

    sys.modules["wiz101_auto.travel"].go_home = home

    async def frozen(c, point, **kwargs):
        return walk.WalkResult("stuck", 500, 500, 0, 1)

    monkeypatch.setattr(walk, "walk_point", frozen)
    monkeypatch.setattr(walk, "keys_for", lambda c: SimpleNamespace(hold=_noop))
    monkeypatch.setattr(walk, "KEYS_WAIT", 0.1)
    client = world.client()
    assert asyncio.run(navigate.go_to_zone(client, "Beta/B_Hub")) is False
    assert went_home == []


async def _noop(*_a, **_k):
    return None


def test_edges_near_a_stuck_spot_are_found_both_ways():
    nodes, edges = grid(3, 1)
    graph = navmesh.NavGraph.parse(nav_bytes(nodes, edges))
    near = navigate.edges_near(graph, (480.0, 100.0), reach=150)
    ids = sorted(graph.nodes, key=lambda n: graph.nodes[n][0])
    assert {(ids[0], ids[1]), (ids[1], ids[0]), (ids[1], ids[2]), (ids[2], ids[1])} == near
    assert navigate.edges_near(graph, (480.0, 400.0), reach=150) == set()


def test_stuck_spots_are_avoided_on_later_walks_then_forgotten(monkeypatch):
    monkeypatch.setattr(navigate, "_trouble", {})
    nodes, edges = grid(3, 1)
    graph = navmesh.NavGraph.parse(nav_bytes(nodes, edges))
    navigate.note_trouble("Z/A", (480.0, 0.0), now=0.0)
    assert navigate.trouble_edges("Z/A", graph, now=60.0)
    assert navigate.trouble_edges("Z/B", graph, now=60.0) == set()
    assert navigate.trouble_edges("Z/A", graph, now=navigate.TROUBLE_MINUTES * 60.0 + 1) == set()


def test_a_jump_lands_on_the_goal_or_the_nearest_clear_point_beside_it():
    nodes, edges = grid(4, 1)  # x = 0, 480, 960, 1440
    graph = navmesh.NavGraph.parse(nav_bytes(nodes, edges))
    goal = (960.0, 0.0, 0.0)
    assert navigate.landing_spot(goal, [], graph) == goal
    creature = [(900.0, 0.0, 0.0)]
    assert navigate.landing_spot(goal, creature, graph, clear=300, near=1500)[:2] == (480.0, 0.0)
    assert navigate.landing_spot(goal, creature, graph, clear=2000, near=1500) is None
    assert navigate.landing_spot(goal, creature, None, clear=300) is None


def test_walk_length_follows_the_graph():
    nodes, edges = grid(3, 1)
    graph = navmesh.NavGraph.parse(nav_bytes(nodes, edges))
    assert navigate.walk_length(graph, (0.0, 0.0), (960.0, 0.0)) == pytest.approx(960.0)
    assert navigate.walk_length(None, (0.0, 0.0), (1000.0, 0.0)) == pytest.approx(1500.0)


def test_a_leg_that_moved_nothing_waits_for_focus_and_is_not_trouble(world, monkeypatch):
    """10-16: the first walk of a seeds run moved 0 four times before Wizard101
    was the active app; each was marked a wall and the walk gave up."""
    client = world.client()
    keys = client._posted_keys
    real_hold = type(keys).hold
    focused = [False]

    async def hold(self, vk, seconds):
        if focused[0]:
            await real_hold(self, vk, seconds)

    def focus(why):
        focused[0] = True
        return True

    monkeypatch.setattr(type(keys), "hold", hold)
    monkeypatch.setattr(walk, "request_focus", focus)
    calls = []
    real = walk.walk_point

    async def unfocused_first(c, point, **kwargs):
        calls.append(point)
        if not focused[0]:
            return walk.WalkResult("timeout", 500, 500, 0, 1)
        return await real(c, point, **kwargs)

    monkeypatch.setattr(walk, "walk_point", unfocused_first)
    nav = navigate.Navigator(client)
    assert asyncio.run(nav.walk_in_zone((1920.0, 960.0, 0.0), why="test")) in ("reached", "left zone")
    assert not any(navigate._trouble.values())  # no stuck spot remembered


def test_a_walk_whose_keys_never_arrive_says_so(world, monkeypatch):
    client = world.client()

    async def frozen(c, point, **kwargs):
        return walk.WalkResult("stuck", 500, 500, 0, 1)

    monkeypatch.setattr(walk, "walk_point", frozen)
    monkeypatch.setattr(walk, "keys_for", lambda c: SimpleNamespace(hold=_noop))
    monkeypatch.setattr(walk, "KEYS_WAIT", 0.1)
    monkeypatch.setattr(walk, "request_focus", lambda why: False)
    nav = navigate.Navigator(client)
    assert asyncio.run(nav.walk_in_zone((1920.0, 960.0, 0.0), why="test")) == "no keys"
