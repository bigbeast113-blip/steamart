"""The layer that ties Steam, SteamGridDB and user settings together."""

from __future__ import annotations

import json
import os
import sys
from collections import OrderedDict

from . import names as names_module
from . import sgdb, steam, vdf

# Things that live next to a game but are not the game.
_SKIP_NAMES = {
    "unins000.exe", "uninstall.exe", "uninstaller.exe", "setup.exe",
    "vcredist_x64.exe", "vcredist_x86.exe", "dxsetup.exe", "dotnetfx.exe",
    "crashreportclient.exe", "crashhandler.exe", "ue4prereqsetup_x64.exe",
    "ue prereqsetup_x64.exe", "oalinst.exe", "directx_jun2010_redist.exe",
    "notification_helper.exe", "unitycrashhandler64.exe",
    "unitycrashhandler32.exe", "config.exe", "settings.exe", "eula.exe",
}
_SKIP_DIR_PARTS = {
    "_commonredist", "commonredist", "redist", "directx", "dotnet",
    "vcredist", "__installer", "engine", "prerequisites", "support",
    "$plugins", "_redist",
}
_SKIP_SUBSTRINGS = ("unins", "vcredist", "dxwebsetup", "crashreport", "crashhandler")

LINUX_GAME_EXTENSIONS = (".x86_64", ".x86", ".sh", ".appimage", ".exe")
WINDOWS_GAME_EXTENSIONS = (".exe",)


def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "SteamArt")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "steamart")


class Config:
    """Small JSON settings file: API key, chosen profile, preferences."""

    DEFAULTS = {
        "api_key": "",
        "steam_root": "",
        "user_id32": "",
        "compat_tool": "proton_experimental",
        "set_compat_for_exe": True,
        "auto_art_on_import": True,
        "allow_nsfw": False,
        "allow_humor": False,
        "overwrite_existing_art": False,
    }

    def __init__(self, path=None):
        self.path = path or os.path.join(config_dir(), "config.json")
        self.data = dict(self.DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                self.data.update({k: v for k, v in stored.items() if k in self.DEFAULTS})
        except (OSError, ValueError):
            pass
        return self.data

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        os.replace(temp_path, self.path)

    def update(self, values):
        for key, value in (values or {}).items():
            if key in self.DEFAULTS:
                self.data[key] = value
        self.save()
        return self.data

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)


class Library:
    """A single Steam user profile's non-Steam games."""

    def __init__(self, config=None):
        self.config = config or Config()
        self.root = None
        self.user = None
        self._client = None
        self.refresh_steam()

    # -- setup ------------------------------------------------------------

    def refresh_steam(self):
        self.root = steam.find_steam_root(self.config.get("steam_root") or None)
        self.user = None
        if not self.root:
            return None
        users = steam.list_users(self.root)
        if not users:
            return None
        wanted = str(self.config.get("user_id32") or "")
        for user in users:
            if user["id32"] == wanted:
                self.user = user
                break
        if self.user is None:
            self.user = users[0]
            self.config.update({"user_id32": self.user["id32"]})
        return self.user

    def users(self):
        return steam.list_users(self.root) if self.root else []

    def select_user(self, id32):
        for user in self.users():
            if user["id32"] == str(id32):
                self.user = user
                self.config.update({"user_id32": user["id32"]})
                return user
        raise steam.SteamError("No Steam profile with ID %s" % id32)

    @property
    def client(self):
        key = self.config.get("api_key", "")
        if self._client is None or self._client.api_key != key:
            self._client = sgdb.Client(key)
        return self._client

    def require_user(self):
        if not self.root:
            raise steam.SteamError(
                "Could not find a Steam installation. Set the Steam folder in Settings."
            )
        if not self.user:
            raise steam.SteamError(
                "No Steam profile found. Sign in to Steam once, then reopen SteamArt."
            )
        return self.user

    # -- reading ----------------------------------------------------------

    def games(self):
        """Every non-Steam shortcut, with artwork status attached."""
        self.require_user()
        user_path = self.user["path"]
        out = []
        for index, entry in enumerate(steam.load_shortcuts(user_path)):
            appid = steam.entry_appid(entry)
            out.append({
                "index": index,
                "appid": appid,
                "name": vdf.get_ci(entry, "AppName", "") or "(unnamed)",
                "exe": steam.entry_exe(entry),
                "start_dir": str(vdf.get_ci(entry, "StartDir", "")).strip('"'),
                "launch_options": vdf.get_ci(entry, "LaunchOptions", ""),
                "icon": str(vdf.get_ci(entry, "icon", "")).strip('"'),
                "art": steam.art_status(user_path, appid),
                "compat_tool": steam.get_compat_tool(self.root, appid),
                "is_windows_exe": steam.entry_exe(entry).lower().endswith(".exe"),
            })
        return out

    def find_by_appid(self, appid):
        appid = steam.to_unsigned32(appid)
        entries = steam.load_shortcuts(self.user["path"])
        for index, entry in enumerate(entries):
            if steam.entry_appid(entry) == appid:
                return index, entry, entries
        raise steam.SteamError("No shortcut with app ID %s" % appid)

    # -- importing --------------------------------------------------------

    def scan_folder(self, folder, depth=3):
        """Find likely game executables under a folder.

        Only the shallowest executable in each game directory is offered, which
        keeps crash handlers and bundled tools out of the list.
        """
        folder = os.path.abspath(os.path.expanduser(folder))
        if not os.path.isdir(folder):
            raise steam.SteamError("%s is not a folder" % folder)

        extensions = (WINDOWS_GAME_EXTENSIONS if sys.platform == "win32"
                      else LINUX_GAME_EXTENSIONS)
        existing = {os.path.normcase(g["exe"]) for g in self.games()}
        found = []
        base_depth = folder.rstrip("\\/").count(os.sep)

        for current, dirnames, filenames in os.walk(folder):
            if current.count(os.sep) - base_depth >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in _SKIP_DIR_PARTS and not d.startswith(".")]
            for filename in sorted(filenames):
                path = os.path.join(current, filename)
                if not _looks_like_game(path, extensions):
                    continue
                found.append({
                    "path": path,
                    "name": steam.nice_name_for(path),
                    "size": _safe_size(path),
                    "already_added": os.path.normcase(path) in existing,
                })

        found.sort(key=lambda item: (item["name"].lower(), item["path"]))
        return found

    def add_games(self, specs, set_compat=None, fetch_art=None, progress=None):
        """Import executables as non-Steam shortcuts.

        ``specs`` is a list of paths or ``{"path": ..., "name": ...}`` dicts.
        Artwork is fetched automatically unless turned off.
        """
        self.require_user()
        user_path = self.user["path"]
        entries = steam.load_shortcuts(user_path)
        known = {steam.entry_appid(e) for e in entries}
        if set_compat is None:
            set_compat = self.config.get("set_compat_for_exe", True)
        if fetch_art is None:
            fetch_art = self.config.get("auto_art_on_import", True)

        added, skipped = [], []
        for spec in specs:
            if isinstance(spec, str):
                spec = {"path": spec}
            path = os.path.abspath(os.path.expanduser(spec["path"]))
            if not os.path.isfile(path):
                skipped.append({"path": path, "reason": "file not found"})
                continue
            name = (spec.get("name") or "").strip() or steam.nice_name_for(path)
            entry = steam.make_shortcut(path, name, tags=spec.get("tags") or [])
            appid = steam.entry_appid(entry)
            if appid in known:
                skipped.append({"path": path, "name": name, "reason": "already in your library"})
                continue
            entries.append(entry)
            known.add(appid)
            added.append({"appid": appid, "name": name, "path": path})

        if not added:
            return {"added": [], "skipped": skipped, "art": []}

        steam.save_shortcuts(user_path, entries)

        compat_errors = []
        if set_compat:
            tool = self.config.get("compat_tool") or "proton_experimental"
            for game in added:
                if not game["path"].lower().endswith(".exe"):
                    continue
                try:
                    steam.set_compat_tool(self.root, game["appid"], tool)
                    game["compat_tool"] = tool
                except Exception as exc:
                    compat_errors.append("%s: %s" % (game["name"], exc))

        art_results = []
        if fetch_art and self.config.get("api_key"):
            for game in added:
                if progress:
                    progress(game["name"])
                art_results.append(self.auto_art(
                    game["appid"], name=game["name"], exe=game["path"]))

        return {
            "added": added,
            "skipped": skipped,
            "art": art_results,
            "compat_errors": compat_errors,
        }

    def remove_game(self, appid, delete_art=True):
        self.require_user()
        index, entry, entries = self.find_by_appid(appid)
        name = vdf.get_ci(entry, "AppName", "")
        del entries[index]
        steam.save_shortcuts(self.user["path"], entries)
        if delete_art:
            steam.clear_art(self.user["path"], appid)
        try:
            steam.set_compat_tool(self.root, appid, None)
        except Exception:
            pass
        return {"appid": steam.to_unsigned32(appid), "name": name}

    def rename_game(self, appid, new_name):
        """Renaming changes the derived app ID, so artwork has to move with it."""
        self.require_user()
        user_path = self.user["path"]
        index, entry, entries = self.find_by_appid(appid)
        old_appid = steam.entry_appid(entry)
        exe = steam.entry_exe(entry)
        new_appid = steam.shortcut_appid(exe, new_name)

        moved = []
        if new_appid != old_appid:
            for kind in steam.ART_KINDS:
                current = steam.existing_art(user_path, old_appid, kind)
                if not current:
                    continue
                with open(current, "rb") as fh:
                    data = fh.read()
                steam.write_art(user_path, new_appid, kind,
                                data, os.path.splitext(current)[1])
                moved.append(kind)
            steam.clear_art(user_path, old_appid)
            tool = steam.get_compat_tool(self.root, old_appid)
            if tool:
                try:
                    steam.set_compat_tool(self.root, new_appid, tool)
                    steam.set_compat_tool(self.root, old_appid, None)
                except Exception:
                    pass

        entry["AppName"] = new_name
        entry["appid"] = steam.to_signed32(new_appid)
        icon = steam.existing_art(user_path, new_appid, "icon")
        if icon:
            entry["icon"] = icon
        entries[index] = entry
        steam.save_shortcuts(user_path, entries)
        return {"appid": new_appid, "name": new_name, "moved_art": moved}

    def set_compat(self, appid, tool_name):
        self.require_user()
        return steam.set_compat_tool(self.root, appid, tool_name or None)

    # -- artwork ----------------------------------------------------------

    def auto_art(self, appid, name=None, kinds=None, game_id=None, overwrite=None,
                 exe=None):
        """Fetch and install the top-ranked artwork for every slot.

        This is the main path: search SteamGridDB for the title, take the best
        image for each of the five slots, write them in. Slots that already
        have artwork are left alone unless overwriting is on.
        """
        self.require_user()
        user_path = self.user["path"]
        appid = steam.to_unsigned32(appid)
        if overwrite is None:
            overwrite = self.config.get("overwrite_existing_art", False)
        kinds = list(kinds or steam.ART_KINDS)

        # The executable path is a second source of truth for the title: the
        # folder above it is often spelled better than the file itself.
        if name is None or exe is None:
            try:
                _, entry, _ = self.find_by_appid(appid)
                if name is None:
                    name = vdf.get_ci(entry, "AppName", "")
                if exe is None:
                    exe = steam.entry_exe(entry)
            except steam.SteamError:
                name = name or ""

        result = {
            "appid": appid,
            "name": name,
            "game": None,
            "applied": {},
            "skipped": {},
            "errors": {},
        }

        pending = []
        for kind in kinds:
            if not overwrite and steam.existing_art(user_path, appid, kind):
                result["skipped"][kind] = "already has artwork"
            else:
                pending.append(kind)
        if not pending:
            return result

        try:
            if game_id is None:
                trace = []
                game = self.client.best_game(name, exe=exe, trace=trace)
                result["trace"] = trace
                if not game:
                    result["errors"]["_"] = (
                        "No SteamGridDB match for %r after %d searches"
                        % (name, len(trace))
                    )
                    return result
                game_id = game["id"]
                result["game"] = {
                    "id": game["id"],
                    "name": game.get("name", ""),
                    "matched_by": game.get("_matched_by"),
                    "confidence": game.get("_confidence", 1.0),
                }
            else:
                result["game"] = {"id": game_id, "name": name, "confidence": 1.0}
        except sgdb.SGDBError as exc:
            result["errors"]["_"] = str(exc)
            return result

        for kind in pending:
            try:
                asset = self.client.best_asset(
                    game_id, kind,
                    nsfw=self.config.get("allow_nsfw", False),
                    humor=self.config.get("allow_humor", False),
                )
                if not asset:
                    result["skipped"][kind] = "nothing available on SteamGridDB"
                    continue
                self._install(appid, kind, asset)
                result["applied"][kind] = asset.get("url")
            except sgdb.SGDBError as exc:
                result["errors"][kind] = str(exc)
            except OSError as exc:
                result["errors"][kind] = "could not write artwork: %s" % exc
        return result

    def preview_match(self, name, exe=None):
        """Dry run of the title matching: what it searches and what it finds.

        Installs nothing. This is what the UI shows when you ask why a game
        ended up with the artwork it did, or with none at all.
        """
        result = {
            "name": name,
            "exe": exe,
            "variants": names_module.query_variants(name, exe),
            "trace": [],
            "game": None,
            "min_confidence": sgdb.MIN_CONFIDENCE,
        }
        if not self.config.get("api_key"):
            result["error"] = ("No SteamGridDB API key set, so these searches "
                               "have not actually been run.")
            return result

        trace = []
        try:
            game = self.client.best_game(name, exe=exe, trace=trace)
        except sgdb.SGDBError as exc:
            result["trace"] = trace
            result["error"] = str(exc)
            return result

        result["trace"] = trace
        if game:
            result["game"] = {
                "id": game["id"],
                "name": game.get("name", ""),
                "matched_by": game.get("_matched_by"),
                "confidence": game.get("_confidence", 1.0),
            }
        return result

    def apply_asset(self, appid, kind, url):
        """Install one specific image chosen by hand."""
        self.require_user()
        appid = steam.to_unsigned32(appid)
        path = self._install(appid, kind, {"url": url})
        return {"appid": appid, "kind": kind, "path": path}

    def _install(self, appid, kind, asset):
        data, extension = self.client.download(asset["url"])
        path = steam.write_art(self.user["path"], appid, kind, data, extension)
        if kind == "icon":
            self._point_icon_at(appid, path)
        return path

    def _point_icon_at(self, appid, icon_path):
        """The icon slot is a path stored in shortcuts.vdf, not just a file."""
        try:
            index, entry, entries = self.find_by_appid(appid)
        except steam.SteamError:
            return
        if str(vdf.get_ci(entry, "icon", "")).strip('"') == icon_path:
            return
        entry["icon"] = icon_path
        entries[index] = entry
        steam.save_shortcuts(self.user["path"], entries)

    def clear_art(self, appid, kinds=None):
        self.require_user()
        return steam.clear_art(self.user["path"], appid, kinds)

    def candidates(self, game_id, kind):
        return self.client.assets(
            game_id, kind,
            nsfw=self.config.get("allow_nsfw", False),
            humor=self.config.get("allow_humor", False),
        )


def _looks_like_game(path, extensions):
    filename = os.path.basename(path)
    lowered = filename.lower()
    if lowered in _SKIP_NAMES:
        return False
    if any(part in lowered for part in _SKIP_SUBSTRINGS):
        return False
    extension = os.path.splitext(lowered)[1]
    if extension:
        if extension not in extensions:
            return False
    elif sys.platform == "win32" or not os.access(path, os.X_OK):
        # Extensionless files only count on Linux, and only if executable.
        return False
    # Tiny binaries are launchers and helpers far more often than games.
    return _safe_size(path) >= 64 * 1024


def _safe_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def list_drives():
    """Sensible starting points for the folder browser."""
    places = []
    home = os.path.expanduser("~")
    places.append({"label": "Home", "path": home})

    if sys.platform == "win32":
        import string

        for letter in string.ascii_uppercase:
            drive = "%s:\\" % letter
            if os.path.isdir(drive):
                places.append({"label": drive, "path": drive})
    else:
        # On a Deck the microSD card and any USB drives land under /run/media.
        for base in ("/run/media", "/media", os.path.join(home, ".local", "share", "Steam")):
            if not os.path.isdir(base):
                continue
            if base.endswith("Steam"):
                places.append({"label": "Steam folder", "path": base})
                continue
            try:
                for name in sorted(os.listdir(base)):
                    full = os.path.join(base, name)
                    if os.path.isdir(full):
                        places.append({"label": name, "path": full})
                        for sub in sorted(os.listdir(full))[:8]:
                            sub_path = os.path.join(full, sub)
                            if os.path.isdir(sub_path):
                                places.append({"label": "%s / %s" % (name, sub),
                                               "path": sub_path})
            except OSError:
                pass
        places.append({"label": "Filesystem root", "path": "/"})

    seen, unique = set(), []
    for place in places:
        key = os.path.normcase(place["path"])
        if key not in seen:
            seen.add(key)
            unique.append(place)
    return unique


def browse(path):
    """Directory listing for the built-in folder picker."""
    path = os.path.abspath(os.path.expanduser(path or os.path.expanduser("~")))
    if not os.path.isdir(path):
        raise steam.SteamError("%s is not a folder" % path)
    extensions = (WINDOWS_GAME_EXTENSIONS if sys.platform == "win32"
                  else LINUX_GAME_EXTENSIONS)
    folders, files = [], []
    try:
        for name in sorted(os.listdir(path), key=str.lower):
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                folders.append({"name": name, "path": full})
            elif _looks_like_game(full, extensions):
                files.append({"name": name, "path": full, "size": _safe_size(full)})
    except PermissionError:
        raise steam.SteamError("No permission to read %s" % path)
    parent = os.path.dirname(path.rstrip("\\/")) or None
    if parent == path:
        parent = None
    return {"path": path, "parent": parent, "folders": folders, "files": files}
