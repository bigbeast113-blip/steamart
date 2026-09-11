"""Desktop launcher installation, so SteamArt can be started with a click.

On the Steam Deck this writes a ``.desktop`` file into the application menu and
onto the desktop. The entry runs the current Python interpreter directly rather
than ``run.sh``, because a ZIP download arrives with the execute bit stripped
and a launcher that silently fails is worse than none.
"""

from __future__ import annotations

import os
import subprocess
import sys

ENTRY_NAME = "steamart.desktop"

TEMPLATE = """[Desktop Entry]
Type=Application
Version=1.0
Name=SteamArt
GenericName=Steam artwork manager
Comment=Import non-Steam games and fetch their Steam artwork
Exec={exec_line}
Path={app_dir}
Icon={icon}
Terminal=false
StartupNotify=false
Categories=Game;Utility;
Keywords=steam;deck;artwork;grid;shortcut;
"""


def supported():
    """Desktop entries are a freedesktop thing, so Linux only."""
    return sys.platform.startswith("linux")


def app_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def icon_path():
    candidate = os.path.join(app_dir(), "assets", "steamart.svg")
    return candidate if os.path.isfile(candidate) else "applications-games"


def applications_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "applications")


def desktop_dir():
    """The user's Desktop, which is not always called that."""
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                             capture_output=True, text=True, timeout=5)
        path = out.stdout.strip()
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    fallback = os.path.expanduser("~/Desktop")
    return fallback if os.path.isdir(fallback) else None


def _exec_line():
    """Command for the Exec key, quoted so paths with spaces survive."""
    root = app_dir()
    entry = os.path.join(root, "steamart.py")
    python = sys.executable or "python3"
    if os.path.isfile(entry):
        return '"%s" "%s"' % (python, entry)
    return '"%s" -m steamart' % python


def entry_text():
    return TEMPLATE.format(
        exec_line=_exec_line(), app_dir=app_dir(), icon=icon_path())


def installed_paths():
    """Where a launcher currently exists."""
    found = []
    for directory in (applications_dir(), desktop_dir()):
        if not directory:
            continue
        path = os.path.join(directory, ENTRY_NAME)
        if os.path.isfile(path):
            found.append(path)
    return found


def installed():
    return bool(installed_paths())


def install(on_desktop=True):
    """Write the launcher. Returns the paths written."""
    if not supported():
        raise RuntimeError(
            "Desktop shortcuts are a Linux feature. On Windows, use run.bat "
            "or right-click it and send a shortcut to your desktop.")

    text = entry_text()
    written = []

    apps = applications_dir()
    os.makedirs(apps, exist_ok=True)
    targets = [os.path.join(apps, ENTRY_NAME)]
    if on_desktop:
        desktop = desktop_dir()
        if desktop:
            targets.append(os.path.join(desktop, ENTRY_NAME))

    for target in targets:
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        # KDE will not treat a launcher as trusted unless it is executable.
        os.chmod(target, 0o755)
        written.append(target)

    # Make the shell wrappers runnable too, for anyone who prefers them. The
    # ZIP download loses the execute bit; a clone keeps it.
    for script in ("run.sh", "install-deck.sh"):
        path = os.path.join(app_dir(), script)
        if os.path.isfile(path):
            try:
                os.chmod(path, os.stat(path).st_mode | 0o111)
            except OSError:
                pass

    _refresh_menu()
    return written


def remove():
    removed = []
    for path in installed_paths():
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    _refresh_menu()
    return removed


def _refresh_menu():
    """Nudge the desktop into noticing the new entry. Failure is harmless."""
    try:
        subprocess.run(["update-desktop-database", applications_dir()],
                       capture_output=True, timeout=10)
    except Exception:
        pass
