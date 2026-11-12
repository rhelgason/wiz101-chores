"""Pet dance game grinding (`mode: pet`, and the `pet` request on Windows).

`mode: pet` walks to the Pet Pavilion from wherever the wizard is (on foot,
navigate.py), walks onto the dance sigil, plays the dance game on the
stage `pet.stage` picks (petplan.pick_stage: by default the one that trains
the stats furthest below their caps) while the energy globe can pay, feeds the first snack
offered after each win, and waits for energy in between. Nothing is
teleported to: on the Mac, teleports closed the client.

The dance moves come straight from memory: a hook on the code that reads
the pet's dance sequence (from Deimos, github.com/Deimos-Wizard101,
GPL-3.0: the hook by peechez in src/dance_game_hook.py, the game flow from
src/auto_pet.py). The moves are 'a'..'d' for up/right/down/left and are
sent as W/D/S/A key presses.
"""

from __future__ import annotations

import asyncio
import ctypes
import math
import re
import time
from pathlib import Path

from loguru import logger

try:
    from wizwalker import XYZ, Keycode
    from wizwalker.memory import HookHandler, SimpleHook
except ImportError:  # the dance hook only exists on Windows, next to the game
    XYZ = Keycode = HookHandler = None

    class SimpleHook:  # type: ignore[no-redef]
        pass

from . import blackbox, ui
from .dayplan import DANCE_COST
from .hookbytes import DANCE_PATTERN, dance_body
from .world import is_free, wait_for_loading

PET_REQUEST = Path("state") / "pet.request"  # `pet`: games to play ("0": until the energy runs out)
PET_PARK = "WizardCity/WC_Streets/Interiors/WC_PET_Park"
GAME_IDLE_SECONDS = 180.0  # a dance game and its rewards: no watchdog recovery meanwhile
SIGIL = (-4450.58, -994.90, -8.04)  # the dance game's sigil in the Pet Pavilion
DANCE_SIGIL = XYZ(*SIGIL) if XYZ else None
ENERGY_WAIT = 300.0  # seconds between energy checks while the globe cannot pay
TRAVEL_RETRY = 120.0  # a walk to the pavilion that failed is tried again after this
DANCE_TITLE = "Dance Game"  # the sigil's prompt title
NPC_TITLE = ["WorldView", "NPCRangeWin", "wndTitleBackground", "NPCRangeTxtTitle"]
MOVES = str.maketrans("abcd", "WDSA")
ROUNDS = 5  # rounds in a dance game
HOOK_SETTLE = 5.0  # the hook misses turns when a game starts right after it's placed
MAX_GAMES = 200  # a ceiling for "until the energy runs out"
WM_KEYDOWN, WM_KEYUP = 0x100, 0x101


class DanceGameMovesHook(SimpleHook):
    """Copies the dance game's move string pointer to an export (peechez, via Deimos).

    11-12: the update changed `mov rbx, rax` to `mov rdi, rax` here; the pattern
    takes either and the body keeps rax in whichever register the game uses
    (hookbytes.dance_body).
    """

    pattern = DANCE_PATTERN
    instruction_length = 7
    exports = [("dance_game_moves", 8)]
    noops = 2

    async def bytecode_generator(self, packed_exports):
        original = await self.read_bytes(self.jump_address, self.instruction_length)
        return dance_body(original, packed_exports[0][1])


async def activate_dance_hook(handler: HookHandler):
    if handler._check_if_hook_active(DanceGameMovesHook):
        return
    await handler._check_for_autobot()
    hook = DanceGameMovesHook(handler)
    await hook.hook()
    handler._active_hooks[DanceGameMovesHook] = hook
    handler._base_addrs["dance_game_moves"] = hook.dance_game_moves


async def deactivate_dance_hook(handler: HookHandler):
    """Lift the dance hook. A closed client must not raise: the bytes die with it."""
    if not handler._check_if_hook_active(DanceGameMovesHook):
        return
    hook = handler._get_hook_by_type(DanceGameMovesHook)
    try:
        await hook.unhook()
    except Exception as exc:
        logger.warning(
            f"pet: could not lift the dance hook ({exc}). "
            "The next start puts those bytes back and hooks this same game."
        )
        return
    handler._active_hooks.pop(DanceGameMovesHook, None)
    handler._base_addrs.pop("dance_game_moves", None)


def decode_moves(raw: bytes) -> str:
    """The hook's 8 bytes -> the keys to press ('acbd' -> 'WSDA')."""
    return raw.partition(b"\0")[0].decode(errors="ignore").translate(MOVES)


async def read_moves(handler: HookHandler) -> str:
    addr = handler._base_addrs.get("dance_game_moves")
    if not addr:
        return ""
    try:
        return decode_moves(await handler.read_bytes(addr, 8))
    except Exception:
        return ""


def post_keys(window_handle: int, keys: str):
    """Key presses straight to the game window (works with it in the background)."""
    user32 = ctypes.windll.user32
    for key in keys:
        user32.PostMessageW(window_handle, WM_KEYDOWN, ord(key), 0)
        user32.PostMessageW(window_handle, WM_KEYUP, ord(key), 0)


def _user32_post(window_handle: int):
    user32 = ctypes.windll.user32

    def post(message: int, vk: int, lparam: int) -> None:
        user32.PostMessageW(window_handle, message, vk, lparam)

    return post


async def post_key(window_handle: int, key: str | int, seconds: float = 0.3, *, post=None) -> None:
    """Hold one key with PostMessageW, the same path the dance arrows use.

    wizwalker's send_key uses SendMessageW. On this Wine build those taps
    never opened the dance sigil; the arrows, posted this way, did.
    `key` is a character or a virtual-key code (Keycode.W is 87).
    """
    send = post or _user32_post(window_handle)
    vk = key if isinstance(key, int) else ord(key)
    end = time.monotonic() + max(seconds, 0.05)
    while True:
        send(WM_KEYDOWN, vk, 0)
        if time.monotonic() >= end:
            break
        await asyncio.sleep(0.05)
    send(WM_KEYUP, vk, 0)


MOVE_VKS = {0x57, 0x41, 0x53, 0x44}  # W A S D


def install_wine_keys(client) -> None:
    """Movement through SendInput, other hotkeys through PostMessageW.

    wizwalker's send_key and goto use SendMessageW. On this Wine build that
    call often never arrives, so the wizard turns and stays put. The dance
    arrows already post their keys; W/A/S/D go as real key state (walk.Keys).
    This replaces those two methods before the pause gate wraps send_key. Teleport is unchanged.
    """
    if getattr(client, "_wine_keys", False):
        return

    async def send_key(key, seconds: float = 0.0):
        vk = int(getattr(key, "value", key))
        if vk in MOVE_VKS:
            # Held movement is read with GetAsyncKeyState: real key state, and only
            # while Wizard101 is the active Mac app (10-10). A posted W moved 0.
            from .walk import keys_for, request_focus

            request_focus("a movement key")
            await keys_for(client).hold(vk, float(seconds or 0.05))
            return
        await post_key(client.window_handle, vk, float(seconds or 0.05))

    async def goto(x: float, y: float):
        from wizwalker.utils import XYZ, calculate_perfect_yaw

        client_obj = client.client_object
        body = await client_obj.actor_body()
        if body is None:
            body = client.body
        current = await body.position()
        try:
            bonus = float(await client_obj.speed_multiplier())
        except Exception:
            bonus = 0.0
        factor = (bonus / 100.0) + 1.0
        if factor <= 0.05:
            factor = 1.0
        target = XYZ(float(x), float(y), current.z)
        distance = current - target
        seconds = 0.0 if distance <= 1.0 else distance / (580.0 * factor)
        await body.write_yaw(calculate_perfect_yaw(current, target))
        await send_key(87, seconds)

    client.send_key = send_key
    client.goto = goto
    client._wine_keys = True


def first_number(text: str) -> int | None:
    """'Energy: 12/45' -> 12."""
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


def games_requested() -> int | None:
    """None: no request; 0: until the energy runs out; N: N games."""
    try:
        text = PET_REQUEST.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    n = first_number(text)
    return n if n is not None else 0


async def _visible(root, *names):
    """The first visible window named names[-1] under one named names[-2]
    under ... (each searched anywhere below the one before), else None."""
    current = root
    for name in names:
        found = None
        try:
            for w in await current.get_windows_with_name(name):
                if await w.is_visible():
                    found = w
                    break
        except Exception:
            return None
        if found is None:
            return None
        current = found
    return current


async def _text(root, *names) -> str:
    w = await _visible(root, *names)
    if w is None:
        return ""
    try:
        return ui._TAGS.sub("", await w.maybe_text() or "").strip()
    except Exception:
        return ""


async def _click(client, *names) -> bool:
    w = await _visible(client.root_window, *names)
    if w is None:
        return False
    try:
        await client.mouse_handler.click_window(w)
        return True
    except Exception as exc:
        logger.debug(f"pet: click {names[-1]} failed: {exc}")
        return False


async def _hidden(root, *names) -> bool:
    return await _visible(root, *names) is None


async def _wait_for(check, timeout: float, every: float = 0.15) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await check():
            return True
        await asyncio.sleep(every)
    return False


STAGES = ("baby", "teen", "adult", "ancient", "epic", "mega", "ultra")
PET_STATE = Path("state") / "pet.json"  # the equipped pet as last seen: kind, stage
PET_WINDOWS = Path("state") / "pet_windows.txt"  # every text of the pet-game windows (for mapping)
ENERGY_CHECK_SECONDS = 60.0


def stage_in(texts: list[str]) -> str | None:
    """The furthest pet stage named in `texts` ('...is now an Adult!')."""
    found = [st for st in STAGES for t in texts if re.search(rf"\b{st}\b", t, re.I)]
    return max(found, key=STAGES.index) if found else None


def kind_in(texts: list[str], kinds) -> str | None:
    """A pet kind from `kinds` (lower case) named in `texts`."""
    for k in kinds:
        if any(re.search(rf"\b{re.escape(k)}\b", t, re.I) for t in texts):
            return k
    return None


def goal_reached(kind: str | None, stage: str | None, goals: dict, default_goal: str) -> bool:
    goal = (goals.get((kind or "").lower()) or default_goal).lower()
    if not stage or goal not in STAGES or stage not in STAGES:
        return False
    return STAGES.index(stage) >= STAGES.index(goal)


def stage_for_cost(cost: int | None) -> str | None:
    """6 -> 'adult'. The track window's price, when the level-up text was missed."""
    for stage in STAGES:
        if DANCE_COST.get(stage) == cost:
            return stage
    return None


def remember_dance(cost: int | None) -> None:
    """Keep the price the dance window showed, and the stage that price is."""
    if not isinstance(cost, int):
        return
    pet = load_pet()
    stage = stage_for_cost(cost)
    changed = pet.get("dance_cost") != cost or (stage is not None and pet.get("stage") != stage)
    pet["dance_cost"] = cost
    if stage:
        pet["stage"] = stage
    save_pet(pet)
    if changed and stage:
        logger.info(f"pet: this dance costs {cost} ({stage})")


def load_pet() -> dict:
    import json

    try:
        return json.loads(PET_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_pet(data: dict):
    import json

    try:
        PET_STATE.parent.mkdir(exist_ok=True)
        PET_STATE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


async def _all_texts(window, depth: int = 0) -> list[str]:
    out: list[str] = []
    if window is None or depth > 10:
        return out
    try:
        text = ui._TAGS.sub("", await window.maybe_text() or "").strip()
        if text:
            out.append(text)
        for child in await window.children():
            out += await _all_texts(child, depth + 1)
    except Exception:
        pass
    return out


def in_pet_game(zone: str) -> bool:
    """The pet game's own zone ("ThePhantomZoneWorld/PetGameDance")."""
    return "petgame" in (zone or "").lower()


class PetDancer:
    """Pet trips: a `pet` request, or (cfg.auto) whenever energy is full, until
    the pet reaches its goal stage. Then it walks back to the zone it left."""

    def __init__(self, client, cfg=None, controller=None):
        from .config import PetConfig

        self.client = client
        self.controller = controller
        self.cfg = cfg or PetConfig()
        self.feed = self.cfg.feed
        self._next_check = 0.0
        self._dumped: set[str] = set()
        self._tracks_dumped = False

    def done(self) -> bool:
        pet = load_pet()
        return goal_reached(pet.get("kind"), pet.get("stage"), self.cfg.goals, self.cfg.default_goal)

    async def energy(self) -> tuple[int | None, int | None]:
        """(the wizard's energy now, its maximum): pet games cost energy."""
        now = first_number(await ui.named_text(self.client, "textEnergy"))
        try:
            most = await self.client.stats.energy_max()
        except Exception:
            most = None
        return now, most

    async def tick(self) -> bool:
        """Call while free. True if it made the trip."""
        wanted = games_requested()
        if wanted is not None:
            try:
                await self.trip(wanted)
            finally:
                PET_REQUEST.unlink(missing_ok=True)
            return True
        if not self.cfg.auto or time.monotonic() < self._next_check:
            return False
        self._next_check = time.monotonic() + ENERGY_CHECK_SECONDS
        if self.done():
            return False
        now, most = await self.energy()
        if now is None or not most or now < most:
            return False
        pet = load_pet()
        what = f"{pet.get('kind') or 'pet'} {pet.get('stage') or ''}".strip()
        logger.info(f"pet: energy full ({now}/{most}): off to the dance game ({what})")
        await self.trip(0)
        return True

    async def _learn(self, window_name: str):
        """Read the pet's kind and stage from a pet-game window's texts."""
        w = await _visible(self.client.root_window, window_name)
        if w is None:
            return
        texts = await _all_texts(w)
        if window_name not in self._dumped and texts:
            self._dumped.add(window_name)
            try:
                with PET_WINDOWS.open("a", encoding="utf-8") as f:
                    when = time.strftime("%Y-%m-%d %H:%M")
                    f.write(f"--- {window_name} ({when})\n" + "\n".join(texts) + "\n")
            except OSError:
                pass
        pet = load_pet()
        kind = kind_in(texts, list(dict.fromkeys([*self.cfg.goals, "ghulture"])))
        stage = stage_in(texts)
        changed = False
        if kind and kind != pet.get("kind"):
            pet["kind"], changed = kind, True
        from .petplan import note_equipped, stats_mentioned, talents_mentioned, trained_stats

        trained = trained_stats(texts)  # "Strength: 120/255": now and max
        if trained:
            now = {stat: [have, most] for stat, (have, most) in trained.items()}
            if now != pet.get("trained"):
                pet["trained"], changed = now, True
                logger.info("pet: stats " + ", ".join(f"{s} {a}/{b}" for s, (a, b) in trained.items()))
            found = {stat: most for stat, (_have, most) in trained.items()}
        else:
            found = stats_mentioned(texts)
        if found and found != pet.get("stats"):
            pet["stats"], changed = found, True
        if window_name == "PetGameTracks" and not trained and not self._tracks_dumped:
            self._tracks_dumped = True
            await self._dump_tracks(w)
        if window_name == "PetLevelUpWindow":
            have = list(pet.get("talents") or [])
            for talent in talents_mentioned(texts):
                if talent not in have:
                    have.append(talent)
                    changed = True
            if have != list(pet.get("talents") or []):
                pet["talents"] = have
        old = pet.get("stage")
        if stage and stage != old and (window_name == "PetLevelUpWindow" or old not in STAGES
                                       or STAGES.index(stage) > STAGES.index(old)):
            pet["stage"], changed = stage, True
        updated = note_equipped(pet)
        if updated != pet:
            pet, changed = updated, True
        if changed:
            save_pet(pet)
            logger.info(f"pet: {pet.get('kind') or 'the pet'} is {pet.get('stage') or '?'}")

    def _choose_stage(self) -> int:
        """Which dance stage (btnTrack0-4) to play: see petplan.pick_stage."""
        import random

        from .petplan import DANCE_STAGES, pick_stage

        pet = load_pet()
        raw = pet.get("trained")
        trained = {s: (int(v[0]), int(v[1])) for s, v in raw.items()} if isinstance(raw, dict) else None
        last = pet.get("dance_stage_last")
        last = last if isinstance(last, int) else None
        track, why = pick_stage(getattr(self.cfg, "stage", "lowest"), trained, last, random.random())
        pet["dance_stage_last"] = track
        save_pet(pet)
        name, gains = DANCE_STAGES[track]
        trains = ", ".join(f"+{n} {s}" for s, n in gains.items())
        logger.info(f"pet: dancing {name} ({trains}): {why}")
        blackbox.event("dance_stage", stage=name, why=why)
        return track

    async def _dump_tracks(self, window) -> None:
        """The stage window's tree, once a run, when its stat block gave no numbers."""
        from pathlib import Path

        try:
            lines = await ui.dump_tree(window, max_depth=8, with_types=True)
            path = (blackbox.folder() or Path("state")) / "pet_tracks_window.txt"
            path.parent.mkdir(exist_ok=True)
            path.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"pet: no stat numbers in the stage window; wrote its windows to {path}")
        except Exception as exc:
            logger.info(f"pet: could not write the stage window ({exc})")

    async def trip(
        self,
        wanted: int,
        energy_floor: int = 0,
        *,
        past_goal: bool = False,
        keep_on_miss: bool = False,
    ):
        start = await self.client.zone_name() or ""
        resumed = in_pet_game(start)
        if resumed:
            logger.info("pet: in the dance game already: playing on")
        elif start != PET_PARK:
            logger.info("pet: going to the Pet Pavilion for the dance game")
            if not await self._go_to_pavilion():
                logger.warning("pet: could not reach the Pet Pavilion")
                return 0, "could not reach the Pet Pavilion"
        games, why = 0, "done"
        snack_logged = False
        handler = self.client.hook_handler
        own_hook = getattr(self.client, "_bot_hooks", None) is None
        if own_hook:
            await activate_dance_hook(handler)
            await asyncio.sleep(HOOK_SETTLE)
        elif not handler._base_addrs.get("dance_game_moves"):
            logger.error("pet: the dance hook is not in; restart the bot in pet mode")
            return 0, "no dance hook"
        elif "dance" in self.client._bot_hooks.installed and not getattr(self, "_settled", False):
            await asyncio.sleep(HOOK_SETTLE)  # written this run: let it see a turn first
            self._settled = True
        try:
            limit = wanted or MAX_GAMES
            while games < limit:
                if self.controller:
                    await self.controller.checkpoint()
                    self.controller.allow_idle(GAME_IDLE_SECONDS)
                result = await self.play_one(energy_floor)
                if result == "no snacks":
                    # The game was won. The snack click stays armed for the next one.
                    games += 1
                    why = "no snacks"
                    if not snack_logged:
                        logger.info("pet: no snack was offered; playing on, and the next win will try again")
                        snack_logged = True
                elif result != "won":
                    why = result
                    break
                else:
                    games += 1
                    snack_logged = False
                    why = "done"
                    logger.success(f"pet: dance game {games} won")
                from .petplan import pet_failed

                if pet_failed(load_pet().get("talents") or []) and not (past_goal or keep_on_miss):
                    why = "a talent outside the ghulture set manifested; stopping"
                    logger.info(f"pet: {why}")
                    break
                if self.done() and not past_goal:
                    pet = load_pet()
                    why = f"{pet.get('kind') or 'the pet'} reached {pet.get('stage')}: its goal"
                    logger.success(f"pet: {why}")
                    break
        finally:
            if self.controller:
                self.controller.end_idle()
            try:
                await self._close_all()
            except Exception as exc:
                logger.warning(f"pet: could not close the dance windows ({exc})")
            if own_hook:
                try:
                    await deactivate_dance_hook(handler)
                except Exception as exc:
                    logger.warning(f"pet: could not lift the dance hook ({exc})")
        logger.info(f"pet: {games} game(s) played; stopped: {why}")
        blackbox.event("dance_trip", games=games, why=why)
        if not resumed and start and start != PET_PARK and await is_free(self.client):
            from .travel import go_to_zone

            if not await go_to_zone(self.client, start):
                logger.info(f"pet: no gate path back to {start}; staying here")
        return games, why

    async def _go_to_pavilion(self) -> bool:
        from .travel import go_to_zone

        # Same route as a farm trip: gates in this world, the world door
        # when the pavilion is in another, then Wizard City's gates.
        await go_to_zone(self.client, PET_PARK)
        await wait_for_loading(self.client)
        return await self.client.zone_name() == PET_PARK

    async def _prompt_up(self) -> bool:
        return await ui.text_at(self.client, NPC_TITLE) == DANCE_TITLE

    async def _on_sigil(self) -> bool:
        """Stand on the sigil until its prompt shows. Walked to, never teleported to."""
        from .walk import position, walk_point

        for attempt in range(4):
            if await _wait_for(self._prompt_up, 3.0):
                return True
            me = await position(self.client)
            away = math.dist(me[:2], SIGIL[:2])
            if away > 600.0:
                # Across the pavilion: the game's own paths, around the fences.
                from .navigate import walk_in_zone

                await walk_in_zone(self.client, SIGIL, why="to the dance sigil", controller=self.controller,
                                   on_fight=getattr(self.client, "_on_fight", None), reach=60.0)
                continue
            if away > 60.0:
                checkpoint = self.controller.checkpoint if self.controller else None
                await walk_point(
                    self.client, SIGIL, why="dance sigil", stop_within=25.0,
                    max_seconds=min(25.0, 3.0 + away / 300.0), checkpoint=checkpoint,
                )
            else:
                # On it, but no prompt: step off and back on.
                logger.info(f"pet: on the sigil without its prompt (try {attempt + 1}); stepping off and on")
                await self.client.send_key(Keycode.S, 0.35)
                await self.client.send_key(Keycode.W, 0.4)
            await asyncio.sleep(0.8)
        return await self._prompt_up()

    async def _cannot_pay(self) -> bool:
        """True when the last dance window priced a game the globe cannot buy.

        Opening the track list to read that again is the click in and out.
        """
        known = load_pet().get("dance_cost")
        if not isinstance(known, int) or known <= 0:
            return False
        now, _most = await self.energy()
        if now is None or now >= known:
            return False
        logger.info(f"pet: energy {now} cannot pay {known}; leaving the dance game closed")
        return True

    async def _open_tracks(self) -> bool:
        """Open the track list. X is only pressed while the Dance Game prompt is still up.

        A click that already used the prompt must not be followed by X: that
        key closes the window the click just opened.
        """
        root = self.client.root_window
        for _ in range(4):
            if await _visible(root, "PetGameTracks"):
                return True
            if await ui.text_at(self.client, NPC_TITLE) != DANCE_TITLE:
                await asyncio.sleep(0.4)
                continue
            clicked = await ui.press_interact(self.client, keys=False)
            logger.info("pet: clicked the dance prompt" if clicked else "pet: no dance prompt to click")
            if await _wait_for(lambda: _visible(root, "PetGameTracks"), 3.0):
                return True
            if await ui.text_at(self.client, NPC_TITLE) != DANCE_TITLE:
                logger.info("pet: the dance prompt closed; waiting for the track list")
                return await _wait_for(lambda: _visible(root, "PetGameTracks"), 3.0)
            logger.info("pet: pressing X for the dance game")
            await ui.press_x(self.client)
            if not await _wait_for(lambda: _visible(root, "PetGameTracks"), 1.5) and await self._prompt_up():
                # The posted X is not always read; X as real key state, the way doors open.
                from .walk import VK, keys_for, request_focus

                request_focus("pressing X for the dance game")
                await asyncio.sleep(0.5)
                await keys_for(self.client).tap(VK["X"])
            if await _wait_for(lambda: _visible(root, "PetGameTracks"), 3.0):
                return True
        return await _visible(root, "PetGameTracks") is not None

    async def play_one(self, energy_floor: int = 0) -> str:
        """One game, from the sigil to the reward screen closed: 'won', or
        why it stopped ('no energy', 'no snacks', 'no sigil', 'no game')."""
        root = self.client.root_window
        if await _visible(root, "PetGameDance"):
            # A game under way (resumed): dance it out, then its rewards.
            if not await self.dance():
                return "no game"
            return await self.collect()
        await self._finish_rewards()  # (a reward page left open from the last game)
        if not await _visible(root, "PetGameTracks"):
            if await self._cannot_pay():
                return "no energy"
            if not await self._on_sigil():
                return "no sigil"
            if not await self._open_tracks():
                return "no game"
        await self._learn("PetGameTracks")
        cost = first_number(await _text(root, "PetGameTracks", "txtEnergyCost"))
        have = first_number(await _text(root, "PetGameTracks", "txtYourEnergy"))
        remember_dance(cost)
        logger.info(f"pet: energy {have} (a game costs {cost}; holding {energy_floor})")
        if cost is not None and have is not None and have < cost:
            return "no energy"
        if cost is not None and have is not None and have - cost < energy_floor:
            return "holding energy"
        # (Clicks while the scroll still unrolls are lost: the game never
        # started on the other window. Settle first, and press again while
        # the window is still up.)
        await asyncio.sleep(1.5)
        track = self._choose_stage()
        for _ in range(3):
            if not await _click(self.client, "PetGameTracks", f"btnTrack{track}"):
                logger.info(f"pet: no stage button {track}; dancing Wizard City")
                track = 0
                await _click(self.client, "PetGameTracks", "btnTrack0")
            await asyncio.sleep(0.5)
            await _click(self.client, "PetGameTracks", "btnNext")  # Play
            if await _wait_for(lambda: _hidden(root, "PetGameTracks"), 4):
                break
            logger.info("pet: Play didn't take; pressing it again")
        if not await self.dance():
            return "no game"
        return await self.collect()

    async def dance(self) -> bool:
        root = self.client.root_window
        if not await _wait_for(lambda: _visible(root, "PetGameDance", "txtAction"), 30):
            logger.warning("pet: the dance game didn't start")
            return False
        for n in range(ROUNDS):
            # The pet shows the moves, then "Go!": the player's turn.
            async def go():
                return "Go!" in await _text(root, "PetGameDance", "txtAction")

            async def not_go():
                return not await go()

            await _wait_for(not_go, 15)
            if not await _wait_for(go, 30):
                logger.warning(f"pet: no 'Go!' in round {n + 1}")
                return False
            await asyncio.sleep(1.5)
            moves = await read_moves(self.client.hook_handler)
            logger.info(f"pet: round {n + 1}: {moves or '(no moves read)'}")
            post_keys(self.client.window_handle, moves)
        await asyncio.sleep(3.0)
        return True

    async def collect(self) -> str:
        """The reward screen: Next, the first snack and Feed Pet, Finish."""
        root = self.client.root_window
        if not await _wait_for(lambda: _visible(root, "PetGameRewards"), 30):
            return "no game"
        await self._learn("PetGameRewards")
        await _click(self.client, "PetGameRewards", "btnNext")  # Next
        await asyncio.sleep(1.5)
        await self._close_level_up()
        result = "won"
        if self.feed:
            if await _click(self.client, "PetGameRewards", "chkSnackCard0"):
                await asyncio.sleep(0.6)
                await _click(self.client, "PetGameRewards", "btnNext")  # Feed Pet
                await asyncio.sleep(1.0)
                await self._close_level_up()
            else:
                result = "no snacks"
        await self._finish_rewards()
        return result

    async def _finish_rewards(self):
        """Through the reward pages to the end: Finish (btnBack) when shown,
        else Next (a page of the pet's improved stats has only Next: leaving
        it open blocked the next game)."""
        root = self.client.root_window
        for _ in range(40):
            if not await _visible(root, "PetGameRewards"):
                return
            await self._close_level_up()
            if not await _click(self.client, "PetGameRewards", "btnBack"):
                await _click(self.client, "PetGameRewards", "btnNext")
            await asyncio.sleep(0.6)

    async def _close_level_up(self):
        root = self.client.root_window
        for _ in range(10):
            if not await _visible(root, "PetLevelUpWindow"):
                return
            await self._learn("PetLevelUpWindow")
            logger.success("pet: the pet leveled up")
            await _click(self.client, "PetLevelUpWindow", "btnPetLevelClose")
            await asyncio.sleep(0.3)

    async def _close_all(self):
        for _ in range(10):
            root = self.client.root_window
            if await _visible(root, "PetGameTracks"):
                await _click(self.client, "PetGameTracks", "btnBack")
            elif await _visible(root, "PetGameRewards"):
                await self._finish_rewards()
            else:
                return
            await asyncio.sleep(0.4)


async def wait_for_energy(dancer: PetDancer, controller, task: dict) -> None:
    """The globe cannot pay: check again every ENERGY_WAIT seconds."""
    now, most = await dancer.energy()
    cost = load_pet().get("dance_cost")
    task["name"] = "waiting for pet energy"
    logger.info(
        f"pet: energy {now if now is not None else '?'}/{most or '?'}; a game costs {cost or '?'}. "
        f"Checking again in {ENERGY_WAIT / 60:.0f} min"
    )
    blackbox.event("energy_wait", energy=now, max=most, cost=cost)
    if controller:
        controller.allow_idle(ENERGY_WAIT + 60)
    try:
        end = time.monotonic() + ENERGY_WAIT
        while time.monotonic() < end:
            if controller:
                await controller.checkpoint()
            await asyncio.sleep(5.0)
    finally:
        if controller:
            controller.end_idle()


async def pet_loop(client, cfg, controller, task: dict) -> None:
    """`mode: pet`: dance while the globe can pay, wait for energy, repeat until stopped."""
    from wizwalker.errors import ClientClosedError

    from .bot import game_gone
    from .farm import in_fight
    from .safety import BotStopped

    dancer = PetDancer(client, cfg, controller)
    said = ""
    closed = failures = total = 0
    next_trip = 0.0

    def say(key: str, text: str) -> None:
        nonlocal said
        if said != key:
            logger.info(text)
            said = key

    logger.info("pet: dance games in the Pet Pavilion (walking there first if the wizard is elsewhere)")
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            if await in_fight(client):
                on_fight = getattr(client, "_on_fight", None)
                if on_fight is not None:
                    task["name"] = "fleeing a duel"
                    say("duel", "pet: a duel is on; fleeing it (played out only if Flee fails twice)")
                    await on_fight()
                else:
                    task["name"] = "waiting for the duel to end"
                    say("duel", "pet: a duel is on; waiting for it to end")
                    await asyncio.sleep(1.0)
                continue
            if not await is_free(client):
                task["name"] = "waiting (dialogue or loading)"
                await asyncio.sleep(0.5)
                continue
            zone = await client.zone_name() or ""
            if zone != PET_PARK and not in_pet_game(zone):
                if time.monotonic() >= next_trip:
                    task["name"] = "walking to the Pet Pavilion"
                    from .travel import go_to_zone

                    if await go_to_zone(client, PET_PARK):
                        continue
                    next_trip = time.monotonic() + TRAVEL_RETRY
                    blackbox.event("needs_player", why="could not walk to the Pet Pavilion", zone=zone)
                    say(
                        f"away:{zone}",
                        f"pet: could not walk from {zone or 'here'} to the Pet Pavilion; trying again in "
                        f"{TRAVEL_RETRY / 60:.0f} min (or walk it there and pet mode carries on).",
                    )
                task["name"] = "waiting to retry the walk to the Pet Pavilion"
                await asyncio.sleep(5.0)
                continue
            said = ""
            if not in_pet_game(zone) and await dancer._cannot_pay():
                await wait_for_energy(dancer, controller, task)
                continue
            task["name"] = "dance"
            games, why = await dancer.trip(0, past_goal=True, keep_on_miss=True)
            total += games
            closed = 0
            if why in ("no energy", "holding energy"):
                failures = 0
                continue
            if games == 0 and why not in ("done",):
                failures += 1
                pause = min(120.0, 15.0 * failures)
                logger.info(f"pet: no game this time ({why}); trying again in {pause:.0f}s")
                await asyncio.sleep(pause)
            else:
                failures = 0
        except BotStopped:
            break
        except ClientClosedError:
            closed += 1
            if game_gone(controller, closed):
                break
            await asyncio.sleep(2.0)
        except Exception as exc:
            closed = 0
            logger.opt(exception=exc).error(f"pet: step failed: {type(exc).__name__}: {exc}")
            blackbox.event("step_error", error=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(3.0)
    logger.info(f"pet: {total} game(s) this run")
