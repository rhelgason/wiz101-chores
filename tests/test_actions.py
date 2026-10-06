"""Garden, hatch, and farm decisions that do not need the game."""

import math
import time

from wiz101_auto.farm import (
    CAMPS,
    approach_point,
    best_mob,
    combo_spell,
    is_death_heal,
    leave_alone,
    mob_matches,
    needs_heal,
    next_combo,
    resting,
    too_hurt_to_pull,
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
    land = approach_point((0.0, 0.0, 0.0), (1000.0, 0.0, 5.0))
    assert abs(math.hypot(land[0] - 1000.0, land[1]) - 450.0) < 1.0
    inside = approach_point((1000.0, 0.0, 5.0), (1000.0, 50.0, 5.0))
    assert abs(math.hypot(inside[0] - 1000.0, inside[1] - 50.0) - 450.0) < 1.0
    leave_alone(77, 20.0)
    assert resting(77)
    assert not resting(77, now=time.monotonic() + 60.0)
    assert not resting(0)


def test_a_closer_rager_loses_to_the_named_barbarian():
    groups = (("troubled", "splithoof barbarian"), ("splithoof",))
    labels = [("Splithoof Rager", 10.0), ("Boar Splithoof Barbarian", 80.0)]
    assert best_mob(labels, groups) == 1
    assert best_mob([("Tree", 1.0)], groups) is None
    assert len(CAMPS["seeds"]) > 4


def test_savarstaad_names_the_three_combo_cards():
    assert combo_spell("Death Blade") == "deathblade"
    assert combo_spell("Empower") == "empower"
    assert combo_spell("Scarecrow Item Card") == "scarecrow"
    assert combo_spell("Empowered") is None
    assert combo_spell("Ghoul") is None


def test_savarstaad_waits_for_the_next_combo_card():
    hand = {"deathblade", "empower", "scarecrow"}
    assert next_combo([], hand, hand) == "deathblade"
    assert next_combo(["deathblade"], hand, hand) == "empower"
    assert next_combo(["deathblade", "empower"], hand, hand) == "scarecrow"
    assert next_combo([], {"empower"}, {"deathblade", "empower"}) is None
    assert next_combo([], {"empower"}, {"empower"}) == "empower"
    assert next_combo(["deathblade", "empower", "scarecrow"], {"scarecrow"}, hand) == "scarecrow"
    assert next_combo([], set(), set()) is None
