"""`mode: tracker`: read story progress while the player plays. Hands off.

`mac/run.sh tracker` connects like the other modes (the same recorded,
adopted data hooks; no mouse hook, no keys, no clicks, no focus requests)
and then only reads, every POLL seconds:

- the game's own quest list, from memory (WizWalker's `quest_manager()`:
  found once by a pattern scan of the game module, then a std::map of
  quest id -> QuestData; each has a name lang key and a main-story flag),
  and the tracked quest's id (`quest_id()`, the character registry). No
  hook, no write: Deimos reads the same for its quest scripts;
- the goal on the HUD's quest helper, the zone and its place name.

The main-story quests feed spiral.Progress (state/quest_progress.json); the
reading itself goes to state/tracker_live.json. mac/spiral_web.py turns
those into the page.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from loguru import logger

from . import blackbox, ui
from .names import lang_name
from .spiral import LIVE_FILE, PROGRESS_FILE, Progress, lists_and_lookup, world_of_zone, write_json

POLL = 2.0  # seconds between readings
READ_TIMEOUT = 10.0  # one reading of the quest list, at most (a map read mid-change can wander)
DOCS = Path("docs")
STATE = Path("state")


async def read_quests(client) -> tuple[list[dict], str]:
    """The quests in the book, from memory: [{id, name, main}], and the tracked quest's name."""
    manager = await client.quest_manager()
    quests = await manager.quest_data()
    tracked_id = 0
    with contextlib.suppress(Exception):
        tracked_id = int(await client.quest_id())
    out = []
    tracked = ""
    for quest_id, quest in quests.items():
        key = await quest.name_lang_key()
        name = key if key == "Quest Finder" else await lang_name(client, key)
        if not name:
            continue
        main = False
        with contextlib.suppress(Exception):
            main = bool(await quest.mainline())
        out.append({"id": int(quest_id), "name": name, "main": main})
        if int(quest_id) == tracked_id:
            tracked = name
    return out, tracked


def place_name(zone: str) -> str:
    """"MooShu/MS_War/MS_War_BattlefieldA" -> "Crimson Fields" ("" for interiors)."""
    if not zone:
        return ""
    with contextlib.suppress(Exception):
        from .travel_data import _data

        name = next((n for n, z in _data()[1] if z == zone), "")
        return name.title().replace("'S", "'s")
    return ""


def write_live(**data) -> None:
    with contextlib.suppress(OSError):
        write_json(STATE / LIVE_FILE, {"time": time.time(), **data})


async def track_loop(client, controller, task: dict) -> None:
    _lists, lookup = lists_and_lookup(DOCS)
    progress = Progress(STATE / PROGRESS_FILE, lookup)
    task["name"] = "tracking quests"
    logger.info("tracker: reading the game's quest list from memory. Nothing is pressed or clicked. "
                "The page: http://127.0.0.1:8101/")
    blackbox.event("tracker_start", reached=progress.reached)
    last = {"zone": "", "goal": "", "tracked": "", "main": [], "error": ""}
    try:
        while not controller.stopped.is_set():
            await controller.checkpoint()  # (the pause key holds the readings too)
            now = time.time()
            zone = ""
            with contextlib.suppress(Exception):
                zone = await client.zone_name() or ""
            goal = ""
            with contextlib.suppress(Exception):
                goal = await ui.quest_goal(client)
            ok = True
            try:
                quests, tracked = await asyncio.wait_for(read_quests(client), READ_TIMEOUT)
            except Exception as exc:  # a loading screen, a logout, or a game patch that moved it
                ok, quests, tracked = False, None, last["tracked"]
                why = f"{type(exc).__name__}: {exc}"
                if why != last["error"]:
                    last["error"] = why
                    logger.warning(f"tracker: could not read the quest list ({why})")
                    blackbox.event("quest_read_failed", why=why)
            if quests is not None:
                last["error"] = ""
                main = [q["name"] for q in quests if q["main"]]
                for name in progress.see(main, now):
                    logger.success(f"tracker: story quest {name!r}")
                    blackbox.event("story_quest", quest=name)
                if main != last["main"]:
                    blackbox.event("quest_book", main=main, all=[q["name"] for q in quests])
                last["main"] = main
                progress.save()
            if tracked != last["tracked"]:
                logger.info(f"tracker: tracked quest: {tracked!r}")
                blackbox.event("quest_tracked", quest=tracked)
            if goal != last["goal"]:
                blackbox.event("quest_goal", goal=goal)
            if zone != last["zone"]:
                blackbox.event("tracker_zone", zone=zone)
            last.update(zone=zone, goal=goal, tracked=tracked)
            write_live(state="running", zone=zone, world=world_of_zone(zone), area=place_name(zone),
                       goal=goal, tracked=tracked, main=last["main"], quests_ok=ok)
            await asyncio.sleep(POLL)
    finally:
        progress.save(force=True)
        write_live(state="stopped", zone=last["zone"], world=world_of_zone(last["zone"]),
                   area=place_name(last["zone"]), goal=last["goal"], tracked=last["tracked"],
                   main=last["main"], quests_ok=False)
