"""Reagent spots from the zone files, and the gather tour (no game needed)."""

import struct

import pytest

from wiz101_auto import gather, navmesh, reagent_spots
from wiz101_auto.gather import (
    Ledger,
    area,
    clear_of,
    is_reagent,
    nearest_spot,
    rank_zones,
    spot_key,
    tour_order,
)


def path_record(name: str, ids: list[int]) -> bytes:
    text = f"Path-{name}".encode()
    head = bytes([len(text) * 2]) + text
    body = struct.pack("<II", 64 + 8 + 64 * len(ids), 0x5544AE1E) + bytes([len(ids) * 2])
    return b"\x00\x00" + head + body + struct.pack(f"<{len(ids)}Q", *ids)


def node_record(node: int, x: float, y: float, z: float) -> bytes:
    return (
        struct.pack("<II", reagent_spots.NODE_CLASS, 704)
        + struct.pack("<II", 160, reagent_spots.LOCATION)
        + struct.pack("<fff", x, y, z)
        + struct.pack("<IIQ", 96, 0x2EF782C5, 0)
        + struct.pack("<II", 128, reagent_spots.ID)
        + struct.pack("<Q", node)
    )


def test_paths_and_nodes_parse():
    # "Path-BlackLotus" followed by a size byte that is a letter ('H'): the name's
    # own length byte decides where it ends (it read "BlackLotusH" at first).
    data = path_record("BlackLotus", [467, 468]) + path_record("Wood", [9])
    paths = reagent_spots.parse_paths(data)
    assert paths == {"BlackLotus": [467, 468], "Wood": [9]}
    nodes = reagent_spots.parse_nodes(node_record(467, 1.0, 2.0, 3.0) + node_record(468, -5.0, 6.5, 0.0))
    assert nodes == {467: (1.0, 2.0, 3.0), 468: (-5.0, 6.5, 0.0)}


def test_names_match_whatever_the_files_spell():
    assert reagent_spots.flat("Black Lotus") == reagent_spots.flat("Blacklotus") == "blacklotus"
    assert reagent_spots.zone_from_wad("MooShu-MS_Hub.wad") == "MooShu/MS_Hub"
    assert is_reagent("BlackLotus", "Black Lotus")
    assert is_reagent("MS_BlackLotus_01", "black lotus")
    assert not is_reagent("Spawn point-BlackLotus", "Black Lotus")
    assert not is_reagent("Mushroom", "Black Lotus")


def test_tour_goes_nearest_first():
    spots = [(1000, 0, 0), (100, 0, 0), (2000, 0, 0), (-500, 0, 0)]
    assert tour_order((0, 0, 0), spots) == [(100, 0, 0), (-500, 0, 0), (1000, 0, 0), (2000, 0, 0)]


def test_zones_rank_by_spots_per_walk_and_keep_to_one_area():
    table = {
        "W/A/compact": [(0, 0, 0)] * 15,
        "W/A/long": [(0, 0, 0)] * 16,
        "W/B/fair": [(0, 0, 0)] * 13,
        "W/Hub": [(0, 0, 0)],
    }
    lengths = {"W/A/compact": 66000, "W/A/long": 250000, "W/B/fair": 150000, "W/Hub": 0}
    assert rank_zones(table, lengths) == ["W/A/compact", "W/A/long", "W/B/fair", "W/Hub"]
    assert area("MooShu/MS_Plague/MS_Plague_Zone3_CliffsidePalace") == "MooShu/MS_Plague"
    assert area("MooShu/MS_Hub") == "MooShu"


def test_creatures_and_listed_spots():
    assert clear_of((0, 0), [(1000, 0)])
    assert not clear_of((0, 0), [(300, 200)])
    assert nearest_spot((10, 10), [(0, 0), (500, 500)]) == (0, 0)
    assert nearest_spot((2000, 0), [(0, 0)]) is None
    assert spot_key((1234.0, -26.0)) == "1250,-50"


def test_ledger_counts_visits_and_sightings(tmp_path):
    book = Ledger(tmp_path / "gather.json")
    book.visit("Black Lotus", "MooShu/X", (0, 0))
    book.seen("Black Lotus", "MooShu/X", (10, 0), picked=True, now=100)
    book.save()
    again = Ledger(tmp_path / "gather.json")
    row = again.data["blacklotus"]["MooShu/X"]["0,0"]
    assert row == {"visits": 1, "seen": 1, "picked": 1, "last_seen": 100}


@pytest.mark.skipif(navmesh.game_data_dir() is None, reason="no Wizard101 game data here")
def test_mooshu_black_lotus_spots_from_the_real_files():
    table = reagent_spots.reagent_spots("Black Lotus", "MooShu")
    shirataki = "MooShu/MS_Plague/MS_Plague_Zone3_CliffsidePalace"
    if shirataki not in table:
        pytest.skip("MooShu is not downloaded")
    assert len(table[shirataki]) == 15
    assert "MooShu/MS_Death/MS_Death_Zone2_HauntedVillage" in table  # Village of Sorrow
    assert gather.plan_zones("Black Lotus")[0] == shirataki


def test_tour_follows_walking_distance_not_a_straight_line():
    # Two spots: B is nearer as the crow flies but far on foot (a cliff between).
    a, b = (1000.0, 0.0, 0.0), (300.0, 0.0, 0.0)

    def on_foot(p, q):
        if {tuple(p[:2]), tuple(q[:2])} >= {(0.0, 0.0), (300.0, 0.0)}:
            return 9000.0
        return gather.math.dist(p[:2], q[:2])

    assert tour_order((0.0, 0.0, 0.0), [a, b])[0] == b
    assert tour_order((0.0, 0.0, 0.0), [a, b], on_foot)[0] == a


def test_the_jump_pilot_jumps_to_every_spot_and_never_walks(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from wiz101_auto import navigate

    spots = [(1000.0, 0.0, 0.0), (5000.0, 0.0, 0.0), (9000.0, 0.0, 0.0)]
    monkeypatch.setattr(gather, "known_spots", lambda zone, reagent: spots)
    monkeypatch.setattr(navmesh, "zone_graph", lambda zone, data_dir=None: None)

    async def no_walking(*_a, **_k):
        raise AssertionError("the pilot walked")

    monkeypatch.setattr(navigate, "walk_in_zone", no_walking)

    class Jumper:
        enabled = True
        jumps = 0

        def __init__(self):
            self.to = []

        async def __call__(self, target, *, why=""):
            self.to.append(tuple(target[:2]))
            here[0] = tuple(target)
            self.jumps += 1
            return True

    here = [(0.0, 0.0, 0.0)]
    jumper = Jumper()

    async def zone():
        return "W/Z"

    async def nothing(*_a, **_k):
        return []

    async def checkpoint():
        return None

    controller = SimpleNamespace(checkpoint=checkpoint)
    cfg = SimpleNamespace(reagent="Black Lotus", zones=[], world="", zone_count=1, respawn_minutes=0)
    g = gather.Gatherer(SimpleNamespace(zone_name=zone), controller, {}, cfg)
    g.ledger = Ledger(tmp_path / "gather.json")

    async def where():
        return here[0]

    g.where, g.reagents_here, g.mob_spots = where, nothing, nothing

    async def not_fighting(client):
        return False

    from wiz101_auto import farm

    monkeypatch.setattr(farm, "in_fight", not_fighting)
    asyncio.run(g.jump_tour("W/Z", jumper))
    assert jumper.to == [(1000.0, 0.0), (5000.0, 0.0), (9000.0, 0.0)]
    assert g.tours == 1
