"""The flight recorder: one folder per run, one JSON line per risky action.

`logs/runs/<stamp>-<mode>/` holds bot.log (everything at DEBUG), events.jsonl
(hooks, code writes, walks, clicks, fights, a heartbeat), hook_session.json
(where the hooks are, for reading a crash dump), and, after a crash,
CRASH.md written by mac/crashwatch.py. Each event line is flushed when it is
written, so the last lines before a game crash are on disk.

The write guard is the rule that came out of reading the crashes: game code
is written while the hooks go in, and not again until the bot stops. Every
WizWalker memory write passes through `MemoryReader.write_bytes`. The guard
asks Windows whether the target page is executable. A code write after
`lock()` raises CodeWriteBlocked instead of reaching the game. Teleports
were exactly that kind of write (WizWalker patches two jumps in the game's
movement code on every teleport), and the Mac client died on them.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

RUNS = Path("logs") / "runs"
EXECUTE_FLAGS = 0x10 | 0x20 | 0x40 | 0x80  # PAGE_EXECUTE, _READ, _READWRITE, _WRITECOPY
MEM_IMAGE = 0x1000000  # a page of a loaded exe or dll, not memory the bot allocated
KEEP = 400


def run_folder(mode: str, given: str | None = None, now: float | None = None) -> Path:
    """The folder for this run: the one mac/run.sh made, else a new one."""
    if given:
        return Path(given)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return RUNS / f"{stamp}-{mode}"


def is_executable(protect: int) -> bool:
    return bool(protect & EXECUTE_FLAGS)


def is_game_code(protect: int | None, kind: int | None) -> bool:
    """An executable page of a loaded module. pymem allocates the bot's own
    buffers (hook outputs, the cursor position) as executable private memory;
    writing those is data and is always allowed."""
    return protect is not None and kind == MEM_IMAGE and is_executable(protect)


class CodeWriteBlocked(RuntimeError):
    """A write to game code after the hooks were in. It did not reach the game."""


class BlackBox:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._events = (folder / "events.jsonl").open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self.recent: deque[dict] = deque(maxlen=KEEP)
        self.code_writes: list[list] = []
        self.last_code_write: dict | None = None

    def event(self, kind: str, /, **fields) -> dict:
        if "kind" in fields:
            fields["kind_"] = fields.pop("kind")
        now = time.time()
        row = {
            "t": time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}",
            "ts": round(now, 3),
            "kind": kind,
            **fields,
        }
        line = json.dumps(row, default=str)
        with self._lock:
            self._events.write(line + "\n")
            self._events.flush()
            self.recent.append(row)
        return row

    def note_code_write(self, label: str, address: int, size: int, protect: int) -> None:
        self.code_writes.append([label, address, size])
        self.last_code_write = self.event(
            "code_write", label=label, address=hex(address), size=size, protect=hex(protect)
        )
        self.write_json("code_writes.json", self.code_writes)

    def write_json(self, name: str, data) -> None:
        path = self.folder / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, path)

    def close(self) -> None:
        with self._lock, contextlib.suppress(Exception):
            self._events.close()


_box: BlackBox | None = None


def start(folder: Path) -> BlackBox:
    global _box
    _box = BlackBox(folder)
    return _box


def box() -> BlackBox | None:
    return _box


def event(kind: str, /, **fields) -> None:
    """Record one event. Without a run folder (tests, one-shot commands) this does nothing."""
    if _box is not None:
        try:
            _box.event(kind, **fields)
        except Exception:
            pass


def folder() -> Path | None:
    return _box.folder if _box is not None else None


class Guard:
    """Which code writes are allowed right now, and what they are for."""

    def __init__(self):
        self.locked = False
        self.label = "unlabeled"
        self.blocked = 0

    def lock(self) -> None:
        self.locked = True
        event("code_writes_locked")

    @contextlib.contextmanager
    def allow(self, label: str):
        """Code writes inside this block are expected (hook install, mouse hook, unhook)."""
        was, old = self.locked, self.label
        self.locked, self.label = False, label
        try:
            yield
        finally:
            self.locked, self.label = was, old


guard = Guard()


def page_protection(process_handle: int, address: int) -> tuple[int | None, int | None]:
    """VirtualQueryEx's (Protect, Type) for that address; Nones when it cannot be read."""
    if sys.platform != "win32":
        return None, None
    import ctypes
    from ctypes import wintypes

    class MBI(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wintypes.DWORD),
            ("PartitionId", wintypes.WORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD),
            ("Type", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.VirtualQueryEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
    query.restype = ctypes.c_size_t
    info = MBI()
    if not query(process_handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)):
        return None, None
    return int(info.Protect), int(info.Type)


def install_write_guard() -> None:
    """Route every WizWalker memory write through the guard. Idempotent."""
    from wizwalker.memory.memory_reader import MemoryReader

    if getattr(MemoryReader, "_guarded", False):
        return
    original = MemoryReader.write_bytes

    async def write_bytes(self, address: int, value: bytes):
        try:
            protect, kind = page_protection(self.process.process_handle, address)
        except Exception:
            protect, kind = None, None
        if is_game_code(protect, kind):
            if guard.locked:
                guard.blocked += 1
                event("code_write_blocked", address=hex(address), size=len(value), protect=hex(protect))
                raise CodeWriteBlocked(
                    f"blocked a {len(value)}-byte write to game code at {address:#x}; "
                    "code is only written while hooks go in or come out"
                )
            if _box is not None:
                _box.note_code_write(guard.label, address, len(value), protect)
        return await original(self, address, value)

    MemoryReader.write_bytes = write_bytes
    MemoryReader._guarded = True
