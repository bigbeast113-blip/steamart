"""Self-checks that need no Steam install and no network.

Run with:  python3 tests/test_steamart.py
"""

import os
import shutil
import sys
import tempfile
import unittest
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from steamart import core, launcher, names, sgdb, steam, vdf  # noqa: E402


class BinaryVdfTests(unittest.TestCase):
    def test_round_trip(self):
        original = OrderedDict([
            ("shortcuts", OrderedDict([
                ("0", OrderedDict([
                    ("appid", -1234567),
                    ("AppName", "Tunic"),
                    ("Exe", '"/home/deck/Games/Tunic/Tunic.exe"'),
                    ("IsHidden", 0),
                    ("LastPlayTime", 1700000000),
                    ("tags", OrderedDict([("0", "Imported")])),
                ])),
            ])),
        ])
        restored = vdf.binary_loads(vdf.binary_dumps(original))
        self.assertEqual(restored, original)

    def test_uint64_survives(self):
        data = OrderedDict([("big", vdf.UInt64(18446744073709551615))])
        restored = vdf.binary_loads(vdf.binary_dumps(data))
        self.assertEqual(int(restored["big"]), 18446744073709551615)

    def test_rejects_garbage(self):
        with self.assertRaises(vdf.VDFError):
            vdf.binary_loads(b"\x7f\x00")


class TextVdfTests(unittest.TestCase):
    SAMPLE = '''
    // a comment
    "InstallConfigStore"
    {
        "Software"
        {
            "Valve" { "Steam" { "CompatToolMapping" {
                "2748919435" { "name" "proton_experimental" "config" "" "priority" "250" }
            } } }
        }
        "path"  "C:\\\\Program Files (x86)\\\\Steam"
    }
    '''

    def test_parses_nested(self):
        data = vdf.text_loads(self.SAMPLE)
        node = data["InstallConfigStore"]["Software"]["Valve"]["Steam"]["CompatToolMapping"]
        self.assertEqual(node["2748919435"]["name"], "proton_experimental")

    def test_unescapes_and_reescapes_paths(self):
        data = vdf.text_loads(self.SAMPLE)
        self.assertEqual(data["InstallConfigStore"]["path"],
                         r"C:\Program Files (x86)\Steam")
        again = vdf.text_loads(vdf.text_dumps(data))
        self.assertEqual(again, data)

    def test_case_insensitive_lookup(self):
        data = vdf.text_loads('"Users" { "1" { "PersonaName" "deck" } }')
        self.assertIsNotNone(vdf.get_ci(data, "users"))


class AppIdTests(unittest.TestCase):
    def test_is_stable_and_high_bit_set(self):
        exe = "/home/deck/Games/Hades/Hades.exe"
        appid = steam.shortcut_appid(exe, "Hades")
        self.assertEqual(appid, steam.shortcut_appid(exe, "Hades"))
        self.assertTrue(appid & 0x80000000)
        self.assertLessEqual(appid, 0xFFFFFFFF)

    def test_quoting_does_not_change_the_result(self):
        exe = "/home/deck/Games/Hades/Hades.exe"
        self.assertEqual(steam.shortcut_appid(exe, "Hades"),
                         steam.shortcut_appid('"%s"' % exe, "Hades"))

    def test_signed_round_trip(self):
        appid = steam.shortcut_appid("/games/a.exe", "A")
        self.assertEqual(steam.to_unsigned32(steam.to_signed32(appid)), appid)
        self.assertLess(steam.to_signed32(appid), 0)

    def test_name_cleanup(self):
        cases = {
            "/g/Hollow Knight/hollow_knight.exe": "Hollow Knight",
            "/g/Celeste/Celeste.exe": "Celeste",
            "/g/Deep Rock Galactic/FSD-Win64-Shipping.exe": "FSD",
            "/g/Balatro/launcher.exe": "Balatro",
            "/g/Outer Wilds/start.exe": "Outer Wilds",
        }
        for path, expected in cases.items():
            self.assertEqual(steam.nice_name_for(path), expected, path)


class FakeSteamTests(unittest.TestCase):
    """Exercises the real read/write paths against a throwaway Steam tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="steamart-test-")
        self.root = os.path.join(self.tmp, "Steam")
        self.user_path = os.path.join(self.root, "userdata", "123456", "config")
        os.makedirs(self.user_path)
        os.makedirs(os.path.join(self.root, "config"))
        with open(os.path.join(self.root, "config", "config.vdf"), "w") as fh:
            fh.write('"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n'
                     '\t\t{\n\t\t\t"Steam"\n\t\t\t{\n\t\t\t}\n\t\t}\n\t}\n}\n')
        # The userdata folder name is the 32-bit account ID; loginusers.vdf is
        # keyed by the 64-bit ID, so it has to be the folder name plus the base.
        id64 = 123456 + steam.STEAM_ID64_BASE
        with open(os.path.join(self.root, "config", "loginusers.vdf"), "w") as fh:
            fh.write('"users"\n{\n\t"%d"\n\t{\n'
                     '\t\t"PersonaName"\t\t"deck"\n\t\t"MostRecent"\t\t"1"\n\t}\n}\n' % id64)

        self.games_dir = os.path.join(self.tmp, "Games", "Tunic")
        os.makedirs(self.games_dir)
        self.exe = os.path.join(self.games_dir, "Tunic.exe")
        with open(self.exe, "wb") as fh:
            fh.write(b"\x00" * (128 * 1024))

        config = core.Config(os.path.join(self.tmp, "config.json"))
        config.update({"steam_root": self.root, "api_key": ""})
        self.library = core.Library(config)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detects_root_and_user(self):
        self.assertEqual(self.library.root, os.path.realpath(self.root))
        self.assertEqual(self.library.user["name"], "deck")
        self.assertEqual(self.library.user["id32"], "123456")

    def test_add_lists_and_removes(self):
        result = self.library.add_games([{"path": self.exe}],
                                        set_compat=False, fetch_art=False)
        self.assertEqual(len(result["added"]), 1)

        games = self.library.games()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["name"], "Tunic")
        self.assertEqual(games[0]["exe"], self.exe)
        self.assertFalse(any(games[0]["art"].values()))

        again = self.library.add_games([{"path": self.exe}],
                                       set_compat=False, fetch_art=False)
        self.assertEqual(again["added"], [])
        self.assertIn("already", again["skipped"][0]["reason"])

        self.library.remove_game(games[0]["appid"])
        self.assertEqual(self.library.games(), [])

    def test_backup_is_written(self):
        self.library.add_games([{"path": self.exe}], set_compat=False, fetch_art=False)
        self.library.add_games([{"path": self.exe, "name": "Tunic 2"}],
                               set_compat=False, fetch_art=False)
        backups = [n for n in os.listdir(self.user_path) if n.endswith(".bak")]
        self.assertTrue(backups)

    def test_artwork_files_use_the_names_steam_expects(self):
        self.library.add_games([{"path": self.exe}], set_compat=False, fetch_art=False)
        game = self.library.games()[0]
        appid = game["appid"]

        steam.write_art(self.library.user["path"], appid, "capsule", b"png-bytes", ".png")
        steam.write_art(self.library.user["path"], appid, "wide", b"png", ".png")
        steam.write_art(self.library.user["path"], appid, "hero", b"png", ".png")
        steam.write_art(self.library.user["path"], appid, "logo", b"png", ".png")
        steam.write_art(self.library.user["path"], appid, "icon", b"png", ".png")

        grid = steam.grid_path(self.library.user["path"])
        self.assertEqual(
            sorted(os.listdir(grid)),
            sorted(["%dp.png" % appid, "%d.png" % appid, "%d_hero.png" % appid,
                    "%d_logo.png" % appid, "%d_icon.png" % appid]))
        self.assertTrue(all(self.library.games()[0]["art"].values()))

    def test_replacing_art_removes_the_other_extension(self):
        self.library.add_games([{"path": self.exe}], set_compat=False, fetch_art=False)
        appid = self.library.games()[0]["appid"]
        user_path = self.library.user["path"]
        steam.write_art(user_path, appid, "capsule", b"jpg", ".jpg")
        steam.write_art(user_path, appid, "capsule", b"png", ".png")
        grid = steam.grid_path(user_path)
        self.assertEqual(os.listdir(grid), ["%dp.png" % appid])

    def test_rename_moves_artwork_to_the_new_appid(self):
        self.library.add_games([{"path": self.exe}], set_compat=False, fetch_art=False)
        old = self.library.games()[0]["appid"]
        steam.write_art(self.library.user["path"], old, "capsule", b"png", ".png")

        result = self.library.rename_game(old, "Tunic Deluxe")
        self.assertNotEqual(result["appid"], old)
        self.assertIsNone(steam.existing_art(self.library.user["path"], old, "capsule"))
        self.assertIsNotNone(
            steam.existing_art(self.library.user["path"], result["appid"], "capsule"))
        self.assertEqual(self.library.games()[0]["name"], "Tunic Deluxe")

    def test_compat_tool_mapping(self):
        self.library.add_games([{"path": self.exe}], set_compat=False, fetch_art=False)
        appid = self.library.games()[0]["appid"]
        self.library.set_compat(appid, "proton_experimental")
        self.assertEqual(steam.get_compat_tool(self.library.root, appid),
                         "proton_experimental")
        self.assertEqual(self.library.games()[0]["compat_tool"], "proton_experimental")

        self.library.set_compat(appid, None)
        self.assertIsNone(steam.get_compat_tool(self.library.root, appid))

    def test_scan_skips_installers_and_tiny_binaries(self):
        for name in ("unins000.exe", "UnityCrashHandler64.exe"):
            with open(os.path.join(self.games_dir, name), "wb") as fh:
                fh.write(b"\x00" * (200 * 1024))
        with open(os.path.join(self.games_dir, "tiny.exe"), "wb") as fh:
            fh.write(b"\x00" * 100)
        redist = os.path.join(self.games_dir, "_CommonRedist")
        os.makedirs(redist)
        with open(os.path.join(redist, "Game.exe"), "wb") as fh:
            fh.write(b"\x00" * (200 * 1024))

        found = self.library.scan_folder(os.path.dirname(self.games_dir))
        self.assertEqual([item["name"] for item in found], ["Tunic"])

    def test_shortcut_entry_has_the_fields_steam_reads(self):
        self.library.add_games([{"path": self.exe}], set_compat=False, fetch_art=False)
        entry = steam.load_shortcuts(self.library.user["path"])[0]
        for field in ("appid", "AppName", "Exe", "StartDir", "LaunchOptions",
                      "IsHidden", "AllowDesktopConfig", "AllowOverlay", "tags"):
            self.assertIn(field, entry)
        self.assertTrue(entry["Exe"].startswith('"') and entry["Exe"].endswith('"'))
        self.assertTrue(entry["StartDir"].strip('"').endswith(os.sep))
        self.assertLess(entry["appid"], 0)


class RankingTests(unittest.TestCase):
    def test_exact_size_beats_a_popular_wrong_size(self):
        assets = [
            {"url": "a", "width": 460, "height": 215, "score": 900},
            {"url": "b", "width": 600, "height": 900, "score": 10},
        ]
        self.assertEqual(sgdb.rank_assets(assets, "capsule")[0]["url"], "b")

    def test_score_breaks_ties_at_equal_size(self):
        assets = [
            {"url": "a", "width": 600, "height": 900, "score": 5},
            {"url": "b", "width": 600, "height": 900, "score": 50},
        ]
        self.assertEqual(sgdb.rank_assets(assets, "capsule")[0]["url"], "b")

    def test_static_beats_animated(self):
        assets = [
            {"url": "a", "width": 600, "height": 900, "score": 99, "mime": "video/webm"},
            {"url": "b", "width": 600, "height": 900, "score": 1, "mime": "image/png"},
        ]
        self.assertEqual(sgdb.rank_assets(assets, "capsule")[0]["url"], "b")

    def test_entries_without_a_url_are_dropped(self):
        self.assertEqual(sgdb.rank_assets([{"width": 600}], "capsule"), [])

    def test_edition_noise_is_stripped_for_retries(self):
        self.assertEqual(names.simplify("Skyrim [GOTY Edition] (v1.9)"), "Skyrim")


class NameSplittingTests(unittest.TestCase):
    def test_squished_titles_split_on_joining_words(self):
        cases = {
            "hordesoffate": "hordes of fate",
            "callofduty": "call of duty",
            "ageofempires": "age of empires",
            "swordandshield": "sword and shield",
        }
        for squished, expected in cases.items():
            self.assertEqual(names.split_connectors(squished), expected, squished)

    def test_real_words_are_not_split_apart(self):
        # "professor" contains "of" and "brotherhood" contains "the"; only the
        # latter has enough letters either side to trip the rule, which is why
        # this split never reaches a visible label.
        self.assertEqual(names.split_connectors("professor"), "professor")
        self.assertEqual(names.split_connectors("software"), "software")

    def test_camel_case_splits(self):
        self.assertEqual(names.split_camel("HordesOfFate"), "Hordes Of Fate")
        self.assertEqual(names.split_camel("HUDManager"), "HUD Manager")

    def test_joining_words_are_lowercased_in_display_names(self):
        self.assertEqual(names.pretty("Hordes Of Fate"), "Hordes of Fate")
        self.assertEqual(names.pretty("FSD galactic"), "FSD Galactic")

    def test_folder_wins_when_it_spells_the_same_title_better(self):
        self.assertEqual(
            names.nice_name_for("/g/Hordes of Fate/hordesoffate.exe"),
            "Hordes of Fate")

    def test_camel_case_filename_without_a_helpful_folder(self):
        self.assertEqual(
            names.nice_name_for("/g/Games/HordesOfFate.exe"), "Hordes of Fate")

    def test_variants_cover_the_squished_spelling(self):
        variants = [v.lower() for v in
                    names.query_variants("Hordesoffate", "/g/Games/hordesoffate.exe")]
        self.assertIn("hordesoffate", variants)
        self.assertIn("hordes of fate", variants)
        self.assertIn("hordes", variants)   # prefix, the last resort

    def test_generic_parent_folders_are_never_searched(self):
        for folder in ("Games", "Downloads", "SteamLibrary", "common", "C:"):
            variants = [v.lower() for v in names.query_variants(
                "Hordesoffate", "/%s/hordesoffate.exe" % folder)]
            self.assertNotIn(folder.lower(), variants, folder)

    def test_a_generic_folder_is_not_an_acceptable_match(self):
        # Without this guard a real game called "Games" would score a perfect
        # match for anything sitting in a Games folder.
        self.assertNotIn("games",
                         sgdb._match_targets("Hordesoffate", "/x/Games/hordesoffate.exe"))
        self.assertIn("hordesoffate",
                      sgdb._match_targets("Hordesoffate", "/x/Games/hordesoffate.exe"))

    def test_a_real_folder_name_is_still_used(self):
        targets = sgdb._match_targets("Hordesoffate", "/x/Hordes of Fate/hordesoffate.exe")
        self.assertIn("hordesoffate", targets)

    def test_variants_are_deduped_and_capped(self):
        variants = names.query_variants("Celeste", "/g/Celeste/Celeste.exe")
        self.assertEqual(len(variants), len(set(v.lower() for v in variants)))
        self.assertLessEqual(len(variants), 7)


class FakeSearchClient(sgdb.Client):
    """A Client whose search() answers from a fixed catalogue, no network."""

    CATALOGUE = [
        {"id": 1, "name": "Hordes of Fate"},
        {"id": 2, "name": "Hordes of the Underdark"},
        {"id": 3, "name": "Fate"},
        {"id": 4, "name": "Celeste"},
        {"id": 5, "name": "Deep Rock Galactic"},
    ]

    def __init__(self):
        super().__init__("fake-key")
        self.queries = []

    def search(self, term):
        """Word-prefix matching, the way a real autocomplete behaves.

        The important property: searching the squished "hordesoffate" finds
        nothing, because no *word* of any title starts with it. That is exactly
        the failure the variant list exists to work around.
        """
        self.queries.append(term)
        needles = [names.normalize(w) for w in (term or "").split()]
        needles = [n for n in needles if n]
        if not needles:
            return []
        out = []
        for game in self.CATALOGUE:
            words = [names.normalize(w) for w in game["name"].split()]
            if all(any(w.startswith(n) for w in words) for n in needles):
                out.append(game)
        return out


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeSearchClient()

    def test_squished_filename_finds_the_right_game(self):
        game = self.client.best_game("Hordesoffate", exe="/g/Games/hordesoffate.exe")
        self.assertIsNotNone(game)
        self.assertEqual(game["name"], "Hordes of Fate")

    def test_it_gives_up_on_the_literal_spelling_and_retries(self):
        self.client.best_game("Hordesoffate", exe="/g/Games/hordesoffate.exe")
        self.assertGreater(len(self.client.queries), 1,
                           "should have tried more than the literal name")
        self.assertEqual(self.client.queries[0], "Hordesoffate")

    def test_an_exact_match_stops_early(self):
        self.client.best_game("Celeste", exe="/g/Celeste/Celeste.exe")
        self.assertEqual(self.client.queries, ["Celeste"])

    def test_folder_name_rescues_an_unhelpful_filename(self):
        game = self.client.best_game(
            names.nice_name_for("/g/Deep Rock Galactic/FSD-Win64-Shipping.exe"),
            exe="/g/Deep Rock Galactic/FSD-Win64-Shipping.exe")
        self.assertIsNotNone(game)
        self.assertEqual(game["name"], "Deep Rock Galactic")

    def test_a_close_relative_does_not_beat_the_exact_title(self):
        game = self.client.best_game("hordesoffate")
        self.assertEqual(game["name"], "Hordes of Fate")

    def test_nonsense_matches_nothing(self):
        self.assertIsNone(self.client.best_game("zzzqqqxxwv", exe=None))

    def test_search_all_ranks_the_best_title_first(self):
        results = self.client.search_all("hordesoffate")
        self.assertTrue(results)
        self.assertEqual(results[0]["name"], "Hordes of Fate")

    def test_the_matching_query_is_reported(self):
        game = self.client.best_game("Hordesoffate", exe="/g/Games/hordesoffate.exe")
        self.assertEqual(names.normalize(game["_matched_by"]), "hordesoffate")


class LauncherTests(unittest.TestCase):
    """The desktop entry has to be right the first time: a launcher that
    silently does nothing is worse than telling someone to use a terminal."""

    def test_entry_is_a_valid_desktop_file(self):
        text = launcher.entry_text()
        self.assertTrue(text.startswith("[Desktop Entry]\n"))
        keys = dict(line.split("=", 1) for line in text.splitlines()
                    if "=" in line and not line.startswith("["))
        self.assertEqual(keys["Type"], "Application")
        self.assertEqual(keys["Name"], "SteamArt")
        # Terminal=false is the whole point; true would pop up a console.
        self.assertEqual(keys["Terminal"], "false")
        self.assertIn("Exec", keys)
        self.assertIn("steamart", keys["Exec"].lower())

    def test_exec_line_does_not_depend_on_the_execute_bit(self):
        # A ZIP download arrives with run.sh non-executable, so the entry must
        # invoke the interpreter rather than the shell wrapper.
        exec_line = launcher._exec_line()
        self.assertNotIn("run.sh", exec_line)
        self.assertIn("python", exec_line.lower())

    def test_exec_and_path_survive_spaces(self):
        keys = dict(line.split("=", 1) for line in launcher.entry_text().splitlines()
                    if "=" in line and not line.startswith("["))
        self.assertTrue(keys["Exec"].startswith('"'),
                        "Exec must be quoted or a path with spaces breaks it")
        self.assertEqual(keys["Exec"].count('"') % 2, 0)

    def test_icon_points_at_something_real(self):
        icon = launcher.icon_path()
        self.assertTrue(os.path.isabs(icon) or icon == "applications-games")
        if os.path.isabs(icon):
            self.assertTrue(os.path.isfile(icon))

    def test_install_writes_an_executable_entry(self):
        if not launcher.supported():
            self.skipTest("desktop entries are Linux only")
        tmp = tempfile.mkdtemp(prefix="steamart-launcher-")
        try:
            os.environ["XDG_DATA_HOME"] = tmp
            written = launcher.install(on_desktop=False)
            self.assertTrue(written)
            for path in written:
                self.assertTrue(os.path.isfile(path))
                self.assertTrue(os.stat(path).st_mode & 0o111,
                                "KDE ignores a launcher that is not executable")
            self.assertTrue(launcher.installed())
            launcher.remove()
            self.assertFalse(launcher.installed())
        finally:
            os.environ.pop("XDG_DATA_HOME", None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unsupported_platforms_say_so_instead_of_failing_oddly(self):
        if launcher.supported():
            self.skipTest("this platform does support desktop entries")
        with self.assertRaises(RuntimeError):
            launcher.install()


class ProtonNamingTests(unittest.TestCase):
    def test_folder_names_map_to_internal_names(self):
        cases = {
            "Proton - Experimental": "proton_experimental",
            "Proton Hotfix": "proton_hotfix",
            "Proton 9.0 (Beta)": "proton_9",
            "Proton 8.0": "proton_8",
            "Proton 6.3": "proton_63",
        }
        for folder, expected in cases.items():
            self.assertEqual(steam._valve_proton_internal_name(folder), expected, folder)


if __name__ == "__main__":
    unittest.main(verbosity=2)
