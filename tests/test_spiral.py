"""The Spiral Progress numbers (spiral.py) and the tracker's memory read (tracker.py)."""

import asyncio
from pathlib import Path

from wiz101_auto import spiral
from wiz101_auto.spiral import Lookup, Progress, build, canonical_world, parse_quest_list, world_of_zone

REPO_DOCS = Path(__file__).resolve().parent.parent / "docs"

WC = """Unicorn Way (3 quests)
1.
Unicorn Way
INSTANCE
SIDE ×1
2.
Ghost Hunters
MOB
3.
Skeleton Crew
TALK
Triton Avenue (2 quests)
4.
Gone Fishing
TALK
5.
Last Stand
BOSS
"""

KT = """Royal Hall (3 quests)
1.
A Whole New World
2.
Digging in the Dirt
3.
Make Some Rubbings
"""


def docs(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    (d / "quests").mkdir(parents=True)
    (d / "quests" / "Wizard City.txt").write_text(WC, encoding="utf-8")
    (d / "quests" / "Krokotopia.txt").write_text(KT, encoding="utf-8")
    return d


def lookup(d: Path) -> Lookup:
    return spiral.lists_and_lookup(d)[1]


def live(state: Path, t: float, **data) -> None:
    spiral.write_json(state / spiral.LIVE_FILE, {"time": t, "state": "running", "quests_ok": True, **data})


def test_parse_list_areas_and_tags():
    qs = parse_quest_list(WC)
    names = ["Unicorn Way", "Ghost Hunters", "Skeleton Crew", "Gone Fishing", "Last Stand"]
    assert [q.name for q in qs] == names
    assert [q.area for q in qs] == ["Unicorn Way"] * 3 + ["Triton Avenue"] * 2
    assert qs[0].tags == ["INSTANCE", "SIDE ×1"]


def test_a_quest_listed_again_under_its_number_is_one_quest():
    text = "A (1 quests)\n1.\nThe King's Fourth\nTALK\nB (1 quests)\n1.\nThe Kings Forth\nBOSS\n"
    qs = parse_quest_list(text)
    assert len(qs) == 1 and qs[0].tags == ["TALK", "BOSS"]


def test_world_names():
    assert world_of_zone("WizardCity/WC_Hub") == "Wizard City"
    assert world_of_zone("DragonSpire/DS_Hub") == "Dragonspyre"
    assert world_of_zone("Grizzleheim/GH_HFjord/GH_Something") == "Wintertusk"
    assert world_of_zone("") == ""
    assert canonical_world("Nowhere") == ""


def test_progress_only_moves_forward_and_keeps_unlisted(tmp_path):
    d = docs(tmp_path)
    p = Progress(tmp_path / "progress.json", lookup(d))
    assert p.see(["Skeleton Crew", "Some Event Quest"], 1.0) == ["Skeleton Crew"]
    assert p.see(["Ghost Hunters"], 2.0) == []  # earlier on the list: nothing moves back
    assert p.see(["skeleton  crew!"], 3.0) == []
    assert p.see(["Gone Fishin"], 4.0) == ["Gone Fishing"]  # a close spelling
    p.save()
    again = Progress(tmp_path / "progress.json", lookup(d))
    assert again.reached["Wizard City"]["index"] == 4
    assert "Some Event Quest" in again.unlisted
    assert [e["quest"] for e in again.events] == ["Skeleton Crew", "Gone Fishing"]


def test_build_everything_before_the_story_quest_is_done(tmp_path):
    d, state = docs(tmp_path), tmp_path / "state"
    p = Progress(state / spiral.PROGRESS_FILE, lookup(d))
    p.see(["Make Some Rubbings"], 100.0)
    p.save()
    live(state, 100.0, zone="Krokotopia/KT_Hub", goal="Find rubbings", tracked="Make Some Rubbings",
         main=["Make Some Rubbings"])
    data = build(d, state, now=101.0)
    assert data["running"] and data["read_ok"]
    worlds = {w["name"]: w for w in data["worlds"]}
    assert worlds["Wizard City"]["pct"] == 100
    kt = worlds["Krokotopia"]
    assert kt["current"] and (kt["done"], kt["total"]) == (2, 3)
    assert [q["status"] for a in kt["areas"] for q in a["quests"]] == ["done", "done", "active"]
    assert kt["areas"][0]["quests"][2]["tracked"]
    assert data["current"]["main"] and data["current"]["index"] == 3
    assert data["story"] == {"done": 7, "total": 8, "pct": 87}
    assert not worlds["Marleybone"]["reached"]


def test_the_furthest_quest_gone_from_the_book_is_done(tmp_path):
    d, state = docs(tmp_path), tmp_path / "state"
    p = Progress(state / spiral.PROGRESS_FILE, lookup(d))
    p.see(["Make Some Rubbings"], 100.0)
    p.save()
    live(state, 200.0, zone="Krokotopia/KT_Hub", tracked="", main=[])
    kt = next(w for w in build(d, state, now=201.0)["worlds"] if w["name"] == "Krokotopia")
    assert kt["done"] == 3
    # Tracker not running: not known whether it is still in the book.
    live(state, 200.0, zone="Krokotopia/KT_Hub", tracked="", main=[])
    stale = build(d, state, now=200.0 + spiral.LIVE_STALE + 1)
    kt = next(w for w in stale["worlds"] if w["name"] == "Krokotopia")
    assert kt["done"] == 2


def test_build_without_progress_uses_the_zone(tmp_path):
    state = tmp_path / "state"
    live(state, 100.0, zone="WizardCity/WC_Hub", main=["Some Event Quest"])
    data = build(docs(tmp_path), state, now=100.0)
    assert data["current"]["world"] == "Wizard City"
    assert data["current"]["unlisted"] == ["Some Event Quest"]
    assert data["story"]["done"] == 0


def test_the_repos_lists_load():
    lists = spiral.load_lists(REPO_DOCS)
    assert set(spiral.WORLDS) - set(lists) == set()
    assert len(lists["Wizard City"]) == 38 and len(lists["Khrysalis"]) > 200
    assert all(qs[-1].index == len(qs) for qs in lists.values())  # numbered 1..n, no gaps
    assert all(q.name and q.area for qs in lists.values() for q in qs)
    data = build(REPO_DOCS, Path("/nonexistent-state"))
    assert data["story"]["total"] > 2000 and data["story"]["done"] == 0


# --- tracker.read_quests, on a fake client --------------------------------------


class FakeQuest:
    def __init__(self, key, main):
        self.key, self.main = key, main

    async def name_lang_key(self):
        return self.key

    async def mainline(self):
        return self.main


class FakeManager:
    def __init__(self, quests):
        self.quests = quests

    async def quest_data(self):
        return self.quests


class FakeClient:
    def __init__(self, quests, tracked):
        self._manager, self._tracked = FakeManager(quests), tracked

    async def quest_manager(self):
        return self._manager

    async def quest_id(self):
        return self._tracked


def test_tracker_reads_the_quest_list_from_memory(monkeypatch):
    from wiz101_auto import tracker

    names = {"Quests_1": "Make Some Rubbings", "Quests_2": "Lost Lamp"}

    async def lang_name(_client, key):
        return names.get(key, "")

    monkeypatch.setattr(tracker, "lang_name", lang_name)
    client = FakeClient({11: FakeQuest("Quests_1", True), 12: FakeQuest("Quests_2", False),
                         13: FakeQuest("Quests_404", True), 14: FakeQuest("Quest Finder", False)}, 11)
    quests, tracked = asyncio.run(tracker.read_quests(client))
    assert quests == [
        {"id": 11, "name": "Make Some Rubbings", "main": True},
        {"id": 12, "name": "Lost Lamp", "main": False},
        {"id": 14, "name": "Quest Finder", "main": False},
    ]
    assert tracked == "Make Some Rubbings"
