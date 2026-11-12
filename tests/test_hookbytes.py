"""hookbytes: hook bodies built from the instruction they replace (11-12 game update)."""

import os
import re
from pathlib import Path

import pytest

from wiz101_auto.hookbytes import (
    CLIENT_PATTERN,
    DANCE_PATTERN,
    HookShapeError,
    client_body,
    dance_body,
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
