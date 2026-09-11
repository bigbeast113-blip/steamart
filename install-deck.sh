#!/usr/bin/env bash
# Put a SteamArt launcher on the Steam Deck's desktop and application menu,
# so you never need the terminal again.
#
# Run once from Desktop Mode, either:
#   ./install-deck.sh
# or by double-clicking this file in Dolphin and choosing "Execute".
set -euo pipefail

cd "$(dirname "$0")"

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)'; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  MESSAGE="Python 3.7 or newer is required but was not found.
SteamOS ships with python3; on another distro, install it first."
  if [ -t 1 ]; then
    echo "$MESSAGE" >&2
  else
    kdialog --error "$MESSAGE" 2>/dev/null \
      || zenity --error --text="$MESSAGE" 2>/dev/null \
      || true
  fi
  exit 1
fi

chmod +x run.sh install-deck.sh steamart.py 2>/dev/null || true

"$PYTHON" steamart.py install

if [ ! -t 1 ]; then
  kdialog --msgbox "SteamArt is installed.

Look for it on your desktop and in the application menu." 2>/dev/null || true
fi
