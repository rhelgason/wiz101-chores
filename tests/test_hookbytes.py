"""hookbytes: hook bodies built from the instruction they replace (11-12 game update)."""

import os
import re
from pathlib import Path

import pytest

from wiz101_auto.hookbytes import (
    CLIENT_PATTERN,
    DANCE_PATTERN,
    MOUSE_LEAVE_BEFORE,
    MOUSE_LEAVE_CANDIDATES,
    MOUSE_LEAVE_IMM,
    HookShapeError,
    client_body,
    dance_body,
    mouse_leave_flag,
    root_window_body,
)

EXPORT = bytes(range(1, 9))


def test_the_old_build_gives_wizwalkers_own_bodies():
    # WizWalker's hand-written bodies, for the build before 11-12.
    assert client_body(bytes.fromhex("488b9bc0010000"), EXPORT) == (
        b"\x50\x48\x8B\xC7\x48\xA3" + EXPORT + b"\x58\x48\x8B\x9B\xC0\x01\x00\x00"
    )
    assert root_window_body(bytes.fromhex("498b8dd8000000"), EXPORT) == (
        b"\x50\x49\x8B\x85\xD8\x00\x00\x00\x48\xA3" + EXPORT + b"\x58\x49\x8B\x8D\xD8\x00\x00\x00"
    )
    assert dance_body(bytes.fromhex("488bd848397010"), EXPORT) == (
        b"\x48\x8B\xD8\x48\x8B\x00\x48\xA3" + EXPORT + b"\x48\x8B\xC3\x48\x39\x70\x10"
    )


def test_the_11_12_build():
    # root window: r13 became r15; the export reads [r15+0xD8] and the replay is r15's.
    assert root_window_body(bytes.fromhex("498b8fd8000000"), EXPORT) == (
        b"\x50\x49\x8B\x87\xD8\x00\x00\x00\x48\xA3" + EXPORT + b"\x58\x49\x8B\x8F\xD8\x00\x00\x00"
    )
    # dance: rbx became rdi.
    assert dance_body(bytes.fromhex("488bf848397010"), EXPORT) == (
        b"\x48\x8B\xF8\x48\x8B\x00\x48\xA3" + EXPORT + b"\x48\x8B\xC7\x48\x39\x70\x10"
    )


def test_unexpected_instructions_are_refused():
    with pytest.raises(HookShapeError):
        root_window_body(bytes.fromhex("488b0d11223344"), EXPORT)  # RIP-relative
    with pytest.raises(HookShapeError):
        root_window_body(bytes.fromhex("498b97e8000000"), EXPORT)  # into rdx, not rcx
    with pytest.raises(HookShapeError):
        client_body(bytes.fromhex("488bbfc0010000"), EXPORT)  # loads rdi, the value it copies
    with pytest.raises(HookShapeError):
        client_body(bytes.fromhex("488b4c2440aabb"), EXPORT)  # a SIB byte, and the wrong length
    with pytest.raises(HookShapeError):
        dance_body(bytes.fromhex("488bf048397010"), EXPORT)  # rsi is the loop index
    with pytest.raises(HookShapeError):
        dance_body(bytes.fromhex("488bf848397018"), EXPORT)


# 11-18 build, 0x141895f42: cmp byte [r14+0x190], 0; je; mov byte [r14+0x190], 0; xor esi, esi; mov
NEW_LEAVE = bytes.fromhex(
    "aa bb 41 80 be 90 01 00 00 00 0f 84 c6 07 00 00 41 c6 86 90 01 00 00 00 33 f6 89 b5"
)
# the same with no REX (an rsi base, as WizWalker's pattern expects)
OLD_LEAVE = bytes.fromhex(
    "aa bb cc 80 be 90 01 00 00 00 0f 84 c6 07 00 00 c6 86 90 01 00 00 00 33 ff 89 b5 70"
)


def split(code):
    at = code.index(b"\xc6\x86")
    return code[at - MOUSE_LEAVE_BEFORE : at], code[at:]


def test_the_mouse_leave_flag_is_the_write_guarded_by_the_same_compare():
    assert mouse_leave_flag(*split(NEW_LEAVE))
    assert mouse_leave_flag(*split(OLD_LEAVE))
    assert split(NEW_LEAVE)[1][MOUSE_LEAVE_IMM] == 0
    other_field = NEW_LEAVE.replace(bytes.fromhex("c6 86 90 01"), bytes.fromhex("c6 86 98 01"))
    assert not mouse_leave_flag(*split(other_field))
    other_reg = NEW_LEAVE.replace(bytes.fromhex("80 be 90"), bytes.fromhex("80 bf 90"))
    assert not mouse_leave_flag(*split(other_reg))
    rex_mismatch = NEW_LEAVE.replace(bytes.fromhex("bb 41 80"), bytes.fromhex("bb cc 80"))
    assert not mouse_leave_flag(*split(rex_mismatch))
    no_je = NEW_LEAVE.replace(bytes.fromhex("0f 84"), bytes.fromhex("0f 85"))
    assert not mouse_leave_flag(*split(no_je))
    sets_one = NEW_LEAVE.replace(bytes.fromhex("01 00 00 00 33"), bytes.fromhex("01 00 00 01 33"))
    assert not mouse_leave_flag(*split(sets_one))


GAME_EXE = Path(os.path.expanduser(
    "~/Library/Application Support/Wizard101/Bottles/wizard101/drive_c/ProgramData/"
    "KingsIsle Entertainment/Wizard101/Bin/WizardGraphicalClient.exe"
))


@pytest.mark.skipif(not GAME_EXE.exists(), reason="the game is not installed here")
def test_the_installed_game_has_one_spot_each_of_the_right_shape():
    data = GAME_EXE.read_bytes()
    client = [m.start() for m in re.finditer(CLIENT_PATTERN, data, re.S)]
    dance = [m.start() for m in re.finditer(DANCE_PATTERN, data, re.S)]
    root = [m.start() for m in re.finditer(rb".......\x48\x8B\x01.......\xFF\x50\x70\x84", data, re.S)]
    assert len(client) == len(dance) == len(root) == 1
    client_body(data[client[0] + 1 : client[0] + 8], EXPORT)
    dance_body(data[dance[0] : dance[0] + 7], EXPORT)
    root_window_body(data[root[0] : root[0] + 7], EXPORT)
    leave = [
        m.start()
        for m in re.finditer(MOUSE_LEAVE_CANDIDATES, data, re.S)
        if mouse_leave_flag(data[m.start() - MOUSE_LEAVE_BEFORE : m.start()], data[m.start() : m.start() + 9])
    ]
    assert len(leave) == 1 and data[leave[0] + MOUSE_LEAVE_IMM] == 0
    assert len(re.findall(rb"\x00\xFF\x50\x18\x66\xC7", data, re.S)) == 1  # WizWalker's bool one
