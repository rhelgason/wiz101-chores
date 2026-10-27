"""`mode: tracker`: read quest progress while the player plays. Hands off.

`mac/run.sh tracker` connects like the other modes (the same recorded,
adopted data hooks; no mouse hook, no keys, no clicks, no focus requests)
and then only reads:

- the HUD's quest helper (QuestHelperHud): the goal on screen, and the
  quest's name when one of its texts is a known quest;
- the quest book, whenever the player has it open (Q): every quest on the
  page, with its area, world, goal, the main-story flag and which one is
  tracked. Turning the pages reads them all;
- the zone, and its place name.

What it learns goes to state/quest_seen.json (spiral.QuestMemory) and
state/tracker_live.json; mac/spiral_web.py turns those into the page.
The first time the HUD and the book are seen their window trees go to the
run folder (quest_hud_window.txt, quest_book_window.txt), and game log lines
that mention quests to quest_log_lines.txt, to learn what more can be read.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from loguru import logger

from . import blackbox, ui
from .spiral import LIVE_FILE, SEEN_FILE, QuestMemory, canonical_world, known_names, world_of_zone, write_json

POLL = 1.0  # seconds between readings, book closed
BOOK_POLL = 0.35  # with the book open: catch every page the player turns past
MAX_QUEST_SLOTS = 6
QUEST_HUD = ["WorldView", "windowHUD", "QuestHelperHud"]
# Quest book window paths (mapped by Deimos; the bot read them until 10-05).
QUEST_LIST = ["WorldView", "DeckConfiguration", "wndQuestList"]
BOOK_FIELDS = {"txtName", "txtWorld", "txtGoal", "txtZone", "LeftMainline", "imgActiveQuest"}
DOCS = Path("docs")
STATE = Path("state")
NAMES_EVERY = 30.0  # rebuild the known-name table this often (new quests seen)
QUEST_LOG_KEEP = 2000  # quest lines copied from the game log per run, at most


async def hud_texts(client) -> list[str]:
    """Every visible text under the quest helper, top to bottom."""
    hud = await ui.window_at(client, QUEST_HUD)
    if hud is None or not await ui.window_visible(hud):
        return []
    out: list[str] = []

    async def walk(w, depth: int):
        if depth > 8 or len(out) > 30:
            return
        try:
            if not await w.is_visible():
                return
            text = await ui.window_text(w)
            if text and text not in out:
                out.append(text)
            for k in await w.children():
                await walk(k, depth + 1)
        except Exception:
            return

    await walk(hud, 0)
    return out


async def book_page(client) -> list[dict] | None:
    """The open quest book's page: one dict per quest. None when the book is closed."""
    panel = await ui.window_at(client, QUEST_LIST)
    if panel is None or not await ui.window_visible(panel):
        return None
    slots = await ui.find_named(panel, {f"wndQuestInfo{i}" for i in range(MAX_QUEST_SLOTS)}, max_depth=2)
    out = []
    for i in range(MAX_QUEST_SLOTS):
        base = slots.get(f"wndQuestInfo{i}")
        if base is None or not await ui.window_visible(base):
            continue
        for part in ("questInfoWindow", "wndQuestInfo"):
            base = (await ui.find_named(base, {part}, max_depth=2)).get(part) if base else None
        if base is None:
            continue
        w = await ui.find_named(base, BOOK_FIELDS, max_depth=4)
        name = await ui.window_text(w.get("txtName"))
        if not name:
            continue
        out.append({
            "name": name,
            "area": await ui.window_text(w.get("txtWorld")),  # the book's "world" line is the area
            "world": canonical_world(await ui.window_text(w.get("txtZone"))),
            "goal": await ui.window_text(w.get("txtGoal")),
            "main": await ui.window_visible(w.get("LeftMainline")),
            "active": await ui.window_visible(w.get("imgActiveQuest")),
        })
    return out


def place_name(zone: str) -> str:
    """"MooShu/MS_War/MS_War_BattlefieldA" -> "Crimson Fields" ("" for interiors)."""
    if not zone:
        return ""
    with contextlib.suppress(Exception):
        from .travel_data import _data

        name = next((n for n, z in _data()[1] if z == zone), "")
        return name.title().replace("'S", "'s")
    return ""


class GameLogQuests:
    """Game log lines that mention a quest, copied to the run folder: the log may
    say when a quest is finished, which memory does not say plainly."""

    def __init__(self):
        from .gamelog import LogWatch

        self.watch = LogWatch()
        self.kept = 0

    def poll(self) -> None:
        if self.kept >= QUEST_LOG_KEEP:
            return
        try:
            text = self.watch.read_new()
        except Exception:
            return
        lines = [ln for ln in text.splitlines() if "quest" in ln.lower()]
        run = blackbox.folder()
        if not lines or run is None:
            return
        lines = lines[: QUEST_LOG_KEEP - self.kept]
        self.kept += len(lines)
        with contextlib.suppress(OSError), (run / "quest_log_lines.txt").open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


async def _dump_once(client, path: list[str], filename: str, done: set) -> None:
    if filename in done:
        return
    run = blackbox.folder()
    w = await ui.window_at(client, path)
    if run is None or w is None:
        return
    done.add(filename)
    with contextlib.suppress(Exception):
        lines = await ui.dump_tree(w, max_depth=12, only_visible=False, with_types=True)
        (run / filename).write_text("\n".join(lines), encoding="utf-8", errors="replace")


def write_live(**data) -> None:
    with contextlib.suppress(OSError):
        write_json(STATE / LIVE_FILE, {"time": time.time(), **data})


async def track_loop(client, controller, task: dict) -> None:
    memory = QuestMemory(STATE / SEEN_FILE)
    names: dict = {}
    names_at = 0.0
    dumped: set = set()
    game_log = GameLogQuests()
    last_goal = last_tracked = last_zone = ""
    book_was_open = False
    task["name"] = "tracking quests"
    logger.info("tracker: reading the quest helper, and the quest book whenever it is open. "
                "Nothing is pressed or clicked. The page: http://127.0.0.1:8101/")
    blackbox.event("tracker_start", known=len(memory.quests))
    try:
        while not controller.stopped.is_set():
            await controller.checkpoint()  # (the pause key holds the readings too)
            now = time.time()
            if now - names_at > NAMES_EVERY:
                names, names_at = known_names(DOCS, memory), now
            zone = ""
            with contextlib.suppress(Exception):
                zone = await client.zone_name() or ""
            texts = await hud_texts(client)
            goal = ""
            with contextlib.suppress(Exception):
                goal = await ui.quest_goal(client)
            if texts:
                await _dump_once(client, QUEST_HUD, "quest_hud_window.txt", dumped)
            page = await book_page(client)
            if page is not None:
                await _dump_once(client, QUEST_LIST, "quest_book_window.txt", dumped)
                for name in memory.see_book(page, now):
                    logger.success(f"tracker: quest done: {name!r} (left the quest book)")
                    blackbox.event("quest_done", quest=name, how="left the quest book")
                book_was_open = True
            elif book_was_open:
                memory.book_closed()
                book_was_open = False
            memory.see_hud(texts, goal, names, now)
            if memory.tracked != last_tracked:
                last_tracked = memory.tracked
                logger.info(f"tracker: tracked quest: {memory.tracked!r}")
                blackbox.event("quest_tracked", quest=memory.tracked)
            if goal != last_goal:
                last_goal = goal
                blackbox.event("quest_goal", goal=goal)
            if zone != last_zone:
                last_zone = zone
                blackbox.event("tracker_zone", zone=zone)
            memory.save()
            write_live(state="running", zone=zone, world=world_of_zone(zone), area=place_name(zone),
                       goal=goal, hud=texts, tracked=memory.tracked, book_open=page is not None,
                       book=page or [])
            game_log.poll()
            await asyncio.sleep(BOOK_POLL if page is not None else POLL)
    finally:
        memory.save(force=True)
        write_live(state="stopped", zone=last_zone, world=world_of_zone(last_zone),
                   area=place_name(last_zone), goal=last_goal, tracked=memory.tracked,
                   book_open=False, book=[])
