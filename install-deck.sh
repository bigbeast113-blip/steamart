#!/usr/bin/env bash
# Put a SteamArt launcher on the Steam Deck's desktop and app menu.
#
# Run once from Desktop Mode:   ./install-deck.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_FILE_NAME="steamart.desktop"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

chmod +x "$APP_DIR/run.sh"

mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/$DESKTOP_FILE_NAME" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SteamArt
Comment=Import non-Steam games and fetch their Steam artwork
Exec=$APP_DIR/run.sh
Path=$APP_DIR
Icon=applications-games
Terminal=true
Categories=Game;Utility;
DESKTOP

chmod +x "$APPS_DIR/$DESKTOP_FILE_NAME"

if [ -d "$DESKTOP_DIR" ]; then
  cp "$APPS_DIR/$DESKTOP_FILE_NAME" "$DESKTOP_DIR/$DESKTOP_FILE_NAME"
  chmod +x "$DESKTOP_DIR/$DESKTOP_FILE_NAME"
  echo "Added a SteamArt icon to your desktop."
fi

echo "Installed. Launch SteamArt from the application menu or your desktop."
echo "It opens http://127.0.0.1:8523/ in your browser."
