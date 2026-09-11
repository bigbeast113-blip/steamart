"""Everything that touches a local Steam installation.

Finding the install, listing user profiles, reading and writing the non-Steam
shortcut list, working out the app IDs Steam derives for those shortcuts, and
mapping artwork onto the filenames Steam expects in the ``grid`` folder.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zlib
from collections import OrderedDict

from . import vdf

STEAM_ID64_BASE = 76561197960265728

# The five artwork slots Steam renders for a game, and the filename each one
# takes inside ``userdata/<id>/config/grid``. ``{id}`` is the unsigned 32-bit
# shortcut app ID.
ART_KINDS = OrderedDict([
    ("capsule", {
        "file": "{id}p",
        "label": "Library capsule",
        "hint": "600x900 portrait, the box art in your library grid",
        "dimensions": "600x900,342x482,660x930,512x512,1024x1024",
        "endpoint": "grids",
    }),
    ("wide", {
        "file": "{id}",
        "label": "Wide capsule",
        "hint": "920x430 landscape, shown in Recent Games and the Deck carousel",
        "dimensions": "920x430,460x215",
        "endpoint": "grids",
    }),
    ("hero", {
        "file": "{id}_hero",
        "label": "Hero banner",
        "hint": "1920x620 background at the top of the game page",
        "dimensions": None,
        "endpoint": "heroes",
    }),
    ("logo", {
        "file": "{id}_logo",
        "label": "Logo",
        "hint": "transparent title treatment laid over the hero",
        "dimensions": None,
        "endpoint": "logos",
    }),
    ("icon", {
        "file": "{id}_icon",
        "label": "Icon",
        "hint": "small icon used in lists and the taskbar",
        "dimensions": None,
        "endpoint": "icons",
    }),
])

# Extensions Steam will load out of the grid folder for a given slot.
ART_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".ico")

# Field order matters less than presence, but matching what Steam writes keeps
# the file familiar if you ever inspect it by hand.
SHORTCUT_DEFAULTS = OrderedDict([
    ("appid", 0),
    ("AppName", ""),
    ("Exe", ""),
    ("StartDir", ""),
    ("icon", ""),
    ("ShortcutPath", ""),
    ("LaunchOptions", ""),
    ("IsHidden", 0),
    ("AllowDesktopConfig", 1),
    ("AllowOverlay", 1),
    ("OpenVR", 0),
    ("Devkit", 0),
    ("DevkitGameID", ""),
    ("DevkitOverrideAppID", 0),
    ("LastPlayTime", 0),
    ("FlatpakAppID", ""),
])


class SteamError(RuntimeError):
    """Raised for problems with the Steam installation itself."""


# --------------------------------------------------------------------------
# locating Steam
# --------------------------------------------------------------------------

def candidate_steam_roots():
    """Every plausible Steam directory for this platform, most likely first."""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        roots = []
        try:
            import winreg

            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(winreg.QueryValueEx(handle, name)[0])
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        roots += [
            r"C:\Program Files (x86)\Steam",
            r"C:\Program Files\Steam",
        ]
        return roots
    if sys.platform == "darwin":
        return [os.path.join(home, "Library", "Application Support", "Steam")]
    # Linux, including SteamOS on the Deck.
    return [
        os.path.join(home, ".steam", "steam"),
        os.path.join(home, ".local", "share", "Steam"),
        os.path.join(home, ".steam", "root"),
        os.path.join(home, ".steam", "debian-installation"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam",
                     ".local", "share", "Steam"),
    ]


def is_steam_root(path):
    return bool(path) and os.path.isdir(os.path.join(path, "userdata"))


def find_steam_root(explicit=None):
    """Return the Steam directory to work with, or None if we cannot find one."""
    if explicit:
        expanded = os.path.abspath(os.path.expanduser(explicit))
        if is_steam_root(expanded):
            return expanded
        raise SteamError("%s does not look like a Steam install (no userdata folder)" % expanded)
    for candidate in candidate_steam_roots():
        try:
            resolved = os.path.realpath(os.path.expanduser(candidate))
        except OSError:
            continue
        if is_steam_root(resolved):
            return resolved
    return None


def is_steam_running():
    """Best-effort check. Writing shortcuts.vdf under a live Steam gets clobbered."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
                capture_output=True, text=True, timeout=8,
            ).stdout
            return "steam.exe" in out.lower()
        result = subprocess.run(["pgrep", "-x", "steam"], capture_output=True, timeout=8)
        return result.returncode == 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# user profiles
# --------------------------------------------------------------------------

def list_users(root):
    """Steam profiles that have a userdata folder, most recently used first."""
    userdata = os.path.join(root, "userdata")
    if not os.path.isdir(userdata):
        return []

    names = {}
    most_recent = None
    login_path = os.path.join(root, "config", "loginusers.vdf")
    if os.path.isfile(login_path):
        try:
            users = vdf.get_ci(vdf.text_load(login_path), "users", {}) or {}
            for id64, info in users.items():
                if not isinstance(info, dict):
                    continue
                try:
                    id32 = int(id64) - STEAM_ID64_BASE
                except ValueError:
                    continue
                names[str(id32)] = (vdf.get_ci(info, "PersonaName")
                                    or vdf.get_ci(info, "AccountName") or "")
                if str(vdf.get_ci(info, "MostRecent", "0")) == "1":
                    most_recent = str(id32)
        except vdf.VDFError:
            pass

    found = []
    for entry in sorted(os.listdir(userdata)):
        path = os.path.join(userdata, entry)
        if not entry.isdigit() or entry == "0" or not os.path.isdir(path):
            continue
        found.append({
            "id32": entry,
            "id64": str(int(entry) + STEAM_ID64_BASE),
            "name": names.get(entry) or ("User %s" % entry),
            "path": path,
            "most_recent": entry == most_recent,
            "shortcut_count": _count_shortcuts(path),
        })
    found.sort(key=lambda u: (not u["most_recent"], -u["shortcut_count"], u["id32"]))
    return found


def _count_shortcuts(user_path):
    try:
        return len(load_shortcuts(user_path))
    except Exception:
        return 0


def shortcuts_path(user_path):
    return os.path.join(user_path, "config", "shortcuts.vdf")


def grid_path(user_path):
    return os.path.join(user_path, "config", "grid")


# --------------------------------------------------------------------------
# app IDs
# --------------------------------------------------------------------------

def to_unsigned32(value):
    return int(value) & 0xFFFFFFFF


def to_signed32(value):
    value = int(value) & 0xFFFFFFFF
    return value - 0x100000000 if value > 0x7FFFFFFF else value


def shortcut_appid(exe, app_name):
    """The 32-bit ID Steam derives for a non-Steam shortcut.

    Steam hashes the quoted executable string concatenated with the display
    name. The same number names the artwork files, so it has to match exactly
    or the art lands on nothing.
    """
    key = ('"%s"' % exe.strip('"')) + app_name
    return zlib.crc32(key.encode("utf-8")) | 0x80000000


def big_appid(appid32):
    """The 64-bit ID used by ``steam://rungameid/`` URLs."""
    return (to_unsigned32(appid32) << 32) | 0x02000000


# --------------------------------------------------------------------------
# shortcuts.vdf
# --------------------------------------------------------------------------

def load_shortcuts(user_path):
    """Return the shortcut list as a list of OrderedDicts (possibly empty)."""
    path = shortcuts_path(user_path)
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return []
    root = vdf.binary_load(path)
    shortcuts = vdf.get_ci(root, "shortcuts", None)
    if not isinstance(shortcuts, dict):
        return []
    ordered = sorted(shortcuts.items(), key=lambda kv: _as_int(kv[0]))
    return [entry for _, entry in ordered if isinstance(entry, dict)]


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1 << 30


def save_shortcuts(user_path, entries, backup=True):
    """Write the shortcut list back, keeping a timestamped backup first."""
    path = shortcuts_path(user_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup_path = None
    if backup and os.path.isfile(path):
        backup_path = "%s.%s.bak" % (path, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup_path)

    body = OrderedDict()
    for index, entry in enumerate(entries):
        body[str(index)] = entry
    payload = vdf.binary_dumps(OrderedDict([("shortcuts", body)]))

    # Write to a sibling temp file then replace, so an interrupted write cannot
    # leave Steam with a half-written shortcut list.
    temp_path = path + ".tmp"
    with open(temp_path, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp_path, path)
    _prune_backups(path)
    return backup_path


def _prune_backups(path, keep=10):
    directory = os.path.dirname(path)
    prefix = os.path.basename(path) + "."
    backups = sorted(
        name for name in os.listdir(directory)
        if name.startswith(prefix) and name.endswith(".bak")
    )
    for name in backups[:-keep]:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass


def entry_appid(entry):
    """Unsigned 32-bit app ID for a shortcut, recomputing it if absent."""
    raw = vdf.get_ci(entry, "appid")
    if raw not in (None, "", 0):
        return to_unsigned32(int(raw))
    return shortcut_appid(entry_exe(entry), vdf.get_ci(entry, "AppName", ""))


def entry_exe(entry):
    return str(vdf.get_ci(entry, "Exe", "")).strip('"')


def make_shortcut(exe, app_name=None, start_dir=None, launch_options="",
                  tags=None, icon=""):
    """Build a shortcut entry for an executable path."""
    exe = os.path.abspath(os.path.expanduser(exe))
    if not app_name:
        app_name = nice_name_for(exe)
    if not start_dir:
        start_dir = os.path.dirname(exe)

    entry = OrderedDict(SHORTCUT_DEFAULTS)
    entry["appid"] = to_signed32(shortcut_appid(exe, app_name))
    entry["AppName"] = app_name
    entry["Exe"] = '"%s"' % exe
    entry["StartDir"] = '"%s"' % (start_dir.rstrip("\\/") + os.sep)
    entry["icon"] = icon
    entry["LaunchOptions"] = launch_options
    entry["tags"] = OrderedDict(
        (str(i), tag) for i, tag in enumerate(tags or [])
    )
    return entry


_NAME_NOISE = (
    "setup", "launcher", "launch", "start", "game", "play", "win64", "win32",
    "x64", "x86", "shipping", "release", "final", "retail", "steam",
)


def nice_name_for(exe):
    """Turn ``.../Hollow_Knight/hollow_knight-Win64-Shipping.exe`` into a title.

    Filenames are noisy, so when the executable's own name is mostly junk we
    fall back to the containing folder, which is usually the real game name.
    """
    base = os.path.splitext(os.path.basename(exe))[0]
    folder = os.path.basename(os.path.dirname(exe))
    if _is_noise(base) and folder and not _is_noise(folder):
        base = folder
    for separator in ("_", "-", "."):
        base = base.replace(separator, " ")
    words = [w for w in base.split() if w and w.lower() not in _NAME_NOISE]
    if not words:
        words = [os.path.splitext(os.path.basename(exe))[0]]
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words)


def _is_noise(text):
    cleaned = text.lower().replace("_", " ").replace("-", " ")
    return all(word in _NAME_NOISE or word.isdigit() for word in cleaned.split())


# --------------------------------------------------------------------------
# artwork files
# --------------------------------------------------------------------------

def art_basename(appid, kind):
    return ART_KINDS[kind]["file"].format(id=to_unsigned32(appid))


def existing_art(user_path, appid, kind):
    """Path to the artwork already installed for a slot, if any."""
    base = os.path.join(grid_path(user_path), art_basename(appid, kind))
    for extension in ART_EXTENSIONS:
        candidate = base + extension
        if os.path.isfile(candidate):
            return candidate
    return None


def art_status(user_path, appid):
    return {kind: bool(existing_art(user_path, appid, kind)) for kind in ART_KINDS}


def write_art(user_path, appid, kind, data, extension=".png"):
    """Install artwork into the grid folder for one slot.

    Any previously installed file for the same slot is removed first: Steam
    picks whichever extension it finds, so leaving a stale ``.jpg`` next to a
    new ``.png`` produces the old image.
    """
    directory = grid_path(user_path)
    os.makedirs(directory, exist_ok=True)
    base = os.path.join(directory, art_basename(appid, kind))
    for old_extension in ART_EXTENSIONS:
        stale = base + old_extension
        if os.path.isfile(stale):
            try:
                os.remove(stale)
            except OSError:
                pass
    if not extension.startswith("."):
        extension = "." + extension
    if extension.lower() not in ART_EXTENSIONS:
        extension = ".png"
    target = base + extension
    with open(target, "wb") as fh:
        fh.write(data)
    return target


def clear_art(user_path, appid, kinds=None):
    removed = []
    for kind in (kinds or list(ART_KINDS)):
        path = existing_art(user_path, appid, kind)
        if path:
            try:
                os.remove(path)
                removed.append(path)
            except OSError:
                pass
    return removed


# --------------------------------------------------------------------------
# Proton compatibility tools
# --------------------------------------------------------------------------

def config_vdf_path(root):
    return os.path.join(root, "config", "config.vdf")


def _compat_mapping(config_root, create=False):
    """Walk down to InstallConfigStore/Software/Valve/Steam/CompatToolMapping."""
    node = config_root
    for key in ("InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping"):
        if create:
            node = vdf.setdefault_ci(node, key)
        else:
            node = vdf.get_ci(node, key)
            if not isinstance(node, dict):
                return None
    return node


def list_compat_tools(root):
    """Compatibility tools Steam knows about, by their internal names."""
    tools = []
    seen = set()

    custom_dir = os.path.join(root, "compatibilitytools.d")
    if os.path.isdir(custom_dir):
        for name in sorted(os.listdir(custom_dir)):
            manifest = os.path.join(custom_dir, name, "compatibilitytool.vdf")
            internal, display = name, name
            if os.path.isfile(manifest):
                try:
                    data = vdf.text_load(manifest)
                    compat = vdf.get_ci(data, "compatibilitytools", {}) or {}
                    tools_node = vdf.get_ci(compat, "compat_tools", {}) or {}
                    for key, info in tools_node.items():
                        internal = key
                        if isinstance(info, dict):
                            display = vdf.get_ci(info, "display_name") or key
                        break
                except vdf.VDFError:
                    pass
            if internal not in seen:
                seen.add(internal)
                tools.append({"name": internal, "label": display, "source": "custom"})

    # Valve's own Proton builds live in steamapps/common with internal names
    # that do not match the folder, so derive them from the folder name.
    common = os.path.join(root, "steamapps", "common")
    if os.path.isdir(common):
        for name in sorted(os.listdir(common)):
            if not name.lower().startswith("proton"):
                continue
            internal = _valve_proton_internal_name(name)
            if internal and internal not in seen:
                seen.add(internal)
                tools.append({"name": internal, "label": name, "source": "valve"})

    for fallback in ("proton_experimental", "proton_hotfix"):
        if fallback not in seen:
            seen.add(fallback)
            tools.append({"name": fallback, "label": fallback.replace("_", " ").title(),
                          "source": "valve"})
    return tools


def _valve_proton_internal_name(folder):
    lowered = folder.lower().strip()
    if "experimental" in lowered:
        return "proton_experimental"
    if "hotfix" in lowered:
        return "proton_hotfix"
    rest = lowered.replace("proton", "").strip("- ").strip()
    if not rest:
        return None
    # "9.0 (Beta)" -> "9.0" -> "proton_9"; "6.3" -> "proton_63" (Valve's own
    # naming drops the dot for the old 3.x-6.x line and keeps only the major
    # version from 7 onward).
    version = rest.split()[0].split("-")[0]
    parts = version.split(".")
    if not parts[0].isdigit():
        return None
    major = int(parts[0])
    if major >= 7:
        return "proton_%d" % major
    if len(parts) > 1 and parts[1].isdigit():
        return "proton_%d%s" % (major, parts[1])
    return "proton_%d" % major


def get_compat_tool(root, appid):
    path = config_vdf_path(root)
    if not os.path.isfile(path):
        return None
    try:
        mapping = _compat_mapping(vdf.text_load(path))
    except vdf.VDFError:
        return None
    if not mapping:
        return None
    entry = vdf.get_ci(mapping, str(to_unsigned32(appid)))
    if isinstance(entry, dict):
        return vdf.get_ci(entry, "name") or None
    return None


def set_compat_tool(root, appid, tool_name, backup=True):
    """Force a compatibility tool for a shortcut by editing config.vdf.

    Steam rewrites this file on exit, so it must not be running.
    """
    path = config_vdf_path(root)
    if not os.path.isfile(path):
        raise SteamError("config.vdf not found at %s" % path)
    config = vdf.text_load(path)
    mapping = _compat_mapping(config, create=True)
    key = str(to_unsigned32(appid))

    if not tool_name:
        for existing in list(mapping):
            if existing == key:
                del mapping[existing]
    else:
        mapping[key] = OrderedDict([
            ("name", tool_name),
            ("config", ""),
            ("priority", "250"),
        ])

    if backup:
        shutil.copy2(path, "%s.%s.bak" % (path, time.strftime("%Y%m%d-%H%M%S")))
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(vdf.text_dumps(config) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp_path, path)
    _prune_backups(path)
    return tool_name
