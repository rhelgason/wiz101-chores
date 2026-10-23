"""Connects to one Wizard101 window and runs the pet or seeds loop.

The connect order is the safety story (hooking.py has the why):
1. hooks: reuse the ones this game already has, or write them once;
2. the mouse hook;
3. lock: from here on a write to game code is refused (blackbox.guard);
4. the loop, with a heartbeat into events.jsonl;
5. stop: the mouse hook comes out, the other hooks stay for the next run.

On the Mac only `pet`, `seeds` and `gather` run. The day plan and reagent sweeps
travel by teleport, and teleports are what closed this Wine client.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time

from loguru import logger
from wizwalker import ClientHandler
from wizwalker.errors import ClientClosedError, MemoryReadError, MemoryWriteError, PatternFailed
from wizwalker.extensions.wizsprinter import SprintyClient

from . import blackbox
from .config import Config
from .safety import BotStopped, Controller
from .world import is_free

HOOK_TIMEOUT = 90
HOOK_SCAN_TIMEOUT = 90.0
CONNECT_TIMEOUT = 300.0
SHUTDOWN_WAIT = 10.0
PARK_TIMEOUT = 25.0
HEARTBEAT = 10.0
MAC_MODES = ("pet", "seeds", "gather")
WORLD_HOOKS = ("player_struct", "player_stat_struct", "current_client", "current_render_context")


class TeleportDisabled(RuntimeError):
    """client.teleport on the Mac. It patches game code each time and closed the client."""


def in_wine() -> bool:
    if os.environ.get("WINEPREFIX") or os.environ.get("WIZ_WINE"):
        return True
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return hasattr(ctypes.WinDLL("ntdll"), "wine_get_version")
    except Exception:
        return False


def wine_version() -> str:
    try:
        import ctypes

        get = ctypes.WinDLL("ntdll").wine_get_version
        get.restype = ctypes.c_char_p
        return get().decode()
    except Exception:
        return ""


def new_handler() -> ClientHandler:
    # SprintyClient adds the entity helpers (mobs, wisps, potions).
    return ClientHandler(client_cls=SprintyClient)


def release_mouse_buttons(client) -> None:
    """Button-up for left and right. A stop between a click's down and up left
    the game thinking the button was still held."""
    import ctypes

    try:
        send = ctypes.windll.user32.SendMessageW
        send(client.window_handle, 0x0202, 0, 0)  # WM_LBUTTONUP
        send(client.window_handle, 0x0205, 0, 0)  # WM_RBUTTONUP
    except Exception as exc:
        logger.debug(f"could not release the mouse buttons: {exc!r}")


async def mouse_on_pause(client, controller):
    """Only with safety.pause_releases_mouse: paused, the mouse hook comes out.

    That is two more code writes per pause, so it is off by default.
    """
    hooks = client._bot_hooks
    released = False
    while not controller.stopped.is_set():
        try:
            if controller.paused and not released:
                release_mouse_buttons(client)
                await hooks.mouse_out()
                released = True
            elif not controller.paused and released:
                await hooks.mouse_in()
                released = False
                logger.info("resumed: the bot has the mouse again")
        except Exception as exc:
            logger.warning(f"mouse hook on pause: {exc}")
        await asyncio.sleep(0.3)


async def close_handler(handler: ClientHandler) -> bool:
    """Stop: park the hooks (mouse out, the rest stay). Returns True if that hung."""
    stuck = False
    for client in list(handler.clients):
        release_mouse_buttons(client)
        if getattr(client, "_posted_keys", None) is not None:
            try:
                client._posted_keys.release_movement()
            except Exception:
                pass
        hooks = getattr(client, "_bot_hooks", None)
        try:
            running = bool(client.is_running())
        except Exception:
            running = False
        if hooks is None:
            if running:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(client.close(), timeout=PARK_TIMEOUT)
            continue
        try:
            await asyncio.wait_for(hooks.park(running=running), timeout=PARK_TIMEOUT)
        except TimeoutError:
            stuck = True
            logger.error("taking the mouse hook out took too long")
        except Exception as exc:
            logger.opt(exception=exc).error("parking the hooks failed")
        if running:
            logger.info("stopped; the game keeps its hooks for the next run, and the mouse is yours")
    return stuck


def _pick_client(handler: ClientHandler):
    from .gamerestart import bot_game_pid, remember_game

    clients = handler.get_new_clients()
    if not clients:
        raise SystemExit("No Wizard101 window found. Start the game and log in to your wizard first.")
    pid = bot_game_pid()
    mine = [c for c in clients if c.process_id == pid]
    if mine:
        client = mine[0]
    elif len(clients) == 1:
        client = clients[0]
    else:
        raise SystemExit(
            f"{len(clients)} Wizard101 windows are open and none is known as the bot's "
            "(state/game_client.json).\nClose the other copy, or put the bot's game process id in "
            "state/game_client.json, then start again."
        )
    if len(clients) > 1:
        logger.info(f"{len(clients)} game windows open; using the bot's own (process {client.process_id})")
    remember_game(client.process_id, client.window_handle)
    return client


async def connect(handler: ClientHandler, mode: str = "seeds", jumps: bool = False):
    from .hooking import Hooks, HookStateError

    client = _pick_client(handler)
    blackbox.install_write_guard()
    hooks = Hooks(client)
    client._bot_hooks = hooks
    teleport = mode in ("chores", "reagents") and not in_wine()
    logger.info(f"hooks: attaching to game process {client.process_id}")
    blackbox.event("connect", pid=client.process_id, hwnd=client.window_handle, mode=mode)
    try:
        await asyncio.wait_for(
            hooks.attach(dance=mode in ("pet", "chores"), teleport=teleport, jump=jumps), HOOK_SCAN_TIMEOUT
        )
    except HookStateError as exc:
        blackbox.event("connect_failed", why=str(exc))
        raise SystemExit(f"\n{exc}") from None
    except (PatternFailed, RuntimeError) as exc:
        # WizWalker reports a missing hook cave as RuntimeError("Pattern scan failed ...").
        if not isinstance(exc, PatternFailed) and "pattern" not in str(exc).lower():
            raise
        blackbox.event("connect_failed", why=f"pattern not found: {exc}")
        raise SystemExit(
            "\nA hook spot was not found in this game. Two causes:\n"
            "- an older version of this bot hooked this same game window and did not record it. "
            "Quit Wizard101 completely, start it, log in, and run again (once).\n"
            "- a game update moved the code. Then WizWalker needs an update."
        ) from None
    except TimeoutError:
        blackbox.event("connect_failed", why="hook scan timed out")
        from .rehook import SCAN_STALL

        raise SystemExit("\n" + SCAN_STALL) from None
    except (ClientClosedError, MemoryWriteError, MemoryReadError) as exc:
        blackbox.event("connect_failed", why=f"{type(exc).__name__}: {exc}")
        logger.opt(exception=exc).error("hooks: could not attach")
        running = False
        with contextlib.suppress(Exception):
            running = bool(client.is_running())
        raise SystemExit(
            f"\nCould not hook into the game ({type(exc).__name__}: {exc}). "
            + (
                "Leave Wizard101 open and look at the run log."
                if running
                else "Wizard101 is not running. Start it, log in, stand in the world, then run again."
            )
        ) from None
    _click_left_of_center(client)
    await asyncio.wait_for(
        hooks.handler._wait_for_value(hooks.handler._base_addrs["current_root_window"], None),
        timeout=HOOK_TIMEOUT,
    )
    await hooks.mouse_in()
    from .relog import at_character_select, play_from_character_select, reconnect_if_asked

    if await reconnect_if_asked(client):
        await asyncio.sleep(3.0)
    if await at_character_select(client):
        await play_from_character_select(client)
    try:
        await asyncio.wait_for(
            asyncio.gather(
                *(
                    hooks.handler._wait_for_value(hooks.handler._base_addrs[name], None)
                    for name in WORLD_HOOKS
                )
            ),
            timeout=HOOK_TIMEOUT,
        )
    except TimeoutError:
        raise SystemExit(
            f"\nThe hooks are in, but the wizard was not seen within {HOOK_TIMEOUT}s. Stand in the world "
            "(not a loading or login screen), take one step, and run again. The hooks stay, so the "
            "next start writes no code."
        ) from None
    if not teleport:
        blackbox.guard.lock()
        _no_teleport(client)
        client._walk_only = True  # travel.go_to_zone walks (navigate.py)
    from .petdance import install_wine_keys
    from .walk import keys_for

    install_wine_keys(client)
    try:
        keys_for(client).release_movement()  # a run that died mid-walk may have left W down
    except Exception as exc:
        logger.debug(f"could not release the movement keys: {exc!r}")
    if teleport:
        from .safe_teleport import install

        install(client)
    logger.success(
        f"connected to the game (hooks reused: {', '.join(hooks.adopted) or 'none'}; "
        f"written now: {', '.join(hooks.installed) or 'none'})"
    )
    blackbox.event("connected", adopted=hooks.adopted, installed=hooks.installed)
    return client


def _fight_on_the_way(client, controller, *, combo: bool):
    """What travel does when a creature pulls the wizard into a duel: flee it.

    Flee is clicked on each planning turn (and its confirm, if the game asks).
    If the duel is still on after two tries, it is played instead.
    """

    async def flee_or_play() -> None:
        from .farm import flee, play_current_fight

        blackbox.event("travel_flee")
        if await flee(client, controller, why="a duel on the way"):
            logger.info("travel: fled; carrying on")
            return
        logger.info("travel: fleeing did not work; fighting it")
        blackbox.event("travel_fight_played")
        await play_current_fight(client, controller, combo=combo)

    return flee_or_play


def _no_teleport(client) -> None:
    async def teleport(*_args, **_kwargs):
        blackbox.event("teleport_refused")
        raise TeleportDisabled("teleport is off on this Mac: it patches game code and closed the client")

    client.teleport = teleport


def _client_origin(hwnd: int, awareness: int):
    """The client area's top-left as a thread with this DPI awareness sees it."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pt = wintypes.POINT(0, 0)
    old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(awareness))
    try:
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
    return pt.x, pt.y


def dpi_click_offset(hwnd: int) -> tuple[int, int]:
    """Shift a click so a DPI-unaware game sees it where we aimed.

    WizWalker writes cursor positions in real pixels. On a monitor that is
    not at 100% the game converts them with a different origin, so thin
    buttons were missed. The fix is the scaled origin minus the real one.
    """
    try:
        sx, sy = _client_origin(hwnd, -1)
        rx, ry = _client_origin(hwnd, -4)
    except Exception:
        return 0, 0
    return sx - rx, sy - ry


def obey_controller(client, controller):
    """Keys and clicks wait while paused and stop once stopped."""

    def gate(fn):
        async def wrapped(*args, **kwargs):
            await controller.checkpoint()
            return await fn(*args, **kwargs)

        return wrapped

    client.teleport = gate(client.teleport)
    client.send_key = gate(client.send_key)
    mouse = client.mouse_handler
    mouse.click = gate(mouse.click)
    for name in ("click_window", "set_mouse_position"):
        if hasattr(mouse, name):
            setattr(mouse, name, gate(getattr(mouse, name)))


def _click_left_of_center(client):
    """Aim clicks where the game will see them, click windows at their center, log each click."""
    mouse = client.mouse_handler
    set_position = mouse.set_mouse_position
    offset_logged: list = []

    async def set_mouse_position(x, y, *args, **kwargs):
        if x >= 0 and y >= 0:
            try:
                dx, dy = dpi_click_offset(client.window_handle)
            except Exception:
                dx, dy = 0, 0
            if (dx, dy) != (0, 0) and offset_logged != [(dx, dy)]:
                offset_logged[:] = [(dx, dy)]
                logger.info(f"correcting clicks by ({dx}, {dy}) px for display scaling")
            x, y = x + dx, y + dy
        return await set_position(x, y, *args, **kwargs)

    async def click_window(window, **kwargs):
        r = await window.scale_to_client()
        name = ""
        with contextlib.suppress(Exception):
            name = await window.name()
        x, y = int((r.x1 + r.x2) / 2), int((r.y1 + r.y2) / 2)
        blackbox.event("click", window=name, x=x, y=y)
        await mouse.click(x, y, **kwargs)

    mouse.set_mouse_position = set_mouse_position
    mouse.click_window = click_window


async def chore_loop(client, cfg: Config, controller: Controller, task: dict):
    if cfg.mode == "seeds":
        from .seeds import seed_loop

        await seed_loop(client, controller, task, cfg.seeds)
        return
    if cfg.mode == "gather":
        from .gather import gather_loop

        await gather_loop(client, controller, task, cfg.gather)
        return
    if cfg.mode == "pet":
        from .petdance import pet_loop

        await pet_loop(client, cfg.pet, controller, task)
        return
    await _day_plan(client, cfg, controller, task)


async def _day_plan(client, cfg: Config, controller: Controller, task: dict):
    """Windows only: the day plan in PLAYBOOK.md (dance, then farm by teleport)."""
    from .dayplan import choose
    from .duty import carry_out, look
    from .petdance import PET_REQUEST, PetDancer, games_requested
    from .reagents import Gatherer

    dancer = PetDancer(client, cfg.pet, controller)
    gatherer = Gatherer(client, cfg.reagents.hop_zones, controller)
    last = ""
    closed = 0
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            from .farm import in_fight, play_current_fight

            if await in_fight(client):
                task["name"] = "fight"
                await play_current_fight(client, controller, combo=False)
                closed = 0
                continue
            if not await is_free(client):
                task["name"] = "waiting (dialogue or loading)"
                await asyncio.sleep(0.4)
                continue
            if games_requested() is not None:
                task["name"] = "pet dance (requested)"
                try:
                    await dancer.trip(games_requested() or 0)
                finally:
                    PET_REQUEST.unlink(missing_ok=True)
                continue
            decision = choose(await look(client, cfg, dancer))
            task["name"] = decision.action
            key = f"{decision.action}|{decision.why}|{decision.target}"
            if key != last:
                logger.info(f"day: {decision.action} — {decision.why}")
                last = key
            await carry_out(decision, client, dancer, gatherer)
            closed = 0
        except BotStopped:
            break
        except ClientClosedError:
            closed += 1
            if game_gone(controller, closed):
                break
            await asyncio.sleep(2.0)
        except Exception as exc:
            closed = 0
            logger.opt(exception=exc).error(f"chore step failed: {exc}")
            await asyncio.sleep(1.0)


def game_gone(controller: Controller, times: int) -> bool:
    """A ClientClosedError. The third in a row stops the run, with the last steps on record."""
    box = blackbox.box()
    last = list(box.recent)[-1] if box and box.recent else {}
    if times == 1:
        logger.warning(
            f"the game stopped answering (last event: {last.get('kind', '?')} at {last.get('t', '?')})"
        )
    if times < 3:
        return False
    detail = ""
    if box and box.last_code_write:
        ago = time.time() - box.last_code_write["ts"]
        detail = f" Last code write: {box.last_code_write.get('label')} {ago:.0f}s earlier."
    logger.error(
        "Wizard101 closed while the bot was running."
        + detail
        + " The crash report (CRASH.md in the run folder) has the game log, any crash dump,"
        " and the last bot steps."
    )
    blackbox.event("game_closed", last_event=last)
    controller.stop("the game client closed")
    return True


async def heartbeat_loop(client, controller: Controller, task: dict):
    """status.json every 5s, and a state line in events.jsonl every HEARTBEAT seconds."""
    from .gamelog import LogWatch
    from .service import write_status

    last_beat = 0.0
    game_log = LogWatch()
    while not controller.stopped.is_set():
        dropped = game_log.poll()
        if dropped:
            logger.warning(f"the game server logged the wizard out: {dropped}")
            why = "the game logged out (log back in, then start the bot)"
            blackbox.event("needs_player", why=why, line=dropped)
            controller.stop("the game server logged the wizard out")
            return
        zone = ""
        with contextlib.suppress(Exception):
            zone = await client.zone_name() or ""
        write_status(state="paused" if controller.paused else "running", zone=zone, task=task["name"])
        if time.monotonic() - last_beat >= HEARTBEAT:
            last_beat = time.monotonic()
            beat = {"zone": zone, "task": task["name"], "paused": controller.paused}
            with contextlib.suppress(Exception):
                from .walk import position

                beat["pos"] = [round(v) for v in await position(client)]
            with contextlib.suppress(Exception):
                p = await client.body.position()  # the player hook's idea, kept to spot it going stale
                beat["pos_hook"] = [round(p.x), round(p.y), round(p.z)]
            with contextlib.suppress(Exception):
                beat["hp"] = [
                    int(await client.stats.current_hitpoints()),
                    int(await client.stats.max_hitpoints()),
                ]
                beat["mana"] = [int(await client.stats.current_mana()), int(await client.stats.max_mana())]
            with contextlib.suppress(Exception):
                beat["in_battle"] = bool(await client.in_battle())
            blackbox.event("heartbeat", **beat)
        await asyncio.sleep(5)


JUMP_MODES = ("gather",)  # the data-only jump pilot; seeds and pet walk (the player, 10-12)


def jumps_on(cfg: Config) -> bool:
    """safety.data_jumps, in a mode that pilots it. Elsewhere no jump hook goes in."""
    return bool(cfg.safety.data_jumps) and cfg.mode in JUMP_MODES


def _apply_mode(cfg: Config):
    if cfg.mode == "pet":
        cfg.pet.auto = True
        cfg.reagents.enabled = False
    elif cfg.mode in ("reagents", "seeds", "gather"):
        cfg.pet.auto = False
        cfg.reagents.enabled = cfg.mode == "reagents"


async def run(cfg: Config):
    _apply_mode(cfg)
    if in_wine() and cfg.mode not in MAC_MODES:
        raise SystemExit(
            f"\nmode {cfg.mode!r} is off on the Mac: it travels by teleport, and teleports closed "
            f"this Wine client. Use one of: {', '.join(MAC_MODES)}."
        )
    from .service import forget_stale_requests

    cleared = forget_stale_requests()
    if cleared:
        logger.info(f"ignored a leftover {', '.join(cleared)} from an earlier run")
    s = cfg.safety
    controller = Controller(s.stop_key, s.pause_key, s.max_hours)
    doing = {
        "seeds": (
            "hunts in turn (seeds.hunts), no teleports"
            if cfg.seeds.hunts
            else "Savarstaad seeds until stopped (Troubled Warriors and Splithoof Barbarians), no teleports"
        ),
        "pet": "the dance game while energy can pay, waiting for energy in between",
        "gather": f"{cfg.gather.reagent} from its spawn spots, on foot, around creatures",
    }.get(cfg.mode, "the day plan")
    logger.info(f"mode={cfg.mode}: {doing}. {s.stop_key}=stop, {s.pause_key}=pause/resume")
    blackbox.event("run_start", mode=cfg.mode, wine=wine_version() or in_wine(), pid=os.getpid())

    from .rehook import claim_bot_lock, release_bot_lock

    busy = claim_bot_lock()
    if busy:
        raise SystemExit("\n" + busy)
    handler = new_handler()
    tasks: list[asyncio.Task] = []
    task = {"name": "starting"}
    try:
        try:
            client = await asyncio.wait_for(
                connect(handler, cfg.mode, jumps=jumps_on(cfg)), CONNECT_TIMEOUT
            )
        except TimeoutError:
            raise SystemExit(
                "\nGetting into this open Wizard101 took too long. Leave the game open and run again."
            ) from None
        obey_controller(client, controller)
        client._controller = controller
        client._on_fight = _fight_on_the_way(client, controller, combo=cfg.mode == "seeds")
        client._jump = None
        if jumps_on(cfg) and "jump_helper" in client.hook_handler._base_addrs:
            from .jump import Jumper

            client._jump = Jumper(client)
            logger.info("jump: data-only jumps are on (gather pilot, experimental)")
        elif cfg.safety.data_jumps:
            logger.info(f"jump: off in {cfg.mode} mode (jumps are a gather-only pilot); walking")
        tasks = [
            asyncio.create_task(controller.watch(), name="safety"),
            asyncio.create_task(chore_loop(client, cfg, controller, task), name="main"),
            asyncio.create_task(heartbeat_loop(client, controller, task), name="heartbeat"),
        ]
        if s.pause_releases_mouse:
            tasks.append(asyncio.create_task(mouse_on_pause(client, controller), name="mouse"))
        stop_waiter = asyncio.create_task(controller.stopped.wait())
        done, _ = await asyncio.wait([*tasks, stop_waiter], return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is not stop_waiter and t.exception() and not isinstance(t.exception(), BotStopped):
                logger.opt(exception=t.exception()).error(f"{t.get_name()} task crashed")
                controller.stop(f"{t.get_name()} crashed")
        controller.stop(controller.stop_reason or "finished")
        logger.info(f"session over ({controller.stop_reason})")
        from .service import write_status

        write_status(state="stopped", reason=controller.stop_reason, task=task["name"])
        return controller.stop_reason
    finally:
        for t in tasks:
            t.cancel()
        _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_WAIT) if tasks else (set(), set())
        if pending:
            logger.warning(f"{len(pending)} task(s) still running at shutdown; parking the hooks anyway")
        stuck = False
        try:
            stuck = await close_handler(handler)
        finally:
            release_bot_lock()
            blackbox.event(
                "run_end", reason=controller.stop_reason, blocked_code_writes=blackbox.guard.blocked
            )
        if stuck:
            os._exit(0)
