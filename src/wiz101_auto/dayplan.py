"""What the bot should do next. Pure: no game client and no files.

The live loop dances while the green globe can pay for a game, then farms
until the wizard is stopped. Splithoof Barbarians drop couch potato seeds.
More than 20 couch potatoes switches that farm to Halfang. Garden, fishing,
and hatching stay in the module for later and are not chosen.

The reasons are the ones in PLAYBOOK.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .collect import REAGENTS, collect_item_name
from .petplan import hatch_text, project_finished, training_goal

DANCE_COST = {
    "baby": 2,
    "teen": 4,
    "adult": 6,
    "ancient": 8,
    "epic": 10,
    "mega": 12,
    "ultra": 14,
}

HATCH_RESERVE = 100_000  # gold kept back so a hatch is always affordable
BARN_GOLD = 125_000  # Red Barn Farm, bought for gold rather than crowns
BOOM_SEED_GOLD = 1_200  # one boom shroom seed from Farley
GARDEN_RESERVE = 10  # energy left untouched while a plant is showing a need
SEED_STOCK = 14  # a first couch-potato plot; a 69-plot comes after this works
CP_RANK = 3  # couch potatoes plant at Apprentice
HALFANG_LEVEL = 40  # Wintertusk gold boss, once seeds are stocked
GRIZZLEHEIM_LEVEL = 20
# More than this many couch potatoes (seeds or snacks) switches the live farm to Halfang.
POTATO_SWITCH = 20

# Display names. The gatherer resolves these through the zone list at runtime.
REAGENT_PLACES = {
    "scrap iron": ("Chelsea Court", "Knight's Court", "Newgate Prison"),
}

FARMS = {
    "splithoof": "Splithoof Barbarians in Boar Camp, Savarstaad Pass (couch potato seeds and vendor gold)",
    "halfang": "Halfang Bristlecrown in Vestrilund, Wintertusk (amulets that sell at the Bazaar)",
    "early-gold": "Grizzleheim is not open yet; no seed or Halfang run until level 20",
}


@dataclass(frozen=True)
class Snapshot:
    """Everything the choice depends on. Missing numbers stay None."""

    energy: int | None = None
    energy_max: int | None = None
    gold: int | None = None
    level: int | None = None
    pet_kind: str | None = None
    pet_stage: str | None = None
    manifested: tuple[str, ...] = ()
    stats: dict | None = None
    confirm_mega: bool = False  # this body is the one we take all the way to mega
    gardening_rank: int = 1
    red_barn: bool = False
    couch_potato_seeds: int = 0
    couch_potatoes: int = 0  # seeds and harvested potatoes, stacks included
    boom_shroom_seeds: int = 0
    plants_need_care: bool = False
    reagent: str | None = None  # asked for, or read off the tracked quest
    fishing: bool = False
    pet_training: bool = True
    mode: str = "chores"  # chores | pet | reagents
    # The owned ghulture closest to the kiosk perfect. Empty until one has been seen.
    base_kind: str | None = None
    base_stage: str | None = None
    base_talents: tuple[str, ...] = ()
    base_stats: dict | None = None
    game_cost: int | None = None  # the price the dance window last showed


@dataclass(frozen=True)
class Decision:
    action: str  # dance, hatch, garden, fish, reagent, gold, buy_barn, wait
    why: str
    train_until: str = ""
    energy_floor: int = 0
    target: str = ""  # reagent name, or a key in FARMS
    games: int = 0  # dance this many times; 0 keeps going until the training goal
    spend: bool = False  # play while energy can pay, past the training goal and a talent miss


def dance_cost(stage: str | None) -> int:
    return DANCE_COST.get((stage or "baby").lower(), DANCE_COST["baby"])


def price_of(snap: Snapshot) -> int:
    """What one dance costs. The track window's price beats the stage table.

    A missed "Adult" on the level-up window used to leave the table at a
    baby price of 2, so 4 energy opened an adult game (6) and backed out.
    """
    if snap.game_cost:
        return snap.game_cost
    return dance_cost(snap.pet_stage)


def reagent_from_quest(goal: str) -> str | None:
    """The reagent a tracked 'Collect ...' goal is asking for, or None."""
    item = collect_item_name(goal or "")
    if not item:
        return None
    key = re.sub(r"[^a-z]", "", item.lower())
    for raw in REAGENTS:
        if key == raw or raw in key:
            return item
    return None


def fishing_quest(goal: str) -> bool:
    return bool(re.search(r"\b(fish|fishing)\b", goal or "", re.I))


def places_for(reagent: str) -> tuple[str, ...]:
    """Zones to walk for a named reagent. Empty when we have no route for it."""
    key = re.sub(r"[^a-z]", "", (reagent or "").lower())
    for name, places in REAGENT_PLACES.items():
        if key == re.sub(r"[^a-z]", "", name):
            return places
    return ()


def _saved_base(snap: Snapshot) -> dict | None:
    if not snap.base_kind and not snap.base_stage and not snap.base_talents and not snap.base_stats:
        return None
    return {
        "kind": snap.base_kind,
        "stage": snap.base_stage,
        "talents": list(snap.base_talents),
        "stats": snap.base_stats,
    }


def needs_training(snap: Snapshot) -> str | None:
    """'adult' or 'mega' while this body is worth the energy. None to leave it."""
    if not snap.pet_training:
        return None
    return training_goal(
        snap.pet_kind,
        snap.pet_stage,
        snap.manifested,
        snap.stats,
        snap.confirm_mega,
        _saved_base(snap),
    )


def hatch_reason(snap: Snapshot) -> str | None:
    """Why to hatch the base with the kiosk pet. None while a body is still worth training."""
    if not snap.pet_training:
        return None
    return hatch_text(
        snap.pet_kind,
        snap.pet_stage,
        snap.manifested,
        snap.stats,
        snap.confirm_mega,
        _saved_base(snap),
    )


def garden_reason(snap: Snapshot) -> str | None:
    """Why to spend energy on the garden. None when there is nothing to tend or plant."""
    if snap.plants_need_care:
        return "a plant is showing a need; cast only the spell for that need"
    if snap.gardening_rank < CP_RANK:
        if snap.boom_shroom_seeds > 0:
            return "boom shrooms, toward gardening rank 3 for couch potatoes"
        if snap.gold is not None and snap.gold >= BOOM_SEED_GOLD:
            return "buy boom shroom seeds from Farley for 1,200 gold and plant them"
        return None
    if snap.couch_potato_seeds > 0:
        return "plant or tend couch potatoes"
    return None


def farm_for(snap: Snapshot) -> str:
    """Splithoof for seeds. Halfang once the bag holds more than 20 couch potatoes.

    Level does not change the target. A locked zone is retried, not replaced.
    """
    if snap.couch_potatoes > POTATO_SWITCH:
        return "halfang"
    return "splithoof"


def _bar_is_full(snap: Snapshot) -> bool:
    """A level-up fills the globe. Leaving it there wastes the refill."""
    if snap.energy is None or not snap.energy_max or snap.energy < snap.energy_max:
        return False
    return snap.energy >= price_of(snap)


def _can_dance(snap: Snapshot, floor: int) -> bool:
    if snap.energy is None:
        return False
    cost = price_of(snap)
    return snap.energy >= cost and snap.energy - cost >= floor


def _can_hatch(snap: Snapshot) -> bool:
    return snap.gold is not None and snap.gold >= HATCH_RESERVE


def _project_open(snap: Snapshot) -> bool:
    """Still building the pet, so one hatch stays in the bank."""
    if not snap.pet_training:
        return False
    return not project_finished(snap.pet_kind, snap.pet_stage, snap.manifested, snap.stats)


def barn_ready(snap: Snapshot) -> bool:
    """Red Barn Farm, after couch potatoes are actually plantable, hatch gold kept back."""
    if snap.red_barn or snap.gold is None:
        return False
    if snap.gardening_rank < CP_RANK and snap.couch_potato_seeds <= 0:
        return False
    reserve = HATCH_RESERVE if _project_open(snap) else 0
    return snap.gold >= BARN_GOLD + reserve


def _farm_decision(snap: Snapshot) -> Decision:
    target = farm_for(snap)
    held = snap.couch_potatoes
    if target == "halfang":
        why = f"{held} couch potatoes is past {POTATO_SWITCH}; Halfang until stopped"
    elif snap.energy is None:
        why = f"{held} couch potatoes; energy was unread, so Splithoof seeds until it can be read"
    else:
        why = f"{held} couch potatoes; the globe cannot pay for a dance, so Splithoof seeds until stopped"
    return Decision("gold", why, target=target)


def choose(snap: Snapshot) -> Decision:
    """The next thing to do. One action; the loop asks again when it finishes.

    Gardening, fishing, and hatching are not chosen. Reagent mode still gathers
    only what it was asked for. Anything else dances, then farms, until stopped.
    """
    if snap.mode == "reagents":
        return Decision(
            "reagent",
            "reagent mode: pick up what was asked, nothing else",
            target=snap.reagent or "",
        )

    if _can_dance(snap, 0):
        return Decision(
            "dance",
            "dance while the globe can pay, feed a snack after each win, then farm",
            games=0,
            spend=True,
        )

    if snap.mode == "pet":
        return Decision("wait", "pet mode: energy cannot pay for another dance")
    return _farm_decision(snap)
