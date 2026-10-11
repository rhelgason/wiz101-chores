#!/usr/bin/env python3
"""Check, offline, that the bot can walk every zone it might travel through.

For each zone in WizSprinter's gate list it reads the game's own zone.nav
from Data/GameData and checks that each way out (gates, the world door) can
be walked to from each way in. Then it prints a trip, step by step.

  python3 mac/navcheck.py                       every world, a summary per world
  python3 mac/navcheck.py --world Grizzleheim   one world, zone by zone
  python3 mac/navcheck.py --trip WizardCity/WC_Streets/Interiors/WC_PET_Park Grizzleheim/GH_Hero

macOS Python, standard library; reads the game files, never the game.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wiz101_auto import navmesh  # noqa: E402
from wiz101_auto.travel_data import (  # noqa: E402
    door_spot_usable,
    gate_kind,
    parse_gates,
    parse_zone_doors,
    route,
)

TRAVERSAL = (
    Path.home()
    / "Library/Application Support/wiz101-chores/python/Lib/site-packages/wizwalker/extensions/wizsprinter"
    / "traversalData"
)
NEAR = 700.0  # a gate this far from the graph is still reachable by walking the last bit straight


def load():
    gates = parse_gates((TRAVERSAL / "gates_list.txt").read_text())
    doors = parse_zone_doors((TRAVERSAL / "uniqueObjectLocations.txt").read_text())
    return gates, doors


def exits(zone, gates, doors):
    """(label, point) for each way out of `zone`, and each way in (arrivals stand by the gate back)."""
    spots = [(f"gate to {to}", (p.x, p.y, p.z)) for p, to in gates.get(zone, [])]
    door = doors.get(zone.split("/")[0])
    if door and door[0] == zone and door_spot_usable(door[1]):
        spots.append(("world door", (door[1].x, door[1].y, door[1].z)))
    return spots


def check_zone(zone, gates, doors, data):
    """(status, details). status: ok | no-nav | split | far."""
    graph = navmesh.zone_graph(zone, data)
    spots = exits(zone, gates, doors)
    if graph is None:
        if not (data / navmesh.wad_name(zone)).exists():
            return "not-downloaded", ["the game fetches this zone on the first visit"]
        return "no-nav", [f"no zone.nav in {navmesh.wad_name(zone)}"]
    notes = []
    far = [(label, graph.gap(p)) for label, p in spots if graph.gap(p) > NEAR]
    for label, gap in far:
        notes.append(f"{label}: {gap:.0f} from the nearest walk point")
    broken = []
    for (la, a), (lb, b) in [(x, y) for i, x in enumerate(spots) for y in spots[i + 1 :]]:
        if graph.route(a, b) is None:
            broken.append(f"{la} <-> {lb}")
    notes += [f"no walk path: {b}" for b in broken[:6]]
    if broken:
        return "split", notes
    if far:
        return "far", notes
    return "ok", [f"{len(graph.nodes)} walk points, {len(spots)} ways in/out"]


def trip(src, dest, gates, doors, data):
    steps = route(src, dest, gates, doors)
    if steps is None:
        print(f"no known route from {src} to {dest}")
        return 1
    zone = src
    total = 0.0
    print(f"{src} -> {dest}: {len(steps)} step(s)")
    for step in steps:
        graph = navmesh.zone_graph(zone, data)
        if step[0] == "gate":
            _k, pos, nxt = step
            target = (pos.x, pos.y, pos.z)
            how = f"walk into the gate to {nxt}"
            if gate_kind(zone, nxt) != "standard":
                how = f"walk to it and press X ({gate_kind(zone, nxt)}) for {nxt}"
        else:
            _k, door_zone, pos = step
            target = (pos.x, pos.y, pos.z) if door_spot_usable(pos) else None
            nxt = doors[dest.split("/")[0]][0]
            how = f"walk to the world door, Spiral Map -> {dest.split('/')[0]} (arrive in {nxt})"
        start = arrival(zone, prev=getattr(trip, "_prev", None), gates=gates)
        if graph is None:
            print(f"  [{zone}] NO NAV DATA: {how}")
        elif target is None:
            print(f"  [{zone}] {how}; door spot unknown: found live by its name, then walked")
        elif start is None:
            print(f"  [{zone}] {how} (start: wherever the wizard stands)")
        else:
            path = graph.route(start, target)
            if path is None:
                print(f"  [{zone}] NO WALK PATH: {how}")
            else:
                length = sum(math.dist(p[:2], q[:2]) for p, q in zip([start] + path, path, strict=False))
                total += length
                print(f"  [{zone}] {how}: {len(path)} waypoints, {length:.0f} units")
        trip._prev = zone
        zone = nxt
    trip._prev = None
    print(f"walking distance known: {total:.0f} units (~{total / 580:.0f}s at base speed)")
    return 0


def arrival(zone, prev, gates):
    """Where a wizard coming from `prev` stands in `zone`: by the gate back to `prev`."""
    if prev is None:
        return None
    for p, to in gates.get(zone, []):
        if to == prev:
            return (p.x, p.y, p.z)
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--world", default=None)
    parser.add_argument("--trip", nargs=2, metavar=("FROM", "TO"))
    args = parser.parse_args(argv)
    data = navmesh.game_data_dir()
    if data is None:
        print("Wizard101's Data/GameData folder was not found")
        return 1
    gates, doors = load()
    if args.trip:
        return trip(args.trip[0], args.trip[1], gates, doors, data)
    zones = sorted(
        {z for z in gates} | {to for v in gates.values() for _p, to in v} | {d[0] for d in doors.values()}
    )
    by_world = defaultdict(list)
    for zone in zones:
        if args.world and zone.split("/")[0].lower() != args.world.lower():
            continue
        status, notes = check_zone(zone, gates, doors, data)
        by_world[zone.split("/")[0]].append((zone, status, notes))
    for world, rows in sorted(by_world.items()):
        counts = defaultdict(int)
        for _z, status, _n in rows:
            counts[status] += 1
        print(f"{world}: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
        for zone, status, notes in rows:
            if args.world or status not in ("ok", "not-downloaded"):
                print(f"  {status:6} {zone}: " + "; ".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
