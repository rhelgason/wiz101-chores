"""The Savarstaad seed mode: who to pull, when to recover, when to stop."""

import asyncio
from types import SimpleNamespace

from wiz101_auto.farm import GROUPS
from wiz101_auto.seeds import (
    Seen,
    bag_is_full,
    health_is_low,
    in_savarstaad,
    low_on,
    mana_is_low,
    mana_is_ready,
    pick_target,
    recovered,
)

HOME = (-23000.0, 14000.0, 502.0)


def row(label, x, y, dist, gid=1, resting=False):
    return Seen(label, (x, y, 502.0), dist, gid, resting)


def test_mana_and_health_thresholds_and_unread_bars():
    assert mana_is_low(0.2)
    assert not mana_is_low(0.4)
    assert not mana_is_low(None)
    assert mana_is_ready(0.6)
    assert mana_is_ready(None)
    assert not mana_is_ready(0.4)
    assert health_is_low(0.3)
    assert not health_is_low(None)


def test_health_is_seen_to_before_mana():
    assert low_on(0.2, 0.1) == "health"
    assert low_on(0.9, 0.1) == "mana"
    assert low_on(0.9, 0.9) == ""
    assert low_on(None, None) == ""
    assert recovered("health", 0.7, 0.0)
    assert not recovered("mana", 1.0, 0.4)


def test_the_run_stops_when_two_slots_remain():
    assert bag_is_full(98, 100)
    assert not bag_is_full(90, 100)
    assert not bag_is_full(None, 100)
    assert not bag_is_full(10, 0)


def test_only_savarstaad_pass_is_the_farm_zone():
    assert in_savarstaad("Grizzleheim/GH_Hero")
    assert not in_savarstaad("Grizzleheim/GH_MainHub")
    assert not in_savarstaad(None)


def test_a_troubled_warrior_beats_a_nearer_boar_of_the_later_group():
    seen = [
        row("GH-Boar-2-R4 Splithoof Rager", -23100, 14000, 100, gid=1),
        row("GH-Troubled Warrior", -23900, 14000, 900, gid=2),
    ]
    assert pick_target(seen, HOME, 4500, GROUPS["seeds"]) == 1


def test_the_nearest_inside_one_group_wins():
    seen = [
        row("GH-Boar-1-R3 Splithoof Barbarian", -24000, 14000, 1000, gid=1),
        row("GH-Boar-1-R3 Splithoof Barbarian", -23300, 14000, 300, gid=2),
    ]
    assert pick_target(seen, HOME, 4500, GROUPS["seeds"]) == 1


def test_the_leash_skipped_and_unnamed_creatures_are_not_pulled():
    seen = [
        row("GH-Boar-1-R3 Splithoof Barbarian", -30000, 14000, 50, gid=1),  # 7000 from home
        row("GH-Boar-1-R3 Splithoof Barbarian", -23100, 14000, 100, gid=2, resting=True),
        row("Basic Positional", -23000, 14000, 10, gid=3),
    ]
    assert pick_target(seen, HOME, 4500, GROUPS["seeds"]) is None
    assert pick_target(seen, HOME, 8000, GROUPS["seeds"]) == 0


def test_seed_config_has_a_leash(tmp_path):
    from wiz101_auto.config import load_config

    path = tmp_path / "c.yaml"
    path.write_text("mode: seeds\nseeds:\n  leash: 3000\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.seeds.leash == 3000.0
    assert cfg.safety.pause_releases_mouse is False
    assert SimpleNamespace(**vars(cfg.seeds)).rest_minutes > 0


def test_hunts_from_config():
    from wiz101_auto.seeds import SEEDS, creature_needle, hunts_from

    assert hunts_from([]) == [SEEDS]
    assert creature_needle("Charmed Slaves") == "charmedslave"
    assert creature_needle("O'Leary Prowler") == "olearyprowler"
    assert creature_needle("Boss") == "boss"
    prowler = {"creature": "O'Leary Prowler", "count": 25, "zone": "W/Z"}
    slaves, prowlers = hunts_from(["Charmed Slaves", prowler])
    assert slaves.groups == (("charmedslave",),) and slaves.count == 30 and not slaves.seeds
    assert prowlers.zone == "W/Z" and prowlers.count == 25 and prowlers.camp is None


def test_a_hunt_counts_the_enemies_in_won_duels():
    from wiz101_auto.seeds import hunt_done, hunts_from, kills_in

    slaves = hunts_from(["Charmed Slave"])[0]
    assert kills_in(["Charmed Slave", "Charmed Slave", "Fire Elf"], slaves) == 2
    assert not hunt_done(slaves, 29)
    assert hunt_done(slaves, 30)
    assert not hunt_done(hunts_from(["seeds"])[0], 10_000)  # the seed farm runs until stopped


def won_duel(farm, monkeypatch, foes):
    import asyncio

    from wiz101_auto import farm as fights
    from wiz101_auto import seeds

    async def fight(_client, _controller):
        fights.last_foes.clear()
        fights.last_foes.update(enumerate(foes))
        return None

    async def ratios():
        return 1.0, 1.0

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(fights, "play_current_fight", fight)
    monkeypatch.setattr(seeds.asyncio, "sleep", no_sleep)
    farm.ratios = ratios
    asyncio.run(farm.play())


def test_a_finished_hunt_moves_on_then_stops(monkeypatch):
    from wiz101_auto import seeds

    stops = []
    controller = SimpleNamespace(stop=stops.append, record_death=lambda zone: None)
    cfg = SimpleNamespace(
        hunts=[{"creature": "Charmed Slave", "count": 3}, {"creature": "O'Leary Prowler", "count": 2}]
    )
    farm = seeds.SeedFarm(client=None, controller=controller, task={}, cfg=cfg)
    monkeypatch.setattr(seeds.blackbox, "event", lambda *a, **k: None)
    farm.home = (1.0, 2.0, 3.0)
    won_duel(farm, monkeypatch, ["Charmed Slave", "Charmed Slave"])
    assert farm.kills == 2 and farm.hunt.name == "Charmed Slave"
    farm.defeated()  # that duel was lost after all
    assert farm.kills == 0
    won_duel(farm, monkeypatch, ["Charmed Slave", "Charmed Slave", "Charmed Slave"])
    assert farm.hunt.name == "O'Leary Prowler" and farm.home is None and farm.kills == 0
    won_duel(farm, monkeypatch, ["MB Thug", "O'Leary Prowler"])
    assert not stops
    won_duel(farm, monkeypatch, ["O'Leary Prowler"])
    assert stops == ["every hunt is done"]


def test_a_next_hunt_with_no_zone_asks_the_player(monkeypatch):
    from wiz101_auto import seeds

    stops = []
    farm = seeds.SeedFarm(
        client=None, controller=SimpleNamespace(stop=stops.append), task={},
        cfg=SimpleNamespace(hunts=[{"creature": "Charmed Slave", "count": 1}, "Nowhere Beast"]),
    )
    monkeypatch.setattr(seeds.blackbox, "event", lambda *a, **k: None)
    won_duel(farm, monkeypatch, ["Charmed Slave"])
    assert stops and "needs a zone" in stops[0]


def test_known_hunts_walk_to_their_zones_and_roam():
    from wiz101_auto.seeds import SeedFarm, hunts_from

    slaves, prowlers = hunts_from(["Charmed Slaves", "O'Leary Prowlers"])
    assert slaves.zone == "Krokotopia/KT_Pyramid/KT_Chamber"
    assert prowlers.zone == "Marleybone/MB_ScotlandYard/MB_KnightsCourt"
    farm = SeedFarm(client=None, controller=None, task={}, cfg=SimpleNamespace(hunts=["Charmed Slave"]))
    assert farm.roams
    assert not SeedFarm(client=None, controller=None, task={}, cfg=SimpleNamespace(hunts=[])).roams


def test_roam_points_spread_over_the_walked_part_of_the_graph():
    from wiz101_auto.navmesh import NavGraph
    from wiz101_auto.seeds import roam_points

    nodes = {i: (i * 1000.0, 0.0, 0.0) for i in range(10)}  # a corridor, 0 to 9000
    nodes[50] = (99999.0, 0.0, 0.0)  # off on its own: never a roam point
    edges = {i: {j for j in (i - 1, i + 1) if 0 <= j < 10} for i in range(10)}
    edges[50] = set()
    tour = roam_points(NavGraph(nodes, edges), (0.0, 0.0, 0.0), spacing=3000.0)
    assert len(tour) == 4 and all(p[0] < 10000 for p in tour)  # cells 0-3k, 3-6k, 6-9k, 9k
    assert [p[0] for p in tour] == sorted(p[0] for p in tour)  # nearest-next from the start
    assert roam_points(None, (0.0, 0.0, 0.0)) == []


def test_seeds_can_follow_the_quest_hunts():
    from wiz101_auto.seeds import SEEDS, hunts_from

    hunts = hunts_from(
        [{"creature": "O'Leary Prowler", "count": 30}, {"creature": "Charmed Slave", "count": 30}, "seeds"]
    )
    assert [h.seeds for h in hunts] == [False, False, True]
    assert hunts[2] == SEEDS and hunts[2].count == 0  # until stopped
    assert hunts_from([{"creature": "boars", "count": 50}])[0].count == 50


def test_a_stand_in_object_is_not_a_creature():
    from wiz101_auto.farm import mob_rank

    groups = (("olearyprowler",),)
    assert mob_rank("MB-Standin-OlearyProwler", groups) is None
    assert mob_rank("Cat-Burglar-Gray-L29 O'Leary Prowler", groups) == 0


def test_a_failed_walk_far_from_the_camp_does_not_make_home_there(monkeypatch):
    """10-16: the first walk moved 0 (no focus yet), and a spot 25000 units
    from the boars became home; the bot waited there for creatures."""
    from wiz101_auto import navigate, seeds

    farm = seeds.SeedFarm(client=None, controller=None, task={}, cfg=SimpleNamespace(hunts=[], leash=4500.0))
    far = (-5481.0, -3692.0, 0.0)

    async def no_walk(*_a, **_k):
        return "no keys"

    async def where():
        return far

    async def no_sleep(*_a):
        return None

    monkeypatch.setattr(navigate, "walk_in_zone", no_walk)
    monkeypatch.setattr(seeds.blackbox, "event", lambda *a, **k: None)
    monkeypatch.setattr(seeds.asyncio, "sleep", no_sleep)
    farm.where = where
    asyncio.run(farm.settle(far))
    assert farm.home is None and farm.camp_fails == 1
