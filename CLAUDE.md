# wiz101-auto: the day plan

A thinned Wizard101 chore bot. It hooks the official client through WizWalker.
Read `PLAYBOOK.md` before changing what it does. If this file and the code
disagree, the code is what ran and this file is what the next session must
know. Working tree is **0.9.0** (teleport-free Mac rebuild, not yet run live).
Push every change to `origin/main` as it is made (player, 10-14).

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
`./mac/run.sh pet`, `./mac/run.sh seeds`, `./mac/run.sh gather` or `./mac/run.sh peril` (`tracker`: below, hands off). Each walks to its place first
(the Pet Pavilion; Savarstaad Pass, then Boar Camp; the Commons' Portal of Peril sigil), world door included. A bare `./mac/run.sh`
loads gitignored `config.yaml` (local file is `mode: seeds`). Every setting
is also a flag after the mode (`cli.add_setting_flags`, generated from the
config dataclasses: `--seeds-leash` and `--leash`, `--no-sell`, `--hunt
"NAME[:COUNT][@World/Zone]"`; `./mac/run.sh --help`); flags beat the file,
which is optional (the player prefers flags, 10-13). `chores` and
`reagents` are refused on the Mac (`gather` is the on-foot reagent farm). Stop from the menu-bar Wiz item,
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
- Experimental, opt in (`safety.data_jumps: true`, the player asked to test
  it supervised on 10-12): `jump.PositionHook` is WizWalker's movement hook
  without the per-jump patches or collision NOPs. It goes in at connect
  (recorded and adopted like the rest; `cave_size` 128). A jump
  (`client._jump`, `jump.Jumper`) writes only the bot's 21-byte helper
  (x, y, z, flag, our object) and taps W; the game's own set-position call
  takes it. `navigate.walk_in_zone` jumps to goals over `JUMP_MIN` (1500)
  away, then walks the rest. Never beside a creature (700). A first jump
  that does not land (hook never ran, or the server put the wizard back)
  turns jumps off for the run.
  Live 10-12 22:44 (run 20261012-224418-gather): it works. 21 jumps landed
  (up to 43k units, the server kept every one), then the game closed with no
  dump and nothing in its log on a jump fired 0.1s after the previous
  landing, the 4th in such a chain. Suspected cause, unproven: moving again
  before the last landing had settled. Now paced (`jump.quiet_after`: 3s +1s
  per 4000 units, max 7s; no loading screen; the wizard still for 0.6s);
  `jump_start` logs `gap` since the last landing.
  Jump first (10-12 23:53: one wedged-wizard miss turned jumps off and the
  run walked into 14 timeouts): `walk_in_zone` jumps any walk longer than
  `JUMP_MIN` (1000, measured on the graph) and jumps out of any stuck or
  timed-out leg; the landing is the goal, or the nearest graph point within
  1500 with no creature within 700 (`landing_spot`). Arming taps W, S, A, D
  (the hooked call only runs when the wizard moves). Jumps go off for the
  run only after `MAX_MISSES` (3) misses in a row.
  Gather-only pilot (player, 10-12 night): `bot.jumps_on` = data_jumps and
  mode in `JUMP_MODES` ("gather"); seeds and pet never install the jump hook
  and walk. In gather with jumps, `Gatherer.pilot_step`: one zone (the
  wizard's, if it lists spots), `jump_tour` jumps to each spot or a clear
  graph point beside it and onto each reagent (X), no walking in the zone,
  then `wait_for_respawn` (`gather.respawn_minutes`, 4); jumps going off
  stops the run (it does not fall back to walking). Pacing 5-10s. Each run
  began with ~40s where taps moved nothing though focus said yes; a jump now
  first proves a tap moves the wizard (`keys_reach_game`, asking for focus,
  up to 90s), and taps that move nothing are not counted as misses.
- Walking (`walk.py`) writes yaw (heap data) and posts W/A/D with real
  scan-code lParams, re-aims every 0.3s, does a measured final approach,
  backs up (S), turns (A/D) and walks on when stalled (the game has no strafe key: Q is the quest list, E the emote wheel, per Bin/config.xml), and reports reached/fight/interrupted/stuck/lost/timeout.

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
- A stuck leg is remembered for an hour (`navigate._trouble`): graph edges
  within 250 of that spot are avoided on every later walk in the zone, and
  the wizard first walks back to the last waypoint it reached. On 10-12 the
  Boar Camp -> Northguard gate route wedged at GH_Hero (-20147, 11978) on 8
  trips (96 stuck legs); the replan blocked a non-edge (straightened legs
  span several edges) and the "sidesteps" were A/D turns, so it never left.
- Go Home only when the start zone has no route at all. A route whose gate
  failed is a travel failure (the caller retries later): on 10-12 01:51 no
  key reached the game (every walk moved 0, focus said ok), the gate
  "failed", Go Home landed in the house (no nav graph) and it stuck there.
  A trip that never moved says so ("keys are not reaching Wizard101").
- A leg that moved 0 first checks the keys (`walk.wait_for_keys`: tap S/W,
  ask for focus, up to 90s). Keys that arrive only after the wait are not a
  wall: no trouble spot, the same walk is planned again (3 times per walk).
  Keys that never arrive: `walk_in_zone` returns "no keys". 10-16 09:00
  (run 20261016-090047-seeds): started in Savarstaad ~25000 from Boar Camp,
  four 0-move legs before focus, the walk "stuck", and seeds made that spot
  home and waited for boars that were not there. `seeds.settle` now keeps
  no home farther than `CAMP_NEAR` from the camp; it retries every 20s.
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
walk onto the sigil, dance while the globe can pay on the stage `pet.stage`
picks (`petplan.pick_stage`). Stages are equal in difficulty and each trains
stats besides XP (player, 10-11): Wizard City +4 agility, Krokotopia +1 str
+3 agi, Marleybone +1 int +3 agi, MooShu +1 int +2 agi +1 will, Dragonspyre
+2 agi +2 int. "lowest" weighs each stage by how far its stats sit below
their caps, read from the stage window's stat block ("Strength: 120/255",
saved as `trained` in state/pet.json); unread, it rotates, and the window
tree goes to `pet_tracks_window.txt` once a run. Then feed the first snack
after each win, wait 5 min between energy checks. A duel is fled
(`client._on_fight`; played only if Flee fails twice). Its `send_key`/`goto`
hold W/A/S/D through walk.Keys (SendInput); X at the sigil falls back to a
SendInput X when the posted one opens nothing. A
failed walk is retried every 2 min and posts a Mac notification.

`mode: seeds` (`seeds.SeedFarm`): walk to Savarstaad Pass if elsewhere;
home is where the run starts if within 6000 of Boar Camp (-23200, 14300),
else it walks to the camp and that is home. Pulls Troubled Warriors first, then Splithoof, nearest first,
within `seeds.leash` of home. Each round: Empower, then Scarecrow; pass
while the next is not ready; a death drain only under ~60% health when
that next card cannot be cast. Sacrifice is not a heal. Deathblade is not
cast. Low health or mana: potion, wisp in reach, then stand still up to
`seeds.rest_minutes`. Outside the pass (a defeat): walk back.

Updated after the 10-10 live runs (these supersede the line above):
- Low health/mana with no potion and no wisp in reach: Mark Location at
  the camp (HUD `MarkButton`), walk to Northguard, walk onto wisps (each
  again after 20s, they respawn fast) until the globe is full
  (`seeds.northguard_full` 0.98), then top up the other one, all within
  `seeds.northguard_minutes` (10), then Recall (`RecallButton`; button names
  from Root.wad GUI/HUDWindow.gui), else walk back (`seeds.northguard_trips`).
  Until 10-12 night it left at the camp's "ready" mark (55% mana): a trip
  every ~6 min with 120 max mana.
- Bag two slots from full: `selling.quick_sell` in place, no travel (the
  Bazaar trip died at the Northguard world door on 10-11 04:41 with the game
  unfocused). B opens the backpack (`InventorySpellbookPage`), its
  `QuickSell_Item` opens SellQuickSell.gui's `buyWindow`: rows `shoplist0-9`
  with `num0-9/Selected` boxes, `leftscroll`/`rightscroll`,
  `labelScrollPage`, `sellAction`, `AllAction`; then
  `QuickSellConfirmationWindow` (`TotalNumber`, `PetNumber`, `SellButton`)
  and `QuickSellWaitingWindow` (names from `_Shared-WorldData.wad` GUI
  files). Each view first gets a category ticked (`selling.CATEGORIES`:
  gear "All", housing categories except Seed, jewels): the list shows only
  the ticked one and the game keeps the last. 10-21 03:16 (run
  20261020-191433-seeds, 459 fights) opened on one treasure card, none of 80
  new items was listed, 0 sold, and the run stopped at 198/200. Ticks only names new this run (LootBook); Select All only when the
  rows have no names and number no more than this run's new gear. Rows do
  carry names (10-23 dump: "Bodkin of Fury"). The first open writes
  `quick_sell_window.txt` to the run folder.
  10-23 (run 20261022-190354-seeds, 460 fights, 7 h): 9 passes looked at all
  270 rows and ticked 0: wizwalker's `maybe_checked` (offset 1028, a
  SpellCheckBox's) read every box unchecked, and `_tick` then clicked the row
  too. Now one click on the box, never the row after; a tick that does not
  read checked counts as "unread" and the sale then needs the confirm box's
  `TotalNumber` to agree. The first tick logs which box bytes changed
  (`checkbox_probe`) to find the real flag. 8 of 9 passes had a boar duel
  start within ~20s at the camp (the list kept paging, the duel unplayed):
  `_sell_listed` stops on a fight each page, and a full bag is sold in
  Northguard (`SeedFarm.sell_away`: trip, sell, mana, Recall); a mana trip
  sells when the bag is within `SELL_AHEAD` (20) of full.
  10-26 (run 20261025-231646-seeds, 643 fights, 10 h): selling works (6 rows
  ticked, the summary agreed, potatoes kept), but the game closes the list
  after each sale and a pass ended there: 6 of ~148 items sold, so a trip
  every ~40 min. `quick_sell` now runs `_sell_round` again (`MAX_ROUNDS`)
  until one sells nothing; still one page per Sell (unknown whether ticks
  survive a page turn). The probe flipped byte 1036 0->1: `CHECKED_AT`.
  The run ended on a flee during the sell trip: its load to Northguard hung
  (ConnectComplete, AppCloseConnection state 5, nothing more; the transient
  freeze) and `world.wait_for_loading` waited silently 74 min until the game
  closed. It now warns and sends `needs_player` after `LOADING_TELL` (5 min).
- 10-26 22:43 (run 20261026-224338-seeds): keys worked all through
  22:57-23:58 with the display off and the Mac idle (two Northguard trips);
  they died at 23:59 a minute after the display woke and stayed dead 00:02-00:12+
  while the fixed frontmost check said Wizard101 was in front every time. Not
  the lock screen (unlocked), not Secure Input (no kCGSSessionSecureInputPID).
  An app switch (Finder, then Wizard101) did not bring them back. They came
  back only after the player had been at the Mac (a click on the window, it
  seems). crashwatch now also tries AXRaise/AXMain on the game window
  (`raise_window`); that needs Accessibility for Terminal, else focus.log says
  so. Unproven that it fixes it.
- The couch potato count now reads the bag (every 5 fights); before 10-11 it
  read a state file only the drops hook updated, so it said 0 all night.
- Fights: `farm.plan_round` — drain when hurt and Scarecrow can't go;
  Empower once per fight whenever castable; Scarecrow when castable;
  Scarecrow not in hand → discard everything but Empower (unused),
  drains, treasure and item cards, then pass. Self-target falls back to
  the combatant frame when its Health child is missing.
- Defeats (10-11 afternoon, ~15 of them): mana ran dry mid-fight, Scarecrow
  showed "not castable" for 10+ rounds while the bot passed, the boars won,
  and the wizard woke in Northguard and walked straight back. client.stats
  never once reported low mana or health in any run (no `recover` event
  ever fired), so the out-of-fight checks never ran. Now each round reads
  the wizard's own combatant (`farm._own_vitals`: health, mana, pips) and
  logs it on the cast/pass line; `farm.flee_reason` flees when Scarecrow is
  held but dead and mana < 7 (or pips ≥ 7 two rounds running, or 7 rounds
  with nothing read), or health < 30% with nothing castable to heal.
  `seeds` then owes that need (or both, on waking in Northguard within 90s
  of a fight) and refills it before the next pull; an owed need counts an
  unread globe as empty, and stats that call it full right after the duel
  ran dry are dropped for the run (`stats_untrusted`).
  Live 10-11 20:00: the combatant read works (health 2744/2744, mana
  6/120: max mana is only 120, ~8 fights); the flee worked, and a fled
  Savarstaad duel lands in Northguard (so that is not a defeat: `last_fled`);
  client.stats read `?` all run. Out of a fight `seeds.ratios` now falls
  back to the HUD globe texts (`textHealth`/`textMana`, HUDWindow.gui) over
  the maxima last read in a duel (`farm.last_vitals`).
  Overnight 10-12 (run 20261012-022316, 357 fights, 0 defeats): the HUD read
  works (recover at ~25% mana, Northguard mana wisps refill to ~70% in
  ~30s, ~45 trips). At 10:19 bot time a flee's loading screen never ended;
  the bot waited 2 h ("in a dialogue or a loading screen"). Now a wait over
  `STUCK_BUSY` (5 min) logs a warning and a `needs_player` notification.
  bot.log times run 1 h ahead of the Mac clock.
- 10-22 (run 20261022-161220-seeds): pulls moved 0 from the start (keys not
  reaching the game; boars walked into the standing wizard for 20 duels),
  then none for 15 min, then the server sent MSG_Disconnect_Afk ("disconnected
  due to inactivity") and the bot waited for boars at the login screen for 2 h.
  Now `gamelog.LogWatch` (heartbeat loop, every mode) reads new
  WizardClient.log lines and stops the run on `MSG_Disconnect` or a
  `GUI_Connection` message box (not AppCloseConnection: every zone change logs
  it); a 0-move pull waits for keys (`walk.wait_for_keys`); crashwatch's focus
  log now checks the frontmost app (its AppleScript did not parse until 10-23; exit 0 of the raise said "ok" 100 times
  while no key arrived). Other "short" runs (10-17..10-22) were stopped by
  the player while farming; 10-17 07:43 was the Mac sleeping at 1% battery.
- 10-13 10:58: another player joined fight 67; the game's "Endorse a Wizard"
  window (`EndorsementWindow`) came up and held the wizard still in Northguard
  for 27 min (every walk moved 0, Recall did nothing). `ui.close_endorsement`
  clicks its `CloseEndorsementWindowButton` after every fight and whenever a
  walk moves nothing, before asking for focus.
- Travel duels: `farm.flee` (its confirm box is clicked even though
  `ui.confirm_modal` refuses flee boxes), fight only if two flees fail.
- Pulls walk to 15 units (into the creature); a walk that ended >150 away
  waits 1s for a duel, not 14s.

Hunts (10-13, the player's Storm quest goals: 30 Charmed Slaves, 30 O'Leary
Prowlers): `seeds.hunts` lists creatures to defeat in turn instead of the
boars (`seeds.Hunt`, `hunts_from`). Each hunt pulls only that creature
(name needle like `charmedslave`, matched on template or plate name),
in its zone (`zone`, or `KNOWN_HUNTS`, else the zone the wizard is in at
the start). `KNOWN_HUNTS` from a scan of the game files: Charmed Slave in
Krokotopia/KT_Pyramid/KT_Chamber (Chamber of Fire; its map labels a
"Chamber of the Charmed"), O'Leary Prowler in
Marleybone/MB_ScotlandYard/MB_KnightsCourt (an "OlearyProwler" stand-in
object); both have walk routes (navcheck). With no camp a hunt roams:
nothing to pull for `ROAM_AFTER` 8s, it walks to the next of `roam_points`
(one graph node per 3500 square of the wizard's graph part, nearest-next;
42 in Chamber of Fire, 81 in Knight's Court) and pulls within the leash of
where it stands. Refills by wisps in reach then rest
(Northguard trips only where a hunt has a `refill` zone). The goal is only
the command's count (player, 10-14: the Storm quest is odd, never trust its
tracker): each duel not fled adds every enemy in it matching the hunt
(`farm.last_foes`, read each round by owner id and name; `kills_in`); a
defeat found afterwards takes that duel back. Then the next hunt; a next
hunt with no known zone stops the run and asks the player to walk there.
After the last the run stops (`needs_player`
"done with every hunt"). `--hunt seeds` (or "boars") is the boar seed farm
as a step (`SEED_WORDS`; until stopped unless given a count), so
`--hunt "O'Leary Prowler:30" --hunt "Charmed Slave:30" --hunt seeds` ends on
the boars. Unverified live.
10-14 overnight: `self.hunt` (the current Hunt) hid the `hunt()` method; every
step failed for 9.5 h while the wizard stood idle. The method is
`hunt_round`; `run_steps` stops the run after `SAME_ERROR_STOP` (20) of one
error in a row.
Live 10-14 10:46 (run 20261014-104641-seeds): walked Wolfminster Abbey to
Knight's Court (64s), won 2 prowler duels. It first chased
"MB-Standin-OlearyProwler" (scenery; `mob_rank` now skips "standin"). The
tracker never showed the goal (it is no longer read). Duel 3: one
Scarecrow left a prowler up, discards emptied the deck, pips fell to 0,
4 min of passing, fled at 19% health. Now after one Scarecrow a round with
none castable casts a drain or damage card at an enemy (`_cast_attack`).
The flee's transfer to MB_Hub hung on the loading screen (game log:
ConnectComplete, then AppCloseConnection state 5, nothing more), like
10-12 10:19; the game closed 8 min later with no dump. The player calls
that freeze transient; do not engineer around it.

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

`mode: gather` (`gather.Gatherer`, `./mac/run.sh gather`): farm
`gather.reagent` (Black Lotus by default) on foot. Spawn spots come from the
zone files (`reagent_spots.py`): `pathData.xml` "Path-<Reagent>" lists
path-node ids, `pathNodeData.bin` gives each node's x, y, z; the server
fills some of those at random. `python3 -m wiz101_auto.reagent_spots
[World] [Reagent]` lists them offline (MooShu Black Lotus: Shirataki Temple
15, Kishibe Village 16, Cave of Solitude 16, Tree of Life 20, Tatakai
Outpost 15, Ancient Burial Grounds 13, Village of Sorrow 12, Crimson Fields
9, Jade Palace 1). Zones are ranked by spots per minute of walking, the best
zone's own area first (default: Shirataki, Kishibe, Cave of Solitude, all
off Shoshun Village). Each zone: a nearest-first tour of its spots; routes
keep 800 from loaded creatures (`navigate` `avoid`; a creature within 500 of the next leg replans before it is walked); the tour follows walking distance on the zone graph; a spot with a creature
on it waits until later in the tour; any loaded reagent within 2500 is
walked onto and X'd (gone afterwards = picked). Duels: `_on_fight` (flee,
play if two flees fail). Sightings and picks per spot go to
`state/gather.json`, to learn which spots really spawn. Unverified live:
the reagent entity's object name (matched loosely: "blacklotus" in it, not
"spawn"/"path"), the X pickup, the Shirataki "dungeon" X door and its
"dungeonExitConfirm" way out (walked, then the box confirmed), and whether
Shirataki is an instance whose reagents re-roll on each entry. No realm
hopping yet.

`chores`/`reagents` (`dayplan`, `duty`, `farm` pulls and camps) are the
Windows day plan; their pulls and camp jumps still teleport, so they stay
refused on the Mac even though zone travel now walks. Garden, fishing, and hatching stay in
the repo and must not be chosen.

## Portal of Peril: `mode: peril` (10-30, unverified live)

The player asked (10-30) for the wizard to ride along with other players
through the Portal of Peril as often as the day allows, fighting with
Mass Feint first, then Empower and Scarecrow. Researched, not seen: Madam
Magda's event, one week a month (the Great Clock Gauntlet until Mon
2026-11-02), level 30+, sigil by her tent in the Fairegrounds corner of the
Commons (`WizardCity/WC_Hub`, near the Golem Court gate). A credit per
entry: 2 a day, 4 for members; they expire when the event closes. Tiers
50/100/150, everyone scaled to the highest wizard on the sigil. A wizard
defeated inside can Dungeon Recall in without a credit (not used yet).

`peril.Rider`: walk to the Commons; find the sigil live (object names with
peril/gauntlet/portal, a "sigil" nearest Madam Magda, else Magda; saved to
`state/peril.json`; every object to `peril_entities.txt`); stand on it.
Once a day, with nobody else on it, use it and click "Claim" if shown
(the window goes to `peril_sigil_window.txt`; it then closes menus). It
never starts a run. A message box about entering is accepted; one saying
the credits are gone stops the run for the day (`box_kind`). A small A/D
turn every 4 min against the AFK kick. Any zone reached from the sigil is
"inside" until the Commons or a house: walk into a duel circle (the group's;
the instance is private), else follow the nearest other "Player Object"
(`peril.follow_gap`); when they are all out of sight, walk to the last spot
and use what is there (X prompt; else W); alone `peril.alone_minutes` (3):
Go Home, yes to the leave box, walk back. Inside fights:
`farm.plan_dungeon_round` (Mass Feint, Empower once, Scarecrow; Mass Feint
again before each later Scarecrow; never discarded) and no flee
(`_cast_combo(dungeon=True)`). Back in the Commons a run is counted per day;
`peril.runs` (0: until out of credits) caps it. First live run should
check: the sigil's object name, what using it opens (claim, tier window, or
a solo start), the entry box text, how doors between rooms work, and how a
run ends.

## Spiral Progress: `mode: tracker` (10-27, unverified live)

The player asked for a page beside the game showing story progress per
world, updating as they play. `./mac/run.sh tracker` (Wizard101 open, the
player playing) starts the page (`mac/spiral_web.py`, macOS python3, stdlib,
read only, http://127.0.0.1:8101/, stays up after the tracker stops;
`./mac/run.sh web` starts only the page) and attaches hands off
(`bot.HANDS_OFF`): the same recorded/adopted data hooks, then no mouse hook,
no relog, no keys, no clicks, no button-up at stop, and a server logout does
not stop it. `tracker.track_loop` only reads, every 2s: the game's quest
list from memory (WizWalker's `client.quest_manager()`, a one-time pattern
scan then a std::map quest id -> QuestData: `name_lang_key` via
`names.lang_name`, `mainline`; `client.quest_id()` is the tracked quest;
no hook, no write, Deimos reads the same), the HUD goal (`ui.quest_goal`),
and the zone. It writes `state/tracker_live.json` and
`state/quest_progress.json` (`spiral.Progress`: the furthest story quest
seen per world, only ever forward; story quests on no list kept as
`unlisted`). Done (`spiral.build`): everything before the furthest story
quest of its world, every world before the furthest world reached, and the
furthest quest itself once it has left the book. Side quests are not
tracked (the player, 10-27: they would always be wrong); the quest-book UI
reader, side-quest lists and hand ticks were removed. A world can be
marked finished by hand (its "Mark finished" button, or `python3
mac/spiral_web.py --finished Wintertusk`; `state/worlds_done.json`, only the
page writes it): the optional worlds (Grizzleheim, Wintertusk, Wysteria,
Catacombs) are never inferred (player, 10-27: all three Arc 1 ones done).
The page server restarts itself when its own code changes (`/version`, a
hash of spiral_web.py and spiral.py), and `--ensure` stops an older one on
the port: on 10-27 a server left running from before "Mark finished"
served the new page with the old code (the button got 501, marks ignored). Lists:
`docs/quests/<World>.txt` (Spiral Tracker's copy-paste format, restored
from before 10-05). First live run should check: names resolve (the
`quest_book` events list them), and the mainline flag is set on story quests.

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
