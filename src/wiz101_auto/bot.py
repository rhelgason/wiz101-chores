"""Connects to one Wizard101 window and runs the chore loops."""

from __future__ import annotations

import asyncio
import contextlib

from loguru import logger
from wizwalker import ClientHandler
from wizwalker.errors import ClientClosedError, MemoryReadError, MemoryWriteError, PatternFailed
from wizwalker.extensions.wizsprinter import SprintyClient

from .config import Config
from .petdance import PetDancer
from .reagents import Gatherer
from .safety import BotStopped, Controller
from .world import is_free

HOOK_TIMEOUT = 90
CONNECT_TIMEOUT = 300.0
SHUTDOWN_WAIT = 10.0


def new_handler() -> ClientHandler:
    # SprintyClient adds the entity helpers (mobs, safe spots) the gatherer uses.
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


async def mouse_on_pause(client, controller, mouseless: bool):
    """Paused, the player gets the real mouse back."""
    if not mouseless:
        return
    from wizwalker.memory.hooks import MouselessCursorMoveHook

    hooks = client.hook_handler

    def hook_on() -> bool:
        return hooks._check_if_hook_active(MouselessCursorMoveHook)

    released = False
    try:
        while not controller.stopped.is_set():
            try:
                if controller.paused and not released:
                    release_mouse_buttons(client)
                    if hook_on():
                        try:
                            await hooks.deactivate_mouseless_cursor_hook()
                        except Exception as exc:
                            logger.warning(f"paused, but the mouse hook stayed ({exc})")
                    released = True
                    logger.info("paused: the mouse is yours")
                elif not controller.paused and released:
                    if client.mouse_handler._ref_count > 0 and not hook_on():
                        try:
                            await hooks.activate_mouseless_cursor_hook()
                        except Exception as exc:
                            logger.warning(f"resumed, but the mouse hook stayed off ({exc})")
                    released = False
                    logger.info("resumed: the bot has the mouse again")
            except Exception as exc:
                logger.warning(f"mouse hook: {exc}")
            await asyncio.sleep(0.3)
    finally:
        if released and client.mouse_handler._ref_count > 0 and not hook_on():
            try:
                await hooks.activate_mouseless_cursor_hook()
            except Exception:
                pass


async def close_handler(handler: ClientHandler):
    """Unhook. One failed client doesn't leave the others patched."""
    for client in list(handler.clients):
        release_mouse_buttons(client)
        try:
            await client.close()
        except Exception as exc:
            logger.opt(exception=exc).error(
                "unhooking failed; restart Wizard101 before running the bot again"
            )


WORLD_HOOKS = ("player_struct", "player_stat_struct", "current_client", "current_render_context")


async def connect(handler: ClientHandler):
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
            "state/game_client.json, then start again.")
    if len(clients) > 1:
        logger.info(f"{len(clients)} game windows open; using the bot's own (process {client.process_id})")
    remember_game(client.process_id, client.window_handle)
    logger.info("activating hooks (can take a few seconds; move your wizard a step if it stalls)")
    try:
        hooks = client.hook_handler
        await hooks.activate_all_hooks(wait_for_ready=False)
        _click_left_of_center(client)
        await asyncio.wait_for(hooks._wait_for_value(hooks._base_addrs["current_root_window"], None),
                               timeout=HOOK_TIMEOUT)
        from .relog import at_character_select, play_from_character_select, reconnect_if_asked

        async with client.mouse_handler:
            if await reconnect_if_asked(client):
                await asyncio.sleep(3.0)
        if await at_character_select(client):
            async with client.mouse_handler:
                await play_from_character_select(client)
        await asyncio.wait_for(
            asyncio.gather(*(
                hooks._wait_for_value(hooks._base_addrs[name], None)
                for name in WORLD_HOOKS
            )),
            timeout=HOOK_TIMEOUT,
        )
    except (PatternFailed, MemoryWriteError, MemoryReadError, ClientClosedError):
        await close_handler(handler)
        raise SystemExit(
            "\nCould not hook into the game: its memory still holds changes from an earlier bot "
            "session that did not shut down cleanly.\n"
            "FIX: fully close Wizard101 (exit to desktop), start it again, log in, then rerun.\n"
            "To avoid this, stop the bot with Ctrl+Shift+Q (or the Wiz menu) instead of closing its window."
        ) from None
    except TimeoutError:
        raise SystemExit(
            f"Hooks did not activate within {HOOK_TIMEOUT}s. Make sure your wizard is loaded into the "
            "world (not the login or character screen), walk a step while it starts, and try running "
            "as Administrator."
        ) from None
    logger.success("connected to the game")
    from .safe_teleport import install

    install(client)
    return client


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
    """Teleports, keys and clicks wait while paused and stop once stopped."""
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
    """Aim clicks where the game will see them, and click windows at their center."""
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
        await mouse.click(int((r.x1 + r.x2) / 2), int((r.y1 + r.y2) / 2), **kwargs)

    mouse.set_mouse_position = set_mouse_position
    mouse.click_window = click_window


async def chore_loop(client, cfg: Config, controller: Controller, task: dict):
    """Dance while energy can pay, then farm until the wizard is stopped."""
    if cfg.mode == "seeds":
        from .seeds import seed_loop

        await seed_loop(client, controller, task)
        return
    from .dayplan import choose
    from .duty import carry_out, look
    from .petdance import PET_REQUEST, games_requested

    dancer = PetDancer(client, cfg.pet, controller)
    gatherer = Gatherer(client, cfg.reagents.hop_zones, controller)
    last = ""
    closed = 0
    while not controller.stopped.is_set():
        await controller.checkpoint()
        try:
            if not await is_free(client):
                task["name"] = "waiting (in a fight, dialogue, or loading)"
                await asyncio.sleep(0.4)
                continue
            if games_requested() is not None:
                task["name"] = "pet dance (requested)"
                wanted = games_requested() or 0
                try:
                    await dancer.trip(wanted)
                    closed = 0
                finally:
                    PET_REQUEST.unlink(missing_ok=True)
                continue
            snap = await look(client, cfg, dancer)
            decision = choose(snap)
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
            from .farm import closed_detail

            detail = closed_detail()
            if closed >= 3:
                logger.error(
                    "Wizard101 closed while the bot was hooked"
                    f"{detail}. Fully quit Wizard101 "
                    "(exit to desktop), start it, log in, stand in the world, then run "
                    "./mac/run.sh again."
                )
                controller.stop("the game client closed")
                break
            if closed == 1:
                logger.warning(f"the game client closed{detail}; retrying")
            task["name"] = "waiting for the game"
            await asyncio.sleep(2.0)
        except Exception as exc:
            closed = 0
            logger.opt(exception=exc).error(f"chore step failed: {exc}")
            task["name"] = "error, retrying"
            await asyncio.sleep(1.0)


async def status_loop(client, controller: Controller, task: dict):
    from .service import write_status

    while not controller.stopped.is_set():
        zone = ""
        try:
            zone = await client.zone_name() or ""
        except Exception:
            pass
        write_status(
            state="paused" if controller.paused else "running",
            zone=zone,
            task=task["name"],
        )
        await asyncio.sleep(5)


def _apply_mode(cfg: Config):
    if cfg.mode == "pet":
        cfg.pet.auto = True
        cfg.reagents.enabled = False
    elif cfg.mode in ("reagents", "seeds"):
        cfg.pet.auto = False
        cfg.reagents.enabled = False if cfg.mode == "seeds" else True


async def run(cfg: Config):
    _apply_mode(cfg)
    from .service import forget_stale_requests

    cleared = forget_stale_requests()
    if cleared:
        logger.info(f"ignored a leftover {', '.join(cleared)} from an earlier run")
    s = cfg.safety
    controller = Controller(s.stop_key, s.pause_key, s.max_hours)
    doing = []
    if cfg.mode == "seeds":
        doing.append("Savarstaad seeds until stopped (Troubled Warriors and Splithoof Barbarians)")
    if cfg.pet.auto:
        doing.append("dance while energy can pay, then Splithoof seeds (Halfang after 20 couch potatoes)")
    if cfg.reagents.enabled:
        doing.append(f"reagent sweeps ({cfg.reagents.hop_zones} gate(s) out)")
    logger.info(f"mode={cfg.mode}: {', '.join(doing) or 'nothing enabled'}. "
                f"{s.stop_key}=stop, {s.pause_key}=pause/resume")

    handler = new_handler()
    tasks: list[asyncio.Task] = []
    stack = contextlib.AsyncExitStack()
    try:
        try:
            client = await asyncio.wait_for(connect(handler), CONNECT_TIMEOUT)
        except TimeoutError:
            from . import gamerestart

            gamerestart.request(f"could not get into the world within {CONNECT_TIMEOUT / 60:.0f} min")
            raise SystemExit(
                f"could not get into the world within {CONNECT_TIMEOUT / 60:.0f} min; "
                "asked for a game restart"
            ) from None
        obey_controller(client, controller)
        if s.mouseless:
            await stack.enter_async_context(client.mouse_handler)
        task = {"name": "starting"}
        tasks = [
            asyncio.create_task(controller.watch(), name="safety"),
            asyncio.create_task(mouse_on_pause(client, controller, s.mouseless), name="mouse"),
            asyncio.create_task(chore_loop(client, cfg, controller, task), name="chores"),
            asyncio.create_task(status_loop(client, controller, task), name="status"),
        ]
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
            logger.warning(f"{len(pending)} task(s) still running at shutdown; unhooking anyway")
        try:
            await stack.aclose()
        except Exception:
            pass
        await close_handler(handler)
