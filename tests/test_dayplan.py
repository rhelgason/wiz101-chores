"""The day plan: energy first, then the thing that was asked for, then gold."""

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


def test_a_full_bar_trains_the_baby_and_holds_energy_for_a_needy_plant():
    decision = choose(_snap(plants_need_care=True))
    assert decision.action == "dance"
    assert decision.train_until == "adult"
    assert decision.energy_floor == 10


def test_a_teen_miss_is_not_trained_on_to_adult():
    decision = choose(_snap(
        pet_stage="teen", manifested=("spell-proof",), energy=4,
        base_kind="ghulture", base_stage="adult",
        base_talents=("death-dealer", "pain-giver"),
        base_stats={"strength": 240, "intellect": 240, "agility": 250, "will": 250, "power": 240},
    ))
    assert decision.action == "hatch"
    assert "do not hatch this body" in decision.why
    assert "kiosk" in decision.why


def test_a_clean_adult_hatches_and_a_poor_one_goes_for_gold():
    ready = choose(_snap(
        pet_stage="adult", manifested=("death-dealer", "pain-giver"),
        energy=20, energy_max=50,
    ))
    assert ready.action == "hatch"
    poor = choose(_snap(
        pet_stage="adult", manifested=("death-dealer",), gold=10_000,
        energy=0, couch_potato_seeds=14,
    ))
    assert poor.action == "gold"
    assert poor.target == "halfang"  # seeds are stocked and the wizard is past 40


def test_perfect_stats_are_trained_out_and_a_worse_body_is_not():
    perfect = choose(_snap(
        pet_stage="adult",
        manifested=("death-dealer", "death-giver"),
        stats={"strength": 255, "intellect": 250, "agility": 260, "will": 260, "power": 250},
        energy=40, energy_max=50,
    ))
    assert perfect.action == "dance"
    assert perfect.train_until == "mega"
    base_stats = {"strength": 230, "intellect": 230, "agility": 240, "will": 240, "power": 230}
    worse = choose(_snap(
        pet_stage="baby", energy=20, energy_max=50,
        stats={"strength": 200, "intellect": 200, "agility": 200, "will": 200, "power": 200},
        base_kind="ghulture", base_stage="adult", base_stats=base_stats,
        base_talents=("death-dealer",),
    ))
    assert worse.action == "hatch"
    assert "kiosk" in worse.why
    closer = choose(_snap(
        pet_stage="baby", energy=20, energy_max=50,
        stats={"strength": 250, "intellect": 250, "agility": 250, "will": 260, "power": 250},
        base_kind="ghulture", base_stage="adult", base_stats=base_stats,
        base_talents=("death-dealer",),
    ))
    assert closer.action == "dance"
    assert closer.train_until == "adult"


def test_the_confirm_pet_is_trained_to_mega_instead_of_hatched():
    decision = choose(_snap(pet_stage="ancient", confirm_mega=True, manifested=("death-dealer",)))
    assert decision.action == "dance"
    assert decision.train_until == "mega"


def test_couch_potatoes_wait_for_rank_and_the_barn_waits_for_them():
    ranking = choose(_snap(pet_stage="adult", pet_kind="bloodbat", gardening_rank=1, boom_shroom_seeds=3))
    assert ranking.action == "garden"
    assert "boom shroom" in ranking.why
    barn = choose(_snap(
        pet_stage="mega", confirm_mega=True, energy=0,
        manifested=("death-dealer", "death-giver", "pain-giver", "mighty", "armor-breaker"),
        gardening_rank=3, couch_potato_seeds=2, gold=130_000,
    ))
    assert barn.action == "buy_barn"


def test_scrap_iron_is_gathered_only_when_energy_has_nothing_to_do():
    busy = choose(_snap(reagent="scrap iron"))
    assert busy.action == "dance"
    # The barn is already owned. At rank 3 and 250,000 gold it would be bought first.
    idle = choose(_snap(
        pet_stage="adult", pet_kind="bloodbat", energy=0,
        reagent="scrap iron", red_barn=True,
    ))
    assert idle.action == "reagent"
    assert idle.target == "scrap iron"


def test_a_full_bar_is_spent_when_nothing_else_can_use_it():
    decision = choose(_snap(
        pet_kind="bloodbat", pet_stage="adult", energy=50, energy_max=50, red_barn=True,
    ))
    assert decision.action == "dance"
    assert decision.games == 1


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


def test_fishing_loses_to_the_pet_and_runs_when_the_garden_is_idle():
    assert choose(_snap(fishing=True)).action == "dance"
    idle = choose(_snap(
        pet_stage="adult", pet_kind="bloodbat", fishing=True,
        gardening_rank=3, couch_potato_seeds=0, energy=8,
    ))
    assert idle.action == "fish"


def test_seeds_come_before_halfang_and_an_unread_bar_does_not_start_a_fight():
    low = _snap(level=25, couch_potato_seeds=0, pet_kind="bloodbat", energy=0, gardening_rank=1)
    assert choose(low).target == "splithoof"
    unread = _snap(energy=None, pet_kind="bloodbat", red_barn=True)
    assert choose(unread).action == "wait"


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
