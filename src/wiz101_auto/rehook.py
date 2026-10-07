"""Hook the game that is already open.

WizWalker rewrites the client and only keeps the original bytes in this
process. Killing the bot leaves those rewrites in place, and the next
pattern scan fails until Wizard101 is exited. The originals are written
here at hook time, keyed by the game's process and module base. The next
start of the same process puts them back, then hooks again.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

HOOKS_FILE = Path("state") / "hooks.json"
REHOOK_SETTLE = 0.5


def _games(payload: dict | None) -> dict:
    if not payload:
        return {}
    games = payload.get("games")
    if isinstance(games, dict):
        return dict(games)
    pid = payload.get("pid")
    if pid:
        return {str(int(pid)): payload}
    return {}


def game_record(payload: dict | None, pid: int) -> dict | None:
    record = _games(payload).get(str(int(pid)))
    return record if isinstance(record, dict) else None


def record_matches(record: dict | None, module_base: int) -> bool:
    if not record:
        return False
    try:
        return int(record.get("module_base") or 0) == int(module_base)
    except (TypeError, ValueError):
        return False


def regions_to_restore(
    record: dict | None,
    module_base: int,
    current: dict[int, bytes],
) -> list[tuple[int, bytes]]:
    """Original bytes that are not in the game anymore. Empty for another process."""
    if not record_matches(record, module_base):
        return []
    writes = []
    for region in record.get("regions") or []:
        try:
            address = int(region["address"])
            original = bytes.fromhex(region["original"])
        except (KeyError, TypeError, ValueError):
            continue
        if not original:
            continue
        have = current.get(address)
        if have is None or len(have) != len(original) or have == original:
            continue
        writes.append((address, original))
    return writes


def _add(found: dict[int, bytes], address, raw) -> None:
    if not address or not raw:
        return
    data = bytes(raw)
    if data:
        found[int(address)] = data


def capture_regions(handler, client=None) -> list[tuple[int, bytes]]:
    """What a clean unhook would write back, taken from the live hook objects."""
    found: dict[int, bytes] = {}
    _add(found, getattr(handler, "_autobot_address", None), getattr(handler, "_original_autobot_bytes", None))
    client = client if client is not None else getattr(handler, "client", None)
    jes = getattr(client, "_je_instruction_forward_backwards", None) if client is not None else None
    for hook in list(getattr(handler, "_active_hooks", {}).values()):
        _add(found, getattr(hook, "jump_address", None), getattr(hook, "jump_original_bytecode", None))
        old_jes = getattr(hook, "_old_jes_bytes", None)
        if jes and old_jes:
            for address, raw in zip(jes, old_jes, strict=False):
                _add(found, address, raw)
        coll_addrs = getattr(hook, "_collision_je_addrs", None) or ()
        coll_bytes = getattr(hook, "_old_collision_jes_bytes", None) or ()
        for address, raw in zip(coll_addrs, coll_bytes, strict=False):
            _add(found, address, raw)
        _add(found, getattr(hook, "_autobot_addr", None), getattr(hook, "_autobot_original_bytes", None))
        cursor = getattr(hook, "set_cursor_pos", None)
        if isinstance(cursor, tuple) and len(cursor) == 2:
            _add(found, cursor[0], cursor[1])
        for address in getattr(hook, "toggle_bool_addrs", None) or ():
            _add(found, address, b"\x00")
    if client is not None and getattr(client, "_movement_update_patched", False):
        _add(
            found,
            getattr(client, "_movement_update_address", None),
            getattr(client, "_movement_update_original_bytes", None),
        )
    return list(found.items())


def snapshot_payload(pid: int, module_base: int, regions: list[tuple[int, bytes]]) -> dict:
    return {
        "pid": int(pid),
        "module_base": int(module_base),
        "regions": [{"address": int(address), "original": data.hex()} for address, data in regions if data],
    }


def load_hooks(path: Path = HOOKS_FILE) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_hooks(payload: dict, path: Path = HOOKS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def forget_hooks(path: Path = HOOKS_FILE, pid: int | None = None) -> None:
    """Drop one game's record. No pid drops the whole file."""
    if pid is None:
        path.unlink(missing_ok=True)
        return
    if not pid:
        return
    games = _games(load_hooks(path))
    if str(int(pid)) not in games:
        return
    games.pop(str(int(pid)), None)
    if games:
        save_hooks({"games": games}, path)
    else:
        path.unlink(missing_ok=True)


def remember_installed_hooks(client, path: Path = HOOKS_FILE) -> int:
    """Record this process's original bytes. A later start of the same process uses them."""
    handler = client.hook_handler
    try:
        base = int(handler.process.base_address)
    except Exception:
        return 0
    regions = capture_regions(handler, client)
    if not regions:
        return 0
    game = snapshot_payload(int(client.process_id), base, regions)
    games = _games(load_hooks(path))
    games[str(game["pid"])] = game
    save_hooks({"games": games}, path)
    return len(game["regions"])


async def restore_leftover_hooks(client, path: Path = HOOKS_FILE) -> int:
    """Put this process's original bytes back. Another process's record is ignored."""
    payload = load_hooks(path)
    pid = int(getattr(client, "process_id", 0) or 0)
    record = game_record(payload, pid) if pid else None
    if record is None:
        return 0
    handler = client.hook_handler
    try:
        base = int(handler.process.base_address)
    except Exception:
        return 0
    if not record_matches(record, base):
        forget_hooks(path, pid)
        return 0
    current: dict[int, bytes] = {}
    for region in record.get("regions") or []:
        try:
            address = int(region["address"])
            original = bytes.fromhex(region["original"])
        except (KeyError, TypeError, ValueError):
            continue
        if not original:
            continue
        try:
            current[address] = bytes(await handler.read_bytes(address, len(original)))
        except Exception:
            continue
    writes = regions_to_restore(record, base, current)
    for address, data in writes:
        await handler.write_bytes(address, data)
    if writes:
        await asyncio.sleep(REHOOK_SETTLE)
    return len(writes)
