"""The day plan: dance while energy can pay, then farm until stopped."""

from wiz101_auto.dayplan import Snapshot, choose, fishing_quest, reagent_from_quest
from wiz101_auto.petplan import note_equipped, pet_failed, stats_mentioned, stats_perfect, talents_mentioned


def _snap(**kw):
    # Rank 3 and no seeds, so garden care does not steal a pet test.
    base = dict(
        energy=50, energy_max=50, gold=250_000, level=60,
        pet_kind="ghulture", pet_stage="baby",
        gardening_rank=3, couch_potato_seeds=0,
    )
    base.update(kw)
    return Snapshot(**base)


def test_energy_that_can_pay_dances_and_does_not_hold_any_back_for_plants():
    decision = choose(_snap(plants_need_care=True, fishing=True, reagent="scrap iron"))
    assert decision.action == "dance"
    assert decision.spend
    assert decision.games == 0
    assert decision.energy_floor == 0


def test_a_teen_miss_still_dances_when_the_globe_can_pay():
    decision = choose(_snap(
        pet_stage="teen", manifested=("spell-proof",), energy=4,
        base_kind="ghulture", base_stage="adult",
        base_talents=("death-dealer", "pain-giver"),
        base_stats={"strength": 240, "intellect": 240, "agility": 250, "will": 250, "power": 240},
    ))
    assert decision.action == "dance"
    assert decision.spend


def test_an_empty_globe_farms_seeds_until_more_than_20_couch_potatoes():
    seeds = choose(_snap(
        pet_stage="adult", energy=0, couch_potato_seeds=14, couch_potatoes=20,
    ))
    assert seeds.action == "gold"
    assert seeds.target == "splithoof"
    gold = choose(_snap(pet_stage="adult", energy=0, couch_potatoes=21))
    assert gold.action == "gold"
    assert gold.target == "halfang"


def test_any_body_dances_while_energy_can_pay():
    perfect = choose(_snap(
        pet_stage="adult",
        manifested=("death-dealer", "death-giver"),
        stats={"strength": 255, "intellect": 250, "agility": 260, "will": 260, "power": 250},
        energy=40, energy_max=50,
    ))
    assert perfect.action == "dance"
    assert perfect.spend
    worse = choose(_snap(
        pet_stage="baby", energy=0,
        stats={"strength": 200, "intellect": 200, "agility": 200, "will": 200, "power": 200},
        base_kind="ghulture", base_stage="adult",
        base_stats={"strength": 230, "intellect": 230, "agility": 240, "will": 240, "power": 230},
        base_talents=("death-dealer",),
    ))
    assert worse.action == "gold"
    assert worse.target == "splithoof"


def test_a_confirm_pet_dances_instead_of_being_sent_to_hatch():
    decision = choose(_snap(pet_stage="ancient", confirm_mega=True, manifested=("death-dealer",)))
    assert decision.action == "dance"
    assert decision.spend


def test_garden_fishing_and_the_barn_are_not_chosen():
    ranking = choose(_snap(
        pet_stage="adult", pet_kind="bloodbat", energy=0,
        gardening_rank=1, boom_shroom_seeds=3,
    ))
    assert ranking.action == "gold"
    assert ranking.target == "splithoof"
    barn = choose(_snap(
        pet_stage="mega", confirm_mega=True, energy=0,
        manifested=("death-dealer", "death-giver", "pain-giver", "mighty", "armor-breaker"),
        gardening_rank=3, couch_potato_seeds=2, gold=130_000,
    ))
    assert barn.action == "gold"


def test_chores_farms_when_energy_is_gone_even_if_a_reagent_was_asked():
    busy = choose(_snap(reagent="scrap iron"))
    assert busy.action == "dance"
    idle = choose(_snap(
        pet_stage="adult", pet_kind="bloodbat", energy=0,
        reagent="scrap iron", red_barn=True,
    ))
    assert idle.action == "gold"
    assert idle.target == "splithoof"
    asked = choose(_snap(mode="reagents", reagent="scrap iron", energy=0))
    assert asked.action == "reagent"
    assert asked.target == "scrap iron"


def test_a_full_bar_keeps_dancing_past_one_game():
    decision = choose(_snap(
        pet_kind="bloodbat", pet_stage="adult", energy=50, energy_max=50, red_barn=True,
    ))
    assert decision.action == "dance"
    assert decision.spend
    assert decision.games == 0


def test_a_miss_drops_back_to_the_previous_base():
    prior = {
        "kind": "ghulture", "stage": "adult", "talents": ["death-dealer"],
        "stats": {"strength": 230, "intellect": 230, "agility": 240, "will": 240, "power": 230},
    }
    current = {
        "kind": "ghulture", "stage": "adult", "talents": ["death-dealer", "pain-giver"],
        "stats": {"strength": 250, "intellect": 250, "agility": 250, "will": 250, "power": 250},
    }
    saved = note_equipped({**current, "base": prior})
    assert saved["base"]["stats"]["strength"] == 250
    assert saved["prior_base"]["stats"]["strength"] == 230
    failed = dict(saved)
    failed["talents"] = ["death-dealer", "pain-giver", "spell-proof"]
    failed["stage"] = "ancient"
    reverted = note_equipped(failed)
    assert reverted["base"]["stats"]["strength"] == 230


def test_fishing_does_not_run_and_a_payable_globe_still_dances():
    assert choose(_snap(fishing=True)).action == "dance"
    idle = choose(_snap(
        pet_stage="adult", pet_kind="bloodbat", fishing=True,
        gardening_rank=3, couch_potato_seeds=0, energy=8,
    ))
    # Adult dance costs 6, so 8 still pays for a game.
    assert idle.action == "dance"
    assert idle.spend


def test_seeds_stay_splithoof_under_the_level_gate_and_an_unread_bar_still_farms():
    low = _snap(level=10, couch_potato_seeds=0, pet_kind="bloodbat", energy=0, gardening_rank=1)
    assert choose(low).target == "splithoof"
    unread = _snap(energy=None, pet_kind="bloodbat", red_barn=True)
    assert choose(unread).action == "gold"
    assert choose(unread).target == "splithoof"
    pet_only = choose(_snap(mode="pet", energy=0))
    assert pet_only.action == "wait"


def test_quest_text_picks_a_reagent_and_a_fishing_goal():
    assert reagent_from_quest("Collect Scrap Iron in Chelsea Court (0 of 5)") == "Scrap Iron"
    assert reagent_from_quest("Defeat 3 Rattlebones") is None
    assert fishing_quest("Catch 2 Death Fish in Triton Avenue")
    assert not fishing_quest("Talk to Duncan")


def test_talent_text_and_perfect_stats():
    assert talents_mentioned(["Your pet learned Death-Dealer!"]) == ["death-dealer"]
    assert pet_failed(talents_mentioned(["learned Spell-Proof"]))
    assert not pet_failed(["death-dealer", "mighty"])
    assert stats_perfect({"strength": 250, "intellect": 250, "agility": 255, "will": 260, "power": 250})
    assert not stats_perfect({"strength": 200, "intellect": 250, "agility": 260, "will": 260, "power": 250})
    text = ["Strength: 255", "Intellect 250", "Agility 260", "Will 260", "Power 250"]
    assert stats_mentioned(text)["strength"] == 255
    assert stats_mentioned(["Strength 255"]) == {}
