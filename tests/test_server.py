"""End-to-end check of the HTTP layer against a throwaway Steam tree.

Run with:  python3 tests/test_server.py
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from steamart import core, server, steam  # noqa: E402


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="steamart-http-")
        root = os.path.join(cls.tmp, "Steam")
        os.makedirs(os.path.join(root, "userdata", "777", "config"))
        os.makedirs(os.path.join(root, "config"))
        with open(os.path.join(root, "config", "config.vdf"), "w") as fh:
            fh.write('"InstallConfigStore"\n{\n}\n')

        cls.games = os.path.join(cls.tmp, "Games", "Celeste")
        os.makedirs(cls.games)
        cls.exe = os.path.join(cls.games, "Celeste.exe")
        with open(cls.exe, "wb") as fh:
            fh.write(b"\x00" * (128 * 1024))

        config = core.Config(os.path.join(cls.tmp, "config.json"))
        config.update({"steam_root": root, "api_key": ""})
        library = core.Library(config)

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.library = library
        cls.httpd.verbose = False
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read(), response.headers.get("Content-Type")

    def json_get(self, path):
        status, body, _ = self.get(path)
        return status, json.loads(body.decode())

    def json_post(self, path, payload):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_serves_the_page_and_assets(self):
        for path, fragment in (("/", b"SteamArt"), ("/app.js", b"autoAll"),
                               ("/style.css", b".game-grid")):
            status, body, _ = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(fragment, body, path)

    def test_static_paths_cannot_escape_the_web_folder(self):
        url = "http://127.0.0.1:%d/../../steamart/core.py" % self.port
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                body = response.read()
            self.assertNotIn(b"class Library", body)
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_state_describes_the_install(self):
        status, data = self.json_get("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(data["steam_root"].endswith("Steam"))
        self.assertEqual(data["user"]["id32"], "777")
        self.assertEqual(data["games"], [])
        self.assertFalse(data["config"]["api_key_set"])
        self.assertEqual([k["key"] for k in data["art_kinds"]], list(steam.ART_KINDS))

    def test_browse_and_add_and_remove(self):
        status, data = self.json_get(
            "/api/browse?path=" + urllib.parse.quote(self.games))
        self.assertEqual(status, 200)
        self.assertEqual([f["name"] for f in data["files"]], ["Celeste.exe"])

        status, data = self.json_post("/api/add", {
            "games": [{"path": self.exe, "name": "Celeste"}],
            "set_compat": False, "fetch_art": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(data["added"]), 1)

        status, data = self.json_get("/api/games")
        self.assertEqual(data["games"][0]["name"], "Celeste")
        appid = data["games"][0]["appid"]

        status, data = self.json_post("/api/remove", {"appid": appid})
        self.assertEqual(status, 200)
        status, data = self.json_get("/api/games")
        self.assertEqual(data["games"], [])

    def test_artwork_is_served_from_the_grid_folder(self):
        self.json_post("/api/add", {
            "games": [{"path": self.exe, "name": "Celeste"}],
            "set_compat": False, "fetch_art": False,
        })
        status, data = self.json_get("/api/games")
        appid = data["games"][0]["appid"]

        # A one-pixel PNG, so the browser gets something real back.
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000a49444154789c6300010000050001"
            "0d0a2db40000000049454e44ae426082")
        steam.write_art(self.httpd.library.user["path"], appid, "capsule", png, ".png")

        status, body, content_type = self.get("/api/art/%d/capsule" % appid)
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "image/png")
        self.assertEqual(body, png)

        self.json_post("/api/remove", {"appid": appid})

    def test_errors_come_back_as_json(self):
        status, data = self.json_post("/api/auto-art", {})
        self.assertEqual(status, 400)
        self.assertIn("appid", data["error"])

        try:
            self.get("/api/nope")
            self.fail("expected 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
            self.assertIn("Unknown endpoint", json.loads(exc.read().decode())["error"])

    def test_state_reports_launcher_availability(self):
        status, data = self.json_get("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("launcher", data)
        self.assertIn("supported", data["launcher"])
        self.assertIsInstance(data["launcher"]["installed"], bool)

    def test_art_lookup_without_a_key_reports_cleanly(self):
        status, data = self.json_post("/api/check-key", {"api_key": ""})
        self.assertEqual(status, 400)
        self.assertIn("API key", data["error"])


class QuitTests(unittest.TestCase):
    """Quit gets its own server, since a passing test kills it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="steamart-quit-")
        root = os.path.join(self.tmp, "Steam")
        os.makedirs(os.path.join(root, "userdata", "5", "config"))
        os.makedirs(os.path.join(root, "config"))
        config = core.Config(os.path.join(self.tmp, "config.json"))
        config.update({"steam_root": root, "api_key": ""})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.library = core.Library(config)
        self.httpd.verbose = False
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_quit_answers_then_stops_the_server(self):
        url = "http://127.0.0.1:%d/api/quit" % self.port
        request = urllib.request.Request(url, data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read().decode())["ok"])

        # The reply comes first and the shutdown lands shortly after, so the
        # browser never sees a dropped connection.
        self.thread.join(timeout=10)
        self.assertFalse(self.thread.is_alive(), "server should have stopped")

    def test_already_running_detects_a_live_instance(self):
        self.assertTrue(server._already_running("127.0.0.1", self.port))

    def test_already_running_is_false_for_a_dead_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        self.assertFalse(server._already_running("127.0.0.1", dead_port, timeout=0.5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
