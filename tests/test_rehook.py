"""Leftover hooks are put back into the same game process."""

import asyncio
import sys
import types

from wiz101_auto import rehook


class _Process:
    def __init__(self, base):
        self.base_address = base


class _Hook:
    def __init__(self):
        self.jump_address = 50
        self.jump_original_bytecode = b"\xf2\x0f\x10\x40\x58"
        self._old_jes_bytes = (b"\x74\x05",)
        self._collision_je_addrs = (80,)
        self._old_collision_jes_bytes = (b"\x74\x11",)
        self._autobot_addr = 90
        self._autobot_original_bytes = b"\x48\x89"
        self.set_cursor_pos = (100, b"\x48\x83\xec\x28\x48\x8b")
        self.toggle_bool_addrs = (110,)


class _Handler:
    def __init__(self, base=0x1000, memory=None):
        self.process = _Process(base)
        self._autobot_address = 10
        self._original_autobot_bytes = b"\x48\x89\x5c"
        self._active_hooks = {_Hook: _Hook()}
        self.client = None
        self.memory = dict(memory or {})
        self.writes = []

    async def read_bytes(self, address, size):
        data = self.memory.get(address)
        if data is None or len(data) < size:
            raise OSError("unreadable")
        return data[:size]

    async def write_bytes(self, address, data):
        self.writes.append((address, data))
        self.memory[address] = data


class _Client:
    def __init__(self, pid=5, base=0x1000, memory=None):
        self.process_id = pid
        self._je_instruction_forward_backwards = (70,)
        self.hook_handler = _Handler(base, memory)
        self.hook_handler.client = self
        self._movement_update_patched = True
        self._movement_update_address = 120
        self._movement_update_original_bytes = b"\xff\x50\x08"


def test_a_killed_bot_is_restored_into_the_same_process(tmp_path, monkeypatch):
    path = tmp_path / "hooks.json"
    monkeypatch.setattr(rehook, "REHOOK_SETTLE", 0)
    client = _Client()
    assert rehook.remember_installed_hooks(client, path) >= 5
    other = _Client(pid=9)
    assert rehook.remember_installed_hooks(other, path) >= 1
    assert rehook.game_record(rehook.load_hooks(path), 5)["module_base"] == 0x1000

    hooked = _Client(
        memory={
            50: b"\xe9\x11\x22\x33\x44",
            10: b"\x00\x00\x00",
            70: b"\x90\x90",
        }
    )
    restored = asyncio.run(rehook.restore_leftover_hooks(hooked, path))
    assert restored == 3
    written = dict(hooked.hook_handler.writes)
    assert written[50] == b"\xf2\x0f\x10\x40\x58"
    assert written[10] == b"\x48\x89\x5c"
    assert written[70] == b"\x74\x05"
    assert 80 not in written

    fresh = _Client(pid=5, base=0x2000, memory={50: b"\xe9\x00\x00\x00\x00\x00"})
    assert asyncio.run(rehook.restore_leftover_hooks(fresh, path)) == 0
    assert rehook.game_record(rehook.load_hooks(path), 5) is None
    assert rehook.game_record(rehook.load_hooks(path), 9) is not None


def test_bytes_that_are_already_original_are_left_alone():
    record = rehook.snapshot_payload(5, 0x1000, [(50, b"\xf2\x0f")])
    assert rehook.regions_to_restore(record, 0x1000, {50: b"\xf2\x0f"}) == []
    assert rehook.regions_to_restore(record, 0x2000, {50: b"\xe9\x00"}) == []
    assert rehook.regions_to_restore(record, 0x1000, {}) == []
    assert rehook.regions_to_restore(record, 0x1000, {50: b"\xe9\x00"}) == [(50, b"\xf2\x0f")]


def test_a_live_game_keeps_its_originals_and_a_closed_one_does_not():
    record = rehook.snapshot_payload(5, 0x1000, [(50, b"\xf2\x0f")])
    assert rehook.leftovers_remain(record, 0x1000, {50: b"\xe9\x00"}) is True
    assert rehook.leftovers_remain(record, 0x1000, {50: b"\xf2\x0f"}) is False
    assert rehook.leftovers_remain(record, 0x1000, {}) is True
    assert rehook.leftovers_remain(record, 0x2000, {50: b"\xe9\x00"}) is False
    assert rehook.keep_hook_record(running=True) is True
    assert rehook.keep_hook_record(running=False) is False


def test_a_second_bot_is_refused_while_the_first_is_alive(tmp_path):
    path = tmp_path / "bot.lock"
    assert rehook.claim_bot_lock(path, pid=4, alive=lambda pid: pid == 4) is None
    busy = rehook.claim_bot_lock(path, pid=8, alive=lambda pid: pid == 4)
    assert busy and "already running" in busy and "leave Wizard101 open" in busy
    assert rehook.claim_bot_lock(path, pid=8, alive=lambda pid: False) is None
    rehook.release_bot_lock(path, pid=8)
    assert not path.exists()


def test_a_stuck_scan_cursor_stops():
    assert rehook.scan_advance(100, 100, 0) is None
    assert rehook.scan_advance(100, 80, 10) is None
    assert rehook.scan_advance(100, 100, 64) == 164
    assert rehook.page_is_scannable(0) is False
    assert rehook.page_is_scannable(rehook.MAX_SCAN_PAGE + 1) is False
    assert rehook.page_is_scannable(4096) is True


def test_wine_autobot_scratch_buffer_is_allocated_and_released(monkeypatch):
    allocations = []
    frees = []
    base = 0x140000000
    size = 4100

    memory = types.ModuleType("pymem.memory")
    structure = types.ModuleType("pymem.ressources.structure")
    structure.MEMORY_STATE = types.SimpleNamespace(
        MEM_COMMIT=types.SimpleNamespace(value=0x1000),
        MEM_RESERVE=types.SimpleNamespace(value=0x2000),
        MEM_FREE=types.SimpleNamespace(value=0x10000),
    )
    structure.MEMORY_PROTECTION = types.SimpleNamespace(
        PAGE_EXECUTE_READWRITE=types.SimpleNamespace(value=0x40)
    )

    def allocate(handle, address, allocation_size, allocation_type, protection_type):
        requested = int(address.value)
        allocations.append((handle, requested, allocation_size, allocation_type, protection_type))
        return requested

    def free(handle, address):
        frees.append((handle, address))
        return True

    memory.free_memory = free
    memory.virtual_query = lambda _handle, _address: types.SimpleNamespace(
        BaseAddress=base + 0x100000,
        RegionSize=0x100000,
        state=structure.MEMORY_STATE.MEM_FREE,
    )
    process = types.ModuleType("pymem.process")
    process.module_from_name = lambda _handle, _name: types.SimpleNamespace(
        lpBaseOfDll=base,
        SizeOfImage=0x100000,
    )
    kernel32 = types.ModuleType("pymem.ressources.kernel32")
    kernel32.VirtualAllocEx = allocate
    kernel32.GetLastError = lambda: 0
    pymem = types.ModuleType("pymem")
    pymem.__path__ = []
    pymem.process = process
    ressources = types.ModuleType("pymem.ressources")
    ressources.__path__ = []
    pymem.memory = memory
    pymem.ressources = ressources
    ressources.kernel32 = kernel32
    ressources.structure = structure
    monkeypatch.setitem(sys.modules, "pymem", pymem)
    monkeypatch.setitem(sys.modules, "pymem.memory", memory)
    monkeypatch.setitem(sys.modules, "pymem.process", process)
    monkeypatch.setitem(sys.modules, "pymem.ressources", ressources)
    monkeypatch.setitem(sys.modules, "pymem.ressources.kernel32", kernel32)
    monkeypatch.setitem(sys.modules, "pymem.ressources.structure", structure)
    monkeypatch.setenv("WINEPREFIX", "/wine/test")

    class Process:
        process_handle = 123

    class Handler:
        AUTOBOT_SIZE = 4100
        AUTOBOT_PATTERN = b"pattern"
        process = Process()

        def __init__(self):
            self._autobot_address = None
            self._autobot_pos = 0
            self._original_autobot_bytes = b""
            self._active_hooks = {}

        async def close(self):
            self._autobot_address = None

    handler = Handler()
    allocation = rehook.install_wine_autobot_buffer(handler)
    assert allocation == base + 0x100000
    low, high = rehook.near_module_bounds(base, 0x100000)
    assert low <= allocation and allocation + size <= high
    assert handler._original_autobot_bytes == b"\0" * len(handler.AUTOBOT_PATTERN)
    assert allocations == [(123, base + 0x100000, size, 0x3000, 0x40)]
    assert rehook.capture_regions(handler) == []

    async def finish_close():
        await handler.close()

    asyncio.run(finish_close())
    assert frees == [(123, allocation)]
    assert handler._wine_autobot_allocation is None


def test_wine_autobot_range_check():
    assert rehook.address_in_range(0x9000, 0x9000, 4100)
    assert rehook.address_in_range(0x9000 + 4099, 0x9000, 4100)
    assert not rehook.address_in_range(0x9000 + 4100, 0x9000, 4100)
    low, high = rehook.near_module_bounds(0x140000000, 0x200000)
    assert high < 0x140000000 + 0x80000000
    assert low > 0x140000000 + 0x200000 - 0x80000000


def test_large_memory_regions_are_scanned_in_bounded_overlapping_chunks():
    total = rehook.MAX_SCAN_PAGE * 2 + 100
    chunks = rehook.scan_ranges(100, total, overlap=32)
    assert chunks[0] == (100, rehook.MAX_SCAN_PAGE)
    assert all(0 < length <= rehook.MAX_SCAN_PAGE for _, length in chunks)
    assert all(
        next_start <= start + length
        for (start, length), (next_start, _) in zip(chunks, chunks[1:], strict=False)
    )
    assert chunks[-1][0] + chunks[-1][1] == 100 + total


def test_a_slow_rehook_does_not_ask_the_game_to_quit(tmp_path, monkeypatch):
    from wiz101_auto import gamerestart

    monkeypatch.setattr(gamerestart, "REQUEST", tmp_path / "req")
    monkeypatch.setattr(gamerestart, "game_windows", lambda: [123])
    assert "Could not hook" not in rehook.SCAN_STALL
    assert "Hooks did not activate" not in rehook.SCAN_STALL
    assert gamerestart.needs_restart(rehook.SCAN_STALL) == ""


def test_forgetting_one_game_keeps_the_other(tmp_path):
    path = tmp_path / "hooks.json"
    rehook.remember_installed_hooks(_Client(pid=5), path)
    rehook.remember_installed_hooks(_Client(pid=9), path)
    rehook.forget_hooks(path, pid=5)
    assert rehook.game_record(rehook.load_hooks(path), 5) is None
    assert rehook.game_record(rehook.load_hooks(path), 9) is not None
    rehook.forget_hooks(path, pid=9)
    assert rehook.load_hooks(path) is None


def test_a_stale_lock_on_a_pid_that_is_not_python_is_taken(tmp_path, monkeypatch):
    """Inside Wine the pid may now be the game. Never os.kill it, never mistake it for a bot."""
    import os

    monkeypatch.setattr(rehook.sys, "platform", "win32")
    monkeypatch.setattr(rehook, "process_image", lambda pid: "C:\\Wizard101\\Bin\\WizardGraphicalClient.exe")
    monkeypatch.setattr(
        os, "kill", lambda *a: (_ for _ in ()).throw(AssertionError("os.kill on Windows kills"))
    )
    lock = tmp_path / "bot.lock"
    lock.write_text("444")
    assert rehook.pid_alive(444) is False
    assert rehook.claim_bot_lock(lock, pid=99) is None
    monkeypatch.setattr(rehook, "process_image", lambda pid: "Y:\\python\\python.exe")
    assert "already running" in rehook.claim_bot_lock(lock, pid=100)
