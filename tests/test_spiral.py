"""The Spiral Progress numbers (spiral.py) and the tracker's readings (tracker.py)."""

import asyncio
from pathlib import Path

from wiz101_auto import spiral
from wiz101_auto.spiral import (
    QuestMemory,
    build,
    canonical_world,
    parse_quest_list,
    parse_side_guide,
    world_of_zone,
)

REPO_DOCS = Path(__file__).resolve().parent.parent / "docs"

WC = """Unicorn Way (3 quests)
1.
Unicorn Way
INSTANCE
SIDE ×1
2.
Ghost Hunters
SIDE ×2
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

GUIDE = """Krokotopia side quests by giver.

ROYAL HALL
(MAIN QUEST)

Prospector Zeke
A Whole New World (100 XP)
- Talk to Zeke

(SIDE QUEST)

Sergeant Skeets
Sand in My Shoes (170 XP)
- Defeat 3 Sand Snakes

Captain Grubb
Lost Lamp (55 gold, 200 XP)
- Find the lamp
"""


def docs(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    (d / "quests").mkdir(parents=True)
    (d / "sidequests").mkdir()
    (d / "quests" / "Wizard City.txt").write_text(WC, encoding="utf-8")
    (d / "quests" / "Krokotopia.txt").write_text(KT, encoding="utf-8")
    (d / "sidequests" / "Krokotopia.txt").write_text(GUIDE, encoding="utf-8")
    return d


def entry(name, active=False, main=False, world="Krokotopia", area="Royal Hall", goal=""):
    return {"name": name, "active": active, "main": main, "world": world, "area": area, "goal": goal}


def test_parse_list_areas_tags_and_side_counts():
    qs = parse_quest_list(WC)
    names = ["Unicorn Way", "Ghost Hunters", "Skeleton Crew", "Gone Fishing", "Last Stand"]
    assert [q.name for q in qs] == names
    assert [q.area for q in qs] == ["Unicorn Way"] * 3 + ["Triton Avenue"] * 2
    assert qs[1].tags == ["SIDE ×2", "MOB"] and qs[1].sides == 2


def test_a_quest_listed_again_under_its_number_is_one_quest():
    text = "A (1 quests)\n1.\nThe King's Fourth\nTALK\nB (1 quests)\n1.\nThe Kings Forth\nBOSS\n"
    qs = parse_quest_list(text)
    assert len(qs) == 1 and qs[0].tags == ["TALK", "BOSS"]


def test_side_guide_leaves_out_main_quests():
    sides = parse_side_guide(GUIDE)
    assert [(s.name, s.giver, s.area) for s in sides] == [
        ("Sand in My Shoes", "Sergeant Skeets", "Royal Hall"),
        ("Lost Lamp", "Captain Grubb", "Royal Hall"),
    ]


def test_world_names():
    assert world_of_zone("WizardCity/WC_Hub") == "Wizard City"
    assert world_of_zone("DragonSpire/DS_Hub") == "Dragonspyre"
    assert world_of_zone("Grizzleheim/GH_HFjord/GH_Something") == "Wintertusk"
    assert world_of_zone("") == ""
    assert canonical_world("Wizard City") == "Wizard City"
    assert canonical_world("Nowhere") == ""


def test_the_tracked_quest_that_left_the_book_is_done(tmp_path):
    m = QuestMemory(tmp_path / "seen.json")
    m.see_book([entry("Lost Lamp", active=True), entry("Sand in My Shoes")], 1.0)
    m.book_closed()
    done = m.see_book([entry("Sand in My Shoes", active=True)], 2.0)
    assert done == ["Lost Lamp"]
    assert m.quests["Lost Lamp"]["done"] == 2.0
    m.save()
    again = QuestMemory(tmp_path / "seen.json")
    assert again.quests["Lost Lamp"]["done"] == 2.0 and again.book_active == "Sand in My Shoes"


def test_switching_the_tracked_quest_by_hand_finishes_nothing(tmp_path):
    m = QuestMemory(tmp_path / "seen.json")
    m.see_book([entry("A", active=True), entry("B")], 1.0)
    m.book_closed()
    # Open again on page 1 (A on it), turn to page 2, track C there.
    assert m.see_book([entry("A", active=True), entry("B")], 2.0) == []
    assert m.see_book([entry("C", active=True)], 3.0) == []
    assert not m.quests["A"]["done"]


def test_a_done_quest_seen_again_is_not_done(tmp_path):
    m = QuestMemory(tmp_path / "seen.json")
    m.see_book([entry("A", active=True)], 1.0)
    m.book_closed()
    m.see_book([entry("B", active=True)], 2.0)
    assert m.quests["A"]["done"]
    m.book_closed()
    m.see_book([entry("A"), entry("B", active=True)], 3.0)
    assert m.quests["A"]["done"] is None
    assert [e["kind"] for e in m.events][-1] == "back"


def test_hud_name_or_a_known_goal_names_the_tracked_quest(tmp_path):
    m = QuestMemory(tmp_path / "seen.json")
    m.see_book([entry("Lost Lamp", goal="Find the lamp"), entry("Sand", goal="Defeat Snakes")], 1.0)
    names = {spiral.norm("Ghost Hunters"): "Ghost Hunters"}
    assert m.see_hud(["Ghost Hunters", "Defeat Ghosts"], "Defeat Ghosts", names, 2.0) == "Ghost Hunters"
    assert m.tracked == "Ghost Hunters"
    assert m.see_hud(["Find the lamp"], "Find the lamp", {}, 3.0) == "Lost Lamp"
    assert m.tracked == "Lost Lamp"
    # A goal two quests share names neither.
    m.see_book([entry("Other", goal="Find the lamp")], 4.0)
    m.tracked = ""
    assert m.see_hud(["Find the lamp"], "Find the lamp", {}, 5.0) == ""


def test_build_story_order_and_earlier_worlds(tmp_path):
    d = docs(tmp_path)
    state = tmp_path / "state"
    m = QuestMemory(state / spiral.SEEN_FILE)
    m.see_book([entry("Make Some Rubbings", active=True, main=True),
                entry("Sand in My Shoes", world="Krokotopia")], 100.0)
    m.save()
    live = {"time": 100.0, "state": "running", "zone": "Krokotopia/KT_Hub", "goal": "Make some rubbings",
            "tracked": "Make Some Rubbings"}
    spiral.write_json(state / spiral.LIVE_FILE, live)
    data = build(d, state, now=101.0)
    assert data["running"]
    worlds = {w["name"]: w for w in data["worlds"]}
    assert worlds["Wizard City"]["pct"] == 100
    kt = worlds["Krokotopia"]
    assert kt["current"] and (kt["done"], kt["total"]) == (2, 3)
    statuses = [q["status"] for a in kt["areas"] for q in a["quests"]]
    assert statuses == ["done", "done", "active"]
    assert kt["areas"][0]["quests"][2]["tracked"]
    sides = {s["name"]: s["status"] for s in kt["side_quests"]}
    assert sides == {"Sand in My Shoes": "active", "Lost Lamp": "todo"}
    assert kt["side_total"] == 2
    assert data["current"]["world"] == "Krokotopia" and data["current"]["main"]
    assert data["current"]["index"] == 3
    assert data["story"] == {"done": 7, "total": 8, "pct": 87}
    assert worlds["Marleybone"]["pct"] == 0 and not worlds["Marleybone"]["reached"]


def test_build_stale_live_file_is_not_running(tmp_path):
    state = tmp_path / "state"
    live = {"time": 100.0, "state": "running", "zone": "WizardCity/WC_Hub"}
    spiral.write_json(state / spiral.LIVE_FILE, live)
    data = build(docs(tmp_path), state, now=100.0 + spiral.LIVE_STALE + 1)
    assert not data["running"]
    assert data["current"]["world"] == "Wizard City"


def test_marks_win_and_auto_gives_back(tmp_path):
    d, state = docs(tmp_path), tmp_path / "state"
    spiral.set_mark(state, "Ghost Hunters", "done")
    spiral.set_mark(state, "Lost Lamp", "done")
    wc = next(w for w in build(d, state)["worlds"] if w["name"] == "Wizard City")
    assert [q["status"] for q in wc["areas"][0]["quests"]] == ["todo", "done", "todo"]
    assert wc["areas"][0]["quests"][1]["how"] == "you"
    spiral.set_mark(state, "Ghost Hunters", "auto")
    wc = next(w for w in build(d, state)["worlds"] if w["name"] == "Wizard City")
    assert wc["done"] == 0


def test_the_repos_lists_load():
    lists = spiral.load_lists(REPO_DOCS)
    assert set(spiral.WORLDS) - set(lists) == set()
    assert len(lists["Wizard City"]) == 38 and len(lists["Khrysalis"]) > 200
    assert all(qs[-1].index == len(qs) for qs in lists.values())  # numbered 1..n, no gaps
    assert all(q.name and q.area for qs in lists.values() for q in qs)
    guides = spiral.load_side_guides(REPO_DOCS)
    assert {"MooShu", "Marleybone", "Dragonspyre", "Zafaria"} <= set(guides)
    data = build(REPO_DOCS, Path("/nonexistent-state"))
    assert data["story"]["total"] > 2000 and data["story"]["done"] == 0


# --- tracker.py's window reads, on fake windows --------------------------------


class Win:
    def __init__(self, name, kids=(), text="", visible=True):
        self._name, self._kids, self._text, self._visible = name, list(kids), text, visible

    async def name(self):
        return self._name

    async def children(self):
        return self._kids

    async def is_visible(self):
        return self._visible

    async def maybe_text(self):
        return self._text

    async def get_windows_with_name(self, name):
        out = []
        for k in self._kids:
            if k._name == name:
                out.append(k)
            out.extend(await k.get_windows_with_name(name))
        return out


def slot(i, name, world, area, goal, main=False, active=False):
    fields = Win("wndQuestInfo", [
        Win("txtName", text=f"<center>{name}</center>"), Win("txtZone", text=world),
        Win("txtWorld", text=area),
        Win("txtGoal", text=goal), Win("LeftMainline", visible=main), Win("imgActiveQuest", visible=active),
    ])
    return Win(f"wndQuestInfo{i}", [Win("questInfoWindow", [fields])])


class Client:
    def __init__(self, root):
        self.root_window = root


def test_tracker_reads_the_open_quest_book_and_the_hud():
    from wiz101_auto import tracker

    book = Win("wndQuestList", [
        slot(0, "Make Some Rubbings", "Krokotopia", "Royal Hall", "Find rubbings", main=True, active=True),
        slot(1, "Lost Lamp", "Krokotopia", "Royal Hall", "Find the lamp"),
        slot(2, "", "", "", ""),
    ])
    hud = Win("QuestHelperHud", [Win("ElementWindow", [Win("", [
        Win("txtGoalName", text="Find rubbings"), Win("old", text="stale", visible=False)])])])
    root = Win("root", [Win("WorldView", [Win("DeckConfiguration", [book]), Win("windowHUD", [hud])])])
    client = Client(root)
    page = asyncio.run(tracker.book_page(client))
    assert page == [
        {"name": "Make Some Rubbings", "area": "Royal Hall", "world": "Krokotopia", "goal": "Find rubbings",
         "main": True, "active": True},
        {"name": "Lost Lamp", "area": "Royal Hall", "world": "Krokotopia", "goal": "Find the lamp",
         "main": False, "active": False},
    ]
    assert asyncio.run(tracker.hud_texts(client)) == ["Find rubbings"]
    book._visible = False
    assert asyncio.run(tracker.book_page(client)) is None
