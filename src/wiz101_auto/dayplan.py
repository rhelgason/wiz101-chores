"""What the bot should do next. Pure: no game client and no files.

Energy (the green globe) is the scarce resource. It refills slowly and jumps
to full on a level-up, and a full bar that sits there is wasted. Pet training
and garden care spend it. Fishing spends it only while a fishing quest is
tracked. Gold and reagent runs happen when energy cannot pay for any of those.

The reasons are the ones in PLAYBOOK.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .collect import REAGENTS, collect_item_name
from .petplan import TARGET, pet_failed, stage_at_least, stats_perfect

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
    boom_shroom_seeds: int = 0
    plants_need_care: bool = False
    reagent: str | None = None  # asked for, or read off the tracked quest
    fishing: bool = False
    pet_training: bool = True
    mode: str = "chores"  # chores | pet | reagents


@dataclass(frozen=True)
class Decision:
    action: str  # dance, hatch, garden, fish, reagent, gold, buy_barn, wait
    why: str
    train_until: str = ""
    energy_floor: int = 0
    target: str = ""  # reagent name, or a key in FARMS


def dance_cost(stage: str | None) -> int:
    return DANCE_COST.get((stage or "baby").lower(), DANCE_COST["baby"])


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


def _project(snap: Snapshot) -> bool:
    """The equipped pet is the ghulture we are building, or we have not read its kind yet."""
    return (snap.pet_kind or "").lower() in ("", "ghulture")


def _ghulture(snap: Snapshot) -> bool:
    return (snap.pet_kind or "").lower() == "ghulture"


def needs_training(snap: Snapshot) -> str | None:
    """'adult' or 'mega' while this body should keep dancing. None to stop."""
    if not snap.pet_training or not _project(snap):
        return None
    if pet_failed(snap.manifested):
        return None
    stage = (snap.pet_stage or "baby").lower()
    if snap.confirm_mega:
        return None if stage_at_least(stage, "mega") else "mega"
    if stage_at_least(stage, "adult"):
        return None
    return "adult"


def hatch_reason(snap: Snapshot) -> str | None:
    """Why to hatch, ignoring gold. None while this body should still be trained."""
    if not snap.pet_training or not _ghulture(snap):
        return None
    if snap.confirm_mega and not pet_failed(snap.manifested):
        return None
    if pet_failed(snap.manifested):
        return (
            "a manifested talent is outside the set; hatch the saved base with a "
            "perfect ghulture, and do not hatch this body"
        )
    if not stage_at_least(snap.pet_stage, "adult"):
        return None
    if stats_perfect(snap.stats):
        return (
            "adult, talents clean, stats at the max; hatch once more with a perfect "
            "ghulture and take that baby to mega"
        )
    return "adult with no failed talent; hatch it with a perfect ghulture"


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
    """Which gold run, once energy has nothing left to spend on."""
    need_seeds = snap.couch_potato_seeds < SEED_STOCK
    if snap.level is not None and snap.level < GRIZZLEHEIM_LEVEL and need_seeds:
        return "early-gold"
    if need_seeds or snap.level is None or snap.level < HALFANG_LEVEL:
        return "splithoof"
    return "halfang"


def _can_dance(snap: Snapshot, floor: int) -> bool:
    if snap.energy is None:
        return False
    cost = dance_cost(snap.pet_stage)
    return snap.energy >= cost and snap.energy - cost >= floor


def _can_hatch(snap: Snapshot) -> bool:
    return snap.gold is not None and snap.gold >= HATCH_RESERVE


def _project_open(snap: Snapshot) -> bool:
    """Still building the pet, so the hatch reserve stays in the bank."""
    if not snap.pet_training:
        return False
    have = [t for t in snap.manifested if t in TARGET]
    finished = (
        _ghulture(snap)
        and stage_at_least(snap.pet_stage, "mega")
        and not pet_failed(snap.manifested)
        and len(have) >= len(TARGET)
    )
    return not finished


def barn_ready(snap: Snapshot) -> bool:
    """Red Barn Farm, after couch potatoes are actually plantable, hatch gold kept back."""
    if snap.red_barn or snap.gold is None:
        return False
    if snap.gardening_rank < CP_RANK and snap.couch_potato_seeds <= 0:
        return False
    reserve = HATCH_RESERVE if _project_open(snap) else 0
    return snap.gold >= BARN_GOLD + reserve


def choose(snap: Snapshot) -> Decision:
    """The next thing to do. One action; the loop asks again when it finishes."""
    if snap.mode == "reagents":
        return Decision(
            "reagent",
            "reagent mode: pick up what was asked, nothing else",
            target=snap.reagent or "",
        )

    floor = GARDEN_RESERVE if snap.plants_need_care else 0
    train = needs_training(snap)
    if train and _can_dance(snap, floor):
        held = f", holding {floor} energy for a plant" if floor else ""
        return Decision(
            "dance",
            f"train the ghulture to {train}{held}",
            train_until=train,
            energy_floor=floor,
        )

    if snap.mode != "pet":
        garden = garden_reason(snap)
        if garden and snap.energy is not None and snap.energy >= 1:
            return Decision("garden", garden)
        if snap.fishing and snap.energy is not None and snap.energy >= 1:
            return Decision(
                "fish",
                "a fishing quest is tracked, and pet and garden are not waiting on energy",
            )

    hatch = hatch_reason(snap)
    if hatch:
        if _can_hatch(snap):
            return Decision("hatch", hatch)
        return Decision(
            "gold",
            f"the pet is ready to hatch and gold is under {HATCH_RESERVE:,}",
            target=farm_for(snap),
        )

    if snap.mode == "chores" and barn_ready(snap):
        return Decision(
            "buy_barn",
            f"gold covers the Red Barn Farm ({BARN_GOLD:,}) with the hatch reserve left over",
        )

    if snap.mode != "pet" and snap.reagent:
        return Decision("reagent", f"asked for {snap.reagent}", target=snap.reagent)

    if snap.mode == "pet":
        return Decision("wait", "pet mode: nothing to train or hatch right now")
    if snap.energy is None:
        return Decision("wait", "energy could not be read; not starting a gold run while it might be full")

    farm = farm_for(snap)
    why = "energy cannot pay for the pet, the garden, or a fishing quest"
    if (snap.pet_kind or "").lower() not in ("", "ghulture"):
        why = f"the equipped pet is a {snap.pet_kind}, not the ghulture, so its training waits; {why}"
    return Decision("gold", why, target=farm)
