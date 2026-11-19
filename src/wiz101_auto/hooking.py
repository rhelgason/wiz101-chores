"""WizWalker's hooks, written into the game once per game launch.

Why: on this Mac the game is x86 code translated by Rosetta, inside Wine.
Every crash with a known step happened right after the bot changed game code
while the game was running it: teleports (two jumps in the movement code are
patched on each one), the camp and unstick jumps, and a same-process rehook
that left the hook cave non-executable (the 2026-10-07 10:55 minidump). See
PLAYBOOK.md, "Why the Mac client crashed".

So:
- Only the hooks the pet and seeds modes read are installed: player, player
  stats, client, root window, render context, and the dance game when the
  pet plays. Not the movement teleport hook (it also turns off two collision
  checks for the whole session), not quest, not drops.
- They stay in the game when the bot stops. `state/hook_session.json`
  records every byte written. The next start compares those bytes with the
  game and, when they match, uses the hooks already there without writing
  any code. A new game process gets new hooks.
- The mouse hook goes in at start and comes out at stop, so the player has
  the mouse back between runs. A run that died without removing it leaves
  it recorded, and the next start adopts it instead of patching over it.
- After that, blackbox.guard refuses every write to game code.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from loguru import logger

from . import blackbox
from .hookbytes import (
    MOUSE_LEAVE_BEFORE,
    MOUSE_LEAVE_CANDIDATES,
    MOUSE_LEAVE_IMM,
    MOUSE_LEAVE_WIZWALKER,
    HookShapeError,
    mouse_leave_flag,
)

SESSION = Path("state") / "hook_session.json"
CAVE_SIZE = 4100  # HookHandler.AUTOBOT_SIZE (the hooks use ~300-430 of it)
# (record name, wizwalker hook class, _base_addrs key, the hook's export attribute)
DATA_HOOKS = (
    ("player", "PlayerHook", "player_struct", "player_struct"),
    ("player_stat", "PlayerStatHook", "player_stat_struct", "stat_addr"),
    ("client", "AnyBuildClientHook", "current_client", "current_client_addr"),
    ("root_window", "AnyBuildRootWindowHook", "current_root_window", "current_root_window_addr"),
    ("render_context", "RenderContextHook", "current_render_context", "current_render_context_addr"),
)
DANCE = ("dance", "DanceGameMovesHook", "dance_game_moves", "dance_game_moves")
# Opt-in (safety.data_jumps): jump.PositionHook, WizWalker's movement hook
# without its per-jump code patches. In once; a jump is data writes after that.
JUMP = ("jump", "PositionHook", "jump_helper", "jump_helper")


class HookStateError(RuntimeError):
    """The game carries hook bytes this bot did not record. Restarting Wizard101 clears them."""


def load_session(path: Path | None = None) -> dict | None:
    try:
        data = json.loads((path or SESSION).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_session(data: dict, path: Path | None = None) -> None:
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = path or SESSION
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    run = blackbox.folder()
    if run is not None:
        try:
            (run / "hook_session.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass


def same_game(session: dict | None, pid: int, module_base: int) -> bool:
    return bool(session) and session.get("pid") == pid and session.get("module_base") == module_base


def verdict(states: list[str]) -> str:
    """'dirty' when any recorded spot holds bytes that are neither ours nor the
    game's; 'fresh' when none is ours (a new game with a reused pid); else
    'adopt': the ours are reused and the originals installed again (a run
    that died between recording a hook and writing it)."""
    if any(s not in ("ours", "original") for s in states):
        return "dirty"
    if not any(s == "ours" for s in states):
        return "fresh"
    return "adopt"


def hook_record(hook, cls_name: str, base_key: str, export: int) -> dict:
    return {
        "class": cls_name,
        "jump": int(hook.jump_address),
        "jump_bytes": bytes(hook.jump_bytecode).hex(),
        "original": bytes(hook.jump_original_bytecode).hex(),
        "hook": int(hook.hook_address),
        "hook_bytes": bytes(hook.hook_bytecode).hex(),
        "base_key": base_key,
        "export": int(export),
    }


async def _bytes_at(handler, address: int, size: int) -> bytes | None:
    try:
        return await handler.read_bytes(address, size)
    except Exception as exc:
        if type(exc).__name__ == "ClientClosedError":
            raise
        return None


async def hook_state(handler, record: dict) -> str:
    """'ours', 'original', or 'other' for one recorded hook."""
    jump = bytes.fromhex(record["jump_bytes"])
    now = await _bytes_at(handler, record["jump"], len(jump))
    if now is None:
        return "other"
    if now == jump:
        body = bytes.fromhex(record["hook_bytes"])
        return "ours" if await _bytes_at(handler, record["hook"], len(body)) == body else "other"
    if now == bytes.fromhex(record["original"])[: len(jump)]:
        return "original"
    return "other"


def _hook_class(name: str):
    if name == "PositionHook":
        from .jump import PositionHook

        return PositionHook
    if name == "DanceGameMovesHook":
        from .petdance import DanceGameMovesHook

        return DanceGameMovesHook
    if name.startswith("AnyBuild"):
        from . import anybuild

        return getattr(anybuild, name)
    from wizwalker.memory import hooks

    return getattr(hooks, name)


def _rebuild(handler, record: dict):
    cls = _hook_class(record["class"])
    hook = cls(handler)
    hook.jump_address = record["jump"]
    hook.jump_bytecode = bytes.fromhex(record["jump_bytes"])
    hook.jump_original_bytecode = bytes.fromhex(record["original"])
    hook.hook_address = record["hook"]
    hook.hook_bytecode = bytes.fromhex(record["hook_bytes"])
    for export_name, _size in getattr(cls, "exports", None) or ():
        setattr(hook, export_name, record["export"])
    handler._active_hooks[cls] = hook
    handler._base_addrs[record["base_key"]] = record["export"]
    return hook


async def _adopt_mouse(client, record: dict) -> bool:
    """Take over a mouse hook a dead run left in user32. True when it was ours."""
    from wizwalker.memory.hooks import MouselessCursorMoveHook

    handler = client.hook_handler
    jump = bytes.fromhex(record["jump_bytes"])
    body = bytes.fromhex(record["hook_bytes"])
    if await _bytes_at(handler, record["jump"], len(jump)) != jump:
        return False
    if await _bytes_at(handler, record["hook"], len(body)) != body:
        return False
    hook = MouselessCursorMoveHook(handler, handler._hook_cache)
    hook._hooked_instances = 1
    hook.jump_address = record["jump"]
    hook.jump_bytecode = jump
    hook.jump_original_bytecode = bytes.fromhex(record["original"])
    hook.hook_address = record["hook"]
    hook.hook_bytecode = bytes.fromhex(record["hook_bytes"])
    hook._autobot_addr = record["cave"]
    hook._autobot_original_bytes = bytes.fromhex(record["cave_original"])
    hook._autobot_bytes_offset = record["cave_used"]
    hook.toggle_bool_addrs = tuple(record["bools"])
    hook.set_cursor_pos = (record["set_cursor_pos"], bytes.fromhex(record["set_cursor_pos_original"]))
    hook.mouse_pos_addr = record["mouse_pos"]
    handler._active_hooks[MouselessCursorMoveHook] = hook
    handler._base_addrs["mouse_position"] = record["mouse_pos"]
    return True


def _mouse_record(hook) -> dict:
    return {
        "jump": int(hook.jump_address),
        "jump_bytes": bytes(hook.jump_bytecode).hex(),
        "original": bytes(hook.jump_original_bytecode).hex(),
        "hook": int(hook.hook_address),
        "hook_bytes": bytes(hook.hook_bytecode).hex(),
        "cave": int(hook._autobot_addr),
        "cave_original": bytes(hook._autobot_original_bytes).hex(),
        "cave_used": int(hook._autobot_bytes_offset),
        "bools": [int(a) for a in hook.toggle_bool_addrs],
        "set_cursor_pos": int(hook.set_cursor_pos[0]),
        "set_cursor_pos_original": bytes(hook.set_cursor_pos[1]).hex(),
        "mouse_pos": int(hook.mouse_pos_addr),
    }


async def mouse_leave_address(handler) -> int:
    """The `mov byte [r+d], 0` whose immediate the mouse hook sets to 1 (hookbytes)."""
    module = "WizardGraphicalClient.exe"
    found = []
    try:
        found = await handler.pattern_scan(MOUSE_LEAVE_CANDIDATES, module=module, return_multiple=True)
    except Exception as exc:
        if type(exc).__name__ == "ClientClosedError":
            raise
    good = []
    for address in found or ():
        before = await handler.read_bytes(address - MOUSE_LEAVE_BEFORE, MOUSE_LEAVE_BEFORE)
        if mouse_leave_flag(before, await handler.read_bytes(address, 16)):
            good.append(int(address))
    if len(good) == 1:
        return good[0]
    try:  # builds before 11-18: WizWalker's own pattern
        return int(await handler.pattern_scan(MOUSE_LEAVE_WIZWALKER, module=module))
    except Exception as exc:
        if type(exc).__name__ == "ClientClosedError":
            raise
        raise HookShapeError(
            f"mouse hook: the game's mouse-leave flag was not found ({len(good)} spots of its shape). "
            "A game update moved it; the bot needs a new pattern."
        ) from exc


class Hooks:
    """The hooks of one game process, and the record that lets the next run reuse them."""

    def __init__(self, client):
        self.client = client
        self.handler = client.hook_handler
        self.pid = int(client.process_id)
        self.session: dict = {}
        self.adopted: list[str] = []
        self.installed: list[str] = []
        self.teleport_hooks = False

    def _save(self) -> None:
        self.session["cave"] = {
            "address": int(self.handler._autobot_address or 0),
            "original": bytes(self.handler._original_autobot_bytes or b"").hex(),
            "used": int(self.handler._autobot_pos),
            "size": CAVE_SIZE,
            "kind": getattr(self.handler, "_cave_kind", "game"),
        }
        save_session(self.session)

    async def attach(self, *, dance: bool = False, teleport: bool = False, jump: bool = False) -> None:
        """Adopt this game's recorded hooks, or install them. Raises HookStateError."""
        from .rehook import install_scan_guard

        handler = self.handler
        install_scan_guard(handler)
        base = int(handler.process.base_address)
        old = load_session()
        records: dict = {}
        if same_game(old, self.pid, base):
            records = dict(old.get("hooks") or {})
            states = {name: await hook_state(handler, rec) for name, rec in records.items()}
            blackbox.event("hook_check", states=states)
            choice = verdict(list(states.values()))
            if choice == "dirty":
                raise HookStateError(
                    "This Wizard101 has hook bytes that do not match the bot's record "
                    f"({', '.join(f'{k}={v}' for k, v in states.items())}). "
                    "Quit Wizard101 completely, start it again, log in, then run the bot."
                )
            if choice == "fresh":
                records = {}
        self.session = {"version": 1, "pid": self.pid, "module_base": base, "state": "live", "hooks": {}}
        if same_game(old, self.pid, base) and await self._reuse_cave(old.get("cave") or {}):
            blackbox.event(
                "hook_cave_reused", address=hex(handler._autobot_address), used=handler._autobot_pos
            )
        if records:
            records = {name: rec for name, rec in records.items() if states[name] == "ours"}
            for name, rec in records.items():
                _rebuild(handler, rec)
                self.session["hooks"][name] = rec
                self.adopted.append(name)
            logger.info(
                f"hooks: reusing the hooks already in this game ({', '.join(self.adopted)}); no code written"
            )
            blackbox.event("hook_adopt", hooks=self.adopted)
        if same_game(old, self.pid, base):
            if old.get("mouse_originals"):
                self.session["mouse_originals"] = old["mouse_originals"]
            mouse = old.get("mouse")
            if mouse and await _adopt_mouse(self.client, mouse):
                self.session["mouse"] = mouse
                logger.info("hooks: the last run left the mouse hook in; reusing it")
                blackbox.event("hook_adopt", hooks=["mouse"])
            elif old.get("mouse_originals"):
                await self._repair_mouse()
        wanted = list(DATA_HOOKS) + ([DANCE] if dance else []) + ([JUMP] if jump else [])
        for name, cls_name, base_key, export_attr in wanted:
            if name in self.session["hooks"]:
                continue
            await self._install(name, cls_name, base_key, export_attr)
        if teleport:
            # Windows chores only. Not recorded: these come out at stop.
            with blackbox.guard.allow("quest and movement teleport hooks"):
                await handler.activate_quest_hook()
                await handler.activate_movement_teleport_hook(wait_for_ready=False)
            self.teleport_hooks = True
        self._save()

    async def _reuse_cave(self, cave: dict) -> bool:
        """Use the recorded cave when this game's copy was already changed.

        A cave the bot zeroed no longer matches WizWalker's pattern, so a
        fresh scan would fail. One that still holds the game's bytes is left
        for the scan.
        """
        if cave.get("kind") == "alloc" and cave.get("address"):
            # Our own allocation in this game: never freed, so it is still there
            # unless the process is gone (then same_game would have said no).
            if await _bytes_at(self.handler, int(cave["address"]), 16) is None:
                return False
            self.handler._autobot_address = int(cave["address"])
            self.handler._original_autobot_bytes = b""
            self.handler._autobot_pos = int(cave.get("used") or 0)
            self.handler._cave_kind = "alloc"
            return True
        if not cave.get("address") or not cave.get("original"):
            return False
        original = bytes.fromhex(cave["original"])
        if await _bytes_at(self.handler, int(cave["address"]), len(original)) == original:
            return False
        self.handler._autobot_address = int(cave["address"])
        self.handler._original_autobot_bytes = original
        self.handler._autobot_pos = int(cave.get("used") or 0)
        return True

    async def _prepare_cave(self) -> None:
        """Scratch memory for the hook bodies: a fresh allocation beside the game module.

        11-12: the update removed the game function WizWalker zeroed and used as
        its cave (AUTOBOT_PATTERN). An allocation writes no game code at all and
        its protection never changes (the 10-07 crash was a cave page whose
        protection did). It is never freed: the hooks stay in the game between
        runs and jump into it.
        """
        from .rehook import _allocate_near_module

        handler = self.handler
        address = _allocate_near_module(handler, CAVE_SIZE)
        if not address:
            raise OSError("could not allocate hook memory beside the game")
        handler._autobot_address = int(address)
        handler._original_autobot_bytes = b""
        handler._autobot_pos = 0
        handler._cave_kind = "alloc"
        self._save()
        blackbox.event("hook_install", hook="cave", address=hex(handler._autobot_address), kind="alloc")

    async def _install(self, name: str, cls_name: str, base_key: str, export_attr: str) -> None:
        """MemoryHook.hook, step by step, so the record is on disk before any write.

        A run that dies between the record and the writes leaves a hook that is
        either ours or still the game's own bytes; the next start handles both.
        """
        handler = self.handler
        cls = _hook_class(cls_name)
        if handler._autobot_address is None:
            await self._prepare_cave()
        hook = cls(handler)
        pattern, module = await hook.get_pattern()
        hook.jump_address = await hook.get_jump_address(pattern, module=module)
        hook.hook_address = await hook.get_hook_address(getattr(cls, "cave_size", 50))
        hook.hook_bytecode = await hook.get_hook_bytecode()
        hook.jump_bytecode = await hook.get_jump_bytecode()
        hook.jump_original_bytecode = await hook.read_bytes(hook.jump_address, len(hook.jump_bytecode))
        export = getattr(hook, export_attr)
        self.session["hooks"][name] = hook_record(hook, cls_name, base_key, export)
        self._save()
        blackbox.event("hook_install", hook=name, jump=hex(hook.jump_address), body=hex(hook.hook_address))
        with blackbox.guard.allow(f"{name} hook"):
            await hook.prehook()
            await hook.write_bytes(hook.hook_address, hook.hook_bytecode)
            await hook.write_bytes(hook.jump_address, hook.jump_bytecode)
            await hook.posthook()
        handler._active_hooks[cls] = hook
        handler._base_addrs[base_key] = export
        self.installed.append(name)
        logger.info(f"hooks: {name} hook in (jump {hook.jump_address:#x} -> {hook.hook_address:#x})")

    async def ensure_dance(self) -> None:
        if "dance" in self.session.get("hooks", {}):
            return
        await self._install(*DANCE)

    async def _mouse_originals(self) -> dict:
        """The bytes the mouse hook will change, read before it changes them."""
        from wizwalker.memory.hooks import User32GetClassInfoBaseHook

        handler = self.handler
        regions = []
        for symbol, size in (
            ("GetCursorPos", 5),
            ("SetCursorPos", 6),
            ("GetClassInfoExA", User32GetClassInfoBaseHook.AUTOBOT_SIZE),
        ):
            address = await handler.get_address_from_symbol("user32.dll", symbol)
            regions.append([symbol, int(address), (await handler.read_bytes(address, size)).hex()])
        if regions[0][2].startswith("e9"):
            raise HookStateError(
                "GetCursorPos already jumps somewhere this bot did not record (a mouse hook left "
                "in by a run that failed). Quit Wizard101 completely, start it, log in, and run again."
            )
        one = await handler.pattern_scan(rb"\x00\xFF\x50\x18\x66\xC7", module="WizardGraphicalClient.exe")
        two = await mouse_leave_address(handler)
        for label, address in (("mouse bool one", one), ("mouse bool two", two + MOUSE_LEAVE_IMM)):
            regions.append([label, int(address), (await handler.read_bytes(address, 1)).hex()])
        return {"regions": regions}

    async def _repair_mouse(self) -> None:
        """A run died with the mouse hook half in or half out: put back the recorded originals."""
        changed = []
        for label, address, original in self.session["mouse_originals"]["regions"]:
            want = bytes.fromhex(original)
            if await _bytes_at(self.handler, address, len(want)) != want:
                changed.append((label, address, want))
        if not changed:
            return
        spots = ", ".join(c[0] for c in changed)
        logger.info(f"hooks: the last run left the mouse hook partly in ({spots}); undoing it")
        blackbox.event("mouse_repair", spots=[c[0] for c in changed])
        with blackbox.guard.allow("mouse hook repair"):
            # The jump first, so nothing runs into the cave while it is put back.
            for _label, address, want in sorted(changed, key=lambda c: c[0] != "GetCursorPos"):
                await self.handler.write_bytes(address, want)

    async def mouse_in(self) -> None:
        """The mouse hook. Adopted already, or installed now and recorded."""
        from wizwalker.memory.hooks import MouselessCursorMoveHook

        mouse = self.client.mouse_handler
        if self.handler._check_if_hook_active(MouselessCursorMoveHook):
            mouse._ref_count = 0
            await mouse.__aenter__()
            return
        if "mouse_originals" not in self.session:
            # No snapshot, no hook: without the original bytes a failed install
            # cannot be put back (11-18: it left GetCursorPos jumping, the game's
            # cursor stuck, and nothing to undo it with).
            self.session["mouse_originals"] = await self._mouse_originals()
            self._save()
        leave = next(r[1] for r in self.session["mouse_originals"]["regions"] if r[0] == "mouse bool two")
        # WizWalker looks for this flag with a pattern the 11-18 build no longer
        # matches; its hook takes the address from its cache when one is there.
        self.handler._hook_cache.setdefault(MouselessCursorMoveHook, {})["bool_two_address"] = (
            leave - MOUSE_LEAVE_IMM
        )
        blackbox.event("hook_install", hook="mouse")
        try:
            with blackbox.guard.allow("mouse hook"):
                await mouse.__aenter__()
        except Exception as exc:
            if type(exc).__name__ == "ClientClosedError":
                raise
            logger.error(f"hooks: the mouse hook failed half way in ({exc}); putting the game's bytes back")
            blackbox.event("mouse_install_failed", error=str(exc)[:200])
            self.handler._active_hooks.pop(MouselessCursorMoveHook, None)
            await self._repair_mouse()
            raise
        hook = self.handler._get_hook_by_type(MouselessCursorMoveHook)
        if hook is not None:
            self.session["mouse"] = _mouse_record(hook)
            self._save()
        logger.info("hooks: mouse hook in")

    async def mouse_out(self) -> None:
        mouse = self.client.mouse_handler
        if mouse._ref_count <= 0:
            return
        blackbox.event("hook_remove", hook="mouse")
        with blackbox.guard.allow("mouse hook removal"):
            while mouse._ref_count > 0:
                await mouse.__aexit__(None, None, None)
        self.session.pop("mouse", None)
        self._save()
        logger.info("hooks: mouse hook out; the mouse is yours")

    async def park(self, *, running: bool) -> None:
        """Stop: the mouse hook comes out, the data hooks stay for the next run."""
        if not self.session.get("pid"):
            return  # attach refused or failed first: the record on disk stays as it was
        if running:
            try:
                await self.mouse_out()
            except Exception as exc:
                logger.opt(exception=exc).error("hooks: could not take the mouse hook out")
            if self.teleport_hooks:
                with blackbox.guard.allow("quest and movement teleport hooks removal"):
                    for off in (
                        self.handler.deactivate_movement_teleport_hook,
                        self.handler.deactivate_quest_hook,
                    ):
                        try:
                            await off()
                        except Exception as exc:
                            logger.warning(f"hooks: could not remove a teleport hook ({exc})")
        self.session["state"] = "parked" if running else "game closed"
        self._save()
        blackbox.event("hooks_parked", running=running)
