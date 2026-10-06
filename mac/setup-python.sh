#!/bin/bash
# Windows CPython for Wizard101.app's Wine. Lives outside the bottle.
# The game's wineserver is what can see WizardGraphicalClient.exe.
set -euo pipefail
WINE="/Applications/Wizard101.app/Contents/SharedSupport/wizard101/bin/wine"
PREFIX="$HOME/Library/Application Support/Wizard101/Bottles/wizard101"
DEST="$HOME/Library/Application Support/wiz101-chores/python"
URL="https://www.python.org/ftp/python/3.13.7/python-3.13.7-embed-amd64.zip"

if [[ ! -x "$WINE" ]]; then
  echo "Wizard101.app's Wine is not installed."
  exit 1
fi
export WINEPREFIX="$PREFIX"
mkdir -p "$DEST"
if [[ ! -f "$DEST/python.exe" ]]; then
  TMP="$(mktemp -d)"
  echo "downloading Windows Python 3.13"
  curl -fsSL -o "$TMP/python.zip" "$URL"
  unzip -q "$TMP/python.zip" -d "$DEST"
  rm -rf "$TMP"
fi
# The embeddable build ships with site disabled. Pip needs it.
PTH="$(echo "$DEST"/python*._pth)"
if [[ -f "$PTH" ]] && grep -q '^#import site' "$PTH"; then
  sed -i '' 's/^#import site/import site/' "$PTH"
fi
# Before any pip or bot copy. See mac/sitecustomize.py.
cp "$(cd "$(dirname "$0")" && pwd)/sitecustomize.py" "$DEST/sitecustomize.py"
if ! "$WINE" "$DEST/python.exe" -c "import sys; print(sys.version)"; then
  echo "Windows Python did not start in the Wizard101 bottle."
  exit 1
fi
# Python 3.13's _wmi.pyd calls propsys.VariantToString, which this Wine build
# does not implement. Pip imports it and the process aborts. The fallback in
# platform.py is enough, and this file is our Python, not the game's.
if [[ -f "$DEST/_wmi.pyd" ]]; then
  mv "$DEST/_wmi.pyd" "$DEST/_wmi.pyd.disabled"
fi
# get-pip.py calls that same missing function and hangs the bottle.
# A wheel unpacked into site-packages is the same pip without that bootstrap.
SITE="$DEST/Lib/site-packages"
if ! "$WINE" "$DEST/python.exe" -m pip --version >/dev/null 2>&1; then
  echo "unpacking pip"
  mkdir -p "$SITE"
  TMP="$(mktemp -d)"
  curl -fsSL -o "$TMP/pip.json" https://pypi.org/pypi/pip/json
  URL="$(python3 -c 'import json,sys; j=json.load(open(sys.argv[1]));
print(next(u["url"] for u in j["urls"] if u["packagetype"]=="bdist_wheel" and u["filename"].endswith("py3-none-any.whl")))' "$TMP/pip.json")"
  curl -fsSL -o "$TMP/pip.whl" "$URL"
  unzip -q -o "$TMP/pip.whl" -d "$SITE"
  rm -rf "$TMP"
fi
if ! "$WINE" "$DEST/python.exe" -c "import loguru, yaml" >/dev/null 2>&1; then
  echo "installing loguru and pyyaml"
  "$WINE" "$DEST/python.exe" -m pip install --no-warn-script-location "loguru>=0.7.2" "pyyaml>=6.0.1"
fi
# Hatchling's wheel build calls CopyFile2, which this Wine does not implement.
# Both libraries are pure Python, so they are copied into site-packages.
if ! "$WINE" "$DEST/python.exe" -c "import wizwalker" >/dev/null 2>&1; then
  echo "installing wizwalker"
  "$WINE" "$DEST/python.exe" -m pip install --no-warn-script-location \
    "pymem==1.13.1" "aiofiles>=23.2.1" "appdirs>=1.4.4" "click>=8.1.7" \
    "click-default-group>=1.2.4" "janus>=1.0.0" "pefile>=2023.2.7" \
    "regex>=2024.5.15" "lark>=1.1.9"
  TMP="$(mktemp -d)"
  curl -fsSL -o "$TMP/deimos.zip" https://github.com/Deimos-Wizard101/Deimos-Wizard101/archive/refs/heads/main.zip
  unzip -q "$TMP/deimos.zip" -d "$TMP"
  ROOT="$TMP/Deimos-Wizard101-main/libs"
  rm -rf "$SITE/wizwalker"
  cp -R "$ROOT/wizwalker/wizwalker" "$SITE/wizwalker"
  mkdir -p "$SITE/wizwalker/extensions"
  cp -R "$ROOT/wizsprinter/wizwalker/extensions/wizsprinter" "$SITE/wizwalker/extensions/wizsprinter"
  rm -rf "$TMP"
fi
echo "windows python is ready at $DEST"
