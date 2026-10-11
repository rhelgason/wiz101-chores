# wiz101-auto

A Wizard101 bot that spends the day on the tedious work. The plan is
[PLAYBOOK.md](PLAYBOOK.md). It reads the game through
[WizWalker](https://github.com/Deimos-Wizard101/Deimos-Wizard101).

Energy (the green globe) pays for dance games. When it cannot, the bot
farms Splithoof Barbarians in Grizzleheim for couch potato seeds. More than
20 couch potatoes switches that to Halfang for gold. It keeps going until
you stop it. A death uses a potion or a health wisp and goes back.

| Work | What runs |
|---|---|
| **Dance** | The dance game, while energy can pay. The first snack is fed after each win. |
| **Seeds** | Splithoof Barbarians, until the bag has more than 20 couch potatoes. |
| **Gold** | Halfang after that. Death drains when health is under about 60%. |
| **Garden, fishing, hatching** | Paused. The code is still there. |
| **Reagents** | Only in reagent mode. |

> Automation breaks KingsIsle's Terms of Use and accounts do get banned.
> That risk is yours.

## What is not clicked

The barn, a hatch, a garden spell, and a fishing cast are not chosen.
The other Pet Pavilion games (Cannon, Maze, Gobbler Drop, Grumpy Gobblers,
Way of the Ninja Pig) are not played. Only the dance game exposes its move
list. Death heals only work when those cards are in the equipped deck.

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

**Stop:** the menu bar item **Wiz on**, or double-click `Stop Wizard Bot.command`,
or `python -m wiz101_auto stop`. That unhooks the game. Ctrl+Shift+Q does the
same from inside the bottle. Ctrl+Shift+P pauses.

On this Mac the game is the Windows client inside Wizard101.app's own Wine,
and only two modes run there, both without teleports:

```bash
./mac/run.sh pet     # walks to the Pet Pavilion and the dance sigil, from anywhere
./mac/run.sh seeds   # walks to Boar Camp in Savarstaad Pass, from anywhere
python3 mac/navcheck.py --trip WizardCity/WC_Streets/Interiors/WC_PET_Park Grizzleheim/GH_Hero
```

Travel walks the game's own paths (each zone's `zone.nav`) and takes the
spiral door between worlds.

Each run writes `logs/runs/<time>-<mode>/`. If Wizard101 dies during a run,
`CRASH.md` in that folder says what the bot was doing, what the game's crash
dump points at, and what the game log said. See PLAYBOOK.md, "Why the Mac
client crashed".

`config.yaml` is created from `configs/chores.yaml`. See `config.example.yaml`.

| Command | What |
|---|---|
| `mac/run.sh pet` / `mac/run.sh seeds` | on this Mac: that mode inside the Wizard101 bottle, the menu bar stop, and the crash watcher |
| `python3 mac/crashwatch.py dump <file.dmp>` | read a Wizard101 crash dump |
| `stop` | unhook and quit, including from the Mac side |
| `run` | the day plan (`-m pet`, `-m reagents`, or `-m seeds`) |
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
