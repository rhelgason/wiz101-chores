"""Where each reagent can spawn, read from the game's own zone files.

Every zone archive (`Data/GameData/<World>-<Zone>.wad`) that grows reagents
names a spawn point and a path for each kind: `spawnData.xml` holds
"Spawn point-BlackLotus", and `pathData.xml` holds "Path-BlackLotus", a list
of path-node ids. `pathNodeData.bin` gives each node's position. The server
picks among those spots at random (SNT_RANDOM), so the spots are where a
reagent can be, not where one is now: the bot walks them and looks.

Formats (KingsIsle's binary property stream, little endian, sizes in bits):

    path record:  ... u8 len*2, name ("Path-BlackLotus"), u32 bits, u32 hash,
                  u8 count*2, count x u64 node id
    node record:  u32 0x04aedbfe (class), u32 bits,
                  u32 160, u32 0x857edc1b, f32 x, f32 y, f32 z  (location)
                  ... u32 128, u32 0x7c9b9045, u64 id

Standard library only, like navmesh.py, so the Mac can list every zone's
spots offline (`python3 -m wiz101_auto.reagent_spots MooShu BlackLotus`).
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

from .navmesh import game_data_dir, wad_entries, wad_name, wad_read

NODE_CLASS = 0x04AEDBFE
LOCATION = 0x857EDC1B
ID = 0x7C9B9045
_PATH = re.compile(rb"Path-([A-Za-z][A-Za-z0-9 _]*)")


def flat(name: str) -> str:
    """'Black Lotus' -> 'blacklotus' (the files write 'BlackLotus')."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def parse_nodes(data: bytes) -> dict[int, tuple[float, float, float]]:
    """Path node id -> (x, y, z)."""
    out: dict[int, tuple[float, float, float]] = {}
    head = struct.pack("<I", NODE_CLASS)
    loc = struct.pack("<II", 160, LOCATION)
    ident = struct.pack("<II", 128, ID)
    start = data.find(head)
    while start >= 0:
        nxt = data.find(head, start + 4)
        end = nxt if nxt >= 0 else len(data)
        record = data[start:end]
        a, b = record.find(loc), record.find(ident)
        if a >= 0 and b >= 0 and a + 20 <= len(record) and b + 16 <= len(record):
            x, y, z = struct.unpack_from("<fff", record, a + 8)
            (node,) = struct.unpack_from("<Q", record, b + 8)
            out[node] = (x, y, z)
        start = nxt
    return out


def _count(data: bytes, at: int) -> tuple[int, int]:
    """KingsIsle's compact length: an even byte is len*2, else a u32 is len*2+1."""
    first = data[at]
    if first & 1 == 0:
        return first >> 1, at + 1
    (value,) = struct.unpack_from("<I", data, at)
    return value >> 1, at + 4


def parse_paths(data: bytes) -> dict[str, list[int]]:
    """'BlackLotus' -> its node ids, for every "Path-<Name>" record."""
    out: dict[str, list[int]] = {}
    for match in _PATH.finditer(data):
        start = match.start()
        if start == 0:
            continue
        length = data[start - 1] >> 1  # the name's own length byte: the regex can run past it
        if length <= len("Path-") or start + length > len(data):
            continue
        name = data[start + len("Path-") : start + length].decode("ascii", "replace")
        at = start + length + 8  # u32 bits, u32 hash
        if at >= len(data):
            continue
        try:
            count, at = _count(data, at)
            if count <= 0 or count > 500 or at + 8 * count > len(data):
                continue
            ids = list(struct.unpack_from(f"<{count}Q", data, at))
        except (IndexError, struct.error):
            continue
        if all(0 < i < 1 << 32 for i in ids):
            out.setdefault(name, []).extend(ids)
    return out


def zone_spots(wad: Path) -> dict[str, list[tuple[float, float, float]]]:
    """Every named spawn path in one zone archive -> its spots."""
    try:
        entries = wad_entries(wad)
        if "pathData.xml" not in entries or "pathNodeData.bin" not in entries:
            return {}
        paths = parse_paths(wad_read(wad, "pathData.xml") or b"")
        nodes = parse_nodes(wad_read(wad, "pathNodeData.bin") or b"")
    except (OSError, ValueError, struct.error):
        return {}
    return {name: [nodes[i] for i in ids if i in nodes] for name, ids in paths.items()}


def zone_from_wad(name: str) -> str:
    """'MooShu-MS_Death-MS_Death_Zone1_BurialGround.wad' -> 'MooShu/MS_Death/MS_Death_Zone1_BurialGround'."""
    return name.removesuffix(".wad").replace("-", "/")


def reagent_spots(
    reagent: str, world: str = "", data_dir: Path | None = None, *, outdoors: bool = True
) -> dict[str, list[tuple[float, float, float]]]:
    """Zone -> spots where `reagent` can spawn, over every downloaded zone of `world`."""
    folder = data_dir or game_data_dir()
    if folder is None:
        return {}
    want = flat(reagent)
    out: dict[str, list[tuple[float, float, float]]] = {}
    for wad in sorted(folder.glob(f"{world}*.wad" if world else "*.wad")):
        if outdoors and "Interiors" in wad.name:
            continue
        for name, spots in zone_spots(wad).items():
            if flat(name) == want and spots:
                out.setdefault(zone_from_wad(wad.name), []).extend(spots)
    return out


def known_spots(zone: str, reagent: str, data_dir: Path | None = None) -> list[tuple[float, float, float]]:
    folder = data_dir or game_data_dir()
    if folder is None:
        return []
    wad = folder / wad_name(zone)
    if not wad.is_file():
        return []
    want = flat(reagent)
    return [p for name, spots in zone_spots(wad).items() if flat(name) == want for p in spots]


def main(argv: list[str]) -> int:
    world = argv[0] if argv else ""
    reagent = argv[1] if len(argv) > 1 else ""
    folder = game_data_dir()
    if folder is None:
        print("no game data folder found (set WIZ_GAME_DATA)")
        return 1
    for wad in sorted(folder.glob(f"{world}*.wad" if world else "*.wad")):
        if "Interiors" in wad.name:
            continue
        spots = zone_spots(wad)
        for name, points in sorted(spots.items()):
            if points and (not reagent or flat(name) == flat(reagent)):
                print(f"{zone_from_wad(wad.name)}\t{name}\t{len(points)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
