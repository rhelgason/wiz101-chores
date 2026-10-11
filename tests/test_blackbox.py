"""The flight recorder and the rule that game code is written only while hooks go in."""

import asyncio
import json
import sys
import types

import pytest

from wiz101_auto import blackbox


def test_run_folder_is_given_or_stamped():
    assert str(blackbox.run_folder("seeds", "logs/runs/x")) == "logs/runs/x"
    made = blackbox.run_folder("pet", None, now=0)
    assert made.parent == blackbox.RUNS
    assert made.name.endswith("-pet")


def test_only_executable_image_pages_are_game_code():
    assert blackbox.is_game_code(0x20, blackbox.MEM_IMAGE)  # PAGE_EXECUTE_READ, the exe
    assert blackbox.is_game_code(0x80, blackbox.MEM_IMAGE)  # copy-on-write code
    assert not blackbox.is_game_code(0x40, 0x20000)  # RWX the bot allocated: hook outputs, the cursor
    assert not blackbox.is_game_code(0x04, blackbox.MEM_IMAGE)  # the exe's data
    assert not blackbox.is_game_code(None, None)


def test_events_are_json_lines_on_disk_at_once(tmp_path):
    box = blackbox.BlackBox(tmp_path)
    box.event("walk_start", why="test", dist=12)
    box.note_code_write("player hook", 0x140001000, 5, 0x20)
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["kind"] == "walk_start"
    assert json.loads(lines[1])["label"] == "player hook"
    assert json.loads((tmp_path / "code_writes.json").read_text()) == [["player hook", 0x140001000, 5]]
    assert box.last_code_write["address"] == "0x140001000"
    box.close()


def test_allow_unlocks_only_inside_the_block():
    guard = blackbox.Guard()
    guard.lock()
    with guard.allow("mouse hook"):
        assert not guard.locked
        assert guard.label == "mouse hook"
    assert guard.locked
    assert guard.label == "unlabeled"


@pytest.fixture
def fake_reader(monkeypatch):
    """A stand-in for wizwalker.memory.memory_reader.MemoryReader."""
    written = []

    class MemoryReader:
        def __init__(self):
            self.process = types.SimpleNamespace(process_handle=1)

        async def write_bytes(self, address, value):
            written.append((address, value))

    module = types.ModuleType("wizwalker.memory.memory_reader")
    module.MemoryReader = MemoryReader
    monkeypatch.setitem(sys.modules, "wizwalker", types.ModuleType("wizwalker"))
    monkeypatch.setitem(sys.modules, "wizwalker.memory", types.ModuleType("wizwalker.memory"))
    monkeypatch.setitem(sys.modules, "wizwalker.memory.memory_reader", module)
    guard = blackbox.Guard()
    monkeypatch.setattr(blackbox, "guard", guard)
    blackbox.install_write_guard()
    return MemoryReader, written, guard


def test_a_code_write_after_lock_never_reaches_the_game(fake_reader, monkeypatch, tmp_path):
    MemoryReader, written, guard = fake_reader
    monkeypatch.setattr(blackbox, "_box", blackbox.BlackBox(tmp_path))
    monkeypatch.setattr(blackbox, "page_protection", lambda handle, address: (0x20, blackbox.MEM_IMAGE))
    reader = MemoryReader()
    with guard.allow("player hook"):
        asyncio.run(reader.write_bytes(0x1000, b"\xe9\0\0\0\0"))
    guard.lock()
    with pytest.raises(blackbox.CodeWriteBlocked):
        asyncio.run(reader.write_bytes(0x2000, b"\x90" * 6))  # what a teleport writes
    assert written == [(0x1000, b"\xe9\0\0\0\0")]
    assert guard.blocked == 1
    kinds = [json.loads(line)["kind"] for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert kinds == ["code_write", "code_writes_locked", "code_write_blocked"]


def test_data_writes_pass_while_locked(fake_reader, monkeypatch):
    MemoryReader, written, guard = fake_reader
    monkeypatch.setattr(blackbox, "page_protection", lambda handle, address: (0x40, 0x20000))
    guard.lock()
    asyncio.run(MemoryReader().write_bytes(0x3000, b"\x01\x02"))  # the cursor position
    assert written == [(0x3000, b"\x01\x02")]


def test_a_field_named_kind_does_not_break_an_event(tmp_path):
    box = blackbox.BlackBox(tmp_path)
    row = box.event("travel_gate", kind="standard")
    assert row["kind"] == "travel_gate" and row["kind_"] == "standard"
