"""Ghulture training rules: which talents count, and when to stop.

A pet manifests one talent at teen, adult, ancient, epic, and mega. Training
a body that already manifested something outside the set wastes energy and
makes that miss more likely to hatch onward. The numbers and the talent list
are the ones PLAYBOOK.md describes.
"""

from __future__ import annotations

import re

# Shown on the pet at mega. A stat within 5 of these still gives the max talent value.
PERFECT_STATS = {
    "strength": 255,
    "intellect": 250,
    "agility": 260,
    "will": 260,
    "power": 250,
}

TARGET = (
    "death-dealer",
    "death-giver",
    "pain-giver",
    "mighty",
    "armor-breaker",
)

# Other talents a death pet commonly manifests. Seeing one of these is a miss.
# A talent that is in neither list is ignored, so a misread window does not trash a pet.
OTHER = (
    "spell-proof",
    "spell-defying",
    "spell-defy",
    "pain-bringer",
    "death-bringer",
    "fairy",
    "unicorn",
    "sprite",
    "defender",
    "medic",
    "may-cast-deathblade",
    "may-cast-feint",
    "balance-dealer",
    "balance-giver",
    "storm-dealer",
    "storm-giver",
    "fire-dealer",
    "fire-giver",
    "ice-dealer",
    "ice-giver",
    "life-dealer",
    "life-giver",
    "myth-dealer",
    "myth-giver",
)

_STAGES = ("baby", "teen", "adult", "ancient", "epic", "mega", "ultra")


def _pattern(canon: str) -> re.Pattern[str]:
    parts = canon.split("-")
    body = r"[\s\-]*".join(re.escape(p) for p in parts)
    return re.compile(rf"\b{body}\b", re.I)


_TARGET_RES = [(name, _pattern(name)) for name in TARGET]
_OTHER_RES = [(name, _pattern(name)) for name in OTHER]


def talents_mentioned(texts: list[str]) -> list[str]:
    """Target and known-other talents named in window text, in list order."""
    blob = "\n".join(texts)
    found: list[str] = []
    for name, pat in (*_TARGET_RES, *_OTHER_RES):
        if pat.search(blob) and name not in found:
            found.append(name)
    return found


def pet_failed(talents: list[str] | tuple[str, ...]) -> bool:
    """True when a manifested talent is one this ghulture must not keep."""
    return any(t in OTHER for t in talents)


def stats_perfect(stats: dict | None) -> bool:
    """True when every stat is within 5 of the mega maximum."""
    if not stats:
        return False
    return all(int(stats.get(name, 0)) >= perfect - 5 for name, perfect in PERFECT_STATS.items())


def stage_at_least(stage: str | None, goal: str) -> bool:
    try:
        return _STAGES.index((stage or "").lower()) >= _STAGES.index(goal)
    except ValueError:
        return False
