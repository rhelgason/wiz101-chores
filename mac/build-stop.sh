#!/bin/bash
# Compile the menu-bar stop. The binary is local; the source is what is committed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/WizStop"
if [[ -x "$OUT" && "$OUT" -nt "$HERE/WizStop.swift" ]]; then
  exit 0
fi
swiftc -O -o "$OUT" "$HERE/WizStop.swift" -framework AppKit
