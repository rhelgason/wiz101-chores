"""Command line: `wiz101-auto run|pet|start|stop|status`."""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from .config import MODES, load_config

ACTIVITY_LOG = "activity.log"


def _setup_logging(log_file: str | None, verbose: bool):
    logger.remove()
    fmt = "<green>{time:HH:mm:ss}</green> {message}"
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO", format=fmt)
    if log_file:
        logger.add(log_file, level="DEBUG", rotation="10 MB", retention=5)
        logger.add(
            ACTIVITY_LOG,
            level="INFO",
            format="{time:HH:mm:ss} | {level: <7} | {message}",
            rotation="5 MB",
            retention=3,
            encoding="utf-8",
        )


_ESC = "\033"
_COLORS = {
    "SUCCESS": _ESC + "[32m",
    "WARNING": _ESC + "[33m",
    "ERROR": _ESC + "[31m",
    "CRITICAL": _ESC + "[31m",
}
_RESET = _ESC + "[0m"


def _watch(path: str, backlog: int = 30):
    """Print the last lines of the activity log, then follow it."""
    import os
    import time

    if sys.platform == "win32":
        os.system("title wiz101-auto live log")
    print(f"following {path} (Ctrl+C to stop)")
    print()
    pos = 0
    shown_backlog = False
    while True:
        try:
            size = os.path.getsize(path)
        except OSError:
            time.sleep(1)
            continue
        if size < pos:
            pos = 0
        with open(path, encoding="utf-8", errors="replace") as f:
            if not shown_backlog:
                lines = f.readlines()
                pos = f.tell()
                new = lines[-backlog:]
                shown_backlog = True
            else:
                f.seek(pos)
                new = f.readlines()
                pos = f.tell()
        for line in new:
            line = line.rstrip()
            level = line.split("|")[1].strip() if line.count("|") >= 2 else ""
            color = _COLORS.get(level, "")
            print(f"{color}{line}{_RESET}" if color else line, flush=True)
        try:
            time.sleep(0.5)
        except KeyboardInterrupt:
            return


def _lower_priority():
    """Run below normal priority so the bot doesn't starve the game."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
    except Exception:
        pass


def main(argv: list[str] | None = None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        prog="wiz101-auto",
        description="Wizard101 chore bot: ghulture, couch potatoes, and gold when energy is spent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the chore bot")
    run_p.add_argument("-c", "--config", default=None, help="path to config YAML")
    run_p.add_argument("-m", "--mode", choices=list(MODES), help="override config mode")
    run_p.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("relog", help="quit to character select and play again (bot stopped)")
    sub.add_parser("set-login", help="save the game login (Windows Credential Manager) for game restarts")
    sub.add_parser("restart-game", help="close Wizard101, start it and log in (bot stopped)")
    insp = sub.add_parser("inspect", help="print zone, position, and nearby reagents (bot stopped)")
    insp.add_argument("--windows", action="store_true", help="also dump the visible UI window tree")

    sub.add_parser("watch", help="follow activity.log live")
    pet_p = sub.add_parser("pet", help="ask the running bot to play the pet dance game")
    pet_p.add_argument(
        "--games", type=int, default=0,
        help="at most this many games (0: until energy runs out)",
    )
    reag_p = sub.add_parser("reagent", help="ask the running bot to gather one reagent")
    reag_p.add_argument("name", nargs="?", default="", help='e.g. "scrap iron"')
    reag_p.add_argument("--clear", action="store_true", help="stop gathering and follow the quest only")
    fish_p = sub.add_parser("fish", help="tell the bot a fishing quest needs doing")
    fish_p.add_argument("--clear", action="store_true", help="the fishing quest is done")
    alt_p = sub.add_parser("pet-alt", help="pet-only bot on the other game window")
    alt_p.add_argument("action", choices=["start", "stop", "status", "run"])
    alt_p.add_argument("--pid", type=int, default=0, help="that game's process id")
    shot_p = sub.add_parser("screenshot", help="save the game window as a PNG")
    shot_p.add_argument("-o", "--output", default="state/screenshot.png")

    start_p = sub.add_parser("start", help="start the bot in the background")
    start_p.add_argument("-c", "--config", default="config.yaml")
    start_p.add_argument("--supervise", action="store_true", help="restart automatically after crashes")
    restart_p = sub.add_parser("restart", help="stop, then start again in the background")
    restart_p.add_argument("-c", "--config", default="config.yaml")
    restart_p.add_argument("--supervise", action="store_true")
    sub.add_parser("stop", help="stop the background bot cleanly")
    sub.add_parser("status", help="is it running, what is it doing, recent log lines")
    logs_p = sub.add_parser("logs", help="show the log")
    logs_p.add_argument("-n", type=int, default=80)
    logs_p.add_argument("-f", "--follow", action="store_true")
    sup_p = sub.add_parser("supervise", help="run in the foreground, restarting after crashes")
    sup_p.add_argument("-c", "--config", default="config.yaml")

    args = parser.parse_args(argv)

    from . import service

    if args.command == "start":
        sys.exit(service.start(args.config, args.supervise))
    if args.command == "stop":
        sys.exit(service.stop())
    if args.command == "restart":
        service.stop()
        sys.exit(service.start(args.config, args.supervise))
    if args.command == "status":
        sys.exit(service.status())
    if args.command == "logs":
        sys.exit(service.logs(args.n, args.follow))
    if args.command == "supervise":
        sys.exit(service.supervise(args.config))
    if args.command == "watch":
        _watch(ACTIVITY_LOG)
        return

    if sys.platform != "win32" and args.command not in ("pet", "reagent", "fish"):
        raise SystemExit("wiz101-auto talks to the Windows game client and must run on Windows.")
    _lower_priority()

    if args.command == "set-login":
        import getpass

        from .gamerestart import load_login, save_login

        print("The login is kept in Windows Credential Manager for this Windows user only.")
        user = input("Wizard101 username: ").strip()
        pw = getpass.getpass("Wizard101 password (not shown): ")
        if not user or not pw:
            print("nothing saved")
            return 1
        ok = save_login(user, pw) and (load_login() or ("", ""))[0] == user
        print("saved: the supervisor can now restart a frozen game and log in" if ok else "could not save it")
        return 0 if ok else 1
    if args.command == "restart-game":
        from .gamerestart import restart_game

        if service.running_pid():
            print("stop the bot first (`stop`)")
            return 1
        return 0 if restart_game() else 1
    if args.command == "relog":
        _setup_logging(None, True)
        from .bot import close_handler, connect, new_handler
        from .relog import relog

        async def _relog():
            handler = new_handler()
            try:
                client = await connect(handler)
                async with client.mouse_handler:
                    await relog(client)
            finally:
                await close_handler(handler)

        asyncio.run(_relog())
        return
    if args.command == "inspect":
        _setup_logging(None, True)
        from .inspect_state import inspect

        asyncio.run(inspect(show_windows=args.windows))
        return
    if args.command == "screenshot":
        from .screenshot import save_screenshot

        print(save_screenshot(args.output))
        return
    if args.command == "pet":
        from .petdance import PET_REQUEST

        PET_REQUEST.parent.mkdir(exist_ok=True)
        PET_REQUEST.write_text(str(max(0, args.games)), encoding="utf-8")
        n = f"{args.games} game(s)" if args.games else "games until the pet is out of energy"
        print(f"requested: at its next free moment the bot goes to the Pet Pavilion for {n}, then comes back")
        return 0
    if args.command == "reagent":
        from .duty import REAGENT_REQUEST

        REAGENT_REQUEST.parent.mkdir(exist_ok=True)
        if args.clear or not args.name.strip():
            REAGENT_REQUEST.unlink(missing_ok=True)
            print("cleared: reagent runs follow the tracked quest only")
            return 0
        REAGENT_REQUEST.write_text(args.name.strip(), encoding="utf-8")
        print(f"requested: gather {args.name.strip()} when energy is not needed for the pet or the garden")
        return 0
    if args.command == "fish":
        from .duty import FISH_REQUEST

        FISH_REQUEST.parent.mkdir(exist_ok=True)
        if args.clear:
            FISH_REQUEST.unlink(missing_ok=True)
            print("cleared: fishing follows the tracked quest only")
            return 0
        FISH_REQUEST.write_text("1", encoding="utf-8")
        print("requested: a fishing quest is on. Energy goes to the pet and garden first")
        return 0
    if args.command == "pet-alt":
        from . import petclient

        if args.action == "run":
            return petclient.main_run(args.pid)
        if args.action == "start":
            return petclient.start(args.pid)
        if args.action == "stop":
            return petclient.stop()
        print(petclient.status_text())
        return 0

    cfg = load_config(args.config)
    if args.mode:
        cfg.mode = args.mode
    _setup_logging(cfg.log_file, args.verbose)

    from .bot import run

    try:
        reason = asyncio.run(run(cfg)) or ""
    except KeyboardInterrupt:
        logger.info("interrupted")
        return
    if "crashed" in reason:
        sys.exit(3)
