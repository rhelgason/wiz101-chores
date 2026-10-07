"""Garden, hatch, and farm decisions that do not need the game."""

import math
import time

from wiz101_auto.farm import (
    CAMPS,
    COMBO,
    FIGHT_WAIT_POLLS,
    GROUPS,
    JOIN_HOLD,
    NORTHGUARD_GATE,
    already_there,
    approach_point,
    best_mob,
    closed_detail,
    combo_spell,
    fight_visible,
    is_death_heal,
    leave_alone,
    mob_matches,
    mob_rank,
    nearest_other,
    needs_heal,
    next_combo,
    note_step,
    phase_disagrees,
    pull_destination,
    pull_plan,
    resting,
    teleport_allowed,
    too_hurt_to_pull,
    unstick_allowed,
    walk_seconds,
    walk_stuck,
)
from wiz101_auto.fishing import bite_ready, can_cast
from wiz101_auto.garden import count_potatoes, count_seeds, loot_mentioned_potato, need_spell, spell_caption
from wiz101_auto.hatch import next_hatch_click, shows_perfect_ghulture

PERFECT = [
    "Ghulture",
    "Death-Dealer",
    "Death-Giver",
    "Pain-Giver",
    "Mighty",
    "Armor Breaker",
    "Hatch",
    "Speed Hatch",
]


def test_a_bite_is_the_hooked_fish_and_a_low_globe_does_not_cast():
    assert bite_ready(["neutral", "unknown2"])
    assert bite_ready([2])
    assert not bite_ready(["neutral", "escaped"])
    assert can_cast(2)
    assert can_cast(None)
    assert not can_cast(1)


def test_seeds_are_counted_and_snacks_are_not():
    assert count_seeds(["Couch Potato Seed", "Boom Shroom Seed", "Couch Potato"]) == (1, 1)
    assert count_seeds(["Seed_CouchPotato", "Fancy Yogurt"]) == (1, 0)


def test_couch_potatoes_include_stacks_and_snacks_and_switch_past_20():
    assert count_potatoes(["Couch Potato Seed", "Fancy Yogurt"]) == 1
    assert count_potatoes([("Couch Potato Seed", 21), "Couch Potato"]) == 22
    assert count_potatoes(["Seed_CouchPotato x20"]) == 20
    assert loot_mentioned_potato(["Couch Potato Seed"])
    assert not loot_mentioned_potato(["Collect Couch Potato Seeds in Grizzleheim"])
    assert not loot_mentioned_potato(["Couch Potato"])


def test_a_death_drain_is_a_heal_and_sacrifice_is_not():
    assert is_death_heal("Ghoul")
    assert is_death_heal("Vampire", ["steal_health"])
    assert is_death_heal("Treasure", ["SpellEffects.steal_health"])
    assert not is_death_heal("Sacrifice", ["steal_health"])
    assert not is_death_heal("Scarecrow")
    assert needs_heal(0.59)
    assert not needs_heal(0.60)
    assert not needs_heal(None)
    assert too_hurt_to_pull(0.2)
    assert not too_hurt_to_pull(None)


def test_a_plant_need_picks_the_short_spell_caption():
    texts = ["Needs Water", "The plant needs water soon.", "Small Water Spell"]
    assert need_spell(texts) == "water"
    assert spell_caption(texts, "water") == "Small Water Spell"
    assert need_spell(["a healthy couch potato"]) is None


def test_hatch_confirms_only_the_perfect_page_and_never_the_speed_button():
    assert shows_perfect_ghulture(PERFECT)
    assert next_hatch_click(PERFECT) == "Hatch"
    assert next_hatch_click(["Browse Pets", "Speed Hatch"]) == "Browse Pets"
    assert next_hatch_click(["Ghulture", "Death-Dealer", "Hatch"]) is None
    assert next_hatch_click(["Hatch Now"]) is None


def test_a_farm_mob_matches_its_name():
    assert mob_matches("Splithoof_Barbarian", ("splithoof", "barbarian"))
    assert mob_matches("Splithoof Barbarian", ("troubled", "splithoof barbarian"))
    assert mob_matches("Troubled Warrior", ("troubled", "splithoof barbarian"))
    assert not mob_matches("Splithoof Rager", ("troubled", "splithoof barbarian"))
    assert not mob_matches("Boar", ("halfang",))


def test_a_pull_lands_beside_the_mob_and_a_miss_is_left_alone():
    mob = (1000.0, 0.0, 5.0)
    assert pull_destination((0.0, 0.0, 0.0), mob) == mob
    land = pull_destination((0.0, 0.0, 0.0), (9000.0, 0.0, 5.0))
    assert abs(math.hypot(land[0] - 9000.0, land[1]) - 800.0) < 1.0
    beside = approach_point((0.0, 0.0, 0.0), mob, 800.0)
    assert abs(math.hypot(beside[0] - 1000.0, beside[1]) - 800.0) < 1.0
    assert pull_plan(100) == "walk"
    assert pull_plan(800) == "walk"
    assert pull_plan(4000) == "walk"
    assert pull_plan(9000) == "teleport"
    groups = (("troubled", "splithoof barbarian"), ("splithoof",))
    rows = [
        (None, "Splithoof Barbarian", 9000.0, 5),
        (None, "Splithoof Barbarian", 300.0, 9),
        (None, "Tree", 10.0, 1),
    ]
    assert nearest_other(rows, 5, groups) == 300.0
    assert nearest_other([(None, "Tree", 10.0, 1)], 5, groups) is None
    assert teleport_allowed(9000, None, known=True)
    assert not teleport_allowed(9000, None, known=False)
    assert not teleport_allowed(9000, 300, known=True)
    assert teleport_allowed(9000, 1500, known=True)
    assert not teleport_allowed(800, None, known=True)
    note_step("step=teleport why=outside gid=5")
    assert "step=teleport" in closed_detail()
    assert abs(walk_seconds(580) - 1.0) < 0.01
    assert abs(walk_seconds(580, 40) - (1.0 / 1.4)) < 0.01
    assert walk_seconds(0) == 0.0
    assert walk_stuck(0, 200)
    assert not walk_stuck(180, 200)
    assert not walk_stuck(0, 20)
    leave_alone(77, 20.0)
    assert resting(77)
    assert not resting(77, now=time.monotonic() + 60.0)
    assert not resting(0)


def test_the_zone_path_is_savarstaad_and_an_unplated_boar_is_a_fallback():
    assert already_there("Grizzleheim/GH_Hero", "seeds")
    assert already_there("Savarstaad Pass", "seeds")
    assert not already_there("Grizzleheim/GH_MainHub", "seeds")
    assert not already_there("", "seeds")
    groups = GROUPS["seeds"]
    assert mob_rank("GH-Boar-1-R3", groups) == 1
    assert mob_rank("GH-Boar-1-R3 Splithoof Barbarian", groups) == 0
    assert mob_rank("Splithoof Rager", groups) == 1
    labels = [("GH-Boar-1-R3", 10.0), ("Splithoof Barbarian", 80.0)]
    assert best_mob(labels, groups) == 1
    assert NORTHGUARD_GATE not in CAMPS["seeds"]
    assert NORTHGUARD_GATE not in CAMPS["splithoof"]


def test_a_closer_rager_loses_to_the_named_barbarian():
    groups = (("troubled", "splithoof barbarian"), ("splithoof",))
    labels = [("Splithoof Rager", 10.0), ("Boar Splithoof Barbarian", 80.0)]
    assert best_mob(labels, groups) == 1
    assert best_mob([("Tree", 1.0)], groups) is None
    assert len(CAMPS["seeds"]) > 4
    assert FIGHT_WAIT_POLLS * 0.4 >= 14.0


def test_no_teleport_while_a_nearby_fight_may_be_joining():
    assert unstick_allowed(None) is True
    assert unstick_allowed(0.0) is False
    assert unstick_allowed(JOIN_HOLD - 0.1) is False
    assert unstick_allowed(JOIN_HOLD) is True
    assert unstick_allowed(30.0, fighting=True) is False


def test_savarstaad_names_the_three_combo_cards():
    assert combo_spell("Death Blade") == "deathblade"
    assert combo_spell("Empower") == "empower"
    assert combo_spell("Scarecrow Item Card") == "scarecrow"
    assert combo_spell("Empowered") is None
    assert combo_spell("Ghoul") is None


def test_seeds_combo_is_empower_then_scarecrow():
    assert COMBO == ("empower", "scarecrow")
    hand = {"deathblade", "empower", "scarecrow"}
    assert next_combo([], hand, hand) == "empower"
    assert next_combo(["empower"], hand, hand) == "scarecrow"
    assert next_combo([], {"empower"}, {"deathblade", "empower"}) == "empower"
    assert next_combo([], set(), {"empower", "scarecrow"}) is None
    assert next_combo(["empower"], {"scarecrow"}, {"empower", "scarecrow"}) == "scarecrow"
    assert next_combo(["empower", "scarecrow"], {"scarecrow"}, hand) == "scarecrow"
    assert next_combo([], set(), set()) is None


def test_a_visible_battle_counts_when_the_phase_says_it_ended():
    assert fight_visible(False, 0, set()) is False
    assert fight_visible(True, 0, set()) is True
    assert fight_visible(False, 3, set()) is True
    assert fight_visible(False, 0, {"CombatantControl"}) is True
    assert fight_visible(False, 0, {"Focus"}) is False
    assert fight_visible(None, 1, set()) is True
    missed = phase_disagrees(False, 2, {"DoneWindow"})
    assert "cards=2" in missed and "DoneWindow" in missed
    assert phase_disagrees(True, 2, {"DoneWindow"}) == ""
