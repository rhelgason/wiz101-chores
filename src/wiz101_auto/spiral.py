"""Quest progress per world, for the Spiral Progress page (mac/spiral_web.py).

Standard library only, and Python 3.9 syntax: macOS's own python3 serves the
page, while the tracker (`mac/run.sh tracker`, tracker.py) feeds it from the
game. Nothing here touches the game.

Where the numbers come from:

- docs/quests/<World>.txt: each world's story quests in order, area by area,
  in Spiral Tracker's copy-paste format ("Unicorn Way (10 quests)", "1.",
  name, tags such as "SIDE ×1").
- docs/sidequests/<World>.txt: side quests by quest giver (four worlds).
- state/quest_seen.json (QuestMemory, written by the tracker): every quest
  the game showed in the quest book or the HUD, with its area, world, goal,
  and whether it looks done.
- state/quest_marks.json: the player's own ticks on the page; they win.
- state/tracker_live.json: what the tracker read just now (zone, goal, the
  tracked quest).

A story quest is done when it was ticked, when a later story quest of its
world has been seen (the lists are in story order), or when its world comes
before the furthest world reached. A side quest is done when it was ticked,
or when it was the quest book's tracked quest and then left the book: the
next time the book was open another quest was tracked and it was on no page
looked at. A quest seen again afterwards is not done after all.
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
# Optional side worlds, by the arc they belong to.
SIDE_WORLDS = {"Grizzleheim": "Arc 1", "Wintertusk": "Arc 1", "Wysteria": "Arc 1", "Catacombs": "Arc 3"}
ARCS = (
    ("Arc 1", ("Wizard City", "Krokotopia", "Marleybone", "MooShu", "Dragonspyre")),
    ("Arc 2", ("Celestia", "Zafaria", "Avalon", "Azteca", "Khrysalis")),
    ("Arc 3", ("Polaris", "Mirage", "Empyrea")),
    ("Arc 4", ("Karamelle", "Lemuria", "Novus", "Wallaru", "Selenopolis")),
)
# Zone ids start with the world's internal name ("WizardCity/WC_Hub").
_WORLD_ALIASES = {"wizardcity": "Wizard City", "dragonspire": "Dragonspyre", "wc": "Wizard City"}
# Wintertusk lives inside Grizzleheim's zones ("Grizzleheim/GH_HFjord/...").
_SUBZONE_WORLDS = {"grizzleheim/ghhfjord": "Wintertusk"}

SEEN_FILE = "quest_seen.json"
MARKS_FILE = "quest_marks.json"
LIVE_FILE = "tracker_live.json"
LIVE_STALE = 15.0  # tracker_live.json older than this: the tracker is not running
EVENTS_KEPT = 60
GOALS_KEPT = 8

_SECTION = re.compile(r"^(?P<area>.+?)\s*\(\d+ quests?\)\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"^(\d+)\.\s*$")
_SIDE_TAG = re.compile(r"^\s*SIDE\s*\D?\s*(\d+)", re.IGNORECASE)
_GUIDE_QUEST = re.compile(r"^\*?\s*(?P<name>[^(]+?)\s*\(\s*\d+\s*(gold|xp)", re.IGNORECASE)


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

    @property
    def sides(self) -> int:
        """Side quests this story quest opens ("SIDE ×2")."""
        return sum(int(m.group(1)) for m in (_SIDE_TAG.match(t) for t in self.tags) if m)


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
    """Same quest, allowing a typo ("King's Fourth"/"Forth", "Mail Calll")."""
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


@dataclass
class SideQuest:
    name: str
    giver: str
    area: str  # the guide's heading ("VILLAGE OF SORROW"), title case


def parse_side_guide(text: str) -> list:
    """Side quests of a giver guide: a quest line ("Name (170 XP)...") right
    under its giver; quests under a "(MAIN QUEST)" heading are left out."""
    out: list = []
    prev, main, area = "", False, ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if "(MAIN QUEST)" in upper:
            main = True
        elif "(SIDE QUEST)" in upper:
            main = False
        heading = re.sub(r"\((MAIN|SIDE) QUEST\)", "", line, flags=re.IGNORECASE).strip()
        if heading and heading.isupper() and not heading.startswith("("):
            area = heading.replace("’", "'").title().replace("'S", "'s")
            main = "(MAIN QUEST)" in upper
        m = _GUIDE_QUEST.match(line)
        if (m and prev and not prev.startswith(("-", "(")) and not prev.isupper()
                and not _GUIDE_QUEST.match(prev)):
            if not main:
                out.append(SideQuest(m.group("name").strip(), prev, area))
        prev = line
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


def load_side_guides(docs: Path) -> dict:
    """{world: [SideQuest]} from docs/sidequests/<World>.txt."""
    out: dict = {}
    folder = docs / "sidequests"
    for f in sorted(folder.glob("*.txt")) if folder.is_dir() else ():
        world = canonical_world(f.stem) or f.stem
        try:
            out[world] = parse_side_guide(f.read_text(encoding="utf-8"))
        except OSError:
            continue
    return out


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


class QuestMemory:
    """Every quest the game has shown, kept in state/quest_seen.json.

    `see_book` takes one reading of the open quest book's page. A quest that
    was the book's tracked quest, and is on no page looked at the next time
    the book is open while another quest is tracked, is done. `see_tracked`
    is the tracker's best guess at the quest being played (the HUD), and
    marks nothing done.
    """

    def __init__(self, path: Path):
        self.path = path
        data = read_json(path)
        self.quests: dict = data.get("quests") or {}
        self.book_active: str = data.get("book_active") or ""
        self.tracked: str = data.get("tracked") or ""
        self.book_read: float = float(data.get("book_read") or 0)
        self.events: list = list(data.get("events") or [])
        self._session: set | None = None  # names seen while the book has been open this time
        self._dirty = False

    def save(self, force: bool = False) -> None:
        if not (self._dirty or force):
            return
        write_json(self.path, {
            "version": 1,
            "quests": self.quests,
            "book_active": self.book_active,
            "tracked": self.tracked,
            "book_read": self.book_read,
            "events": self.events[-EVENTS_KEPT:],
        })
        self._dirty = False

    def _event(self, kind: str, quest: str, now: float, **extra) -> None:
        self.events.append({"t": now, "kind": kind, "quest": quest, **extra})
        self.events = self.events[-EVENTS_KEPT:]
        self._dirty = True

    def _upsert(self, name: str, now: float, **info) -> dict:
        row = self.quests.get(name)
        if row is None:
            row = self.quests[name] = {"first": now, "last": now, "main": False, "area": "", "world": "",
                                       "goals": [], "done": None}
            self._event("new", name, now)
        row["last"] = now
        if row.get("done"):
            row["done"] = None
            row.pop("how", None)
            self._event("back", name, now)
        for key in ("area", "world"):
            if info.get(key):
                row[key] = info[key]
        if "main" in info and info["main"] is not None:
            row["main"] = bool(info["main"])
        goal = info.get("goal") or ""
        if goal and goal not in row["goals"]:
            row["goals"] = (row["goals"] + [goal])[-GOALS_KEPT:]
        self._dirty = True
        return row

    def book_closed(self) -> None:
        self._session = None

    def see_book(self, entries: list, now: float) -> list:
        """One page of the open quest book: [{name, area, world, goal, main,
        active}]. Returns the quests found done by it."""
        if self._session is None:
            self._session = set()
        done: list = []
        active = ""
        for e in entries:
            name = (e.get("name") or "").strip()
            if not name:
                continue
            self._session.add(name)
            self._upsert(name, now, area=e.get("area"), world=e.get("world"), goal=e.get("goal"),
                         main=e.get("main"))
            if e.get("active"):
                active = name
        if entries:
            self.book_read = now
            self._dirty = True
        if active and active != self.book_active:
            before = self.book_active
            if before and before not in self._session and before in self.quests:
                row = self.quests[before]
                if not row.get("done"):
                    row["done"] = now
                    row["how"] = "left the quest book"
                    self._event("done", before, now)
                    done.append(before)
            self.book_active = active
            self._dirty = True
        if active:
            self.set_tracked(active, now)
        return done

    def set_tracked(self, name: str, now: float) -> None:
        if name and name != self.tracked:
            self.tracked = name
            self._event("tracked", name, now)

    def quest_for_goal(self, goal: str) -> str:
        """The one not-done quest whose recorded goals include `goal` ("" if none or several)."""
        if not goal:
            return ""
        hits = [n for n, row in self.quests.items()
                if not row.get("done") and goal in (row.get("goals") or [])]
        if self.tracked in hits:
            return self.tracked
        return hits[0] if len(hits) == 1 else ""

    def see_hud(self, texts: list, goal: str, known_names: dict, now: float) -> str:
        """The HUD's texts: a quest name among them (`known_names`: norm ->
        name) or a goal recorded for one quest makes it the tracked quest."""
        for t in texts:
            name = known_names.get(norm(t))
            if name:
                if name in self.quests:
                    self._upsert(name, now, goal=goal)
                self.set_tracked(name, now)
                return name
        name = self.quest_for_goal(goal)
        if name:
            self.set_tracked(name, now)
        return name


# --- the page's numbers -------------------------------------------------------


class _Lookup:
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


def _docs(docs: Path) -> tuple:
    """The lists, reread when a file in docs/quests or docs/sidequests changes."""
    stamp = []
    for sub in ("quests", "sidequests"):
        folder = docs / sub
        for f in sorted(folder.glob("*.txt")) if folder.is_dir() else ():
            try:
                stamp.append((f.name, f.stat().st_mtime))
            except OSError:
                pass
    key = (str(docs), tuple(stamp))
    if _cache.get("key") != key:
        lists = load_lists(docs)
        _cache.update(key=key, lists=lists, guides=load_side_guides(docs), lookup=_Lookup(lists))
    return _cache["lists"], _cache["guides"], _cache["lookup"]


def known_names(docs: Path, memory: QuestMemory | None = None) -> dict:
    """norm -> quest name, for every listed, guide and seen quest (the HUD matcher)."""
    lists, guides, _ = _docs(docs)
    out = {}
    for listed in lists.values():
        for q in listed:
            out.setdefault(norm(q.name), q.name)
    for sides in guides.values():
        for s in sides:
            out.setdefault(norm(s.name), s.name)
    for name in (memory.quests if memory else {}):
        out[norm(name)] = name
    return out


def _pct(done: int, total: int) -> int:
    return int(100 * done / total) if total else 0


def build(docs: Path, state: Path, now: float | None = None) -> dict:
    """Everything the page shows, as one JSON-able dict."""
    now = time.time() if now is None else now
    lists, guides, lookup = _docs(docs)
    seen_data = read_json(state / SEEN_FILE)
    seen: dict = seen_data.get("quests") or {}
    marks: dict = read_json(state / MARKS_FILE).get("marks") or {}
    live = read_json(state / LIVE_FILE)
    fresh = bool(live) and now - float(live.get("time") or 0) < LIVE_STALE
    running = fresh and live.get("state") != "stopped"

    # Seen quests by world: story ones by their list, side ones by the book's world.
    reached: dict = {}  # world -> furthest story position seen
    side_seen: dict = {}  # world -> {norm: (name, row)}
    for name, row in seen.items():
        hit = lookup.find(name)
        if hit:
            world, q = hit
            reached[world] = max(reached.get(world, 0), q.index)
            continue
        world = canonical_world(row.get("world") or "") or "Other"
        side_seen.setdefault(world, {})[norm(name)] = (name, row)

    live_world = world_of_zone(live.get("zone") or "") if live else ""
    here_index = max((WORLDS.index(w) for w in reached if w in WORLDS), default=-1)
    if live_world in WORLDS:
        here_index = max(here_index, WORLDS.index(live_world))
    here = WORLDS[here_index] if here_index >= 0 else ""
    tracked = (live.get("tracked") if live else "") or seen_data.get("tracked") or ""
    tracked_hit = lookup.find(tracked) if tracked else None
    tracked_row = seen.get(tracked) or {}
    tracked_world = tracked_hit[0] if tracked_hit else canonical_world(tracked_row.get("world", ""))
    # The world being played: the tracked quest's, else where the wizard stands.
    current_world = tracked_world or live_world or here

    seen_by_norm = {norm(n): row for n, row in seen.items()}

    def story_status(world: str, q: ListedQuest) -> tuple:
        mark = marks.get(norm(q.name))
        if mark in ("done", "todo"):
            return mark, "you"
        row = seen_by_norm.get(norm(q.name))
        if row and row.get("done"):
            return "done", row.get("how") or "seen done"
        if world in WORLDS and 0 <= WORLDS.index(world) < here_index:
            return "done", "a later world is reached"
        top = reached.get(world, 0)
        if q.index < top:
            return "done", "a later story quest is reached"
        if row:
            return "active", "in the quest book"
        return "todo", ""

    def side_status(name: str, row: dict | None) -> tuple:
        mark = marks.get(norm(name))
        if mark in ("done", "todo"):
            return mark, "you"
        if row and row.get("done"):
            return "done", row.get("how") or ""
        if row:
            return "active", "in the quest book"
        return "todo", ""

    worlds = []
    for name in (*WORLDS, *SIDE_WORLDS, *sorted(set(side_seen) - set(WORLDS) - set(SIDE_WORLDS))):
        listed = lists.get(name, [])
        areas: list = []
        for q in listed:
            if not areas or areas[-1]["name"] != q.area:
                areas.append({"name": q.area, "quests": [], "side_floor": 0})
            status, how = story_status(name, q)
            areas[-1]["quests"].append({"index": q.index, "name": q.name, "tags": q.tags, "status": status,
                                        "how": how, "tracked": norm(q.name) == norm(tracked)})
            areas[-1]["side_floor"] += q.sides
        for a in areas:
            a["total"] = len(a["quests"])
            a["done"] = sum(x["status"] == "done" for x in a["quests"])
            a["pct"] = _pct(a["done"], a["total"])
        done = sum(a["done"] for a in areas)

        # Side quests: the giver guide's, then any other seen in the book for this world.
        sides: dict = {}
        mine = side_seen.get(name, {})
        for s in guides.get(name, []):
            if lookup.find(s.name):
                continue  # (a story quest the guide lists as a side quest)
            key = norm(s.name)
            row = mine.get(key, (None, None))[1]
            status, how = side_status(s.name, row)
            sides.setdefault(key, {"name": s.name, "giver": s.giver, "area": s.area, "status": status,
                                   "how": how, "tracked": key == norm(tracked)})
        for key, (qname, row) in mine.items():
            if key in sides:
                continue
            status, how = side_status(qname, row)
            sides[key] = {"name": qname, "giver": "", "area": row.get("area") or "", "status": status,
                          "how": how, "tracked": key == norm(tracked)}
        side_rows = sorted(sides.values(), key=lambda r: (r["status"] == "done", r["area"], r["name"]))
        side_done = sum(r["status"] == "done" for r in side_rows)
        side_floor = sum(a["side_floor"] for a in areas)
        is_side = name in SIDE_WORLDS
        i = WORLDS.index(name) if name in WORLDS else -1
        worlds.append({
            "name": name,
            "side": is_side or name not in WORLDS,
            "arc": SIDE_WORLDS.get(name) or next((a for a, ws in ARCS if name in ws), ""),
            "has_list": bool(listed),
            "total": len(listed),
            "done": done,
            "pct": _pct(done, len(listed)) if listed else (100 if 0 <= i < here_index else 0),
            "current": name == current_world,
            "reached": 0 <= i <= here_index or (i < 0 and (done > 0 or bool(mine) or name == current_world)),
            "areas": areas,
            "side_quests": side_rows,
            "side_done": side_done,
            "side_total": max(len(side_rows), side_floor),
        })

    by_name = {w["name"]: w for w in worlds}
    arcs = []
    for arc, names in ARCS:
        members = [by_name[n] for n in names if n in by_name]
        listed = [w for w in members if w["has_list"]]
        total = sum(w["total"] for w in listed)
        done = sum(w["done"] for w in listed)
        arcs.append({
            "name": arc,
            "worlds": list(names),
            "side_worlds": [w for w, a in SIDE_WORLDS.items() if a == arc],
            "total": total,
            "done": done,
            "pct": _pct(done, total),
            "current": any(w["current"] for w in members),
        })
    story_total = sum(w["total"] for w in worlds if w["name"] in WORLDS)
    story_done = sum(w["done"] for w in worlds if w["name"] in WORLDS)

    goal = (live.get("goal") or "") if live else ""
    current = {
        "world": current_world,
        "zone": (live.get("zone") or "") if live else "",
        "area": (live.get("area") or "") if live else "",
        "goal": goal,
        "quest": tracked,
        "quest_world": tracked_world,
        "main": bool(tracked_hit) or bool(tracked_row.get("main")),
        "index": tracked_hit[1].index if tracked_hit else None,
        "book_open": bool(live.get("book_open")) if live else False,
    }
    recent = [e for e in reversed(seen_data.get("events") or []) if e.get("kind") in ("done", "new")][:15]
    return {
        "updated": now,
        "running": running,
        "live_age": (now - float(live.get("time") or 0)) if live else None,
        "live_state": (live.get("state") or "") if live else "",
        "book_read": float(seen_data.get("book_read") or 0),
        "seen_count": len(seen),
        "current": current,
        "story": {"done": story_done, "total": story_total, "pct": _pct(story_done, story_total)},
        "arcs": arcs,
        "worlds": worlds,
        "recent": recent,
    }


def set_mark(state: Path, name: str, status: str) -> dict:
    """The player's tick on the page: "done", "todo", or "auto" (take it back)."""
    path = state / MARKS_FILE
    data = read_json(path)
    marks = data.get("marks") or {}
    key = norm(name)
    if not key:
        return marks
    if status in ("done", "todo"):
        marks[key] = status
    else:
        marks.pop(key, None)
    write_json(path, {"marks": marks})
    return marks
