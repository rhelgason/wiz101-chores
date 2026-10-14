#!/bin/bash
# Start the bot inside Wizard101.app's own Wine bottle, with the menu-bar stop
# and the crash watcher.
#
#   mac/run.sh pet        dance games in the Pet Pavilion
#   mac/run.sh seeds      Savarstaad seed farm (start in Boar Camp)
#   mac/run.sh gather     reagent farm (Black Lotus by default)
#   mac/run.sh            whatever config.yaml says (pet or seeds on this Mac)
#   mac/run.sh doctor     versions and paths
#   mac/run.sh --help     every setting as a flag, e.g.
#     mac/run.sh seeds --hunt "Charmed Slave:30" --hunt "O'Leary Prowler:30"
#     mac/run.sh gather --reagent "Black Lotus" --zones "Shirataki Temple" --data-jumps
#     mac/run.sh seeds --leash 4000 --no-sell
# Flags override config.yaml, which is optional (read when it exists).
#
# Every run gets logs/runs/<time>-<mode>/: bot.log, events.jsonl, console.log,
# hook_session.json, run.json, and CRASH.md if the game dies while it runs.
#
# The game is a 64-bit Windows exe. macOS Python cannot hook it. CrossOver.app
# is a different Wine from the one bundled in Wizard101.app; this script does
# not use it, and it does not point that Wine at the game's bottle.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WINE="/Applications/Wizard101.app/Contents/SharedSupport/wizard101/bin/wine"
PREFIX="$HOME/Library/Application Support/Wizard101/Bottles/wizard101"
PY="$HOME/Library/Application Support/wiz101-chores/python/python.exe"
MACPY="/usr/bin/python3"

if [[ ! -x "$WINE" ]]; then
  echo "Wizard101.app is not installed, so there is no Wine that shares the game."
  echo "macOS itself cannot hook Wizard101."
  exit 1
fi
if [[ ! -d "$PREFIX" ]]; then
  echo "The Wizard101 bottle is not at:"
  echo "  $PREFIX"
  exit 1
fi
export WINEPREFIX="$PREFIX"
export WINEDEBUG="${WINEDEBUG:-fixme-all}"

"$ROOT/mac/build-stop.sh"
if ! pgrep -f "$ROOT/mac/WizStop" >/dev/null 2>&1; then
  "$ROOT/mac/WizStop" "$ROOT" >/dev/null 2>&1 &
  disown || true
  echo "menu bar: Wiz on. Stop is in that menu."
fi

if [[ "${1:-}" == "doctor" ]]; then
  echo "Wine (Wizard101.app, not CrossOver.app):"
  "$WINE" --version
  echo "bottle: $PREFIX"
  echo "client: $PREFIX/drive_c/ProgramData/KingsIsle Entertainment/Wizard101/Bin/WizardGraphicalClient.exe"
  echo "machine: $(uname -m) $(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
  if [[ -f "$PY" ]]; then
    echo "windows python:"
    "$WINE" "$PY" -c "import sys; print(sys.version)"
  else
    echo "windows python: not set up yet. Run mac/setup-python.sh"
  fi
  exit 0
fi

if [[ ! -f "$PY" ]]; then
  "$ROOT/mac/setup-python.sh" || exit 1
fi

cd "$ROOT" || exit 1
# Wine 8 aborts on CopyFile2. This file makes shutil use a normal copy.
cp "$ROOT/mac/sitecustomize.py" "$(dirname "$PY")/sitecustomize.py"
# The embeddable Python ignores PYTHONPATH while python313._pth exists.
# That file is the module path. Point it at this checkout on every start.
WINSRC="$("$WINE" winepath -w "$ROOT/src")"
PTH="$(dirname "$PY")/python313._pth"
printf 'python313.zip\n.\nimport site\n%s\n' "$WINSRC" > "$PTH"

CONFIG="config.yaml"
MODE=""
case "${1:-}" in
  "" | -* ) ;;
  pet|seeds|gather) MODE="$1"; shift ;;
  chores|reagents)
    echo "$1 travels by teleport, and teleports closed this Wine client. On the Mac: pet, seeds or gather."
    exit 1 ;;
  *) CONFIG="$1"; shift ;;
esac
# Everything left is settings for the bot (mac/run.sh --help lists them).
EXTRA=("$@")
for arg in ${EXTRA[@]+"${EXTRA[@]}"}; do
  if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
    "$WINE" "$PY" -m wiz101_auto run --help 2>/dev/null
    exit 0
  fi
done

# The bracket keeps this check from matching its own command line.
if pgrep -f '[w]iz101_auto run' >/dev/null 2>&1; then
  echo "A bot is already running. Stop it from the Wiz menu, leave Wizard101 open, then run again."
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="logs/runs/$STAMP-${MODE:-config}"
mkdir -p "$ROOT/$RUN_DIR"
export WIZ_RUN_DIR="$RUN_DIR"
export WIZ_WINE_VERSION="$("$WINE" --version 2>/dev/null || true)"
"$MACPY" "$ROOT/mac/crashwatch.py" start --run-dir "$RUN_DIR"

echo "starting ${MODE:-the configured} mode in the Wizard101 bottle"
echo "this run's logs: $RUN_DIR"
echo "stop: the Wiz menu, Stop Wizard Bot.command, Ctrl+C here, or: python3 -m wiz101_auto stop"

ARGS=(-u -m wiz101_auto run --run-dir "$RUN_DIR")
[[ -f "$ROOT/$CONFIG" || -f "$CONFIG" ]] && ARGS+=(-c "$CONFIG")
[[ -n "$MODE" ]] && ARGS+=(-m "$MODE")
ARGS+=(${EXTRA[@]+"${EXTRA[@]}"})
"$WINE" "$PY" "${ARGS[@]}" > >(tee -a "$ROOT/$RUN_DIR/console.log") 2>&1 &
BOT=$!
"$MACPY" "$ROOT/mac/crashwatch.py" watch --run-dir "$RUN_DIR" --bot-pid "$BOT" &
WATCH=$!

# Ctrl+C asks for a clean stop (mouse hook out, hook record saved). Never kill the bot.
trap 'mkdir -p "$ROOT/state"; touch "$ROOT/state/stop.request"; echo; echo "stopping cleanly..."' INT TERM
STATUS=0
while kill -0 "$BOT" 2>/dev/null; do
  wait "$BOT"
  STATUS=$?
done
wait "$WATCH" 2>/dev/null || true
trap - INT TERM

echo "run folder: $RUN_DIR"
if [[ -f "$ROOT/$RUN_DIR/CRASH.md" ]]; then
  echo "CRASH REPORT: $RUN_DIR/CRASH.md"
fi
exit "$STATUS"
