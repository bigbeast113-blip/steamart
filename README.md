# SteamArt

Import your non-Steam games on a Steam Deck and give them real artwork — the
library capsule, the wide capsule, the hero banner, the logo and the icon — so
the **Non-Steam** section of your library looks like the rest of it.

Artwork comes from [SteamGridDB](https://www.steamgriddb.com). SteamArt picks
the best-rated image for each slot automatically; you only go hunting by hand
if you don't like what it chose.

- Pure Python 3 standard library. **Nothing to `pip install`**, which matters
  on SteamOS where the filesystem is read-only.
- Runs as a small local web app so you can see the artwork before it lands.
- Also has a CLI if you'd rather stay in the terminal.
- Writes a timestamped backup of `shortcuts.vdf` and `config.vdf` every time
  it touches them.

---

## Quick start on a Steam Deck

1. Switch to **Desktop Mode**.

2. Get the code:

   ```bash
   git clone https://github.com/bigbeast113-blip/steamart.git
   cd steamart
   ./install-deck.sh     # optional: adds a desktop + app menu launcher
   ```

3. Get a free SteamGridDB API key: sign in at
   [steamgriddb.com](https://www.steamgriddb.com), then
   **Preferences → API → Generate key**.

4. **Close Steam.** This is the one step people skip. Steam rewrites its own
   config files when it exits and will happily undo everything you just did.

5. Start it:

   ```bash
   ./run.sh
   ```

   Your browser opens on `http://127.0.0.1:8523/`.

6. Paste the API key into **Settings**, then go to **Add games**, browse to
   your games folder and hit **Scan this folder**. Tick what you want and press
   **Add selected** — artwork is fetched as part of the import.

7. Start Steam again. Your games are under **Library → Non-Steam** with full
   artwork.

---

## What it actually does to your Steam install

| File | What changes |
|---|---|
| `userdata/<id>/config/shortcuts.vdf` | Your non-Steam shortcuts. New games are appended. |
| `userdata/<id>/config/grid/` | Artwork images, named with the app ID Steam derives for each shortcut. |
| `config/config.vdf` | Only the `CompatToolMapping` entry, so Windows `.exe` games get Proton. |

Every write goes to a temp file first and is then renamed into place, so an
interrupted run can't leave a half-written shortcut list. The previous version
is kept beside it as `shortcuts.vdf.20260910-143022.bak`; the last ten are
retained.

### The artwork slots

Steam derives a 32-bit app ID for each shortcut by hashing the quoted
executable path plus the display name. That number names the files:

| Slot | File | Size | Where you see it |
|---|---|---|---|
| Library capsule | `<id>p.png` | 600×900 | the box art in your library grid |
| Wide capsule | `<id>.png` | 920×430 | Recent Games, the Deck carousel |
| Hero | `<id>_hero.png` | 1920×620 | banner across the game's page |
| Logo | `<id>_logo.png` | transparent | title laid over the hero |
| Icon | `<id>_icon.png` | small | lists, taskbar |

Because the ID depends on the name, renaming a game changes it. SteamArt's
rename moves the artwork and the Proton setting across for you — renaming in
Steam directly will silently orphan the art.

---

## Command line

```bash
./run.sh info                        # what Steam install and profiles were found
./run.sh key YOUR_SGDB_KEY           # save your API key
./run.sh scan ~/Games                # list game executables it can see
./run.sh add ~/Games/Hades/Hades.exe # import (fetches art and sets Proton)
./run.sh list                        # every game and which slots are filled
./run.sh art                         # fill in every empty slot, everywhere
./run.sh art Hades --overwrite       # redo one game from scratch
./run.sh remove Hades                # drop it and its artwork
```

Useful flags: `--steam-root` if autodetection fails, `--user` to pick a profile,
`--kinds capsule,hero` to limit which slots get filled, `--no-art` / `--no-proton`
on import.

On Windows, use `run.bat` in place of `./run.sh`.

---

## Notes and gotchas

**Close Steam first.** Worth repeating. If SteamArt warns you Steam is running,
it is telling the truth.

**Windows `.exe` games need Proton.** SteamArt sets Proton Experimental by
default on import; change the version in Settings. Linux-native binaries and
`.sh` launchers are left alone.

**Scanning is deliberately picky.** It skips uninstallers, crash handlers,
redistributables and anything under 64 KB, and offers one executable per game
folder. If your launcher got filtered out, browse to the folder and tick the
file directly instead of scanning.

**Names drive the search.** The name box next to each game in the Add view is
what gets searched on SteamGridDB, so fixing an ugly folder name there before
importing usually fixes a bad match.

**Nothing found for a game?** Press **Pick** on its card, search under a
different title, and choose each slot yourself.

**Where settings live:** `~/.config/steamart/config.json` on Linux,
`%APPDATA%\SteamArt\config.json` on Windows. Your API key is stored there in
plain text.

**Network access:** the server binds to `127.0.0.1` only. The only outbound
requests are to `steamgriddb.com`.

---

## Tests

```bash
python3 tests/test_steamart.py   # VDF round-trips, app IDs, a fake Steam tree
python3 tests/test_server.py     # the HTTP API end to end
```

Neither needs a Steam install or network access.

To poke at the UI without touching your real library:

```bash
python3 tests/_uicheck.py        # demo server on :8524 with six fake games
```

## Requirements

Python 3.7 or newer. That's it — SteamOS already has it.
