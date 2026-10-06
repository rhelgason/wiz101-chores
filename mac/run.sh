#!/bin/bash
# Start the day plan inside Wizard101.app's own Wine bottle, and the menu-bar stop.
#
# The game is a 64-bit Windows exe. macOS Python cannot hook it. CrossOver.app
# is a different Wine from the one bundled in Wizard101.app; this script does
# not use it, and it does not point that Wine at the game's bottle.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WINE="/Applications/Wizard101.app/Contents/SharedSupport/wizard101/bin/wine"
PREFIX="$HOME/Library/Application Support/Wizard101/Bottles/wizard101"
PY="$HOME/Library/Application Support/wiz101-chores/python/python.exe"

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

"$ROOT/mac/build-stop.sh"
if ! pgrep -f "$ROOT/mac/WizStop" >/dev/null 2>&1; then
  "$ROOT/mac/WizStop" "$ROOT" >/dev/null 2>&1 &
  disown || true
  echo "menu bar: Wiz on. Stop and unhook is in that menu."
fi

if [[ "${1:-}" == "doctor" ]]; then
  echo "Wine (Wizard101.app, not CrossOver.app):"
  "$WINE" --version
  echo "bottle: $PREFIX"
  echo "client: $PREFIX/drive_c/ProgramData/KingsIsle Entertainment/Wizard101/Bin/WizardGraphicalClient.exe"
  if [[ -f "$PY" ]]; then
    echo "windows python:"
    "$WINE" "$PY" -c "import sys; print(sys.version)"
  else
    echo "windows python: not set up yet. Run mac/setup-python.sh"
  fi
  exit 0
fi

if [[ ! -f "$PY" ]]; then
  "$ROOT/mac/setup-python.sh"
fi

cd "$ROOT"
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
  "" ) ;;
  chores|pet|reagents|seeds) MODE="$1" ;;
  *) CONFIG="$1" ;;
esac
if [[ -n "$MODE" ]]; then
  echo "starting $MODE mode in the Wizard101 bottle"
else
  echo "starting the day plan in the Wizard101 bottle"
fi
echo "stop: the Wiz menu, Stop Wizard Bot.command, or: python3 -m wiz101_auto stop"
if [[ -n "$MODE" ]]; then
  exec "$WINE" "$PY" -m wiz101_auto run -c "$CONFIG" -m "$MODE"
fi
exec "$WINE" "$PY" -m wiz101_auto run -c "$CONFIG"
