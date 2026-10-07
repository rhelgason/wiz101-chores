"""Leftover hooks are put back into the same game process."""

import asyncio

from wiz101_auto import rehook


class _Process:
    def __init__(self, base):
        self.base_address = base


class _Hook:
    def __init__(self):
        self.jump_address = 50
        self.jump_original_bytecode = b"\xF2\x0F\x10\x40\x58"
        self._old_jes_bytes = (b"\x74\x05",)
        self._collision_je_addrs = (80,)
        self._old_collision_jes_bytes = (b"\x74\x11",)
        self._autobot_addr = 90
        self._autobot_original_bytes = b"\x48\x89"
        self.set_cursor_pos = (100, b"\x48\x83\xEC\x28\x48\x8B")
        self.toggle_bool_addrs = (110,)


class _Handler:
    def __init__(self, base=0x1000, memory=None):
        self.process = _Process(base)
        self._autobot_address = 10
        self._original_autobot_bytes = b"\x48\x89\x5C"
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
        self._movement_update_original_bytes = b"\xFF\x50\x08"


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
            50: b"\xE9\x11\x22\x33\x44",
            10: b"\x00\x00\x00",
            70: b"\x90\x90",
        }
    )
    restored = asyncio.run(rehook.restore_leftover_hooks(hooked, path))
    assert restored == 3
    written = dict(hooked.hook_handler.writes)
    assert written[50] == b"\xF2\x0F\x10\x40\x58"
    assert written[10] == b"\x48\x89\x5C"
    assert written[70] == b"\x74\x05"
    assert 80 not in written

    fresh = _Client(pid=5, base=0x2000, memory={50: b"\xE9\x00\x00\x00\x00\x00"})
    assert asyncio.run(rehook.restore_leftover_hooks(fresh, path)) == 0
    assert rehook.game_record(rehook.load_hooks(path), 5) is None
    assert rehook.game_record(rehook.load_hooks(path), 9) is not None


def test_bytes_that_are_already_original_are_left_alone():
    record = rehook.snapshot_payload(5, 0x1000, [(50, b"\xF2\x0F")])
    assert rehook.regions_to_restore(record, 0x1000, {50: b"\xF2\x0F"}) == []
    assert rehook.regions_to_restore(record, 0x2000, {50: b"\xE9\x00"}) == []
    assert rehook.regions_to_restore(record, 0x1000, {}) == []
    assert rehook.regions_to_restore(record, 0x1000, {50: b"\xE9\x00"}) == [(50, b"\xF2\x0F")]


def test_forgetting_one_game_keeps_the_other(tmp_path):
    path = tmp_path / "hooks.json"
    rehook.remember_installed_hooks(_Client(pid=5), path)
    rehook.remember_installed_hooks(_Client(pid=9), path)
    rehook.forget_hooks(path, pid=5)
    assert rehook.game_record(rehook.load_hooks(path), 5) is None
    assert rehook.game_record(rehook.load_hooks(path), 9) is not None
    rehook.forget_hooks(path, pid=9)
    assert rehook.load_hooks(path) is None
