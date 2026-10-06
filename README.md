# wiz101-auto

A Wizard101 **chore** bot. It reads the game through
[WizWalker](https://github.com/Deimos-Wizard101/Deimos-Wizard101) and does the
repetitive side work:

| Chore | What it does |
|---|---|
| **Pet dance** | Walks to the Pet Pavilion, plays the dance game until energy runs out (moves are read from memory), feeds the first snack after each win, then walks back. |
| **Reagents** | Picks up reagents and unlocked chests in the current zone, then in zones a gate or two away. It will not teleport onto an enemy. |

This tree was cut down from a quest-and-combat bot. It does not follow the
quest arrow, play battles, farm dungeons, build decks, or train spells.

> Automation breaks KingsIsle's Terms of Use and accounts do get banned.
> That risk is yours.

## What is not here

The friend's bot never played these, and they are not guessed at here:

- The other Pet Pavilion games (Cannon, Maze, Gobbler Drop, Grumpy Gobblers,
  Way of the Ninja Pig). Only the dance game exposes its move list in memory.
  The others are live aiming and steering games. Pressing keys without that
  read spends energy and fails the game.
- Fishing.
- Gardening.

Those need a Windows session with the game open, so the real windows and
memory can be mapped the way the dance game already is.

## Setup (Windows)

1. Python 3.13+ and Git for Windows.
2. Log into Wizard101 and stand in the world.
3. Double-click `wiz101.bat`, or from the repo:

```powershell
.\wiz101.bat start --supervise
.\wiz101.bat status
.\wiz101.bat logs -n 100
.\wiz101.bat stop
```

`stop` unhooks the game. Killing the process leaves the hooks in, and the
next start fails until Wizard101 is fully restarted.

**Keys:** Ctrl+Shift+Q stops. Ctrl+Shift+P pauses.

`config.yaml` is created from `configs/chores.yaml`. See `config.example.yaml`.

| Command | What |
|---|---|
| `run` | pet dance when energy is full, and reagent sweeps (`-m pet` or `-m reagents` for one of them) |
| `pet [--games N]` | ask the running bot to go dance now |
| `pet-alt start` | dance-only bot on a second Wizard101 window that is already in the Pet Pavilion |
| `inspect` | zone, position, nearby reagents (bot stopped) |
| `inspect --windows` | plus the UI window tree |
| `relog` | quit to character select and Play again |
| `set-login` / `restart-game` | saved login, used when `start --supervise` restarts a frozen game |

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

The matching and config tests run on macOS. Anything that hooks the game
needs Windows.

## License

GPL-3.0-or-later. The dance-game hook is from Deimos
(`dance_game_hook.py`, by peechez), also GPL-3.0.
The quest, combat, and dungeon code this started from is not in this tree.
