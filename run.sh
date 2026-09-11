#!/usr/bin/env bash
# Start SteamArt. On a Steam Deck: switch to Desktop Mode, then run this.
#
# Prefer a desktop shortcut over typing this every time:  ./install-deck.sh
set -euo pipefail

cd "$(dirname "$0")"

fail() {
  # When launched from a desktop icon there is no terminal to print to, so
  # put the error somewhere the user will actually see it.
  if [ -t 2 ]; then
    echo "$1" >&2
  else
    kdialog --error "$1" 2>/dev/null \
      || zenity --error --text="$1" 2>/dev/null \
      || notify-send "SteamArt" "$1" 2>/dev/null \
      || true
  fi
  exit 1
}

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
  fail "Python 3.7 or newer is required but was not found.
SteamOS ships with python3; on another distro, install it first."
fi

exec "$PYTHON" steamart.py "$@"
