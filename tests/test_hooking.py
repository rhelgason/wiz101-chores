"""Hooks go into a game once; later runs reuse them without writing code."""

import asyncio
import sys
import types

import pytest

from wiz101_auto import hooking

CAVE = 0x14179F150
ORIGINAL_CAVE = b"\x48\x89\x5c\x24\x10"


def record(name, jump, hook, base_key, export):
    return {
        "class": f"{name.title()}Hook",
        "jump": jump,
        "jump_bytes": "e9" + "11223344",
        "original": "f20f104058",
        "hook": hook,
        "hook_bytes": "518b8874040000",
        "base_key": base_key,
        "export": export,
    }


class Memory:
    def __init__(self, cells):
        self.cells = dict(cells)
        self.writes = []

    async def read_bytes(self, address, size):
        for start, data in self.cells.items():
            if start <= address and address + size <= start + len(data):
                return data[address - start : address - start + size]
        raise OSError("unreadable")


def test_verdict():
    assert hooking.verdict([]) == "fresh"
    assert hooking.verdict(["ours", "ours"]) == "adopt"
    assert hooking.verdict(["original", "original"]) == "fresh"
    assert hooking.verdict(["ours", "original"]) == "adopt"  # one was recorded, then the run died
    assert hooking.verdict(["ours", "other"]) == "dirty"
    assert hooking.verdict(["other"]) == "dirty"


def test_same_game_needs_pid_and_module_base():
    session = {"pid": 444, "module_base": 0x140000000}
    assert hooking.same_game(session, 444, 0x140000000)
    assert not hooking.same_game(session, 445, 0x140000000)
    assert not hooking.same_game(session, 444, 0x150000000)
    assert not hooking.same_game(None, 444, 0x140000000)


def test_hook_state_reads_the_jump_and_the_body():
    rec = record("player", 0x1000, 0x2000, "player_struct", 0x9000)
    ours = Memory({0x1000: bytes.fromhex(rec["jump_bytes"]), 0x2000: bytes.fromhex(rec["hook_bytes"])})
    assert asyncio.run(hooking.hook_state(ours, rec)) == "ours"
    fresh = Memory({0x1000: bytes.fromhex(rec["original"]), 0x2000: b"\0" * 7})
    assert asyncio.run(hooking.hook_state(fresh, rec)) == "original"
    half = Memory({0x1000: bytes.fromhex(rec["jump_bytes"]), 0x2000: b"\0" * 7})
    assert asyncio.run(hooking.hook_state(half, rec)) == "other"


def test_session_round_trip(tmp_path):
    path = tmp_path / "hook_session.json"
    hooking.save_session({"pid": 1, "hooks": {}}, path)
    loaded = hooking.load_session(path)
    assert loaded["pid"] == 1 and "updated" in loaded
    assert hooking.load_session(tmp_path / "missing.json") is None


@pytest.fixture
def fake_wizwalker(monkeypatch):
    hooks_mod = types.ModuleType("wizwalker.memory.hooks")
    for name in (
        "PlayerHook",
        "PlayerStatHook",
        "ClientHook",
        "RootWindowHook",
        "RenderContextHook",
        "MouselessCursorMoveHook",
    ):
        exports = [("x", 8)]
        hooks_mod.__dict__[name] = type(
            name, (), {"exports": exports, "__init__": lambda self, *a, **k: None}
        )
    memory_mod = types.ModuleType("wizwalker.memory")
    memory_mod.hooks = hooks_mod
    monkeypatch.setitem(sys.modules, "wizwalker", types.ModuleType("wizwalker"))
    monkeypatch.setitem(sys.modules, "wizwalker.memory", memory_mod)
    monkeypatch.setitem(sys.modules, "wizwalker.memory.hooks", hooks_mod)
    return hooks_mod


class Handler(Memory):
    def __init__(self, cells):
        super().__init__(cells)
        self.process = types.SimpleNamespace(base_address=0x140000000, process_handle=1)
        self._autobot_address = None
        self._original_autobot_bytes = b""
        self._autobot_pos = 0
        self._active_hooks = {}
        self._base_addrs = {}
        self._hook_cache = {}

    async def write_bytes(self, address, value):
        self.writes.append((address, value))

    def _check_if_hook_active(self, cls):
        return cls in self._active_hooks


def test_a_recorded_game_is_adopted_without_writing(tmp_path, monkeypatch, fake_wizwalker):
    monkeypatch.setattr(hooking, "SESSION", tmp_path / "hook_session.json")
    names = [(n, b, a) for n, _c, b, a in hooking.DATA_HOOKS]
    records = {}
    cells = {CAVE: b"\0" * 400}
    for i, (name, base_key, _attr) in enumerate(names):
        rec = record(name, 0x141000000 + 0x100 * i, CAVE + 50 * i, base_key, 0x9000 + 8 * i)
        rec["class"] = dict((n, c) for n, c, _b, _a in hooking.DATA_HOOKS)[name]
        records[name] = rec
        cells[rec["jump"]] = bytes.fromhex(rec["jump_bytes"])
        body = bytes.fromhex(rec["hook_bytes"])
        cave = bytearray(cells[CAVE])
        cave[50 * i : 50 * i + len(body)] = body
        cells[CAVE] = bytes(cave)
    hooking.save_session(
        {
            "pid": 444,
            "module_base": 0x140000000,
            "state": "parked",
            "hooks": records,
            "cave": {"address": CAVE, "original": ORIGINAL_CAVE.hex(), "used": 250, "size": 4100},
        },
        hooking.SESSION,
    )
    handler = Handler(cells)
    client = types.SimpleNamespace(hook_handler=handler, process_id=444)
    hooks = hooking.Hooks(client)
    asyncio.run(hooks.attach())
    assert handler.writes == []
    assert hooks.adopted == [n for n, _b, _a in names]
    assert hooks.installed == []
    assert handler._autobot_address == CAVE and handler._autobot_pos == 250
    assert handler._base_addrs["player_struct"] == 0x9000
    assert hooking.load_session(hooking.SESSION)["state"] == "live"


def test_hook_bytes_that_do_not_match_the_record_refuse(tmp_path, monkeypatch, fake_wizwalker):
    monkeypatch.setattr(hooking, "SESSION", tmp_path / "hook_session.json")
    rec = record("player", 0x141000000, CAVE, "player_struct", 0x9000)
    rec["class"] = "PlayerHook"
    other = dict(rec, jump=0x141000100, hook=CAVE + 50)
    hooking.save_session(
        {
            "pid": 444,
            "module_base": 0x140000000,
            "hooks": {"player": rec, "client": other},
            "cave": {"address": CAVE, "original": ORIGINAL_CAVE.hex(), "used": 100},
        },
        hooking.SESSION,
    )
    cells = {
        0x141000000: bytes.fromhex(rec["jump_bytes"]),
        0x141000100: b"\xcc" * 5,  # neither ours nor the game's: someone else's patch
        CAVE: bytes.fromhex(rec["hook_bytes"]) + b"\0" * 200,
    }
    handler = Handler(cells)
    with pytest.raises(hooking.HookStateError):
        asyncio.run(hooking.Hooks(types.SimpleNamespace(hook_handler=handler, process_id=444)).attach())
    assert handler.writes == []


def test_a_hook_recorded_but_never_written_is_installed_again(tmp_path, monkeypatch, fake_wizwalker):
    """The run died after recording the client hook and before writing it."""
    monkeypatch.setattr(hooking, "SESSION", tmp_path / "hook_session.json")
    handler = None
    seen_on_disk = []

    class FakeHook:
        exports = [("x", 8)]

        def __init__(self, h, *a, **k):
            self.h = h

        async def get_pattern(self):
            return b"p", "WizardGraphicalClient.exe"

        async def get_jump_address(self, pattern, module=None):
            return 0x141000100

        async def get_hook_address(self, size):
            address = self.h._autobot_address + self.h._autobot_pos
            self.h._autobot_pos += size
            return address

        async def get_hook_bytecode(self):
            self.current_client_addr = 0x9100  # what SimpleHook sets from `exports`
            return b"\x50\x58"

        async def get_jump_bytecode(self):
            return b"\xe9\x01\x02\x03\x04"

        async def read_bytes(self, address, size):
            return await self.h.read_bytes(address, size)

        async def write_bytes(self, address, value):
            seen_on_disk.append("client" in hooking.load_session(hooking.SESSION)["hooks"])
            await self.h.write_bytes(address, value)

        async def prehook(self):
            pass

        async def posthook(self):
            pass

    for name in ("PlayerHook", "PlayerStatHook", "ClientHook", "RootWindowHook", "RenderContextHook"):
        setattr(fake_wizwalker, name, type(name, (FakeHook,), {}))
    player = record("player", 0x141000000, CAVE, "player_struct", 0x9000)
    player["class"] = "PlayerHook"
    client = dict(player, jump=0x141000100, hook=CAVE + 50, base_key="current_client", export=0x9008)
    client["class"] = "ClientHook"
    hooking.save_session(
        {
            "pid": 444,
            "module_base": 0x140000000,
            "hooks": {"player": player, "client": client},
            "cave": {"address": CAVE, "original": ORIGINAL_CAVE.hex(), "used": 100},
        },
        hooking.SESSION,
    )
    cells = {
        0x141000000: bytes.fromhex(player["jump_bytes"]),
        0x141000100: bytes.fromhex(client["original"]),
        CAVE: bytes.fromhex(player["hook_bytes"]) + b"\0" * 400,
    }
    handler = Handler(cells)
    hooks = hooking.Hooks(types.SimpleNamespace(hook_handler=handler, process_id=444))
    # Only the client hook is wanted again; the other data hooks pretend to be adopted.
    monkeypatch.setattr(hooking, "DATA_HOOKS", hooking.DATA_HOOKS[:1] + (hooking.DATA_HOOKS[2],))
    asyncio.run(hooks.attach())
    assert hooks.adopted == ["player"]
    assert hooks.installed == ["client"]
    assert [a for a, _v in handler.writes] == [CAVE + 100, 0x141000100]  # body after the old ones, then jump
    assert seen_on_disk == [True, True]  # recorded before each write
    assert handler._base_addrs["current_client"] == 0x9100


def test_a_half_removed_mouse_hook_is_put_back_jump_first(tmp_path, monkeypatch):
    monkeypatch.setattr(hooking, "SESSION", tmp_path / "hook_session.json")
    regions = [
        ["GetClassInfoExA", 0x7B001000, "4889"],
        ["GetCursorPos", 0x7B000000, "4883ec28"],
        ["mouse bool one", 0x141500000, "00"],
    ]
    handler = Handler({0x7B001000: b"\x48\x89", 0x7B000000: b"\xe9\x00\x10\x00", 0x141500000: b"\x01"})
    hooks = hooking.Hooks(types.SimpleNamespace(hook_handler=handler, process_id=444))
    hooks.session = {"mouse_originals": {"regions": regions}}
    asyncio.run(hooks._repair_mouse())
    assert handler.writes == [(0x7B000000, bytes.fromhex("4883ec28")), (0x141500000, b"\x00")]
