# wiz101-auto

A Wizard101 bot that spends the day on the tedious work. The plan is
[PLAYBOOK.md](PLAYBOOK.md). It reads the game through
[WizWalker](https://github.com/Deimos-Wizard101/Deimos-Wizard101).

Energy (the green globe) goes to the ghulture and the garden first. A full
bar interrupts everything else. Gold is what it does when energy cannot pay
for those. Reagents and fishing happen when a quest asks.

| Work | What runs |
|---|---|
| **Ghulture** | Dance game, read from memory, to adult. A talent outside death-dealer, death-giver, pain-giver, mighty, and armor breaker stops the body. Hatching is decided and not clicked. |
| **Garden** | Boom shrooms until rank 3, then couch potatoes. It goes home when a plant needs care. Spells are not cast blind. |
| **Reagents** | One named reagent from the tracked quest, or `reagent "scrap iron"`. Scrap iron is the Marleybone streets. |
| **Fishing** | Noticed when a fishing quest is tracked. The reel is not played, because a missed cast still spends the energy. |
| **Gold** | Named (Splithoof while seeds are short, then Halfang). Fights are not started. |

> Automation breaks KingsIsle's Terms of Use and accounts do get banned.
> That risk is yours.

## What is not clicked

Hatching, the housing shop, garden spells, the fishing reel, and gold fights
need their windows mapped on a live Windows client. The bot decides each of
them and will not invent the click. The other Pet Pavilion games (Cannon,
Maze, Gobbler Drop, Grumpy Gobblers, Way of the Ninja Pig) are not played.
Only the dance game exposes its move list.

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
| `run` | the day plan (`-m pet` or `-m reagents` for one of those) |
| `pet [--games N]` | dance now, ahead of the plan |
| `reagent "scrap iron"` / `reagent --clear` | gather that reagent, or go back to the tracked quest |
| `fish` / `fish --clear` | a fishing quest is waiting, or it is done |
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
