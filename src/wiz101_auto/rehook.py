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
import sys
from pathlib import Path

from loguru import logger

HOOKS_FILE = Path("state") / "hooks.json"
LOCK_FILE = Path("state") / "bot.lock"
REHOOK_SETTLE = 0.5
UNHOOK_TIMEOUT = 20.0
MAX_SCAN_PAGE = 8 * 1024 * 1024
MAX_SCAN_OVERLAP = 64 * 1024
RELATIVE_JUMP_MAX = (1 << 31) - 1
ALLOCATION_GRANULARITY = 64 * 1024
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
    allocation = getattr(handler, "_wine_autobot_allocation", None)
    allocation_size = getattr(handler, "_wine_autobot_allocation_size", None)
    autobot_address = getattr(handler, "_autobot_address", None)
    if not address_in_range(autobot_address, allocation, allocation_size):
        _add(found, autobot_address, getattr(handler, "_original_autobot_bytes", None))
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
        hook_autobot = getattr(hook, "_autobot_addr", None)
        if not address_in_range(hook_autobot, allocation, allocation_size):
            _add(found, hook_autobot, getattr(hook, "_autobot_original_bytes", None))
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
    """A live bot at that pid: a running python process.

    Never os.kill(pid, 0) here. Inside the bottle Python is a Windows
    program, and on Windows os.kill with any signal but Ctrl+C/Break calls
    TerminateProcess: that would kill whatever process holds the pid now.
    Wine reuses small pids, so a stale lock could name the game itself, and
    a pid that is not a python process is not a bot.
    """
    if not pid or int(pid) <= 0:
        return False
    if sys.platform == "win32":
        name = process_image(int(pid))
        return bool(name) and "python" in name.lower()
    try:
        os.kill(int(pid), 0)  # POSIX: signal 0 only checks
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_image(pid: int) -> str:
    """The exe path of a running Windows process, '' when it is gone (Windows only)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return ""
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return "?"
        return buf.value
    finally:
        k32.CloseHandle(handle)


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


def address_in_range(address: int | None, base: int | None, size: int | None) -> bool:
    if address is None or base is None or size is None:
        return False
    try:
        address, base, size = int(address), int(base), int(size)
    except (TypeError, ValueError):
        return False
    return base <= address < base + size


def near_module_bounds(module_base: int, module_size: int) -> tuple[int, int]:
    """Address bounds where rel32 jumps can reach every point in the module."""
    base, size = int(module_base), int(module_size)
    margin = ALLOCATION_GRANULARITY + 16
    reach = RELATIVE_JUMP_MAX - margin
    return max(0, base + size - reach), base + reach


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _align_down(value: int, alignment: int) -> int:
    return value // alignment * alignment


def _allocate_near_module(handler, size: int) -> int:
    """Reserve executable scratch space within rel32 reach of client code."""
    import ctypes

    import pymem.memory
    import pymem.process
    import pymem.ressources.kernel32
    import pymem.ressources.structure

    process_handle = handler.process.process_handle
    free_memory = pymem.memory.free_memory
    module = pymem.process.module_from_name(process_handle, "WizardGraphicalClient.exe")
    if module is None:
        raise OSError("could not find WizardGraphicalClient.exe to place hook memory")

    module_base = int(module.lpBaseOfDll)
    module_size = int(module.SizeOfImage)
    low, high = near_module_bounds(module_base, module_size)
    module_end = module_base + module_size
    state = pymem.ressources.structure.MEMORY_STATE
    protection = pymem.ressources.structure.MEMORY_PROTECTION
    allocation_type = state.MEM_COMMIT.value | state.MEM_RESERVE.value
    executable = protection.PAGE_EXECUTE_READWRITE.value
    kernel32 = pymem.ressources.kernel32
    virtual_alloc = kernel32.VirtualAllocEx
    last_error = 0

    def try_allocate(candidate: int) -> int:
        nonlocal last_error
        result = virtual_alloc(
            process_handle,
            ctypes.c_void_p(candidate),
            size,
            allocation_type,
            executable,
        )
        if result:
            address = int(result)
            if low <= address and address + size <= high:
                return address
            free_memory(process_handle, address)
        last_error = int(kernel32.GetLastError())
        return 0

    cursor = module_end
    while cursor < high:
        try:
            info = pymem.memory.virtual_query(process_handle, cursor)
        except Exception:
            break
        base = int(info.BaseAddress)
        end = base + int(info.RegionSize)
        if end <= cursor:
            break
        if info.state == state.MEM_FREE:
            candidate = _align_up(max(base, cursor, low), ALLOCATION_GRANULARITY)
            if candidate + size <= min(end, high):
                address = try_allocate(candidate)
                if address:
                    return address
        cursor = end

    cursor = module_base - 1
    while cursor >= low:
        try:
            info = pymem.memory.virtual_query(process_handle, cursor)
        except Exception:
            break
        base = int(info.BaseAddress)
        end = base + int(info.RegionSize)
        if end <= base:
            break
        if info.state == state.MEM_FREE:
            candidate = _align_down(min(cursor - size + 1, end - size), ALLOCATION_GRANULARITY)
            if candidate >= max(base, low):
                address = try_allocate(candidate)
                if address:
                    return address
        cursor = base - 1

    raise OSError(
        f"could not allocate {size} bytes within rel32 range of Wizard101 "
        f"module [{module_base:#x}, {module_end:#x}] "
        f"(Windows error {last_error})"
    )


def install_wine_autobot_buffer(handler) -> int:
    """Give WizWalker its scratch space in newly allocated writable game memory."""
    if not os.environ.get("WINEPREFIX") or getattr(handler, "_wine_autobot_allocation", None):
        return int(getattr(handler, "_wine_autobot_allocation", 0) or 0)

    import pymem.memory

    size = int(handler.AUTOBOT_SIZE)
    address = _allocate_near_module(handler, size)
    if not address:
        raise OSError("Wine returned a null address for WizWalker hook memory")
    address = int(address)

    handler._autobot_address = address
    handler._autobot_pos = 0
    handler._original_autobot_bytes = b"\x00" * len(handler.AUTOBOT_PATTERN)
    handler._wine_autobot_allocation = address
    handler._wine_autobot_allocation_size = size

    original_close = handler.close
    free_memory = pymem.memory.free_memory

    async def close():
        await original_close()
        allocation = getattr(handler, "_wine_autobot_allocation", None)
        if allocation:
            released = free_memory(handler.process.process_handle, allocation)
            if not released:
                raise OSError(f"Wine could not free WizWalker hook memory at {allocation:#x}")
            handler._wine_autobot_allocation = None
            logger.info("released WizWalker hook memory allocated in the game process")

    handler.close = close
    logger.info(f"Wine: allocated WizWalker hook scratch memory at {address:#x} ({size} bytes)")
    return address


def scan_ranges(base: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Split a memory region into bounded reads without leaving gaps."""
    try:
        start = int(base)
        end = start + int(size)
        overlap = max(int(overlap), 0)
    except (TypeError, ValueError):
        return []
    if end <= start:
        return []
    overlap = min(overlap, MAX_SCAN_OVERLAP, MAX_SCAN_PAGE - 1)
    step = MAX_SCAN_PAGE - overlap
    ranges = []
    while start < end:
        length = min(MAX_SCAN_PAGE, end - start)
        ranges.append((start, length))
        start += step
    return ranges


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
            and size > 0
        ):
            end = min(nxt, limit)
            for chunk_start, chunk_size in scan_ranges(page, end - page, len(pattern)):
                if not page_is_scannable(chunk_size):
                    break
                try:
                    data = pymem.memory.read_bytes(handle, chunk_start, chunk_size)
                except Exception:
                    data = b""
                if data:
                    found.extend(
                        chunk_start + match.span()[0]
                        for match in regex.finditer(pattern, data, regex.DOTALL)
                    )
        page = nxt
    return sorted(set(found))


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
