# wiz101-auto: the day plan

A thinned Wizard101 chore bot. It hooks the official client through WizWalker.
Read `PLAYBOOK.md` before changing what it does. If this file and the code
disagree, the code is what ran and this file is what the next session must
know. Working tree is **0.9.0** (teleport-free Mac rebuild, not yet run live).
Last pushed: `8af0dac` on `origin/main`.

Private repo only: `git@github.com:rhelgason/wiz101-chores.git` (`origin`,
fetch and push). `upstream` is `git@github.com:szatcg/wiz101-auto.git` (fetch
only; push is disabled). Never open a pull request. Never push upstream.

Do not commit `config.yaml`, `state/`, `logs/`, or `.venv`. Never read or
print the game password.

## Where it runs

This machine is an Apple silicon Mac (M5 Pro). Wizard101 is the official
app's Windows client inside `Wizard101.app`'s own Wine bottle, running as
x86 under Rosetta 2. Not native Mac and not CrossOver.app.
Wine is `/Applications/Wizard101.app/Contents/SharedSupport/wizard101/bin/wine`.
`WINEPREFIX` is `~/Library/Application Support/Wizard101/Bottles/wizard101`.
Bottle Python is `~/Library/Application Support/wiz101-chores/python/python.exe`.
`mac/run.sh` rewrites `python313._pth` (that file ignores `PYTHONPATH`) and
installs `mac/sitecustomize.py` so Wine's missing `CopyFile2` does not abort
Python. Do not enable `_wmi`, do not use `get-pip.py`, and do not pip-build
wizwalker. Do not patch `site-packages`; shim from this repo.

Do not start Wizard101, do not kill it, do not `wineserver -k`, and do not
attach or hook while the user is in the game unless they asked to test.
Do not force-kill Python. A `pgrep -f` whose pattern also appears in the
bash command matches itself. Reading files in the game folder is fine.

Start: Wizard101 logged in, wizard standing in the world anywhere, then
`./mac/run.sh pet` or `./mac/run.sh seeds`. Each walks to its place first
(the Pet Pavilion; Savarstaad Pass, then Boar Camp), world door included. A bare `./mac/run.sh`
loads gitignored `config.yaml` (local file is `mode: seeds`). `chores` and
`reagents` are refused on the Mac. Stop from the menu-bar Wiz item,
`Stop Wizard Bot.command`, Ctrl+C in that terminal, Ctrl+Shift+Q, or
`state/stop.request`. A second `./mac/run.sh` while one bot runs is refused.
A running process does not pick up edits.

Tests on the Mac, not the bottle:
`/tmp/wiz-test-venv/bin/python -m pytest -q` and
`/tmp/wiz-test-venv/bin/ruff check .` (if `/tmp` was cleaned, recreate it:
`python3 -m venv /tmp/wiz-test-venv` then `pip install pytest pyyaml loguru ruff`).
Tests must not import `duty`, `reagents`, `bot.py`, or `travel.py` (those
import wizwalker); stub `wizwalker` modules in `sys.modules` instead.

## The rule that came out of the crashes

**No teleports on the Mac, and no game-code writes after connect.**
Every client death with a known step followed a teleport. WizWalker's
teleport patches two jumps in the game's per-frame movement code from
outside the process, then patches them back from inside; under Rosetta
that is the riskiest thing the bot did. The one surviving minidump
(2026-10-07 10:55, archived in `logs/dumps/`) is an execute fault in
WizWalker's hook cave after a same-process rehook. Details: PLAYBOOK.md,
"Why the Mac client crashed".

How the code enforces it:
- `hooking.py` installs only player, player stat, client, root window,
  render context, and (pet) the dance hook. Not movement-teleport, quest,
  or drops. Each byte written is recorded in `state/hook_session.json`.
- Hooks stay in the game when the bot stops. The next start in the same
  game compares bytes and adopts them with zero writes, so stopping and
  starting the bot never needs a game restart. Each hook (and the cave) is
  recorded *before* its bytes are written: a run killed mid-install leaves
  spots that are ours (adopted) or still original (installed again). Only
  bytes that are neither → refuse and ask for a game restart. Game-module
  hook bytes are never "repaired" by rewriting.
- The mouse hook's original bytes (GetCursorPos, SetCursorPos, the
  GetClassInfoExA cave, two game bools) are snapshotted before it goes in;
  a run that died with it half in is put back from that snapshot.
- `rehook.pid_alive` must never `os.kill(pid, 0)` in the bottle: on Windows
  that is TerminateProcess, and Wine reuses small pids (the game was pid
  444). The lock now counts only a live python.exe; `service.stop` never
  force-kills.
- The mouse hook goes in at start and out at stop (the player needs the
  mouse). Pause does not toggle it unless `safety.pause_releases_mouse`.
- `blackbox.guard.lock()` after connect: `MemoryReader.write_bytes` asks
  VirtualQueryEx and refuses writes to executable image pages
  (`CodeWriteBlocked`). Bot-allocated RWX buffers are data and pass.
- `client.teleport` raises `TeleportDisabled` on the Mac.
- Walking (`walk.py`) writes yaw (heap data) and posts W/A/D with real
  scan-code lParams, re-aims every 0.3s, does a measured final approach,
  sidesteps when stalled, and reports reached/fight/interrupted/stuck/lost/timeout.

## Travel on foot

`navigate.py` (used by `travel.go_to_zone` whenever `client._walk_only`):
- the route: WizSprinter's `gates_list.txt` (zone → zone gates, with kind)
  and `uniqueObjectLocations.txt` (each world's door zone), via
  `travel_data.travel_step`; the spiral door changes worlds;
- inside a zone: `navmesh.py` reads the game's own `zone.nav` from
  `Data/GameData/<World>-<Zone>.wad` (u16, u32 n, n×(f32 x,y,z,u16 id),
  u32 m, m×(u16,u16)); A* from the node nearest the wizard to the node
  nearest the target, straight runs collapsed; a stuck/timed-out leg blocks
  that edge and replans (3 times); no graph → straight walk;
- doorways: standard gate = walk onto it, then hold W until the zone
  changes; x-gate = walk to it and `ui.press_interact` (this Wine build
  answers a click on the X prompt, not the key); world door = found live by
  object name (`travel.DOOR_NEEDLES`), spot saved to `state/world_doors.json`,
  walked to, prompt clicked, Spiral Map picked (a server zone change);
- a fight on the way is played (`client._on_fight`), then travel goes on.
  Failures dump `entities-<zone>.txt` and `spiral_door_windows.txt` into the
  run folder.

`python3 mac/navcheck.py` checks every zone offline (every gate and the
door joined by a walk path); `--trip FROM TO` prints a trip leg by leg.
On 2026-10-10 every downloaded zone in Wizard City, Krokotopia, Marleybone,
MooShu, DragonSpire, Grizzleheim and Celestia/Wysteria's hubs had nav data;
later worlds are not downloaded yet (the game fetches a zone on first
visit). Known gaps: WC_Cyclops (Colossus gate off the graph), MooShu's
War ruined-village hub (split graph). Wizard City's door spot is not in any
data file; it is found live the first time.

`rehook.py`'s restore and Wine-autobot-buffer paths are no longer used by
`run` (only `install_scan_guard`, the bot lock, and `SCAN_STALL` are).
`pet-alt` still uses them and is refused on the Mac.

## What each mode does

`mode: pet` (`petdance.pet_loop`): walk to the Pet Pavilion if elsewhere,
walk onto the sigil, dance while the globe can pay, feed the first snack
after each win, wait 5 min between energy checks. Waits out a duel. A
failed walk is retried every 2 min and posts a Mac notification.

`mode: seeds` (`seeds.SeedFarm`): walk to Savarstaad Pass if elsewhere;
home is where the run starts if within 6000 of Boar Camp (-23200, 14300),
else it walks to the camp and that is home. Pulls Troubled Warriors first, then Splithoof, nearest first,
within `seeds.leash` of home. Each round: Empower, then Scarecrow; pass
while the next is not ready; a death drain only under ~60% health when
that next card cannot be cast. Sacrifice is not a heal. Deathblade is not
cast. Low health or mana: potion, wisp in reach, then stand still up to
`seeds.rest_minutes`. Bag two slots from full: clean stop. Outside the
pass (a defeat): walk back. No Northguard rest, no Bazaar.

Updated after the 10-10 live runs (these supersede the line above):
- Low health/mana with no potion and no wisp in reach: Mark Location at
  the camp (HUD `MarkButton`), walk to Northguard, walk onto wisps or stand
  up to `seeds.rest_minutes`, then Recall (`RecallButton`; button names
  from Root.wad GUI/HUDWindow.gui), else walk back (`seeds.northguard_trips`).
- Bag two slots from full: `selling.sell_trip` marks, walks to the Bazaar,
  sells only names new this run (LootBook baseline read when home is set),
  recalls back (`seeds.sell`). Stops only if that fails. Shop clicks are
  still unverified live; failures dump `bazaar_window.txt` and entities.
- Fights: `farm.plan_round` — drain when hurt and Scarecrow can't go;
  Empower once per fight whenever castable; Scarecrow when castable;
  Scarecrow not in hand → discard everything but Empower (unused),
  drains, treasure and item cards, then pass. Self-target falls back to
  the combatant frame when its Health child is missing.
- Travel duels: Flee (its confirm box is clicked even though
  `ui.confirm_modal` refuses flee boxes), fight only if two flees fail.
- Pulls walk to 15 units (into the creature); a walk that ended >150 away
  waits 1s for a duel, not 14s.

Live findings (10-10):
- Position: read `client_object.actor_body()` (walk.position). The player
  hook (`client.body`) froze on the first duel's spot after it.
- Keys: the game polls GetAsyncKeyState (imports show it, no DirectInput),
  so held keys go through SendInput (walk.Keys). They only arrive while
  Wizard101 is the active Mac app; Wine's GetForegroundWindow says yes
  regardless. walk.request_focus writes `state/focus.request` on the first
  walk and on any 0-move walk; mac/crashwatch.py raises the game with
  System Events (works from Terminal without extra permission here).
- Visible SpellCheckBox windows are not a duel (7 show outside battle).
- Hook adoption across bot restarts worked live (`written now: none`).

A fight counts when the duel phase says so, or when a hand `SpellCheckBox`,
`CombatantControl`, or `DoneWindow` is visible (the phase lagged ~11s after
contact on 10-06). The wait after contact is 14s.

`chores`/`reagents` (`dayplan`, `duty`, `farm` pulls and camps) are the
Windows day plan; their pulls and camp jumps still teleport, so they stay
refused on the Mac even though zone travel now walks. Garden, fishing, and hatching stay in
the repo and must not be chosen.

## Logs and crash reports

Each `mac/run.sh` run: `logs/runs/<time>-<mode>/` with `bot.log` (DEBUG),
`events.jsonl` (flushed per line: hooks, code writes, walks, clicks, casts,
fights, 10s heartbeat), `hook_session.json`, `code_writes.json`,
`console.log` (Wine stdout/stderr), `run.json`. `mac/crashwatch.py` (macOS
Python, stdlib) watches beside it; on game death, a Wine crash handler, or
a new minidump it writes `CRASH.md` with the dump verdict
(`crashdump.classify`), the last bot events, time since the last code
write, and the game log's error lines, and copies `WizardClient.log`
(rewritten each launch) and the `.dmp` (overwritten each crash).
`activity.log` and `wiz101-auto.log` still exist; loguru `diagnose` is off.

## When it is stuck or crashed

1. `stop` if a bot is running. Do not kill the game.
2. Read the newest `logs/runs/*/CRASH.md` if there is one, then that run's
   `events.jsonl` tail and `bot.log`. `activity.log` is the short version.
3. `python3 mac/crashwatch.py dump <file.dmp>` reads any minidump.
4. Patch the module that owns it: `seeds.py` (seeds loop), `walk.py`
   (movement), `navigate.py` / `navmesh.py` (travel), `farm.py` (the fight and casting), `petdance.py`,
   `hooking.py` (hooks and the session record), `blackbox.py` (logging and
   the guard), `bot.py` (connect and lifecycle), `mac/crashwatch.py`.
5. Pytest and ruff, as above.
6. They restart the bot. Do not hook the game from here.

Unverified live (first real run should confirm): the posted-key walk with
scan codes, walking a whole trip (gates, the Ravenwood world door, the
Spiral Map clicks), sigil walking, hook adoption on a second start, and
whether health/mana refill while standing still out of combat.
