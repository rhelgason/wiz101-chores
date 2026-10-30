"""Portal of Peril ride-along: the decisions that do not need the game."""

import datetime as dt

import pytest

from wiz101_auto import peril
from wiz101_auto.config import load_config
from wiz101_auto.farm import combo_spell, feint_up, plan_dungeon_round, plan_round
from wiz101_auto.peril import (
    GOLEM_GATE,
    box_kind,
    count_run,
    done_for_today,
    follow_plan,
    runs_on,
    sigil_choice,
    today,
)

GATE = GOLEM_GATE


def near(dx: float, dy: float = 0.0):
    return (GATE[0] + dx, GATE[1] + dy, GATE[2])


def card(spell, castable=True, **kw):
    return {"name": spell or "Card", "spell": spell, "castable": castable, **kw}


def test_mass_feint_is_its_own_card():
    assert combo_spell("Mass Feint") == "massfeint"
    assert combo_spell("Spell_MassFeint") == "massfeint"
    assert combo_spell("Feint") is None


def test_feint_first_then_empower_then_scarecrow():
    hand = [card("scarecrow"), card("empower"), card("massfeint")]
    done: list[str] = []
    order = []
    for _ in range(3):
        action = plan_dungeon_round(hand, done)
        assert action[0] == "cast"
        order.append(hand[action[1]]["spell"])
        done.append(hand[action[1]]["spell"])
    assert order == ["massfeint", "empower", "scarecrow"]


def test_feint_again_before_the_next_scarecrow():
    hand = [card("scarecrow"), card("massfeint")]
    done = ["massfeint", "empower", "scarecrow"]
    assert not feint_up(done)
    assert plan_dungeon_round(hand, done) == ("cast", 1)
    assert feint_up([*done, "massfeint"])
    assert plan_dungeon_round(hand, [*done, "massfeint"]) == ("cast", 0)


def test_uncastable_feint_does_not_hold_scarecrow():
    hand = [card("massfeint", castable=False), card("scarecrow")]
    assert plan_dungeon_round(hand, ["empower"]) == ("cast", 1)


def test_no_feint_in_hand_is_the_seeds_combo():
    hand = [card("empower"), card("scarecrow")]
    assert plan_dungeon_round(hand, []) == plan_round(hand, []) == ("cast", 0)


def test_dungeon_discards_keep_mass_feint():
    hand = [card("massfeint", castable=False), card(None), card("empower", castable=False)]
    assert plan_dungeon_round(hand, []) == ("discard", [1])


def test_dungeon_drain_when_hurt():
    assert plan_dungeon_round([card("massfeint")], [], heal=True) == ("drain",)


def test_sigil_by_name_first():
    rows = [
        ("Madam Magda", near(200)),
        ("Sigil_Generic", near(300)),
        ("PortalOfPeril_Sigil", near(900)),
    ]
    spot, why = sigil_choice(rows)
    assert spot == near(900) and "named sigil" in why


def test_sigil_nearest_magda_when_nothing_is_named():
    rows = [("Madam Magda", near(200)), ("Sigil_A", near(2400)), ("Sigil_B", near(350))]
    spot, why = sigil_choice(rows)
    assert spot == near(350) and "Magda" in why


def test_magda_alone_is_the_anchor_and_far_things_are_ignored():
    assert sigil_choice([("Madam Magda", near(100))])[0] == near(100)
    assert sigil_choice([("Peril_Sigil", near(20000))]) is None
    assert sigil_choice([]) is None


def test_box_kinds():
    assert box_kind("You do not have any Portal credits remaining. Come back tomorrow!") == "out"
    assert box_kind("You have no more Portal Passes.") == "out"
    assert box_kind("Enter the Portal of Peril? This will use 1 credit.") == "enter"
    assert box_kind("Are you sure you want to leave the dungeon?") == "leave"
    assert box_kind("Do you want to flee?") == "leave"
    assert box_kind("Quest Helper is not allowed here") == "other"


def test_follow_plan():
    me = (0.0, 0.0, 0.0)
    assert follow_plan(me, [(100.0, 0.0, 0.0)], [(2000.0, 0.0, 0.0)])[0] == "join"
    assert follow_plan(me, [(100.0, 0.0, 0.0)], [(9000.0, 0.0, 0.0)]) == ("stay", None)
    assert follow_plan(me, [(1000.0, 0.0, 0.0), (5000.0, 0.0, 0.0)], []) == ("follow", (1000.0, 0.0, 0.0))
    assert follow_plan(me, [], []) == ("lost", None)


def test_day_count_and_cap():
    data: dict = {}
    day = today(dt.datetime(2026, 10, 30, 9))
    assert runs_on(data, day) == 0
    assert count_run(data, day) == 1
    assert count_run(data, day) == 2
    assert not done_for_today(data, day, 0)
    assert done_for_today(data, day, 2)
    assert not done_for_today(data, "2026-10-31", 2)
    data["out"] = day
    assert done_for_today(data, day, 0)
    assert not done_for_today(data, "2026-10-31", 0)


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(peril, "STATE", tmp_path / "peril.json")
    assert peril.load_state() == {}
    peril.save_state({"days": {"2026-10-30": 3}})
    assert runs_on(peril.load_state(), "2026-10-30") == 3


def test_config_and_flags():
    cfg = load_config(None, {"mode": "peril", "peril.runs": 4})
    assert cfg.mode == "peril" and cfg.peril.runs == 4 and cfg.peril.claim
    with pytest.raises(ValueError):
        load_config(None, {"peril.runs": -1})
