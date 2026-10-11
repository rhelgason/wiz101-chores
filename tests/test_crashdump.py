"""Reading a Wizard101 minidump, and the crash report the Mac watcher writes."""

import importlib.util
import json
import struct
import time
from pathlib import Path

from wiz101_auto import crashdump

EXE = 0x140000000
D3D = 0x367600000
CAVE = 0x14179F150


def minidump(address: int, access: int = 8, rip: int | None = None, code_at_rip: bytes = b"") -> bytes:
    """A small but well-formed minidump: two modules, one exception, a context, one memory range."""
    rip = address if rip is None else rip
    names = [
        "C:\\ProgramData\\KingsIsle Entertainment\\Wizard101\\Bin\\WizardGraphicalClient.exe",
        "C:\\windows\\system32\\wined3d.dll",
    ]
    body = bytearray(b"\0" * 32)  # header, filled in last
    streams = []

    def put(data: bytes) -> int:
        at = len(body)
        body.extend(data)
        return at

    name_rvas = [put(struct.pack("<I", len(n) * 2) + n.encode("utf-16le")) for n in names]
    modules = struct.pack("<I", 2)
    for base, size, rva in ((EXE, 0x370E000, name_rvas[0]), (D3D, 0x1A1000, name_rvas[1])):
        modules += struct.pack("<QIIII", base, size, 0, 0, rva) + b"\0" * (108 - 24)
    streams.append((4, len(modules), put(modules)))
    context = bytearray(0x4D0)
    regs = dict.fromkeys(crashdump.REGISTERS, 0)
    regs["rip"], regs["rsp"] = rip, 0x11EAD0
    struct.pack_into(f"<{len(regs)}Q", context, crashdump.CONTEXT_REGS, *regs.values())
    ctx_rva = put(bytes(context))
    code = (b"\xcc" * 16 + code_at_rip).ljust(48, b"\x90")
    stack = struct.pack("<4Q", 0, EXE + 0x1C7B63A, 5, EXE + 0x1C7B40B)
    mem_rva = put(code + stack)
    memory = (
        struct.pack("<I", 2)
        + struct.pack("<QII", rip - 16, 48, mem_rva)
        + struct.pack("<QII", 0x11EAD0, len(stack), mem_rva + 48)
    )
    streams.append((5, len(memory), put(memory)))
    exc = struct.pack("<II", 448, 0) + struct.pack("<IIQQI", 0xC0000005, 0, 0, address, 2) + b"\0" * 4
    exc += struct.pack("<15Q", access, address, *([0] * 13)) + struct.pack("<II", len(context), ctx_rva)
    streams.append((6, len(exc), put(exc)))
    directory = b"".join(struct.pack("<III", *s) for s in streams)
    dir_rva = put(directory)
    struct.pack_into(
        "<IIIIIIQ", body, 0, crashdump.SIGNATURE, 0xA793, len(streams), dir_rva, 0, int(time.time()), 0
    )
    return bytes(body)


def test_an_execute_fault_in_the_hook_cave_is_the_bot():
    dump = crashdump.parse(minidump(CAVE))
    assert dump.code == 0xC0000005 and dump.params[:2] == [8, CAVE]
    assert dump.where(CAVE) == "WizardGraphicalClient.exe+0x179f150"
    assert "execute" in crashdump.describe_exception(dump)
    verdict = crashdump.classify(dump, {"cave": {"address": CAVE, "size": 4100}})
    assert verdict.startswith("BOT") and "hook cave" in verdict
    assert [crashdump.parse(minidump(CAVE)).where(v) for v in dump.stack_returns][:1] == [
        "WizardGraphicalClient.exe+0x1c7b63a"
    ]


def test_wizwalker_shellcode_at_rip_is_recognised_without_a_hook_map():
    data = minidump(CAVE, code_at_rip=bytes.fromhex("518b8874040000"))
    verdict = crashdump.classify(crashdump.parse(data))
    assert verdict.startswith("BOT") and "player hook" in verdict


def test_a_read_fault_in_wined3d_is_graphics():
    dump = crashdump.parse(minidump(0x10, access=0, rip=D3D + 0x1234))
    assert crashdump.classify(dump).startswith("Graphics")
    assert "wined3d.dll" in crashdump.summarize(minidump(0x10, access=0, rip=D3D + 0x1234))


def test_a_jump_site_write_from_the_events_counts():
    dump = crashdump.parse(minidump(0x10, access=0, rip=EXE + 0x500002))
    hooks = {"extra_writes": [["teleport je", EXE + 0x500000, 6]]}
    assert "teleport je" in crashdump.classify(dump, hooks)


def load_watcher():
    path = Path(__file__).resolve().parent.parent / "mac" / "crashwatch.py"
    spec = importlib.util.spec_from_file_location("crashwatch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_watcher_writes_crash_md_with_the_dump_the_events_and_the_game_log(tmp_path, monkeypatch):
    watcher = load_watcher()
    game_bin = tmp_path / "Bin"
    game_bin.mkdir()
    log = game_bin / "WizardClient.log"
    log.write_text("old session line\n", encoding="utf-8")
    monkeypatch.setattr(watcher, "GAME_BIN", game_bin)
    monkeypatch.setattr(watcher, "GAME_LOG", log)
    monkeypatch.setattr(watcher, "DIAG", tmp_path / "diag")
    monkeypatch.setattr(watcher, "ROOT", tmp_path)
    monkeypatch.setattr(watcher, "game_pids", lambda rows=None: [])
    run = tmp_path / "run"
    watcher.start(run)
    with log.open("a", encoding="utf-8") as f:
        f.write(
            "10/10/26 17:00:00 [WARN] ZONE  ok\n10/10/26 17:00:01 [ERR ] NET  lost connection to server\n"
        )
    (run / "events.jsonl").write_text(
        json.dumps(
            {
                "t": "17:00:00.000",
                "ts": time.time() - 4,
                "kind": "code_write",
                "label": "mouse hook",
                "address": "0x7b0001",
            }
        )
        + "\n"
        + json.dumps({"t": "17:00:02.000", "ts": time.time() - 2, "kind": "walk_start", "why": "to boar"})
        + "\n",
        encoding="utf-8",
    )
    (run / "hook_session.json").write_text(json.dumps({"cave": {"address": CAVE, "size": 4100}}))
    (game_bin / "WizardGraphicalClient_r1.dmp").write_bytes(minidump(CAVE))
    report = watcher.collect(run, "the Wizard101 process exited", time.time())
    text = report.read_text(encoding="utf-8")
    assert "BOT: the game tried to run code in the WizWalker hook cave" in text
    assert "walk_start" in text and "last write to game code: mouse hook" in text
    assert "lost connection to server" in text
    assert "old session line" not in (run / "WizardClient.during-run.log").read_text()
    assert list(run.glob("*-WizardGraphicalClient_r1.dmp"))


def test_the_watcher_notifies_only_for_events_that_need_the_player(tmp_path):
    watcher = load_watcher()
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"kind": "walk_start"}) + "\n"
        + json.dumps({"kind": "needs_player", "why": "backpack full", "used": 98, "capacity": 100}) + "\n"
        + '{"kind": "needs_pla',  # a line still being written
        encoding="utf-8",
    )
    rows, offset = watcher.new_events(path, 0)
    assert [r["kind"] for r in rows] == ["walk_start", "needs_player"]
    assert watcher.player_message(rows[0]) == ""
    assert "98/100" in watcher.player_message(rows[1])
    with path.open("a", encoding="utf-8") as f:
        f.write('yer", "why": "outside Savarstaad Pass", "zone": "Grizzleheim/GH_MainHub"}\n')
    rows, _ = watcher.new_events(path, offset)
    assert "GH_MainHub" in watcher.player_message(rows[0])
