"""`mode: peril`: ride along with other wizards through the Portal of Peril.

The Portal of Peril (researched 10-30; nothing here was seen live yet):
Madam Magda's monthly event, one week a month (the Great Clock Gauntlet
until Mon 2 Nov 2026), level 30 and up. Its sigil sits by her tent in the
Fairegrounds of the Commons, near the Golem Court gate. Each entry spends a
credit: two a day to claim, four for members; unspent ones expire when the
event closes. The run is a Housing Gauntlet at tier 50, 100 or 150; everyone
is scaled up to the highest wizard on the sigil. Fortune Coins drop. A wizard
defeated inside may Dungeon Recall back in without another credit.

What the bot does (the player, 10-30: "just ride along with people"):
1. Walk to the Commons, find the sigil live (`SIGIL_NEEDLES`, else Magda),
   save its spot to `state/peril.json`, and stand on it. Once a day it uses
   the sigil once (click the prompt) and clicks a "Claim" button if one is
   shown, so the day's credits are claimed; every window it sees there goes
   to the run folder (`peril_sigil_window.txt`), and it closes the rest. It
   never starts a run itself: the tier is the group's choice.
2. Wait on the sigil for someone to start. A message box that asks about
   entering (credit, portal, gauntlet...) is accepted; one that says the
   credits are gone stops the run for the day. A small turn every
   `ANTI_AFK` keeps the server from logging the wizard out.
3. Inside (any zone reached from the sigil, until back in the Commons):
   walk into any duel circle (it is the group's: the instance is private),
   else follow the nearest other wizard. When every wizard has gone (a door
   to the next room, the exit), walk to where the last one was seen and use
   whatever is there (X, then W through a gate). Alone for
   `peril.alone_minutes`, leave by Go Home and walk back.
4. Fights inside: Mass Feint, Empower, Scarecrow (farm.plan_dungeon_round),
   never a flee. After a fight a potion if health is low.
5. Back in the Commons a run is counted (`state/peril.json`, per day);
   `peril.runs` (0: until the credits run out) stops the run.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import re
import time
from pathlib import Path

from loguru import logger

from . import blackbox

HUB = "WizardCity/WC_Hub"  # the Commons; the Fairegrounds are a corner of it
GOLEM_GATE = (-6302.26, 2653.47, 229.0)  # the Commons' gate to Golem Court: the tent is near it
STATE = Path("state") / "peril.json"  # {"sigil": [x, y, z], "days": {"2026-10-30": runs}, "claimed": day}
SIGIL_NEEDLES = ("peril", "gauntlet", "portal")  # object names that may be the event sigil (unseen)
SIGIL_WORD = "sigil"
MAGDA = "magda"  # Madam Magda stands at the tent: the fallback anchor
SEARCH_REACH = 6000.0  # sigil candidates this close to the Golem Court gate
SIGIL_REACH = 60.0  # standing this close to the sigil's spot counts as on it
ANTI_AFK = 240.0  # seconds between small turns on the sigil (the server's AFK kick came at ~35 min)
WAIT_TELL = 300.0  # log "still waiting" this often
TRAVEL_RETRY = 120.0
FOLLOW_GAP = 350.0  # closer than this to a group member: stand still
JOIN_REACH = 4000.0  # a duel circle this close is walked into
LOST_AFTER = 4.0  # seconds with no other wizard loaded before following their last spot
POTION_BELOW = 0.45
ENTER_WORDS = ("credit", "portal", "peril", "gauntlet", "enter", "dungeon", "pass", "scale", "tier", "join")
OUT_WORDS = ("no more", "not enough", "out of", "don't have any", "do not have any", "no remaining",
             "have 0", "0 remaining", "used all", "come back tomorrow")
LEAVE_WORDS = ("leave", "exit", "abandon", "quit")
YES_BUTTONS = ("rightButton", "centerButton", "okButton", "yesButton")
CLAIM_TEXTS = ("claim", "claim credits", "claim passes", "claim credit")


# ---- pure parts (tested) ------------------------------------------------------------------


def _flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def sigil_choice(rows: list[tuple[str, tuple[float, float, float]]], anchor=GOLEM_GATE):
    """(spot, why) for the Portal of Peril sigil among loaded objects (name, spot).

    Best: a name with the event's words and 'sigil'; then the event's words
    alone; then the sigil nearest Madam Magda; then Magda herself. Only
    within SEARCH_REACH of `anchor`. None when nothing fits.
    """
    near = [(n, p) for n, p in rows if math.dist(p[:2], anchor[:2]) <= SEARCH_REACH]
    flat = [(_flat(n), n, p) for n, p in near]

    def event(f: str) -> bool:
        return any(w in f for w in SIGIL_NEEDLES)

    for test, why in (
        (lambda f: event(f) and SIGIL_WORD in f, "named sigil"),
        (lambda f: event(f) and MAGDA not in f, "named object"),
    ):
        hits = [(n, p) for f, n, p in flat if test(f)]
        if hits:
            name, spot = min(hits, key=lambda h: math.dist(h[1][:2], anchor[:2]))
            return spot, f"{why} {name!r}"
    magda = [p for f, _n, p in flat if MAGDA in f]
    sigils = [(n, p) for f, n, p in flat if SIGIL_WORD in f]
    if magda and sigils:
        name, spot = min(sigils, key=lambda h: math.dist(h[1][:2], magda[0][:2]))
        if math.dist(spot[:2], magda[0][:2]) <= 1500:
            return spot, f"sigil {name!r} nearest Madam Magda"
    if magda:
        return magda[0], "Madam Magda (no sigil named)"
    return None


def box_kind(text: str) -> str:
    """'out' (no credits left), 'leave' (a question about leaving: never
    answered on the sigil), 'enter' (a question about going in), or 'other'."""
    low = (text or "").lower()
    about_credits = any(w in low for w in ("credit", "pass", "scale", "portal", "peril"))
    if about_credits and any(w in low for w in OUT_WORDS):
        return "out"
    if "flee" in low or any(w in low for w in LEAVE_WORDS):
        return "leave"
    if any(w in low for w in ENTER_WORDS):
        return "enter"
    return "other"


def follow_plan(me, players: list, circles: list, *, gap: float = FOLLOW_GAP, join: float = JOIN_REACH):
    """('join', circle) | ('follow', player) | ('stay', None) | ('lost', None)."""
    near_circles = [c for c in circles if math.dist(me[:2], c[:2]) <= join]
    if near_circles:
        return ("join", min(near_circles, key=lambda c: math.dist(me[:2], c[:2])))
    if not players:
        return ("lost", None)
    nearest = min(players, key=lambda p: math.dist(me[:2], p[:2]))
    if math.dist(me[:2], nearest[:2]) > gap:
        return ("follow", nearest)
    return ("stay", None)


def today(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y-%m-%d")


def load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data: dict) -> None:
    try:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError as exc:
        logger.info(f"peril: could not save {STATE} ({exc})")


def runs_on(data: dict, day: str) -> int:
    return int((data.get("days") or {}).get(day, 0))


def count_run(data: dict, day: str) -> int:
    days = data.setdefault("days", {})
    days[day] = int(days.get(day, 0)) + 1
    return days[day]


def done_for_today(data: dict, day: str, cap: int) -> bool:
    return bool(data.get("out") == day) or (cap > 0 and runs_on(data, day) >= cap)


# ---- live parts ---------------------------------------------------------------------------


async def _objects(client) -> list[tuple[str, tuple[float, float, float], object]]:
    """(template name, spot, entity) for every loaded object."""
    out = []
    try:
        entities = await client.get_base_entity_list()
    except Exception as exc:
        if type(exc).__name__ == "ClientClosedError":
            raise
        return out
    for e in entities or []:
        try:
            template = await e.object_template()
            name = (await template.object_name() or "") if template else ""
            p = await e.location()
        except Exception:
            continue
        out.append((name, (p.x, p.y, p.z), e))
    return out


async def _my_gid(client) -> int:
    for method in ("global_id_full", "global_id"):
        try:
            gid = int(await getattr(client.client_object, method)() or 0)
        except Exception:
            continue
        if gid:
            return gid
    return 0


async def _others(client, me) -> list[tuple[float, float, float]]:
    """Spots of the other wizards loaded here (template "Player Object")."""
    from .farm import _entity_gid

    mine = await _my_gid(client)
    out = []
    for name, spot, e in await _objects(client):
        if name.lower() != "player object":
            continue
        if mine and await _entity_gid(e) == mine:
            continue
        if math.dist(spot, me) < 5.0:  # ourselves, when the id did not read
            continue
        out.append(spot)
    return out


async def _circles(client) -> list[tuple[float, float, float]]:
    from .collect import duel_circles

    return await duel_circles(client)


def _pt(p) -> str:
    return f"({p[0]:.0f}, {p[1]:.0f}, {p[2]:.0f})"


def _dump(name: str, lines: list[str]) -> None:
    folder = blackbox.folder()
    if folder is None:
        return
    try:
        (folder / name).write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


class Rider:
    def __init__(self, client, controller, task: dict, cfg):
        self.client = client
        self.controller = controller
        self.task = task
        self.cfg = cfg
        self.hub = cfg.zone or HUB
        self.data = load_state()
        self.inside = False
        self.waiting_since = 0.0
        self.last_tell = 0.0
        self.last_turn = time.monotonic()
        self.last_seen = None  # (spot, monotonic) of a group member, inside
        self.alone_since = None
        self.next_trip = 0.0
        self.boxes_seen: set[str] = set()
        self.said = ""
        self.sigil = None  # (spot, why) once found this run

    def say(self, key: str, text: str) -> None:
        if self.said != key:
            logger.info(text)
            self.said = key

    async def here(self):
        from .walk import position

        return await position(self.client)

    # -- the Commons -------------------------------------------------------------

    async def find_sigil(self):
        if self.sigil is not None:
            return self.sigil
        saved = self.data.get("sigil")
        if saved and self.data.get("sigil_zone") == self.hub:
            self.sigil = tuple(saved)
            return self.sigil
        from .navigate import walk_in_zone

        rows = await _objects(self.client)
        pick = sigil_choice([(n, p) for n, p, _e in rows])
        if pick is None:
            # Objects load only near the wizard: walk toward the tent and look again.
            self.task["name"] = "walking toward the Fairegrounds"
            await walk_in_zone(self.client, GOLEM_GATE, why="toward the Portal of Peril tent",
                               controller=self.controller, on_fight=getattr(self.client, "_on_fight", None))
            rows = await _objects(self.client)
            pick = sigil_choice([(n, p) for n, p, _e in rows])
        me = await self.here()
        _dump(
            "peril_entities.txt",
            sorted(f"{n}\t{_pt(p)}\t{math.dist(p[:2], me[:2]):.0f} away" for n, p, _e in rows),
        )
        if pick is None:
            return None
        spot, why = pick
        logger.info(f"peril: the Portal of Peril sigil is {why} at {_pt(spot)}")
        blackbox.event("peril_sigil", spot=[round(v) for v in spot], why=why)
        if "magda" not in why.lower():  # a guess at Magda's feet is not saved as the sigil
            self.data["sigil"] = [round(v, 1) for v in spot]
            self.data["sigil_zone"] = self.hub
            save_state(self.data)
        elif "no sigil" in why:
            logger.warning("peril: no sigil found, only Madam Magda; is the event on? waiting beside her")
            blackbox.event("needs_player", why="no Portal of Peril sigil, only Madam Magda (event over?)")
        self.sigil = spot
        return spot

    async def onto_sigil(self, spot) -> bool:
        from .navigate import walk_in_zone
        from .walk import walk_point

        me = await self.here()
        if math.dist(me[:2], spot[:2]) <= SIGIL_REACH:
            return True
        self.task["name"] = "walking onto the Portal of Peril sigil"
        if math.dist(me[:2], spot[:2]) > 300:
            await walk_in_zone(
                self.client, spot, why="to the Portal of Peril sigil", controller=self.controller,
                on_fight=getattr(self.client, "_on_fight", None),
            )
        await walk_point(self.client, spot, why="Portal of Peril sigil", stop_within=25.0)
        me = await self.here()
        return math.dist(me[:2], spot[:2]) <= SIGIL_REACH * 2

    async def claim(self, spot) -> None:
        """Once a day: use the sigil, click Claim if shown, write down what opened, close it.

        Only with nobody else on the sigil: there, using it may start their run.
        """
        from . import ui

        day = today()
        if self.data.get("claimed") == day:
            return
        others = await _others(self.client, await self.here())
        if any(math.dist(p[:2], spot[:2]) <= 400 for p in others):
            return
        self.data["claimed"] = day
        save_state(self.data)
        prompt = await ui.text_at(self.client, ui.NPC_RANGE_TEXT)
        logger.info(f"peril: using the sigil once to claim today's credits (prompt: {prompt!r})")
        await ui.press_interact(self.client, keys=False)
        await asyncio.sleep(1.5)
        lines = await ui.dump_tree(self.client.root_window, max_depth=7)
        _dump("peril_sigil_window.txt", [f"prompt: {prompt}", *lines])
        claimed = False
        for text in CLAIM_TEXTS:
            if await ui.click_text(self.client, text):
                claimed = True
                logger.info(f"peril: clicked {text!r}")
                await asyncio.sleep(1.0)
                await self.answer_box()
                break
        texts = (await ui.visible_texts(self.client))[:40]
        blackbox.event("peril_claim", claimed=claimed, prompt=prompt, texts=texts)
        if not claimed:
            logger.info("peril: no Claim button seen (the window is in the run folder); closing it")
        await ui.close_menus(self.client)

    async def answer_box(self) -> str:
        """Answer the message box on the screen, if any. Its kind, or ''."""
        from . import ui

        box = await ui.modal_box(self.client)
        if box is None:
            return ""
        text = await ui.modal_text(box)
        kind = box_kind(text)
        if text not in self.boxes_seen:
            self.boxes_seen.add(text)
            logger.info(f"peril: message box ({kind}): {text[:160]!r}")
            blackbox.event("peril_box", kind=kind, text=text[:300])
        if kind == "out":
            self.data["out"] = today()
            save_state(self.data)
            await ui.press_modal_button(self.client, box, "centerButton")
            return kind
        if kind == "enter":
            for name in YES_BUTTONS:
                if await ui.press_modal_button(self.client, box, name):
                    logger.info(f"peril: accepted ({name})")
                    break
            return kind
        if kind == "other":
            await ui.dismiss_notice(self.client)
        return kind

    async def wait_on_sigil(self, spot) -> None:
        from .walk import VK, keys_for

        if not self.waiting_since:
            self.waiting_since = time.monotonic()
            logger.info("peril: on the Portal of Peril sigil; waiting for a group to start a run")
            blackbox.event("peril_wait")
        self.task["name"] = "waiting on the Portal of Peril sigil"
        self.controller.allow_idle(ANTI_AFK + 60)
        await self.answer_box()
        now = time.monotonic()
        if now - self.last_tell >= WAIT_TELL:
            self.last_tell = now
            others = await _others(self.client, await self.here())
            close = sum(1 for p in others if math.dist(p[:2], spot[:2]) <= 400)
            logger.info(
                f"peril: still waiting ({(now - self.waiting_since) / 60:.0f} min; "
                f"{close} wizard(s) at the sigil, {len(others)} in sight)"
            )
        if now - self.last_turn >= ANTI_AFK:
            self.last_turn = now
            keys = keys_for(self.client)
            await keys.hold(VK["A"], 0.12)
            await keys.hold(VK["D"], 0.12)
        await asyncio.sleep(0.5)

    # -- inside --------------------------------------------------------------------

    async def after_fight(self) -> None:
        from .farm import _potion, last_vitals

        hp, top = last_vitals.get("health"), last_vitals.get("max_health")
        if hp is not None and top and hp / top < POTION_BELOW:
            await _potion(self.client)

    async def follow_step(self) -> None:
        from .navigate import walk_in_zone

        me = await self.here()
        players = await _others(self.client, me)
        circles = await _circles(self.client)
        now = time.monotonic()
        if players:
            self.last_seen = (min(players, key=lambda p: math.dist(me[:2], p[:2])), now)
            self.alone_since = None
        plan, spot = follow_plan(me, players, circles, gap=self.cfg.follow_gap)
        fight = self.fight
        if plan == "join":
            self.task["name"] = "joining the group's duel"
            self.say("join", "peril: a duel is on; walking into its circle")
            await walk_in_zone(self.client, spot, why="into the group's duel", controller=self.controller,
                               on_fight=fight, reach=30.0)
            return
        if plan == "follow":
            self.task["name"] = "following the group"
            self.say("follow", "peril: following the group")
            await walk_in_zone(self.client, spot, why="after the group", controller=self.controller,
                               on_fight=fight, reach=self.cfg.follow_gap * 0.6)
            return
        if plan == "stay":
            self.task["name"] = "with the group"
            await asyncio.sleep(0.5)
            return
        # Nobody in sight: they went through a door, or are out of loading range.
        if self.alone_since is None:
            self.alone_since = now
        if self.last_seen and now - self.last_seen[1] >= LOST_AFTER:
            spot = self.last_seen[0]
            self.last_seen = None
            self.task["name"] = "following the group through"
            logger.info(f"peril: the group is out of sight; walking to where they were {_pt(spot)}")
            blackbox.event("peril_lost", spot=[round(v) for v in spot])
            outcome = await walk_in_zone(
                self.client, spot, why="where the group was", controller=self.controller,
                on_fight=fight, reach=60.0,
            )
            if outcome != "left zone":
                await self.go_through()
            return
        if now - self.alone_since >= self.cfg.alone_minutes * 60:
            await self.leave("alone in the dungeon")
            return
        self.say("alone", "peril: no other wizard in sight; waiting for them")
        await asyncio.sleep(1.0)

    async def go_through(self) -> None:
        """Use what is here (a door's X prompt), else push on with W for a moment."""
        from . import ui
        from .walk import VK, keys_for
        from .world import wait_for_loading

        zone = await self.client.zone_name()
        prompt = await ui.text_at(self.client, ui.NPC_RANGE_TEXT)
        if prompt:
            logger.info(f"peril: using {prompt!r}")
            await ui.press_interact(self.client)
            await asyncio.sleep(1.0)
            await self.answer_inside()
        else:
            await keys_for(self.client).hold(VK["W"], 1.2)
        await wait_for_loading(self.client)
        if await self.client.zone_name() != zone:
            logger.info(f"peril: through to {await self.client.zone_name()}")

    async def answer_inside(self) -> None:
        """Inside, a door's question ('enter the next room?') is a yes; leaving is not."""
        from . import ui

        box = await ui.modal_box(self.client)
        if box is None:
            return
        text = await ui.modal_text(box)
        kind = box_kind(text)
        logger.info(f"peril: message box inside ({kind}): {text[:160]!r}")
        blackbox.event("peril_box", kind=kind, text=text[:300], inside=True)
        if kind == "enter":
            for name in YES_BUTTONS:
                if await ui.press_modal_button(self.client, box, name):
                    return
        elif kind == "leave":
            await ui.press_modal_button(self.client, box, "leftButton")  # stay with the group
        else:
            await ui.dismiss_notice(self.client)

    async def leave(self, why: str) -> None:
        """Go Home, and yes to the dungeon's 'leave?' box. step() sees the house and walks back."""
        from . import ui
        from .travel import DORM_BUTTON
        from .travel_data import note_zone_jump
        from .world import wait_for_loading

        logger.info(f"peril: {why} for {self.cfg.alone_minutes:.0f} min; leaving by Go Home")
        blackbox.event("peril_leave", why=why)
        self.task["name"] = "leaving the dungeon"
        self.alone_since = time.monotonic()  # if it does not work, the next try is a while away
        note_zone_jump()
        if not await ui.click_named(self.client, DORM_BUTTON):
            logger.warning("peril: no Go Home button to click")
            return
        await asyncio.sleep(1.0)
        box = await ui.modal_box(self.client)
        if box is not None:
            logger.info(f"peril: leaving: {(await ui.modal_text(box))[:160]!r}")
            for name in YES_BUTTONS:
                if await ui.press_modal_button(self.client, box, name):
                    break
        await wait_for_loading(self.client, appear_timeout=6.0)

    async def fight(self) -> None:
        from .farm import play_current_fight

        self.task["name"] = "fighting with the group"
        await play_current_fight(self.client, self.controller, combo=True, dungeon=True)
        await self.after_fight()

    def finish_run(self, how: str) -> None:
        if not self.inside:
            return
        self.inside = False
        self.last_seen = self.alone_since = None
        day = today()
        n = count_run(self.data, day)
        save_state(self.data)
        logger.success(f"peril: run over ({how}); {n} today")
        blackbox.event("peril_run", how=how, today=n)

    # -- the loop --------------------------------------------------------------------

    async def step(self) -> bool:
        """One look and one action. False when the run should stop."""
        from .farm import in_fight
        from .travel import is_house
        from .world import is_free

        if await in_fight(self.client):
            on_way = getattr(self.client, "_on_fight", None)
            if self.inside or on_way is None:
                await self.fight()
            else:
                await on_way()  # outside the dungeon: flee, as travel does
            return True
        if not await is_free(self.client):
            self.task["name"] = "waiting (dialogue or loading)"
            if self.inside:
                await self.answer_inside()
            else:
                await self.answer_box()
            await asyncio.sleep(0.5)
            return True
        zone = await self.client.zone_name() or ""
        if zone == self.hub:
            if self.inside:
                self.finish_run("back in the Commons")
            day = today()
            if done_for_today(self.data, day, self.cfg.runs):
                why = (
                    "the Portal of Peril credits are used up for today"
                    if self.data.get("out") == day
                    else f"{runs_on(self.data, day)} Portal of Peril run(s) today (peril.runs)"
                )
                logger.success(f"peril: done: {why}")
                blackbox.event("needs_player", why=f"peril done: {why}")
                return False
            spot = await self.find_sigil()
            if spot is None:
                self.say("nosigil", "peril: no Portal of Peril sigil found near the Golem Court gate "
                         "(the event may be closed; objects are in peril_entities.txt)")
                blackbox.event("needs_player", why="no Portal of Peril sigil found (is the event on?)")
                return False
            if not await self.onto_sigil(spot):
                self.say("offsigil", "peril: could not stand on the sigil; trying again")
                await asyncio.sleep(3.0)
                return True
            if self.cfg.claim:
                await self.claim(spot)
            self.said = ""
            await self.wait_on_sigil(spot)
            return True
        if self.waiting_since and not self.inside and not is_house(zone):
            self.inside = True
            self.waiting_since = 0.0
            self.boxes_seen.clear()
            logger.success(f"peril: in! ({zone})")
            blackbox.event("peril_enter", zone=zone)
        if self.inside and not is_house(zone):
            await self.follow_step()
            return True
        if self.inside:  # a house: Go Home after the group left, or a defeat sent the wizard there
            self.finish_run(f"left to {zone}")
        if time.monotonic() < self.next_trip:
            await asyncio.sleep(2.0)
            return True
        from .travel import go_to_zone

        self.task["name"] = "walking to the Commons"
        self.say(f"trip:{zone}", f"peril: walking from {zone or 'here'} to the Commons")
        if not await go_to_zone(self.client, self.hub):
            self.next_trip = time.monotonic() + TRAVEL_RETRY
            blackbox.event("needs_player", why="could not walk to the Commons", zone=zone)
            logger.info(f"peril: could not walk to the Commons; trying again in {TRAVEL_RETRY / 60:.0f} min")
        return True


async def peril_loop(client, controller, task: dict, cfg) -> None:
    from wizwalker.errors import ClientClosedError

    from .bot import game_gone
    from .safety import BotStopped

    rider = Rider(client, controller, task, cfg)
    day = today()
    logger.info(
        f"peril: riding along through the Portal of Peril ({runs_on(rider.data, day)} run(s) so far today; "
        f"{'until the credits run out' if cfg.runs <= 0 else f'up to {cfg.runs}'})"
    )
    closed = errors = 0
    last_error = ""
    while not controller.stopped.is_set():
        try:
            await controller.checkpoint()
            if not await rider.step():
                controller.stop("peril: done for today")
                break
            closed = errors = 0
        except BotStopped:
            break
        except ClientClosedError:
            closed += 1
            if game_gone(controller, closed):
                break
            await asyncio.sleep(2.0)
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            errors = errors + 1 if text == last_error else 1
            last_error = text
            logger.opt(exception=exc).error(f"peril: step failed: {text}")
            blackbox.event("step_error", error=text)
            if errors >= 20:
                controller.stop(f"peril: the same error 20 times ({text})")
                break
            await asyncio.sleep(3.0)
    logger.info(f"peril: {runs_on(rider.data, today())} run(s) today")
