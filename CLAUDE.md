# wiz101-auto: the day plan

A thinned Wizard101 chore bot. It hooks the official client through WizWalker.
Read `PLAYBOOK.md` before changing what it does. If this file and the code
disagree, the code is what ran and this file is what the next session must
know. Latest shipped version is **0.8.16** (`a6848c1` on `origin/main`).

Private repo only: `git@github.com:rhelgason/wiz101-chores.git` (`origin`,
fetch and push). `upstream` is `git@github.com:szatcg/wiz101-auto.git` (fetch
only; push is disabled). Never open a pull request. Never push upstream.

Do not commit `config.yaml`, `state/`, logs, or `.venv`. Never read or print
the game password.

## Where it runs

This machine is macOS. Wizard101 is the official app's Windows client inside
`Wizard101.app`'s own Wine bottle, not native Mac and not CrossOver.app.
Wine is `/Applications/Wizard101.app/Contents/SharedSupport/wizard101/bin/wine`.
`WINEPREFIX` is `~/Library/Application Support/Wizard101/Bottles/wizard101`.
Bottle Python is `~/Library/Application Support/wiz101-chores/python/python.exe`.
`mac/run.sh` rewrites `python313._pth` (that file ignores `PYTHONPATH`) and
installs `mac/sitecustomize.py` so Wine's missing `CopyFile2` does not abort
Python. Do not enable `_wmi`, do not use `get-pip.py`, and do not pip-build
wizwalker. Do not patch `site-packages`; shim from this repo.

Do not start Wizard101, do not kill it, do not `wineserver -k`, and do not
attach or hook while the user is in the game unless they asked to test.
Do not force-kill Python: that leaves hooks in the client. A `pgrep -f`
whose pattern also appears in the bash command matches itself.

Start: Wizard101 logged in, wizard standing in the world, then
`./mac/run.sh seeds` (or `pet`, `chores`, `reagents`). A bare `./mac/run.sh`
loads gitignored `config.yaml` (local file is `mode: seeds`). Stop from the
menu-bar Wiz item, `Stop Wizard Bot.command`, Ctrl+Shift+Q, or
`state/stop.request`. A second `./mac/run.sh` while one bot is running is
refused. A running process does not pick up edits.

Tests on the Mac, not the bottle:
`/tmp/wiz-test-venv/bin/python -m pytest -q` and
`/tmp/wiz-test-venv/bin/ruff check .`. Tests must not import `duty`,
`reagents`, `bot.py`, or `travel.py` (those import wizwalker).

## What it is supposed to do

`dayplan.choose` for chores and pet: dance while the green pet globe can pay,
feed a snack after each win, then farm. Garden, fishing, and hatching stay
in the repo and must not be chosen. More than 20 couch potatoes switches
chores farming to Halfang; otherwise Splithoof. Deaths, low health, a missed
zone, and an empty snack pile do not end the session.

`mode: seeds` does not dance. It fights Troubled Warriors and Splithoof
Barbarians in Savarstaad Pass (`Grizzleheim/GH_Hero`) until the user stops
it. Each round casts **Empower, then Scarecrow**. Deathblade is not cast.
Pass while the next of those is not ready. A death drain is cast only when
health is under about 60% and that next card cannot be cast. Sacrifice is
not a heal. Only cards in the equipped deck.

A fight counts when the duel phase says so, or when a hand `SpellCheckBox`,
`CombatantControl`, or `DoneWindow` is visible. A memory miss in the phase
is reported as ended, which is how a visible battle was skipped (23:20).
The wait after contact is 14s (`FIGHT_WAIT_POLLS` 35). Fights in the 23:34
session showed up about 11s after the wizard stood on the boar.

After the wizard is within 80 of a mob, no teleport for 15s (`JOIN_HOLD`).
The 23:34 client died on an unstick teleport a few seconds after an 8s
"did not start a fight". W moving 0 is often the duel locking movement.
Posted W (`petdance.post_key`) sometimes moves in the overworld and
sometimes moves 0. The SendMessage fallback also moved 0. Camp teleports,
especially onto the Northguard gate, have closed this Wine client. That
gate is not a camp. Jumps wait `CAMP_GAP` (8s) and skip it.

Mana under about 30% rests at Northguard (`Grizzleheim/GH_MainHub`) until
about 55%. A bag with two free slots goes to Elik at the Wizard City Bazaar,
presses X, and sells only names gained this run. Couch potato seeds, equipped
gear, and anything in the bag at run start are not sold. The bazaar click
and the spiral-door click are not verified live. A failed spiral map from
the dorm (23:16) was followed by the client closing.

## Rehook does not work

Deferred on 2026-10-06. The user said to stop for the night. Do not keep
patching it until they ask.

`rehook.py` (0.8.13 through 0.8.15) saves original hook bytes to
`state/hooks.json`, restores them for the same pid and module base, refuses
a second bot, and tries not to quit the game when the pattern scan stalls.
That has not removed the restart. Live evidence: starts sat on
"activating hooks" with no "connected" (23:21 and 23:23); the next error
was `MemoryWriteError` and "original bytes were not recorded", which told
them to fully quit once; 23:26 connected only after that new game process.
The user confirmed again after 0.8.16 that rehooking still is not working.

Until a log shows a same-window restart ("saved hook bytes already match"
or "put the original code back", then "connected") without Wizard101 being
quit, assume a quit and a fresh login are still required. If the client
process has already exited, the window is gone: start the game, log in,
stand in the world, then `./mac/run.sh seeds`. Rehook cannot attach to a
dead process.

## When it is stuck

1. `stop` if a bot is running. Do not kill the game.
2. Read `activity.log` first, then `wiz101-auto.log` and `state/status.json`.
3. Patch the module that owns it: `seeds.py` (the seeds loop), `farm.py`
   (pulls, teleports, the combo), `bot.py` (connect and chores),
   `rehook.py` (only when they ask), `petdance.py`, `travel.py`,
   `dayplan.py`.
4. Pytest and ruff, as above.
5. They restart the bot. Do not hook the game from here.
