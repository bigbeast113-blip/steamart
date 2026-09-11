"""Local web server for the SteamArt UI.

Binds to localhost only. Everything runs in-process against the Library
object; the browser is just the front end.
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import core, launcher, sgdb, steam
from .steam import ART_KINDS

WEB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Raw:
    """An API result that is bytes rather than JSON, e.g. an artwork file."""

    def __init__(self, data, content_type):
        self.data = data
        self.content_type = content_type


class Handler(BaseHTTPRequestHandler):
    server_version = "SteamArt"
    protocol_version = "HTTP/1.1"

    # Steam operations mutate shared files, so serialise them.
    lock = threading.Lock()

    # -- helpers ----------------------------------------------------------

    @property
    def library(self):
        return self.server.library

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, status, body, content_type="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, default=str))

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ApiError("Request body was not valid JSON")
        return parsed if isinstance(parsed, dict) else {}

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if path.startswith("/api/"):
                with self.lock:
                    payload = self._api(method, path[5:], query)
                if isinstance(payload, Raw):
                    self._send(200, payload.data, payload.content_type)
                else:
                    self._json(payload)
            else:
                self._static(path)
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except (steam.SteamError, sgdb.SGDBError) as exc:
            self._json({"error": str(exc)}, 400)
        except BrokenPipeError:
            pass
        except Exception as exc:
            if self.server.verbose:
                traceback.print_exc()
            self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        target = os.path.normpath(os.path.join(WEB_ROOT, path.lstrip("/")))
        if not target.startswith(WEB_ROOT) or not os.path.isfile(target):
            self._send(404, "Not found", "text/plain")
            return
        content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as fh:
            self._send(200, fh.read(), content_type)

    # -- API --------------------------------------------------------------

    def _api(self, method, route, query):
        library = self.library

        if route == "state":
            return self._state()

        if route == "settings" and method == "POST":
            values = self._body()
            if "api_key" in values:
                values["api_key"] = str(values["api_key"]).strip()
            library.config.update(values)
            if "steam_root" in values or "user_id32" in values:
                if values.get("user_id32"):
                    try:
                        library.select_user(values["user_id32"])
                    except steam.SteamError:
                        library.refresh_steam()
                else:
                    library.refresh_steam()
            return self._state()

        if route == "check-key" and method == "POST":
            key = str(self._body().get("api_key", "")).strip()
            client = sgdb.Client(key)
            client.check_key()
            library.config.update({"api_key": key})
            return {"ok": True}

        if route == "games":
            return {"games": library.games()}

        if route == "places":
            return {"places": core.list_drives()}

        if route == "browse":
            return core.browse(query.get("path") or "")

        if route == "scan" and method == "POST":
            body = self._body()
            found = library.scan_folder(body.get("path") or "",
                                        int(body.get("depth") or 3))
            return {"found": found}

        if route == "add" and method == "POST":
            body = self._body()
            games = body.get("games") or []
            if not games:
                raise ApiError("Nothing selected to add")
            return library.add_games(
                games,
                set_compat=body.get("set_compat"),
                fetch_art=body.get("fetch_art"),
            )

        if route == "remove" and method == "POST":
            body = self._body()
            return library.remove_game(_appid(body), bool(body.get("delete_art", True)))

        if route == "rename" and method == "POST":
            body = self._body()
            name = str(body.get("name", "")).strip()
            if not name:
                raise ApiError("A name is required")
            return library.rename_game(_appid(body), name)

        if route == "auto-art" and method == "POST":
            body = self._body()
            return library.auto_art(
                _appid(body),
                name=body.get("name"),
                kinds=body.get("kinds"),
                game_id=body.get("game_id"),
                overwrite=body.get("overwrite"),
            )

        if route == "search":
            return {"results": library.client.search_all(
                query.get("q", ""), exe=query.get("exe") or None)}

        if route == "candidates":
            kind = query.get("kind", "capsule")
            if kind not in ART_KINDS:
                raise ApiError("Unknown artwork kind %r" % kind)
            game_id = query.get("game_id")
            if not game_id:
                raise ApiError("game_id is required")
            return {"kind": kind, "assets": library.candidates(game_id, kind)}

        if route == "apply" and method == "POST":
            body = self._body()
            kind = body.get("kind")
            if kind not in ART_KINDS:
                raise ApiError("Unknown artwork kind %r" % kind)
            url = body.get("url")
            if not url:
                raise ApiError("An image url is required")
            return library.apply_asset(_appid(body), kind, url)

        if route == "clear-art" and method == "POST":
            body = self._body()
            removed = library.clear_art(_appid(body), body.get("kinds"))
            return {"removed": removed}

        if route == "compat" and method == "POST":
            body = self._body()
            tool = library.set_compat(_appid(body), body.get("tool"))
            return {"tool": tool}

        if route == "install-launcher" and method == "POST":
            body = self._body()
            if body.get("remove"):
                return {"removed": launcher.remove(), "installed": False}
            written = launcher.install()
            return {"written": written, "installed": True}

        if route == "quit" and method == "POST":
            # Answer first, shut down a beat later, so the browser sees the
            # reply instead of a connection error.
            threading.Timer(0.4, self.server.shutdown).start()
            return {"ok": True}

        if route.startswith("art/"):
            return self._art(route)

        raise ApiError("Unknown endpoint /api/%s" % route, 404)

    def _art(self, route):
        # Served through a helper because the grid folder is outside WEB_ROOT.
        parts = route.split("/")
        if len(parts) != 3:
            raise ApiError("Bad artwork path", 404)
        _, appid, kind = parts
        if kind not in ART_KINDS:
            raise ApiError("Unknown artwork kind", 404)
        self.library.require_user()
        path = steam.existing_art(self.library.user["path"], int(appid), kind)
        if not path:
            raise ApiError("No artwork installed", 404)
        content_type = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as fh:
            return Raw(fh.read(), content_type)

    def _state(self):
        library = self.library
        config = dict(library.config.data)
        config["api_key_set"] = bool(config.get("api_key"))
        config["api_key"] = config["api_key"][:4] + "..." if config.get("api_key") else ""
        state = {
            "version": _version(),
            "steam_root": library.root,
            "steam_running": steam.is_steam_running(),
            "users": library.users(),
            "user": library.user,
            "config": config,
            "compat_tools": steam.list_compat_tools(library.root) if library.root else [],
            "art_kinds": [
                {"key": key, "label": spec["label"], "hint": spec["hint"]}
                for key, spec in ART_KINDS.items()
            ],
            "platform": os.name,
            "launcher": {
                "supported": launcher.supported(),
                "installed": launcher.installed(),
                "paths": launcher.installed_paths(),
            },
        }
        try:
            state["games"] = library.games()
        except steam.SteamError as exc:
            state["games"] = []
            state["warning"] = str(exc)
        return state


def _appid(body):
    value = body.get("appid")
    if value in (None, ""):
        raise ApiError("appid is required")
    return int(value)


def _version():
    from . import __version__
    return __version__


def _already_running(host, port, timeout=1.5):
    """True when a SteamArt instance is already answering on this port.

    Clicking the desktop launcher twice should reopen the tab, not start a
    second copy writing to the same files.
    """
    url = "http://%s:%d/api/state" % (host, port)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return "art_kinds" in payload
    except Exception:
        return False


def _free_port(preferred, host="127.0.0.1"):
    for port in [preferred] + list(range(preferred + 1, preferred + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
                return port
            except OSError:
                continue
    raise steam.SteamError("No free port near %d" % preferred)


def serve(library=None, port=8523, host="127.0.0.1", open_browser=True, verbose=False,
          reuse=True):
    if reuse and _already_running(host, port):
        url = "http://%s:%d/" % (host, port)
        print("SteamArt is already running at %s - opening that instead." % url)
        if open_browser:
            webbrowser.open(url)
        return

    library = library or core.Library()
    port = _free_port(port, host)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.library = library
    httpd.verbose = verbose
    httpd.daemon_threads = True

    url = "http://%s:%d/" % (host, port)
    print("SteamArt is running at %s" % url)
    if library.root:
        print("Steam:   %s" % library.root)
        if library.user:
            print("Profile: %s (%s)" % (library.user["name"], library.user["id32"]))
    else:
        print("Steam:   not found - set the folder in Settings")
    if not library.config.get("api_key"):
        print("Note:    add a SteamGridDB API key in Settings before fetching art.")
    print("Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
