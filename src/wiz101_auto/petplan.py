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
    got = _stage_index(stage)
    want = _stage_index(goal)
    return got >= 0 and want >= 0 and got >= want


def _stage_index(stage: str | None) -> int:
    try:
        return _STAGES.index((stage or "").lower())
    except ValueError:
        return -1


# The hatch partner that never changes. It is not a pet we own.
KIOSK = "the kiosk perfect ghulture"


def shortfall(stats: dict | None) -> int | None:
    """How far the max stats are below the perfect line. 0 is perfect. None if unread."""
    if not stats:
        return None
    total = 0
    for name, cap in PERFECT_STATS.items():
        try:
            value = int(stats[name])
        except (KeyError, TypeError, ValueError):
            return None
        total += max(0, cap - 5 - value)
    return total


def _hits(talents) -> int:
    return sum(1 for talent in talents or () if talent in TARGET)


def rank_key(stats, talents, stage) -> tuple | None:
    """Higher is closer to the perfect ghulture. None when a talent has already failed."""
    if pet_failed(talents or ()):
        return None
    missing = shortfall(stats)
    known = 1 if missing is not None else 0
    gap = -(missing if missing is not None else 0)
    return (known, gap, _hits(talents), _stage_index(stage))


def is_closer(body: dict | None, base: dict | None) -> bool:
    """True when `body` should replace the saved base. A miss never does."""
    if not body:
        return False
    key = rank_key(body.get("stats"), body.get("talents") or (), body.get("stage"))
    if key is None:
        return False
    if not base:
        return True
    other = rank_key(base.get("stats"), base.get("talents") or (), base.get("stage"))
    if other is None:
        return True
    return key > other


def _same_body(saved: dict, equipped: dict) -> bool:
    """The equipped pet is the saved one, maybe a stage or a talent further on."""
    saved_kind = (saved.get("kind") or "").lower()
    equipped_kind = (equipped.get("kind") or "").lower()
    if saved_kind and equipped_kind and saved_kind != equipped_kind:
        return False
    saved_stats, equipped_stats = saved.get("stats"), equipped.get("stats")
    if isinstance(saved_stats, dict) and saved_stats and isinstance(equipped_stats, dict) and equipped_stats:
        try:
            return all(int(saved_stats[name]) == int(equipped_stats[name]) for name in PERFECT_STATS)
        except (KeyError, TypeError, ValueError):
            return False
    saved_known = isinstance(saved_stats, dict) and bool(saved_stats)
    equipped_known = isinstance(equipped_stats, dict) and bool(equipped_stats)
    if saved_known or equipped_known:
        return False
    old = list(saved.get("talents") or [])
    new = list(equipped.get("talents") or [])
    if not all(talent in new for talent in old):
        return False
    return _stage_index(equipped.get("stage")) >= _stage_index(saved.get("stage"))


def _view(kind, stage, talents, stats) -> dict:
    return {
        "kind": kind or "ghulture",
        "stage": stage,
        "talents": list(talents or []),
        "stats": stats if isinstance(stats, dict) else None,
    }


def _worth_adult(body: dict, base: dict | None) -> bool:
    """Adult is the hatch line. Only the base, or a body that should become it, is worth that."""
    if base and _same_body(base, body):
        return True
    return is_closer(body, base)


def training_goal(kind, stage, talents, stats, confirm_mega, base) -> str | None:
    """'adult' or 'mega' while this body should be danced. None when it should not."""
    if (kind or "").lower() not in ("", "ghulture"):
        return None
    if (kind or "") == "" and not talents and not stats and not base:
        # The first look has not named the pet yet. One stretch of games reads it.
        return None if stage_at_least(stage, "adult") else "adult"
    if (kind or "").lower() != "ghulture":
        return None
    if pet_failed(talents or ()):
        return None
    # Max stats are fixed at the hatch. Leveling does not raise them. A body
    # already at the caps can still be the perfect pet, so it is trained out.
    # Every other body stops at adult, and only if it is the one we hatch.
    if confirm_mega or shortfall(stats) == 0:
        return None if stage_at_least(stage, "mega") else "mega"
    body = _view(kind, stage, talents, stats)
    if not _worth_adult(body, base):
        return None
    return None if stage_at_least(stage, "adult") else "adult"


def project_finished(kind, stage, talents, stats) -> bool:
    """The equipped pet is the perfect ghulture. The hatch reserve can be spent."""
    if (kind or "").lower() != "ghulture" or pet_failed(talents or ()):
        return False
    if not stage_at_least(stage, "mega") or _hits(talents) < len(TARGET):
        return False
    missing = shortfall(stats)
    return missing is None or missing == 0


def _hatchable(base: dict | None) -> bool:
    if not base or (base.get("kind") or "").lower() != "ghulture":
        return False
    if pet_failed(base.get("talents") or ()) or not stage_at_least(base.get("stage"), "adult"):
        return False
    return not project_finished(base.get("kind"), base.get("stage"), base.get("talents"), base.get("stats"))


def hatch_text(kind, stage, talents, stats, confirm_mega, base) -> str | None:
    """Why to hatch the base with the kiosk pet. None while this body is still worth training."""
    if training_goal(kind, stage, talents, stats, confirm_mega, base):
        return None
    if project_finished(kind, stage, talents, stats):
        return None
    parent = base if _hatchable(base) else None
    equipped = _view(kind, stage, talents, stats)
    if parent and _same_body(parent, equipped) and pet_failed(talents or ()):
        parent = None
    failed = pet_failed(talents or ())
    if failed:
        if not parent:
            return None
        return (
            "a manifested talent is outside the set; do not hatch this body. "
            f"Hatch the base with {KIOSK}"
        )
    if parent is None and (kind or "").lower() == "ghulture" and _hatchable(equipped):
        parent = equipped
    if parent is None:
        return None
    return (
        f"hatch the base with {KIOSK}: 255/250/260/260/250, "
        "death-dealer, death-giver, pain-giver, mighty, and armor breaker"
    )


def note_equipped(record: dict) -> dict:
    """Remember the closest clean ghulture as the base. A new miss drops back to the one before it."""
    rec = dict(record)
    kind = (rec.get("kind") or "").lower()
    talents = list(rec.get("talents") or [])
    stats = rec.get("stats") if isinstance(rec.get("stats"), dict) else None
    if kind not in ("", "ghulture"):
        return rec
    if kind == "" and not talents and not stats:
        return rec
    equipped = _view(rec.get("kind"), rec.get("stage"), talents, stats)
    base = rec.get("base") if isinstance(rec.get("base"), dict) else None
    prior = rec.get("prior_base") if isinstance(rec.get("prior_base"), dict) else None
    if pet_failed(talents):
        if base and _same_body(base, equipped):
            rec["base"] = prior
            rec["prior_base"] = None
        return rec
    if base and _same_body(base, equipped):
        rec["base"] = equipped
        return rec
    if is_closer(equipped, base):
        if base:
            rec["prior_base"] = base
        rec["base"] = equipped
    return rec


_STAT_LINE = re.compile(
    r"\b(strength|intellect|agility|will|power)\b\D{0,12}(\d{1,3})",
    re.I,
)


def stats_mentioned(texts: list[str]) -> dict:
    """All five max stats, when a window names every one. A partial read is ignored."""
    found: dict[str, int] = {}
    for text in texts:
        for match in _STAT_LINE.finditer(text):
            name = match.group(1).lower()
            value = int(match.group(2))
            if name not in found and 0 < value <= 400:
                found[name] = value
    if any(name not in found for name in PERFECT_STATS):
        return {}
    return found


# The dance game's stages (btnTrack0-4) are the same difficulty; each trains
# different stats on top of XP (the player's numbers, 10-11).
DANCE_STAGES = (
    ("Wizard City", {"agility": 4}),
    ("Krokotopia", {"strength": 1, "agility": 3}),
    ("Marleybone", {"intellect": 1, "agility": 3}),
    ("MooShu", {"intellect": 1, "agility": 2, "will": 1}),
    ("Dragonspyre", {"agility": 2, "intellect": 2}),
)
STAGE_CHOICES = ("lowest", "rotate", "random")
_TRAINED_LINE = re.compile(
    r"\b(strength|intellect|agility|will|power)\b\D{0,12}?(\d{1,3})\s*/\s*(\d{1,3})",
    re.I,
)


def trained_stats(texts: list[str]) -> dict[str, tuple[int, int]]:
    """{stat: (now, max)} from the dance window's stat block ("Strength: 120/255").
    All five or nothing."""
    found: dict[str, tuple[int, int]] = {}
    for match in _TRAINED_LINE.finditer(" ".join(texts)):
        name, now, most = match.group(1).lower(), int(match.group(2)), int(match.group(3))
        if name not in found and 0 <= now <= most <= 400 and most > 0:
            found[name] = (now, most)
    if any(name not in found for name in PERFECT_STATS):
        return {}
    return found


def stage_score(gains: dict[str, int], trained: dict[str, tuple[int, int]]) -> float:
    """How much a stage helps: points that still fit under each cap, weighted
    by how far below its cap that stat is (the lowest stats count most)."""
    score = 0.0
    for stat, points in gains.items():
        now, most = trained.get(stat, (0, 0))
        gap = max(0, most - now)
        if gap:
            score += min(points, gap) * gap / most
    return score


def pick_stage(choice: str, trained: dict | None, last: int | None, roll: float = 0.0) -> tuple[int, str]:
    """(track index, why). `choice`: lowest, rotate, random, or a stage name.
    "lowest" without a stat read rotates, so the stats still spread."""
    count = len(DANCE_STAGES)
    names = [name.lower() for name, _gains in DANCE_STAGES]
    choice = (choice or "lowest").strip().lower()
    if choice in names:
        return names.index(choice), "set in config"
    if choice == "random":
        return min(int(roll * count), count - 1), "random"
    after = 0 if last is None else (last + 1) % count
    if choice == "lowest" and trained:
        scores = [stage_score(gains, trained) for _name, gains in DANCE_STAGES]
        if max(scores) > 0:
            order = [(after + i) % count for i in range(count)]  # ties go round
            best = max(order, key=lambda i: scores[i])
            low = sorted((now / most, stat) for stat, (now, most) in trained.items() if most and now < most)
            lowest = ", ".join(f"{stat} {trained[stat][0]}/{trained[stat][1]}" for _r, stat in low[:2])
            return best, f"trains the lowest stats ({lowest})"
        return after, "every trained stat is at its cap; rotating"
    return after, "rotating (no stat read yet)" if choice == "lowest" else "rotating"
