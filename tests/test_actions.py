"""Garden, hatch, and farm decisions that do not need the game."""

from wiz101_auto.farm import mob_matches
from wiz101_auto.fishing import bite_ready, can_cast
from wiz101_auto.garden import count_seeds, need_spell, spell_caption
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
    assert not mob_matches("Boar", ("halfang",))
