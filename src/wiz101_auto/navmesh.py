"""The game's own walking graph for each zone, and paths over it.

Every zone archive (`Data/GameData/<World>-<Zone>.wad`) holds `zone.nav`:
the points the game's creatures walk between, about 480 units apart on open
ground, and which of them connect. Format (little endian):

    u16 highest id, u32 node count, node count x (f32 x, f32 y, f32 z, u16 id),
    u32 edge count, edge count x (u16 from, u16 to)

Edges are stored both ways. A path is A* over that graph from the node
nearest the wizard to the node nearest the goal, then straightened where
the nodes run in a line. Standard library only, so the Mac can check every
zone offline (`mac/navcheck.py`) and the bot can use it inside the bottle.
"""

from __future__ import annotations

import heapq
import math
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

GAME_DATA_CANDIDATES = (
    Path("C:/ProgramData/KingsIsle Entertainment/Wizard101/Data/GameData"),
    Path.home()
    / "Library/Application Support/Wizard101/Bottles/wizard101/drive_c/ProgramData/KingsIsle Entertainment"
    / "Wizard101/Data/GameData",
)
JOIN_REACH = 1500.0  # farther than this from every node: the point is off the graph


def game_data_dir() -> Path | None:
    given = os.environ.get("WIZ_GAME_DATA")
    for path in ([Path(given)] if given else []) + list(GAME_DATA_CANDIDATES):
        if path.is_dir():
            return path
    return None


def wad_name(zone: str) -> str:
    """'Grizzleheim/GH_Hero' -> 'Grizzleheim-GH_Hero.wad'."""
    return zone.strip("/").replace("/", "-") + ".wad"


def wad_entries(path: Path) -> dict[str, tuple[int, int, int, bool]]:
    """name -> (offset, size, stored size, compressed) for a KIWAD archive."""
    out = {}
    with path.open("rb") as f:
        head = f.read(14)
        if head[:5] != b"KIWAD":
            raise ValueError(f"{path.name} is not a KIWAD archive")
        version, count = struct.unpack_from("<ll", head, 5)
        f.seek(13 + (1 if version >= 2 else 0))
        for _ in range(count):
            offset, size, zsize, zipped, _crc, name_len = struct.unpack("<lll?ll", f.read(21))
            name = f.read(name_len).decode("utf-8", "replace").rstrip("\0")
            out[name] = (offset, size, zsize, zipped)
    return out


def wad_read(path: Path, name: str) -> bytes | None:
    entry = wad_entries(path).get(name)
    if entry is None:
        return None
    offset, size, zsize, zipped = entry
    with path.open("rb") as f:
        f.seek(offset)
        raw = f.read(zsize if zipped else size)
    if zipped:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return raw
    return raw


@dataclass
class NavGraph:
    nodes: dict[int, tuple[float, float, float]]
    edges: dict[int, set[int]] = field(default_factory=dict)

    @classmethod
    def parse(cls, data: bytes) -> NavGraph:
        _top, count = struct.unpack_from("<HI", data, 0)
        off = 6
        nodes: dict[int, tuple[float, float, float]] = {}
        for _ in range(count):
            x, y, z, node = struct.unpack_from("<fffH", data, off)
            off += 14
            nodes[node] = (x, y, z)
        (links,) = struct.unpack_from("<I", data, off)
        off += 4
        edges: dict[int, set[int]] = {n: set() for n in nodes}
        for _ in range(links):
            a, b = struct.unpack_from("<HH", data, off)
            off += 4
            if a in nodes and b in nodes:
                edges[a].add(b)
                edges[b].add(a)
        return cls(nodes, edges)

    def nearest(self, point, k: int = 1) -> list[int]:
        ranked = sorted(self.nodes, key=lambda n: math.dist(self.nodes[n][:2], point[:2]))
        return ranked[:k]

    def gap(self, point) -> float:
        """Distance from `point` to the nearest node."""
        if not self.nodes:
            return math.inf
        return math.dist(self.nodes[self.nearest(point)[0]][:2], point[:2])

    def components(self) -> list[set[int]]:
        seen: set[int] = set()
        out = []
        for start in self.nodes:
            if start in seen:
                continue
            group, stack = {start}, [start]
            seen.add(start)
            while stack:
                for nxt in self.edges.get(stack.pop(), ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        group.add(nxt)
                        stack.append(nxt)
            out.append(group)
        return sorted(out, key=len, reverse=True)

    def astar(self, start: int, goal: int, blocked: set[tuple[int, int]] = frozenset()) -> list[int] | None:
        pos = self.nodes
        best = {start: 0.0}
        came: dict[int, int] = {}
        queue = [(math.dist(pos[start], pos[goal]), start)]
        while queue:
            _f, node = heapq.heappop(queue)
            if node == goal:
                path = [node]
                while node in came:
                    node = came[node]
                    path.append(node)
                return path[::-1]
            for nxt in self.edges.get(node, ()):
                if (node, nxt) in blocked:
                    continue
                cost = best[node] + math.dist(pos[node], pos[nxt])
                if cost < best.get(nxt, math.inf):
                    best[nxt] = cost
                    came[nxt] = node
                    heapq.heappush(queue, (cost + math.dist(pos[nxt], pos[goal]), nxt))
        return None

    def route(
        self,
        start,
        goal,
        blocked: set[tuple[int, int]] = frozenset(),
        tries: int = 4,
    ) -> list[tuple[float, float, float]] | None:
        """Waypoints from `start` to `goal`, both (x, y, z). None when the graph
        does not join them. The few nodes nearest each end are tried, so a
        wizard standing beside a wall is not tied to the node behind it."""
        if not self.nodes:
            return None
        if math.dist(start[:2], goal[:2]) < 1.0:
            return [tuple(goal)]
        firsts = [
            n for n in self.nearest(start, tries) if math.dist(self.nodes[n][:2], start[:2]) <= JOIN_REACH
        ]
        lasts = [n for n in self.nearest(goal, tries) if math.dist(self.nodes[n][:2], goal[:2]) <= JOIN_REACH]
        best = None
        for a in firsts:
            for b in lasts:
                path = self.astar(a, b, blocked)
                if path is None:
                    continue
                points = [self.nodes[n] for n in path]
                length = (
                    math.dist(start[:2], points[0][:2])
                    + sum(math.dist(p[:2], q[:2]) for p, q in zip(points, points[1:], strict=False))
                    + math.dist(points[-1][:2], goal[:2])
                )
                if best is None or length < best[0]:
                    best = (length, path)
        if best is None:
            return None
        return straighten([self.nodes[n] for n in best[1]]) + [tuple(goal)]

    def node_path(self, start, goal, blocked=frozenset()) -> list[int] | None:
        """The node ids a route() would use (for marking an edge blocked)."""
        a, b = self.nearest(start)[0], self.nearest(goal)[0]
        return self.astar(a, b, blocked)


def straighten(points: list[tuple[float, float, float]], tolerance: float = 0.12) -> list:
    """Drop the middle of runs that keep the same heading (the grid's straight lines)."""
    if len(points) <= 2:
        return list(points)
    out = [points[0]]
    for prev, here, nxt in zip(points, points[1:], points[2:], strict=False):
        a = math.atan2(here[1] - prev[1], here[0] - prev[0])
        b = math.atan2(nxt[1] - here[1], nxt[0] - here[0])
        turn = abs((b - a + math.pi) % (2 * math.pi) - math.pi)
        if turn > tolerance:
            out.append(here)
    out.append(points[-1])
    return out


_graphs: dict[str, NavGraph | None] = {}


def zone_graph(zone: str, data_dir: Path | None = None) -> NavGraph | None:
    """The zone's walking graph, read once. None when the zone has no zone.nav."""
    if zone in _graphs:
        return _graphs[zone]
    graph = None
    folder = data_dir or game_data_dir()
    if folder is not None:
        path = folder / wad_name(zone)
        if path.is_file():
            try:
                data = wad_read(path, "zone.nav")
                if data:
                    graph = NavGraph.parse(data)
            except (OSError, ValueError, struct.error):
                graph = None
    _graphs[zone] = graph
    return graph
