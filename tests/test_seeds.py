"""The Savarstaad seed mode: who to pull, when to recover, when to stop."""

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
