# wiz101-auto: the chore bot

A Wizard101 bot (Python, Windows only) that hooks the game through WizWalker.
It plays the **pet dance game** and **picks up reagents**. It does not quest,
fight, farm dungeons, or build decks. Do not add that back unless asked.

Fishing, gardening, and the other pet minigames are not implemented. They were
not in the code this was cut from. Do not invent click paths for them.

## Environment

- Windows. Wizard101 must be logged in, wizard standing in the world.
- Python venv at `.venv`. Entry: `.venv\Scripts\python -m wiz101_auto <command>`
  or `.\wiz101.bat <command>`.
- Settings: `config.yaml` (git-ignored), copied from `configs/chores.yaml`.

## Commands

| Command | Use |
|---|---|
| `start [--supervise]` | background. `--supervise` restarts after crashes, and restarts a frozen game at most once every 15 minutes |
| `stop` | clean stop (unhooks). Always use this. Force-kill only after 30s |
| `restart [--supervise]` | stop + start |
| `status` / `logs -n 200` | heartbeat, zone, task, recent log |
| `pet [--games N]` | ask the running bot to dance now |
| `pet-alt start\|stop\|status` | dance-only bot on the other window (already in the Pet Pavilion) |
| `inspect` / `inspect --windows` | what it sees (bot stopped) |
| `relog` | character select and Play again (bot stopped) |
| `set-login` / `restart-game` | Credential Manager login. Never read or print the password |
| `screenshot` | game window to `state/screenshot.png` |

Ctrl+Shift+Q stops and must not be restarted. Ctrl+Shift+P pauses.

## When it is stuck

1. `stop`.
2. Read `wiz101-auto.log`, `activity.log`, and `state/status.json`.
3. Patch the module that owns the behaviour: `petdance.py` (dance), `reagents.py`
   and `collect.py` (pickups), `travel.py` (gates, home, Spiral Map), `bot.py`
   (connect and the loop).
4. `.venv\Scripts\python -m ruff check .` and `.venv\Scripts\python -m pytest -q`.
5. `start --supervise` and confirm the log.

Never commit `config.yaml`, `state/`, logs, or `.venv`.

This fork does not pull or open pull requests against the original quest bot.
If you publish, the repo stays private.
