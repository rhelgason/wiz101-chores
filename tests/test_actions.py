"""Garden, hatch, and farm decisions that do not need the game."""

import asyncio
import math
import time
from types import SimpleNamespace

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
    assert (22112.1, 1220.5, 601.3) not in CAMPS["seeds"]
    assert (22112.1, 1220.5, 601.3) not in CAMPS["splithoof"]


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


def test_a_stuck_walk_skips_the_unsafe_teleport_in_wine(monkeypatch):
    from wiz101_auto import farm

    class Body:
        async def position(self):
            return SimpleNamespace(x=0, y=0, z=0)

    class Client:
        body = Body()

        async def in_battle(self):
            return False

    teleports = []

    async def hold_forward(*_args):
        return 0.0, 200.0

    async def teleport(*_args, **_kwargs):
        teleports.append(True)

    monkeypatch.setattr(farm, "_hold_forward", hold_forward)
    monkeypatch.setattr(farm, "_teleport_to", teleport)
    monkeypatch.delenv("WINEPREFIX", raising=False)
    asyncio.run(farm._walk_to(Client(), (200.0, 0.0, 0.0), "windows-test"))
    assert teleports == [True]

    teleports.clear()
    monkeypatch.setenv("WINEPREFIX", "/wine/bottle")
    asyncio.run(farm._walk_to(Client(), (200.0, 0.0, 0.0), "wine-test"))
    assert teleports == []


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
    assert fight_visible(False, 7, set()) is False  # 7 card windows stay visible out of battle (10-10 19:36)
    assert fight_visible(False, 0, {"CombatantControl"}) is True
    assert fight_visible(False, 0, {"Focus"}) is False
    assert fight_visible(None, 1, set()) is False
    missed = phase_disagrees(False, 2, {"DoneWindow"})
    assert "cards=2" in missed and "DoneWindow" in missed
    assert phase_disagrees(True, 2, {"DoneWindow"}) == ""


def card(spell=None, castable=True, keep=False, drain=False):
    return {"spell": spell, "castable": castable, "keep": keep, "drain": drain, "name": spell or "junk"}


def test_round_plan_empower_then_scarecrow():
    from wiz101_auto.farm import plan_round

    hand = [card("scarecrow"), card("empower")]
    assert plan_round(hand, []) == ("cast", 1)
    assert plan_round(hand, ["empower"]) == ("cast", 0)


def test_round_plan_does_not_hold_scarecrow_for_an_empower_without_pips():
    from wiz101_auto.farm import plan_round

    assert plan_round([card("empower", castable=False), card("scarecrow")], []) == ("cast", 1)


def test_round_plan_digs_for_scarecrow_but_keeps_treasure_cards_and_drains():
    """10-10 19:53: Empower was cast, Scarecrow never drawn, and it passed 16 rounds."""
    from wiz101_auto.farm import plan_round

    hand = [card(), card("empower"), card(keep=True), card(drain=True), card()]
    assert plan_round(hand, ["empower"]) == ("discard", [0, 1, 4])
    assert plan_round(hand, []) == ("cast", 1)
    waiting = [card("scarecrow", castable=False), card()]
    assert plan_round(waiting, ["empower"]) == ("pass",)  # in hand, waiting for pips: no discard


def test_round_plan_drains_when_hurt_unless_scarecrow_can_go():
    from wiz101_auto.farm import plan_round

    assert plan_round([card("empower")], [], heal=True) == ("drain",)
    assert plan_round([card("scarecrow")], ["empower"], heal=True) == ("cast", 0)


def test_empower_is_found_under_an_internal_name():
    assert combo_spell("Spell_Empower") == "empower"
    assert combo_spell("Empowerment") is None


def test_ship_of_fools_goes_first_with_colossal():
    from wiz101_auto.farm import combo_spell, plan_round

    assert combo_spell("Ship of Fools") == "ship"
    assert combo_spell("Colossal") == "colossal"
    hand = [card("scarecrow"), card("empower"), {"spell": "ship", "castable": True}, card("colossal")]
    assert plan_round(hand, []) == ("enchant", 3, 2)
    enchanted = [card("empower"), {"spell": "ship", "castable": True, "enchanted": True}]
    assert plan_round(enchanted, []) == ("cast", 1)
    assert plan_round([card("empower"), {"spell": "ship", "castable": True}], []) == ("cast", 1)


def test_ship_of_fools_waits_for_pips_then_the_old_plan_follows():
    from wiz101_auto.farm import plan_round

    waiting = [card("empower"), card("scarecrow"), {"spell": "ship", "castable": False}]
    assert plan_round(waiting, []) == ("pass",)
    assert plan_round(waiting, [], heal=True) == ("drain",)
    assert plan_round(waiting, ["ship"]) == ("cast", 0)  # the ship went and someone lived
    assert plan_round([card("empower"), card("scarecrow")], []) == ("cast", 0)  # no ship drawn


def test_digging_for_scarecrow_keeps_ship_and_colossal():
    from wiz101_auto.farm import plan_round

    hand = [card(), {"spell": "ship", "castable": True}, card("colossal")]
    assert plan_round(hand, ["ship", "empower"]) == ("discard", [0])
