"""Command line entry point. With no arguments it opens the web UI."""

from __future__ import annotations

import argparse
import sys

from . import __version__, core, server, sgdb, steam

TICK = "+"
CROSS = "-"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="steamart",
        description="Import non-Steam games and give them proper Steam artwork.",
    )
    parser.add_argument("--version", action="version", version="SteamArt %s" % __version__)
    parser.add_argument("--steam-root", help="Path to the Steam folder if autodetection fails")
    parser.add_argument("--user", help="Steam profile ID (the userdata folder name)")

    subparsers = parser.add_subparsers(dest="command")

    ui = subparsers.add_parser("ui", help="Open the web interface (default)")
    ui.add_argument("--port", type=int, default=8523)
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    ui.add_argument("--verbose", action="store_true")

    subparsers.add_parser("info", help="Show the detected Steam install and profiles")
    subparsers.add_parser("list", help="List non-Steam games and their artwork status")

    key = subparsers.add_parser("key", help="Set your SteamGridDB API key")
    key.add_argument("api_key")

    scan = subparsers.add_parser("scan", help="Find game executables under a folder")
    scan.add_argument("folder")
    scan.add_argument("--depth", type=int, default=3)

    add = subparsers.add_parser("add", help="Add executables as non-Steam games")
    add.add_argument("paths", nargs="+")
    add.add_argument("--name", help="Override the display name (single path only)")
    add.add_argument("--no-art", action="store_true", help="Skip the artwork fetch")
    add.add_argument("--no-proton", action="store_true", help="Do not set a compatibility tool")

    art = subparsers.add_parser("art", help="Fetch the best artwork for games")
    art.add_argument("names", nargs="*", help="Game names to match; omit for all")
    art.add_argument("--overwrite", action="store_true", help="Replace artwork that already exists")
    art.add_argument("--kinds", help="Comma-separated slots, e.g. capsule,hero")

    remove = subparsers.add_parser("remove", help="Remove a non-Steam game")
    remove.add_argument("name")
    remove.add_argument("--keep-art", action="store_true")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "ui"

    config = core.Config()
    if args.steam_root:
        config.update({"steam_root": args.steam_root})

    if command == "key":
        return cmd_key(config, args)

    try:
        library = core.Library(config)
    except steam.SteamError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2

    if args.user:
        try:
            library.select_user(args.user)
        except steam.SteamError as exc:
            print("Error: %s" % exc, file=sys.stderr)
            return 2

    handlers = {
        "ui": cmd_ui,
        "info": cmd_info,
        "list": cmd_list,
        "scan": cmd_scan,
        "add": cmd_add,
        "art": cmd_art,
        "remove": cmd_remove,
    }
    try:
        return handlers[command](library, args) or 0
    except (steam.SteamError, sgdb.SGDBError) as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


# --------------------------------------------------------------------------

def cmd_ui(library, args):
    server.serve(
        library,
        port=getattr(args, "port", 8523),
        host=getattr(args, "host", "127.0.0.1"),
        open_browser=not getattr(args, "no_browser", False),
        verbose=getattr(args, "verbose", False),
    )


def cmd_key(config, args):
    key = args.api_key.strip()
    try:
        sgdb.Client(key).check_key()
    except sgdb.SGDBError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2
    config.update({"api_key": key})
    print("API key saved to %s" % config.path)
    return 0


def cmd_info(library, args):
    print("SteamArt %s" % __version__)
    print("Steam folder : %s" % (library.root or "NOT FOUND"))
    print("Steam running: %s" % ("yes - close it before importing" if steam.is_steam_running() else "no"))
    print("API key      : %s" % ("set" if library.config.get("api_key") else "NOT SET"))
    print("Settings file: %s" % library.config.path)
    if not library.root:
        return 1
    print("\nProfiles:")
    for user in library.users():
        marker = "*" if library.user and user["id32"] == library.user["id32"] else " "
        print("  %s %-12s %-24s %d non-Steam games"
              % (marker, user["id32"], user["name"], user["shortcut_count"]))
    tools = steam.list_compat_tools(library.root)
    if tools:
        print("\nCompatibility tools: %s" % ", ".join(t["name"] for t in tools))
    return 0


def cmd_list(library, args):
    games = library.games()
    if not games:
        print("No non-Steam games yet. Add some with:  steamart add /path/to/game.exe")
        return 0
    kinds = list(steam.ART_KINDS)
    print("%-40s %s  %s" % ("GAME", "  ".join(k[:4].upper() for k in kinds), "PROTON"))
    for game in games:
        marks = "  ".join(
            (TICK if game["art"][kind] else CROSS).center(4) for kind in kinds
        )
        print("%-40s %s  %s" % (game["name"][:40], marks, game["compat_tool"] or "-"))
    missing = sum(1 for g in games for k in kinds if not g["art"][k])
    print("\n%d games, %d artwork slots empty." % (len(games), missing))
    return 0


def cmd_scan(library, args):
    found = library.scan_folder(args.folder, args.depth)
    if not found:
        print("No game executables found under %s" % args.folder)
        return 0
    for item in found:
        flag = "(already added)" if item["already_added"] else ""
        print("%-34s %s %s" % (item["name"][:34], item["path"], flag))
    print("\n%d executables found." % len(found))
    return 0


def cmd_add(library, args):
    _warn_if_steam_running()
    specs = [{"path": p} for p in args.paths]
    if args.name:
        if len(specs) != 1:
            print("Error: --name only works with a single path", file=sys.stderr)
            return 2
        specs[0]["name"] = args.name

    result = library.add_games(
        specs,
        set_compat=not args.no_proton,
        fetch_art=not args.no_art,
        progress=lambda name: print("  fetching art for %s..." % name),
    )
    for game in result["added"]:
        print("%s added %s" % (TICK, game["name"]))
    for skip in result["skipped"]:
        print("%s skipped %s (%s)" % (CROSS, skip.get("name") or skip["path"], skip["reason"]))
    for art in result.get("art", []):
        _print_art_result(art)
    for error in result.get("compat_errors", []):
        print("%s compatibility tool: %s" % (CROSS, error))
    if result["added"]:
        print("\nRestart Steam to see them.")
    return 0


def cmd_art(library, args):
    games = library.games()
    if args.names:
        wanted = [n.lower() for n in args.names]
        games = [g for g in games
                 if any(w in g["name"].lower() for w in wanted)]
        if not games:
            print("No games matched %s" % ", ".join(args.names))
            return 1
    kinds = [k.strip() for k in args.kinds.split(",")] if args.kinds else None
    if kinds:
        unknown = [k for k in kinds if k not in steam.ART_KINDS]
        if unknown:
            print("Error: unknown slot(s) %s. Valid: %s"
                  % (", ".join(unknown), ", ".join(steam.ART_KINDS)), file=sys.stderr)
            return 2

    for game in games:
        print("%s..." % game["name"])
        result = library.auto_art(game["appid"], name=game["name"],
                                  kinds=kinds, overwrite=args.overwrite)
        _print_art_result(result, indent="  ")
    print("\nRestart Steam to see the new artwork.")
    return 0


def cmd_remove(library, args):
    _warn_if_steam_running()
    matches = [g for g in library.games() if args.name.lower() in g["name"].lower()]
    if not matches:
        print("No game matched %r" % args.name)
        return 1
    if len(matches) > 1:
        print("%r matched several games:" % args.name)
        for game in matches:
            print("  %s" % game["name"])
        return 1
    result = library.remove_game(matches[0]["appid"], delete_art=not args.keep_art)
    print("%s removed %s" % (TICK, result["name"]))
    return 0


def _print_art_result(result, indent=""):
    if result.get("errors", {}).get("_"):
        print("%s%s %s: %s" % (indent, CROSS, result.get("name", ""), result["errors"]["_"]))
        return
    parts = []
    for kind in steam.ART_KINDS:
        if kind in result.get("applied", {}):
            parts.append("%s %s" % (kind, TICK))
        elif kind in result.get("errors", {}):
            parts.append("%s !" % kind)
        elif kind in result.get("skipped", {}):
            parts.append("%s ." % kind)
    print("%s%s  %s" % (indent, result.get("name", ""), "  ".join(parts)))
    for kind, message in result.get("errors", {}).items():
        if kind != "_":
            print("%s   %s: %s" % (indent, kind, message))


def _warn_if_steam_running():
    if steam.is_steam_running():
        print("Warning: Steam is running. It rewrites shortcuts.vdf when it "
              "exits, which can undo changes made now. Close Steam first.\n")
