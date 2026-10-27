"""Story progress per world, for the Spiral Progress page (mac/spiral_web.py).

Standard library only, and Python 3.9 syntax: macOS's own python3 serves the
page, while the tracker (`mac/run.sh tracker`, tracker.py) feeds it from the
game. Nothing here touches the game.

Where the numbers come from:

- docs/quests/<World>.txt: each world's story quests in order, area by area,
  in Spiral Tracker's copy-paste format ("Unicorn Way (10 quests)", "1.",
  name, tags such as "BOSS").
- state/quest_progress.json (Progress, written by the tracker): the furthest
  story quest seen in each world. The tracker reads the game's own quest
  list from memory (the quests in the book, each with its main-story flag).
- state/tracker_live.json: what the tracker read just now (zone, the HUD
  goal, the tracked quest, the main-story quests in the book).
- state/worlds_done.json: worlds the player marked finished on the page
  (the optional ones, Grizzleheim, Wintertusk, Wysteria, Catacombs, cannot
  be inferred: a later world says nothing about them). Only the page
  writes it.

The furthest story quest seen is in progress (done, once it has left the
book); every quest before it on its world's list is done, and so is every
world before the furthest world reached. Side quests are not counted.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

WORLDS = (
    "Wizard City", "Krokotopia", "Marleybone", "MooShu", "Dragonspyre", "Celestia", "Zafaria",
    "Avalon", "Azteca", "Khrysalis", "Polaris", "Mirage", "Empyrea", "Karamelle", "Lemuria",
    "Novus", "Wallaru", "Selenopolis",
)
# Optional worlds with a story of their own, by the arc they belong to.
SIDE_WORLDS = {"Grizzleheim": "Arc 1", "Wintertusk": "Arc 1", "Wysteria": "Arc 1", "Catacombs": "Arc 3"}
ARCS = (
    ("Arc 1", ("Wizard City", "Krokotopia", "Marleybone", "MooShu", "Dragonspyre")),
    ("Arc 2", ("Celestia", "Zafaria", "Avalon", "Azteca", "Khrysalis")),
    ("Arc 3", ("Polaris", "Mirage", "Empyrea")),
    ("Arc 4", ("Karamelle", "Lemuria", "Novus", "Wallaru", "Selenopolis")),
)
# Zone ids start with the world's internal name ("WizardCity/WC_Hub").
_WORLD_ALIASES = {"wizardcity": "Wizard City", "dragonspire": "Dragonspyre"}
# Wintertusk lives inside Grizzleheim's zones ("Grizzleheim/GH_HFjord/...").
_SUBZONE_WORLDS = {"grizzleheim/ghhfjord": "Wintertusk"}

PROGRESS_FILE = "quest_progress.json"
LIVE_FILE = "tracker_live.json"
WORLDS_DONE_FILE = "worlds_done.json"
LIVE_STALE = 15.0  # tracker_live.json older than this: the tracker is not running
EVENTS_KEPT = 40

_SECTION = re.compile(r"^(?P<area>.+?)\s*\(\d+ quests?\)\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"^(\d+)\.\s*$")


def norm(name: str) -> str:
    """Compare quest names loosely: case, punctuation and spacing don't matter."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower().replace("’", "'"))


def canonical_world(text: str) -> str:
    """"DragonSpire", "Wizard City", "WizardCity" -> the list's world name ("" if none)."""
    key = norm(text)
    if key in _WORLD_ALIASES:
        return _WORLD_ALIASES[key]
    return next((w for w in (*WORLDS, *SIDE_WORLDS) if norm(w) == key), "")


def world_of_zone(zone: str) -> str:
    """"Krokotopia/KT_Hub" -> "Krokotopia" ("" if unknown)."""
    parts = (zone or "").split("/")
    if len(parts) >= 2 and f"{norm(parts[0])}/{norm(parts[1])}" in _SUBZONE_WORLDS:
        return _SUBZONE_WORLDS[f"{norm(parts[0])}/{norm(parts[1])}"]
    return canonical_world(parts[0])


# --- the lists ----------------------------------------------------------------


@dataclass
class ListedQuest:
    index: int  # 1-based position in the world's story
    name: str
    area: str
    tags: list = field(default_factory=list)


def parse_quest_list(text: str) -> list:
    quests: list = []
    area = ""
    expect_name = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _SECTION.match(line)
        if m and not expect_name:
            area = m.group("area").strip().replace("’", "'")
            continue
        m = _NUMBER.match(line)
        if m:
            quests.append(ListedQuest(int(m.group(1)), "", area))
            expect_name = True
            continue
        if expect_name:
            quests[-1].name = line
            expect_name = False
        elif quests:
            quests[-1].tags.append(line)
    return tidy([q for q in quests if q.name])


def _same(a: str, b: str) -> bool:
    """Same quest, allowing a typo ("King's Fourth"/"Forth")."""
    a, b = norm(a), norm(b)
    return a == b or (min(len(a), len(b)) >= 6 and SequenceMatcher(None, a, b).ratio() >= 0.9)


def tidy(listed: list) -> list:
    """One entry per quest number: a quest whose steps span areas is listed
    again under the same number, and its later tags join the first."""
    out: list = []
    by_index: dict = {}
    for q in listed:
        if q.index in by_index and _same(by_index[q.index].name, q.name):
            by_index[q.index].tags.extend(q.tags)
            continue
        by_index[q.index] = q
        out.append(q)
    return out


def load_lists(docs: Path) -> dict:
    """{world: [ListedQuest]} from docs/quests/<World>.txt."""
    out: dict = {}
    folder = docs / "quests"
    for f in sorted(folder.glob("*.txt")) if folder.is_dir() else ():
        world = canonical_world(f.stem) or f.stem
        try:
            out[world] = parse_quest_list(f.read_text(encoding="utf-8"))
        except OSError:
            continue
    return out


class Lookup:
    """Quest name -> (world, ListedQuest), exact first, then a close spelling."""

    def __init__(self, lists: dict):
        self.by_norm: dict = {}
        for world, listed in lists.items():
            for q in listed:
                self.by_norm.setdefault(norm(q.name), (world, q))
        self._fuzzy: dict = {}

    def find(self, name: str):
        key = norm(name)
        if key in self.by_norm:
            return self.by_norm[key]
        if key not in self._fuzzy:
            hit = None
            if len(key) >= 6:
                for k, v in self.by_norm.items():
                    if abs(len(k) - len(key)) <= 3 and SequenceMatcher(None, k, key).ratio() >= 0.9:
                        hit = v
                        break
            self._fuzzy[key] = hit
        return self._fuzzy[key]


_cache: dict = {}


def lists_and_lookup(docs: Path) -> tuple:
    """The lists, reread when a file in docs/quests changes."""
    stamp = []
    folder = docs / "quests"
    for f in sorted(folder.glob("*.txt")) if folder.is_dir() else ():
        try:
            stamp.append((f.name, f.stat().st_mtime))
        except OSError:
            pass
    key = (str(docs), tuple(stamp))
    if _cache.get("key") != key:
        lists = load_lists(docs)
        _cache.update(key=key, lists=lists, lookup=Lookup(lists))
    return _cache["lists"], _cache["lookup"]


# --- state files --------------------------------------------------------------


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    """Whole file or nothing: the page reads these while they are written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class Progress:
    """The furthest story quest seen per world (state/quest_progress.json).

    Only ever moves forward: a quest earlier on the list than the one on
    record changes nothing. Main-story quests that are on no list are kept
    apart ("unlisted") so the page can say so.
    """

    def __init__(self, path: Path, lookup: Lookup):
        self.path = path
        self.lookup = lookup
        data = read_json(path)
        self.reached: dict = data.get("reached") or {}
        self.unlisted: dict = data.get("unlisted") or {}
        self.events: list = list(data.get("events") or [])
        self._dirty = False

    def see(self, main_quests: list, now: float) -> list:
        """The main-story quests in the book now. Returns the ones that moved a world on."""
        moved = []
        for name in main_quests:
            hit = self.lookup.find(name)
            if not hit:
                if name not in self.unlisted:
                    self.unlisted[name] = now
                    self._dirty = True
                continue
            world, q = hit
            row = self.reached.get(world) or {}
            if q.index > int(row.get("index") or 0):
                self.reached[world] = {"index": q.index, "quest": q.name, "t": now}
                self.events = (self.events + [{"t": now, "world": world, "index": q.index, "quest": q.name}])
                self.events = self.events[-EVENTS_KEPT:]
                self._dirty = True
                moved.append(q.name)
        return moved

    def save(self, force: bool = False) -> None:
        if not (self._dirty or force):
            return
        write_json(self.path, {"version": 2, "reached": self.reached, "unlisted": self.unlisted,
                               "events": self.events[-EVENTS_KEPT:]})
        self._dirty = False


# --- the page's numbers -------------------------------------------------------


def _pct(done: int, total: int) -> int:
    return int(100 * done / total) if total else 0


def build(docs: Path, state: Path, now: float | None = None) -> dict:
    """Everything the page shows, as one JSON-able dict."""
    now = time.time() if now is None else now
    lists, lookup = lists_and_lookup(docs)
    progress = read_json(state / PROGRESS_FILE)
    reached: dict = progress.get("reached") or {}
    live = read_json(state / LIVE_FILE)
    fresh = bool(live) and now - float(live.get("time") or 0) < LIVE_STALE
    running = fresh and live.get("state") != "stopped"
    in_book = {norm(n) for n in (live.get("main") or [])} if running else None
    marked = set(worlds_done(state))

    live_world = world_of_zone(live.get("zone") or "") if live else ""
    here_index = max((WORLDS.index(w) for w in reached if w in WORLDS), default=-1)
    if live_world in WORLDS:
        here_index = max(here_index, WORLDS.index(live_world))

    tracked = (live.get("tracked") if live else "") or ""
    tracked_hit = lookup.find(tracked) if tracked else None
    # The world being played: the tracked story quest's, else the latest
    # story quest's, else where the wizard stands.
    latest = max(reached.items(), key=lambda kv: float(kv[1].get("t") or 0), default=None)
    current_world = (tracked_hit[0] if tracked_hit else "") or (latest[0] if latest else "") or live_world

    worlds = []
    for name in (*WORLDS, *SIDE_WORLDS):
        listed = lists.get(name, [])
        i = WORLDS.index(name) if name in WORLDS else -1
        top = int((reached.get(name) or {}).get("index") or 0)
        earlier_world = 0 <= i < here_index or name in marked
        areas: list = []
        done = 0
        for q in listed:
            if earlier_world or q.index < top:
                status = "done"
            elif q.index == top:
                # Seen in the book; gone from it (the tracker is reading) = turned in.
                status = "done" if in_book is not None and norm(q.name) not in in_book else "active"
            else:
                status = "todo"
            done += status == "done"
            if not areas or areas[-1]["name"] != q.area:
                areas.append({"name": q.area, "quests": []})
            areas[-1]["quests"].append({"index": q.index, "name": q.name, "tags": q.tags, "status": status,
                                        "tracked": bool(tracked_hit) and tracked_hit[1] is q})
        for a in areas:
            a["total"] = len(a["quests"])
            a["done"] = sum(x["status"] == "done" for x in a["quests"])
            a["pct"] = _pct(a["done"], a["total"])
        worlds.append({
            "name": name,
            "side": i < 0,
            "arc": SIDE_WORLDS.get(name) or next((a for a, ws in ARCS if name in ws), ""),
            "has_list": bool(listed),
            "total": len(listed),
            "done": done,
            "pct": _pct(done, len(listed)) if listed else (100 if earlier_world else 0),
            "current": name == current_world,
            "reached": 0 <= i <= here_index or top > 0 or name in marked,
            "marked": name in marked,
            "areas": areas,
        })

    by_name = {w["name"]: w for w in worlds}
    arcs = []
    for arc, names in ARCS:
        members = [by_name[n] for n in names if n in by_name]
        total = sum(w["total"] for w in members)
        done = sum(w["done"] for w in members)
        arcs.append({
            "name": arc,
            "worlds": list(names),
            "side_worlds": [w for w, a in SIDE_WORLDS.items() if a == arc],
            "total": total,
            "done": done,
            "pct": _pct(done, total),
            "current": any(w["current"] for w in members),
        })
    story = [w for w in worlds if w["name"] in WORLDS]
    story_total = sum(w["total"] for w in story)
    story_done = sum(w["done"] for w in story)

    current = {
        "world": current_world,
        "zone": (live.get("zone") or "") if live else "",
        "area": (live.get("area") or "") if live else "",
        "goal": (live.get("goal") or "") if live else "",
        "quest": tracked,
        "main": bool(tracked_hit),
        "index": tracked_hit[1].index if tracked_hit else None,
        "quest_world": tracked_hit[0] if tracked_hit else "",
        "unlisted": [n for n in (live.get("main") or []) if not lookup.find(n)] if running else [],
    }
    return {
        "updated": now,
        "running": running,
        "live_age": (now - float(live.get("time") or 0)) if live else None,
        "read_ok": bool(live.get("quests_ok")) if live else False,
        "current": current,
        "story": {"done": story_done, "total": story_total, "pct": _pct(story_done, story_total)},
        "arcs": arcs,
        "worlds": worlds,
        "recent": list(reversed(progress.get("events") or []))[:12],
    }


def worlds_done(state: Path) -> list:
    """Worlds the player marked finished."""
    names = read_json(state / WORLDS_DONE_FILE).get("worlds") or []
    return [canonical_world(n) for n in names if canonical_world(n)]


def set_world_done(state: Path, world: str, done: bool) -> list:
    """Mark a world finished (or take the mark back). Returns the marked worlds."""
    name = canonical_world(world)
    if not name:
        raise ValueError(f"no world called {world!r}")
    marked = [w for w in worlds_done(state) if w != name] + ([name] if done else [])
    write_json(state / WORLDS_DONE_FILE, {"worlds": marked})
    return marked
