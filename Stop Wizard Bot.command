#!/bin/bash
# Double-click to unhook the bot so the game can be played.
cd "$(dirname "$0")"
mkdir -p state
date "+%Y-%m-%d %H:%M:%S" > state/stop.request
osascript -e 'display notification "It will unhook the game. You can play once that finishes." with title "Wizard bot stopping"'
