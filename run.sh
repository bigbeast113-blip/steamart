#!/usr/bin/env bash
# Start SteamArt. On a Steam Deck: switch to Desktop Mode, then run this.
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
  echo "Python 3.7+ is required but was not found." >&2
  echo "SteamOS ships with python3; if this is another distro, install it first." >&2
  exit 1
fi

exec "$PYTHON" steamart.py "$@"
