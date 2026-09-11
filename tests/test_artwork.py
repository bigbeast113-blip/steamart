"""End-to-end test of the artwork pipeline against a mocked SteamGridDB.

This is the test that matters: it proves that after a game is matched, the
highest-rated image for each of the five slots is downloaded and written under
exactly the filename Steam reads. No network, no API key.

Run with:  python3 tests/test_artwork.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from steamart import core, sgdb, steam  # noqa: E402

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")


class FakeResponse(io.BytesIO):
    def __init__(self, payload, content_type="application/json"):
        super().__init__(payload)
        self.status = 200
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeSteamGridDB:
    """Stands in for urlopen. Records every URL the client asks for."""

    GAME_ID = 3532
    GAME_NAME = "Hand of Fate"

    # Deliberately not in rating order, and with the wrong sizes mixed in.
    ASSETS = {
        "grids": [
            {"id": 1, "url": "https://cdn/x/wrongsize.png", "width": 460,
             "height": 215, "score": 900, "style": "alternate", "mime": "image/png"},
            {"id": 2, "url": "https://cdn/x/capsule-ok.png", "width": 600,
             "height": 900, "score": 12, "style": "alternate", "mime": "image/png"},
            {"id": 3, "url": "https://cdn/x/capsule-best.png", "width": 600,
             "height": 900, "score": 480, "style": "alternate", "mime": "image/png"},
            {"id": 4, "url": "https://cdn/x/wide-best.png", "width": 920,
             "height": 430, "score": 300, "style": "alternate", "mime": "image/png"},
        ],
        "heroes": [
            {"id": 5, "url": "https://cdn/x/hero-meh.jpg", "width": 1920,
             "height": 620, "score": 5, "mime": "image/jpeg"},
            {"id": 6, "url": "https://cdn/x/hero-best.jpg", "width": 1920,
             "height": 620, "score": 77, "mime": "image/jpeg"},
        ],
        "logos": [
            {"id": 7, "url": "https://cdn/x/logo-best.png", "width": 640,
             "height": 360, "score": 40, "mime": "image/png"},
        ],
        "icons": [
            {"id": 8, "url": "https://cdn/x/icon-best.png", "width": 256,
             "height": 256, "score": 9, "mime": "image/png"},
        ],
    }

    def __init__(self):
        self.calls = []
        self.downloads = []
        self.dimension_404 = False
        # Number of leading asset requests to answer with HTTP 429.
        self.rate_limit_first = 0
        self._limited = 0

    def __call__(self, request, timeout=None):
        url = getattr(request, "full_url", request)
        self.calls.append(url)
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        if (self.rate_limit_first and "/game/" in parsed.path
                and self._limited < self.rate_limit_first):
            self._limited += 1
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

        if parsed.netloc == "cdn":
            self.downloads.append(url)
            mime = "image/jpeg" if url.endswith(".jpg") else "image/png"
            return FakeResponse(PNG, mime)

        path = parsed.path
        if path.startswith("/api/v2/search/autocomplete/"):
            term = urllib.parse.unquote(path.rsplit("/", 1)[1]).lower()
            hit = term in ("hand of fate", "fate", "handoffate")
            data = [{"id": self.GAME_ID, "name": self.GAME_NAME}] if hit else []
            return FakeResponse(json.dumps({"success": True, "data": data}).encode())

        for endpoint, assets in self.ASSETS.items():
            if path.startswith("/api/v2/%s/game/" % endpoint):
                if "dimensions" in params:
                    if self.dimension_404:
                        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
                    wanted = set(params["dimensions"][0].split(","))
                    assets = [a for a in assets
                              if "%dx%d" % (a["width"], a["height"]) in wanted]
                return FakeResponse(
                    json.dumps({"success": True, "data": assets}).encode())

        raise AssertionError("unexpected request: %s" % url)


class ArtworkPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="steamart-art-")
        root = os.path.join(self.tmp, "Steam")
        os.makedirs(os.path.join(root, "userdata", "42", "config"))
        os.makedirs(os.path.join(root, "config"))
        with open(os.path.join(root, "config", "config.vdf"), "w") as fh:
            fh.write('"InstallConfigStore"\n{\n}\n')

        games = os.path.join(self.tmp, "Games")
        os.makedirs(games)
        self.exe = os.path.join(games, "handsoffate.exe")
        with open(self.exe, "wb") as fh:
            fh.write(b"\x00" * (128 * 1024))

        config = core.Config(os.path.join(self.tmp, "config.json"))
        config.update({"steam_root": root, "api_key": "test-key"})
        self.library = core.Library(config)

        self.http = FakeSteamGridDB()
        self.original_urlopen = sgdb.urllib.request.urlopen
        sgdb.urllib.request.urlopen = self.http

    def tearDown(self):
        sgdb.urllib.request.urlopen = self.original_urlopen
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add(self, name="Handsoffate"):
        result = self.library.add_games(
            [{"path": self.exe, "name": name}], set_compat=False, fetch_art=False)
        return result["added"][0]["appid"]

    @property
    def grid(self):
        return steam.grid_path(self.library.user["path"])

    # -- the whole point -------------------------------------------------

    def test_all_five_slots_are_downloaded_and_written(self):
        appid = self._add()
        result = self.library.auto_art(appid, name="Handsoffate", exe=self.exe)

        self.assertEqual(result["errors"], {}, "no slot should have errored")
        self.assertEqual(sorted(result["applied"]), sorted(steam.ART_KINDS),
                         "every slot should have been filled")
        self.assertEqual(result["game"]["name"], "Hand of Fate")

    def test_files_land_on_the_names_steam_reads(self):
        appid = self._add()
        self.library.auto_art(appid, name="Handsoffate", exe=self.exe)

        expected = {
            "%dp.png" % appid,        # library capsule
            "%d.png" % appid,         # wide capsule
            "%d_hero.jpg" % appid,
            "%d_logo.png" % appid,
            "%d_icon.png" % appid,
        }
        self.assertTrue(expected.issubset(set(os.listdir(self.grid))),
                        "missing: %s" % (expected - set(os.listdir(self.grid))))

    def test_the_appid_matches_steams_own_derivation(self):
        """If this drifts, artwork lands beside the game instead of on it."""
        appid = self._add(name="Hand of Fate")
        entry = steam.load_shortcuts(self.library.user["path"])[0]
        expected = zlib.crc32(
            ('"%s"' % self.exe + "Hand of Fate").encode("utf-8")) | 0x80000000
        self.assertEqual(appid, expected)
        self.assertEqual(steam.to_unsigned32(entry["appid"]), expected)

    def test_the_highest_rated_image_wins_each_slot(self):
        appid = self._add()
        result = self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        self.assertEqual(result["applied"]["capsule"], "https://cdn/x/capsule-best.png")
        self.assertEqual(result["applied"]["wide"], "https://cdn/x/wide-best.png")
        self.assertEqual(result["applied"]["hero"], "https://cdn/x/hero-best.jpg")
        self.assertEqual(result["applied"]["logo"], "https://cdn/x/logo-best.png")
        self.assertEqual(result["applied"]["icon"], "https://cdn/x/icon-best.png")

    def test_a_popular_image_of_the_wrong_shape_does_not_win_the_capsule(self):
        appid = self._add()
        result = self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        self.assertNotEqual(result["applied"]["capsule"], "https://cdn/x/wrongsize.png")

    def test_the_library_reports_the_art_as_present_afterwards(self):
        appid = self._add()
        self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        game = self.library.games()[0]
        self.assertTrue(all(game["art"].values()),
                        "card would still show 'no artwork': %s" % game["art"])

    def test_the_icon_path_is_recorded_in_the_shortcut(self):
        appid = self._add()
        self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        game = self.library.games()[0]
        self.assertTrue(game["icon"].endswith("%d_icon.png" % appid), game["icon"])
        self.assertTrue(os.path.isfile(game["icon"]))

    def test_writing_the_icon_does_not_disturb_the_appid(self):
        # Setting the icon rewrites shortcuts.vdf; if that changed the derived
        # ID, the artwork just written would instantly be orphaned.
        appid = self._add()
        self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        self.assertEqual(self.library.games()[0]["appid"], appid)

    # -- robustness ------------------------------------------------------

    def test_a_404_on_the_filtered_query_still_finds_artwork(self):
        """A 404 must not be mistaken for 'there is artwork, just none here'."""
        self.http.dimension_404 = True
        appid = self._add()
        result = self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        self.assertIn("capsule", result["applied"],
                      "should have retried without the dimension filter")
        self.assertEqual(result["errors"], {})

    def test_existing_artwork_is_left_alone_by_default(self):
        appid = self._add()
        self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        before = len(self.http.downloads)
        again = self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        self.assertEqual(len(self.http.downloads), before)
        self.assertEqual(sorted(again["skipped"]), sorted(steam.ART_KINDS))

    def test_overwrite_replaces_every_slot(self):
        appid = self._add()
        self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        result = self.library.auto_art(appid, name="Handsoffate", exe=self.exe,
                                       overwrite=True)
        self.assertEqual(sorted(result["applied"]), sorted(steam.ART_KINDS))

    def test_a_game_costs_only_a_handful_of_requests(self):
        """Request count is what decides whether a big library survives the
        rate limit. Capsule and wide must share one /grids call."""
        appid = self._add()
        self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        api_calls = [c for c in self.http.calls if "/api/v2/" in c]
        asset_calls = [c for c in api_calls if "/game/" in c]
        self.assertLessEqual(len(asset_calls), 4,
                             "one call per endpoint at most: %s" % asset_calls)
        self.assertEqual(len([c for c in asset_calls if "/grids/" in c]), 1,
                         "capsule and wide should reuse a single grids request")

    def test_no_dimension_filter_is_sent(self):
        # Sizes are narrowed locally so the two grid slots can share a request.
        appid = self._add()
        self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        self.assertFalse([c for c in self.http.calls if "dimensions=" in c])

    def test_a_rate_limit_is_retried_rather_than_failing_the_slot(self):
        self.http.rate_limit_first = 2
        waits = []
        original = sgdb.Client._sleep
        sgdb.Client._sleep = staticmethod(lambda s: waits.append(s))
        try:
            appid = self._add()
            result = self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        finally:
            sgdb.Client._sleep = original
        self.assertEqual(result["errors"], {})
        self.assertEqual(sorted(result["applied"]), sorted(steam.ART_KINDS))
        self.assertEqual(len(waits), 2, "should have backed off twice")
        self.assertTrue(all(w <= sgdb.RATE_LIMIT_MAX_WAIT for w in waits))

    def test_a_persistent_rate_limit_is_reported_clearly(self):
        self.http.rate_limit_first = 99
        original = sgdb.Client._sleep
        sgdb.Client._sleep = staticmethod(lambda s: None)
        try:
            appid = self._add()
            result = self.library.auto_art(appid, name="Handsoffate", exe=self.exe)
        finally:
            sgdb.Client._sleep = original
        self.assertIn("rate limit", str(result["errors"]).lower())

    def test_retry_after_header_is_honoured(self):
        exc = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "4"}, None)
        self.assertEqual(sgdb._retry_delay(exc, 0), 4.0)
        capped = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "900"}, None)
        self.assertEqual(sgdb._retry_delay(capped, 0), sgdb.RATE_LIMIT_MAX_WAIT)

    def test_import_with_art_fills_everything_in_one_go(self):
        result = self.library.add_games(
            [{"path": self.exe, "name": "Handsoffate"}],
            set_compat=False, fetch_art=True)
        self.assertEqual(len(result["art"]), 1)
        self.assertEqual(sorted(result["art"][0]["applied"]), sorted(steam.ART_KINDS))
        self.assertTrue(all(self.library.games()[0]["art"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
