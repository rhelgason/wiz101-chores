"""Fleeing a seeds duel the wizard can only lose, and refilling before the next pull.

On 10-11 the wizard ran out of mana, passed with Scarecrow uncastable for
ten rounds, and was defeated about 15 times; client.stats never said low.
"""

import asyncio
from types import SimpleNamespace

from wiz101_auto import farm, seeds
from wiz101_auto.farm import flee_reason, pips_up, scarecrow_dry, vitals_text
from wiz101_auto.seeds import recovered, stats_contradict


def card(name, castable, spell=None, drain=False):
    return {"name": name, "spell": spell, "castable": castable, "keep": False, "drain": drain}


DRY_HAND = [
    card("Scarecrow", False, "scarecrow"),
    card("Wraith", False, drain=True),
    card("Deathblade", True, "deathblade"),
    card("Mass Feint", True),
]


def test_scarecrow_dry_needs_it_held_and_dead():
    assert scarecrow_dry(DRY_HAND)
    assert not scarecrow_dry([card("Scarecrow", True, "scarecrow")])
    assert not scarecrow_dry([card("Deathblade", True, "deathblade")])


def test_read_mana_below_scarecrow_flees_at_once():
    vitals = {"mana": 3, "max_mana": 412, "pips": 2, "power_pips": 0}
    assert flee_reason(DRY_HAND, vitals, 1) == "mana"


def test_early_rounds_with_mana_do_not_flee():
    vitals = {"mana": 300, "max_mana": 412, "pips": 2, "power_pips": 1}
    assert flee_reason(DRY_HAND, vitals, 5) == ""


def test_unread_mana_flees_when_pips_are_up_twice():
    vitals = {"pips": 5, "power_pips": 1}
    assert pips_up(vitals) == 7
    assert flee_reason(DRY_HAND, vitals, 1) == ""  # once may be a stun
    assert flee_reason(DRY_HAND, vitals, 2) == "mana"
    assert flee_reason(DRY_HAND, {"pips": 4, "power_pips": 0}, 6) == ""  # still saving pips


def test_nothing_read_flees_after_many_dry_rounds():
    assert flee_reason(DRY_HAND, {}, farm.DRY_BLIND - 1) == ""
    assert flee_reason(DRY_HAND, {}, farm.DRY_BLIND) == "mana"


def test_low_health_flees_only_with_no_heal_castable():
    low = {"health": 200, "max_health": 1000}
    assert flee_reason(DRY_HAND, low, 0) == "health"
    healing = [card("Wraith", True, drain=True)]
    assert flee_reason(healing, low, 0) == ""
    assert flee_reason(DRY_HAND, {"health": 900, "max_health": 1000}, 0) == ""


def test_vitals_text():
    assert vitals_text({}) == "health ?, mana ?, pips ?"
    assert vitals_text({"health": 5, "max_health": 9, "mana": 1, "pips": 3}) == (
        "health 5/9, mana 1/?, pips 3+0p"
    )


class Member:
    def __init__(self, **values):
        self.values = values

    def __getattr__(self, name):
        if name not in self.values:
            raise AttributeError(name)

        async def read():
            return self.values[name]

        return read


class Card:
    def __init__(self, name, castable):
        self._name, self._castable = name, castable

    async def name(self):
        return self._name

    async def display_name(self):
        return self._name

    async def is_castable(self):
        return self._castable

    async def is_treasure_card(self):
        return False

    async def is_item_card(self):
        return False


class Handler:
    def __init__(self, cards, member):
        self.cards, self.member, self.passed = cards, member, 0

    async def get_cards(self):
        return self.cards

    async def get_client_member(self):
        return self.member

    async def pass_button(self):
        self.passed += 1


def test_cast_combo_returns_flee_when_dry():
    member = Member(health=900, max_health=1000, mana=2, max_mana=400, normal_pips=7, power_pips=0)
    handler = Handler([Card("Scarecrow", False), Card("Deathblade", True)], member)
    memo = {}
    step = asyncio.run(farm._cast_combo(handler, ["empower"], memo=memo))
    assert step == "flee:mana"
    assert memo["dry"] == 1 and handler.passed == 0


def test_cast_combo_drops_a_misread_max():
    member = Member(health=900, max_health=0, mana=50, max_mana=10)
    vitals = asyncio.run(farm._own_vitals(Handler([], member)))
    assert "health" not in vitals and "mana" not in vitals


def test_strict_recovery_does_not_take_unread_as_full():
    assert recovered("mana", None, None)
    assert not recovered("mana", None, None, strict=True)
    assert recovered("mana", None, 0.9, strict=True)


def test_stats_that_say_full_after_running_dry_are_misread():
    assert stats_contradict("mana", 1.0, 0.95)
    assert not stats_contradict("mana", 1.0, 0.05)
    assert not stats_contradict("mana", None, None)
    assert stats_contradict("health", 0.9, None)


def farm_for_test():
    controller = SimpleNamespace(deaths=[], record_death=lambda zone: controller.deaths.append(zone))
    cfg = SimpleNamespace(
        rest_minutes=4.0,
        northguard_trips=True,
        sell=True,
        leash=4500.0,
        northguard_full=0.98,
        northguard_minutes=10.0,
    )
    return seeds.SeedFarm(client=None, controller=controller, task={}, cfg=cfg), controller


def test_owe_stops_trusting_stats_that_call_an_empty_globe_full():
    farm_, _ = farm_for_test()

    async def ratios():
        return 1.0, 1.0

    farm_.ratios = ratios
    asyncio.run(farm_.owe("mana", "fled fight 3, out of mana"))
    assert farm_.owed == ["mana"]
    assert not farm_.trust_stats


def test_a_defeat_owes_both_globes():
    farm_, controller = farm_for_test()
    farm_.owed = ["mana"]
    farm_.defeated()
    assert farm_.owed == ["mana", "health"]
    assert controller.deaths == [seeds.NORTHGUARD]


def test_hud_globe_text_over_the_duel_maximum():
    from wiz101_auto.seeds import hud_ratio

    assert hud_ratio("2744", 2744) == 1.0
    assert hud_ratio("60", 120) == 0.5
    assert hud_ratio("6/120", None) == 0.05
    assert hud_ratio("1,372", 2744) == 0.5
    assert hud_ratio("60", None) is None  # no duel has given the maximum yet
    assert hud_ratio("", 120) is None
    assert hud_ratio("130", 120) == 1.0


def test_a_loading_screen_that_never_ends_is_reported_once(monkeypatch):
    events = []
    monkeypatch.setattr(seeds.blackbox, "event", lambda kind, **kw: events.append((kind, kw)))
    farm_, _ = farm_for_test()

    async def yes():
        return True

    async def zone():
        return seeds.SAVARSTAAD

    farm_.client = SimpleNamespace(is_loading=yes, zone_name=zone)
    asyncio.run(farm_.note_busy())
    assert events == []
    farm_.busy_since -= seeds.STUCK_BUSY + 1
    asyncio.run(farm_.note_busy())
    asyncio.run(farm_.note_busy())
    assert [k for k, _ in events] == ["needs_player"]
    assert events[0][1]["why"].startswith("stuck on a loading screen")


def test_crashwatch_words_a_stuck_loading_screen():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).parent.parent / "mac" / "crashwatch.py"
    spec = importlib.util.spec_from_file_location("crashwatch_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = module.player_message({"kind": "needs_player", "why": "stuck on a loading screen for 5 min"})
    assert text.startswith("The game is stuck on a loading screen")


def test_a_northguard_trip_fills_the_globe_not_just_to_ready():
    """10-12 night: trips left at 55-60% mana, ~6 min of fighting each."""
    from wiz101_auto.seeds import is_full

    assert not is_full(0.60, 0.98)
    assert is_full(0.99, 0.98)
    assert is_full(None, 0.98)
    assert not is_full(None, 0.98, strict=True)


def test_northguard_tops_up_the_need_then_the_other_globe(monkeypatch):
    farm_, _ = farm_for_test()
    globes = {"health": 0.5, "mana": 0.2}
    walked = []

    async def ratios():
        return globes["health"], globes["mana"]

    async def zone():
        return seeds.NORTHGUARD

    async def checkpoint():
        return None

    async def wisp_spot(kind, taken):
        return (len(walked), (0.0, 0.0, 0.0))

    async def walk(client, place, *, why, controller, reach):
        kind = why.split()[0]
        walked.append(kind)
        globes[kind] = min(1.0, globes[kind] + 0.3)
        return "reached"

    async def no_sleep(_s):
        return None

    from wiz101_auto import navigate

    monkeypatch.setattr(navigate, "walk_in_zone", walk)
    monkeypatch.setattr(seeds.asyncio, "sleep", no_sleep)
    farm_.ratios, farm_.wisp_spot = ratios, wisp_spot
    farm_.client = SimpleNamespace(zone_name=zone)
    farm_.controller.checkpoint = checkpoint
    farm_.controller.allow_idle = lambda s: None
    farm_.controller.end_idle = lambda: None
    asyncio.run(farm_.gather("mana"))
    assert globes["mana"] >= 0.98 and globes["health"] >= 0.98
    assert walked[:4] == ["mana"] * 3 + ["health"]


def test_crashwatch_words_finished_hunts():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).parent.parent / "mac" / "crashwatch.py"
    spec = importlib.util.spec_from_file_location("crashwatch_for_hunts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    done = module.player_message({"kind": "needs_player", "why": "done with every hunt"})
    assert done.startswith("Every hunt")
    text = module.player_message({"kind": "needs_player", "why": "done with A; walk to the Bs and start"})
    assert text.startswith("The bot is done with A")


def test_the_seeds_step_reaches_its_round_method():
    """10-14: self.hunt (the current Hunt) hid the hunt() method; every step failed for ten hours."""
    farm_, _ = farm_for_test()
    assert callable(farm_.hunt_round)
    assert isinstance(farm_.hunt, seeds.Hunt)


def test_a_repeating_step_error_stops_the_run(monkeypatch):
    events = []
    monkeypatch.setattr(seeds.blackbox, "event", lambda kind, **kw: events.append((kind, kw)))

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(seeds.asyncio, "sleep", no_sleep)
    stopped = asyncio.Event()
    calls = []

    async def checkpoint():
        return None

    def stop(why):
        calls.append(why)
        stopped.set()

    controller = SimpleNamespace(stopped=stopped, checkpoint=checkpoint, stop=stop)
    farm_ = SimpleNamespace(hunts=[], summary=lambda: "nothing")
    tries = []

    async def step():
        tries.append(1)
        raise TypeError("'Hunt' object is not callable")

    farm_.step = step
    asyncio.run(seeds.run_steps(farm_, controller))
    assert len(tries) == seeds.SAME_ERROR_STOP
    assert calls and "not callable" in calls[0]
    assert ("needs_player" in [k for k, _ in events])


def test_finishing_a_hunt_moves_on_to_the_boars():
    farm_, _ = farm_for_test()
    farm_.hunts = seeds.hunts_from([{"creature": "Charmed Slave", "count": 2}, "seeds"])
    farm_.hunt = farm_.hunts[0]
    farm_.kills = 2
    asyncio.run(farm_.check_hunt())
    assert farm_.hunt.seeds and farm_.zone == seeds.SAVARSTAAD and farm_.home is None
    farm_.kills = 500
    asyncio.run(farm_.check_hunt())  # the seed farm runs until stopped
    assert farm_.hunt.seeds
