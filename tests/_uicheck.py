"""Boot a demo server against a fake Steam tree, for eyeballing the UI.

Not part of the test suite. Run with:  python3 tests/_uicheck.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from steamart import core, server, steam  # noqa: E402

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")


def build_fake_steam(tmp):
    root = os.path.join(tmp, "Steam")
    os.makedirs(os.path.join(root, "userdata", "777", "config"))
    os.makedirs(os.path.join(root, "config"))
    os.makedirs(os.path.join(root, "steamapps", "common", "Proton - Experimental"))
    os.makedirs(os.path.join(root, "steamapps", "common", "Proton 9.0 (Beta)"))
    with open(os.path.join(root, "config", "config.vdf"), "w") as fh:
        fh.write('"InstallConfigStore"\n{\n}\n')
    with open(os.path.join(root, "config", "loginusers.vdf"), "w") as fh:
        fh.write('"users"\n{\n\t"%d"\n\t{\n\t\t"PersonaName"\t\t"deck"\n'
                 '\t\t"MostRecent"\t\t"1"\n\t}\n}\n' % (777 + steam.STEAM_ID64_BASE))

    games_root = os.path.join(tmp, "Games")
    titles = ["Hades", "Celeste", "Hollow Knight", "Balatro", "Tunic", "Outer Wilds"]
    for title in titles:
        folder = os.path.join(games_root, title)
        os.makedirs(folder)
        with open(os.path.join(folder, title.replace(" ", "") + ".exe"), "wb") as fh:
            fh.write(b"\x00" * (256 * 1024))
    return root, games_root, titles


def main():
    tmp = tempfile.mkdtemp(prefix="steamart-demo-")
    root, games_root, titles = build_fake_steam(tmp)

    config = core.Config(os.path.join(tmp, "config.json"))
    config.update({"steam_root": root, "api_key": "", "compat_tool": "proton_experimental"})
    library = core.Library(config)

    library.add_games(
        [{"path": os.path.join(games_root, t, t.replace(" ", "") + ".exe"), "name": t}
         for t in titles],
        set_compat=True, fetch_art=False)

    # Give a couple of games artwork so both card states are visible.
    for game in library.games()[:2]:
        for kind in ("capsule", "wide", "hero", "logo", "icon"):
            steam.write_art(library.user["path"], game["appid"], kind, PNG_1PX, ".png")

    print("Demo Steam tree: %s" % tmp)
    print("Games folder to browse: %s" % games_root)
    try:
        server.serve(library, port=8524, open_browser=False, verbose=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
