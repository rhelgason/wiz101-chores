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
import os
from pathlib import Path

HOOKS_FILE = Path("state") / "hooks.json"
LOCK_FILE = Path("state") / "bot.lock"
REHOOK_SETTLE = 0.5
UNHOOK_TIMEOUT = 20.0
MAX_SCAN_PAGE = 8 * 1024 * 1024
# This text must not contain "Could not hook" or "Hooks did not activate".
# The supervisor treats those as a reason to quit Wizard101.
SCAN_STALL = (
    "Hooking this open Wizard101 took too long.\n"
    "Stop any other bot from the Wiz menu, leave the game open, and run again.\n"
    "The next start puts the saved hook bytes back. "
    "Quit the game only when its window is already gone."
)


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


def leftovers_remain(
    record: dict | None,
    module_base: int,
    current: dict[int, bytes],
) -> bool:
    """A saved region is still not its original bytes. Another process is not leftover."""
    if not record_matches(record, module_base):
        return False
    for region in (record or {}).get("regions") or []:
        try:
            address = int(region["address"])
            original = bytes.fromhex(region["original"])
        except (KeyError, TypeError, ValueError):
            return True
        if not original:
            continue
        have = current.get(address)
        if have is None or have != original:
            return True
    return False


def keep_hook_record(*, running: bool) -> bool:
    """A live game keeps its saved originals, even after a clean unhook.

    The unhook write does not always stick on this Wine build. The next start
    of the same process puts the saved bytes back when they differ, and writes
    nothing when they already match. A closed game has a new process next time.
    """
    return bool(running)


def pid_alive(pid: int) -> bool:
    if not pid or int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def claim_bot_lock(
    path: Path = LOCK_FILE,
    pid: int | None = None,
    alive=pid_alive,
) -> str | None:
    """Take the single-bot lock. A message means another live bot has it."""
    mine = os.getpid() if pid is None else int(pid)
    if path.exists():
        try:
            other = int(path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            other = 0
        if other and other != mine and alive(other):
            return (
                f"A bot is already running (pid {other}). "
                "Stop it from the Wiz menu, leave Wizard101 open, then run again."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(mine), encoding="utf-8")
    return None


def release_bot_lock(path: Path = LOCK_FILE, pid: int | None = None) -> None:
    mine = os.getpid() if pid is None else int(pid)
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == str(mine):
            path.unlink()
    except OSError:
        pass


def scan_advance(page: int, region_base: int, region_size: int) -> int | None:
    """The next address in a module scan. None when the cursor would not move."""
    try:
        nxt = int(region_base) + int(region_size)
        here = int(page)
    except (TypeError, ValueError):
        return None
    if nxt <= here:
        return None
    return nxt


def page_is_scannable(size: int) -> bool:
    try:
        size = int(size)
    except (TypeError, ValueError):
        return False
    return 0 < size <= MAX_SCAN_PAGE


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


async def originals_back(client, path: Path = HOOKS_FILE) -> bool:
    """True when this process has no saved region left patched."""
    payload = load_hooks(path)
    pid = int(getattr(client, "process_id", 0) or 0)
    record = game_record(payload, pid) if pid else None
    if record is None:
        return True
    handler = client.hook_handler
    try:
        base = int(handler.process.base_address)
    except Exception:
        return False
    current: dict[int, bytes] = {}
    for region in record.get("regions") or []:
        try:
            address = int(region["address"])
            original = bytes.fromhex(region["original"])
        except (KeyError, TypeError, ValueError):
            return False
        if not original:
            continue
        try:
            current[address] = bytes(await handler.read_bytes(address, len(original)))
        except Exception:
            return False
    return not leftovers_remain(record, base, current)


def _scan_module(handle, module_name: str, pattern: bytes) -> list[int]:
    """Scan one module. A stuck cursor or a huge page cannot spin forever.

    wizwalker's scanner reads every region and, when the next address does
    not advance, loops until the connect timeout. Two of those at once were
    the starts that never got past "activating hooks".
    """
    import pymem.memory
    import pymem.process
    import pymem.ressources.structure
    import regex

    module = pymem.process.module_from_name(handle, module_name)
    if module is None:
        raise ValueError(f"{module_name} module not found.")
    allowed = (
        pymem.ressources.structure.MEMORY_PROTECTION.PAGE_EXECUTE_READ,
        pymem.ressources.structure.MEMORY_PROTECTION.PAGE_EXECUTE_READWRITE,
        pymem.ressources.structure.MEMORY_PROTECTION.PAGE_READWRITE,
        pymem.ressources.structure.MEMORY_PROTECTION.PAGE_READONLY,
    )
    committed = pymem.ressources.structure.MEMORY_STATE.MEM_COMMIT
    page = int(module.lpBaseOfDll)
    limit = page + int(module.SizeOfImage)
    found: list[int] = []
    while page < limit:
        try:
            info = pymem.memory.virtual_query(handle, page)
        except Exception:
            break
        nxt = scan_advance(page, info.BaseAddress, info.RegionSize)
        if nxt is None:
            break
        size = int(info.RegionSize)
        if (
            info.state == committed
            and info.protect in allowed
            and page_is_scannable(size)
        ):
            try:
                data = pymem.memory.read_bytes(handle, page, size)
            except Exception:
                data = b""
            if data:
                for match in regex.finditer(pattern, data, regex.DOTALL):
                    found.append(page + match.span()[0])
        page = nxt
    return found


def install_scan_guard(handler) -> None:
    """Replace this handler's pattern scan with the bounded one."""
    if getattr(handler, "_scan_guard", False):
        return

    async def pattern_scan(pattern, *, module=None, return_multiple=False):
        if not module:
            from wizwalker.memory.memory_reader import MemoryReader

            return await MemoryReader.pattern_scan(
                handler, pattern, module=module, return_multiple=return_multiple
            )
        found = await handler.run_in_executor(_scan_module, handler.process.process_handle, module, pattern)
        from wizwalker import PatternFailed, PatternMultipleResults

        if not found:
            raise PatternFailed(pattern)
        if len(found) > 1 and not return_multiple:
            raise PatternMultipleResults(f"Got {len(found)} results for {pattern}")
        if return_multiple:
            return found
        return found[0]

    handler.pattern_scan = pattern_scan
    handler._scan_guard = True
