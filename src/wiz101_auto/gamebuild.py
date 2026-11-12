"""Which build of the game the hooks last worked on (state/game_build.json).

11-12: a Wizard101 update (a new world) replaced WizardGraphicalClient.exe and
three hook spots were gone; the bot said "quit the game and start again",
which cannot help. Now a missing spot is compared with the build the hooks
last went in on, and an update is named as one.

The fingerprint is the PE header's link time (TimeDateStamp) and the file's
size and modified time. Reading the file only; never the running game.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

GAME_EXE = "WizardGraphicalClient.exe"
DEFAULT_EXE = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Bin") / GAME_EXE
RECORD = Path("state") / "game_build.json"


def pe_stamp(path: Path) -> int | None:
    """The PE file header's TimeDateStamp (the link time), or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if head[:2] != b"MZ" or len(head) < 64:
        return None
    pe = int.from_bytes(head[60:64], "little")
    if pe + 12 > len(head) or head[pe : pe + 4] != b"PE\0\0":
        return None
    return int.from_bytes(head[pe + 8 : pe + 12], "little")


def fingerprint(path: Path | None = None) -> dict | None:
    path = Path(path or DEFAULT_EXE)
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"stamp": pe_stamp(path), "size": st.st_size, "mtime": int(st.st_mtime), "path": str(path)}


def same_build(a: dict | None, b: dict | None) -> bool | None:
    """True/False, or None when either side is unknown."""
    if not a or not b:
        return None
    if a.get("stamp") and b.get("stamp"):
        return a["stamp"] == b["stamp"] and a.get("size") == b.get("size")
    return a.get("size") == b.get("size") and a.get("mtime") == b.get("mtime")


def last_good(record: Path = RECORD) -> dict | None:
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def remember_good(path: Path | None = None, record: Path = RECORD, now: float | None = None) -> dict | None:
    """After the hooks went in (or were adopted): this build works."""
    fp = fingerprint(path)
    if fp is None:
        return None
    fp["ok_at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    record.parent.mkdir(parents=True, exist_ok=True)
    tmp = record.with_name(record.name + ".tmp")
    tmp.write_text(json.dumps(fp, indent=1), encoding="utf-8")
    os.replace(tmp, record)
    return fp


def _when(fp: dict) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(fp["mtime"])) if fp.get("mtime") else "?"


def missing_spot_message(now_fp: dict | None, good: dict | None) -> str:
    """What to tell the player when a hook pattern was not found."""
    same = same_build(now_fp, good)
    if same is False:
        return (
            "\nWizard101 was updated: its program is a new build (changed "
            f"{_when(now_fp)}; the hooks last worked on the build from {_when(good)}, "
            f"on {good.get('ok_at', '?')}).\n"
            "The new build moved code the hooks look for. Restarting the game will not help; "
            "the bot needs new hook patterns for this build."
        )
    if same is True:
        return (
            "\nA hook spot was not found, but this is the same game build the hooks worked on "
            f"({good.get('ok_at', '?')}). Most likely an older version of this bot hooked this same "
            "game window without recording it: quit Wizard101 completely, start it, log in, and run "
            "again (once)."
        )
    return (
        "\nA hook spot was not found in this game. Two causes:\n"
        "- a game update moved the code (no record of the build the hooks last worked on). "
        "Then the bot needs new hook patterns.\n"
        "- an older version of this bot hooked this same game window and did not record it. "
        "Quit Wizard101 completely, start it, log in, and run again (once)."
    )
