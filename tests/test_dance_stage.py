"""Which dance stage to play: the one that trains the stats furthest below their caps."""

from wiz101_auto.petplan import DANCE_STAGES, pick_stage, stage_score, trained_stats


def full(**now):
    caps = {"strength": 255, "intellect": 250, "agility": 260, "will": 260, "power": 250}
    return {stat: (now.get(stat, cap), cap) for stat, cap in caps.items()}


def test_the_stat_block_is_read_as_now_and_max():
    texts = ["Strength: 120/255", "Intellect", "40 / 250", "Agility: 200/260"]
    texts += ["Will: 90/260", "Power: 250/250"]
    assert trained_stats(texts) == {
        "strength": (120, 255),
        "intellect": (40, 250),
        "agility": (200, 260),
        "will": (90, 260),
        "power": (250, 250),
    }
    assert trained_stats(["Strength: 120/255"]) == {}  # partial: nothing
    assert trained_stats(["Strength 255", "Agility 260"]) == {}  # max stats alone are not a read


def test_low_intellect_picks_dragonspyre_and_low_strength_krokotopia():
    assert DANCE_STAGES[pick_stage("lowest", full(intellect=40), None)[0]][0] == "Dragonspyre"
    assert DANCE_STAGES[pick_stage("lowest", full(strength=30, agility=100), None)[0]][0] == "Krokotopia"
    assert DANCE_STAGES[pick_stage("lowest", full(agility=50), None)[0]][0] == "Wizard City"


def test_a_capped_stat_gains_nothing():
    assert stage_score({"agility": 4}, full()) == 0
    assert stage_score({"agility": 4}, full(agility=259)) > 0
    index, why = pick_stage("lowest", full(), 2)
    assert index == 3 and "cap" in why


def test_ties_take_turns():
    both = full(intellect=100, will=100, strength=100, agility=100)
    first = pick_stage("lowest", both, None)[0]
    assert pick_stage("lowest", both, first)[0] in range(5)


def test_without_a_read_it_rotates_and_config_can_fix_or_randomize():
    assert pick_stage("lowest", None, None)[0] == 0
    assert pick_stage("lowest", None, 4)[0] == 0
    assert pick_stage("rotate", full(intellect=1), 1)[0] == 2
    assert pick_stage("MooShu", None, None) == (3, "set in config")
    assert pick_stage("random", None, None, roll=0.99)[0] == 4
