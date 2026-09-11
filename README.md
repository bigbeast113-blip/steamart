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

2. Get the code. Cloning is worth it over the ZIP — a ZIP download loses the
   execute permission on the scripts:

   ```bash
   git clone https://github.com/bigbeast113-blip/steamart.git
   cd steamart
   ./run.sh
   ```

   Your browser opens on `http://127.0.0.1:8523/`.

3. Click **Add shortcut** in the blue bar at the top. That puts SteamArt on
   your desktop and in the application menu — **from now on it's a
   double-click, no terminal**. See [Running it with a click](#running-it-with-a-click).

4. Get a free SteamGridDB API key: sign in at
   [steamgriddb.com](https://www.steamgriddb.com), then
   **Preferences → API → Generate key**. Paste it into **Settings** and hit
   **Save & test**.

5. **Close Steam.** This is the one step people skip. Steam rewrites its own
   config files when it exits and will happily undo everything you just did.

6. Go to **Add games**, browse to your games folder and hit **Scan this
   folder**. Tick what you want and press **Add selected** — artwork is
   fetched automatically as part of the import.

7. Press **Quit** in SteamArt, then start Steam again. Your games are under
   **Library → Non-Steam** with full artwork.

## Running it with a click

The first launch has to come from a terminal, because on Linux something has
to grant permission to execute before anything can run. After that it never
does again.

Once SteamArt is open, hit **Add shortcut** in the banner (or **Settings →
Desktop shortcut**). It writes a launcher to your desktop and application
menu that:

- runs without opening a terminal window;
- calls the Python interpreter directly, so it works even from a ZIP download
  where `run.sh` lost its execute bit;
- replaces a copy that's already running, so you always get a current view of
  your library rather than whatever state was left behind. It asks the old
  instance to quit over its own endpoint rather than killing the process, so
  it can finish any write it's partway through. Pass `--reuse` to attach to
  the running one instead.

Use the **Quit** button in the top right to stop it — with no terminal
attached there's no Ctrl+C.

Prefer the command line, or want it without launching the UI first?

```bash
python3 steamart.py install             # desktop + application menu
python3 steamart.py install --remove    # take it back off
```

**If you downloaded the ZIP** and `./run.sh` says *Permission denied*, either
run `python3 steamart.py` instead (works regardless), or fix it with the mouse:
right-click `run.sh` in Dolphin → **Properties** → **Permissions** → tick
**Is executable**.

**On Windows** there's no desktop-entry standard to hook into: right-click
`run.bat` → **Show more options** → **Send to** → **Desktop (create
shortcut)**.

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

## Matching awkward filenames

Game executables are rarely named like the game. `hordesoffate.exe` matches
nothing if you search it literally, so SteamArt tries several spellings and
keeps whichever result actually corresponds to the title:

| Attempt | Query for `handsoffate.exe` |
|---|---|
| the name as given | `Handsoffate` |
| edition junk stripped | *(no change here)* |
| camel case split | `Hands Of Fate` for `HandsOfFate.exe` |
| **joining words split** | `hands of fate` |
| **plural made singular** | `hand of fate` |
| the parent folder | `Hand of Fate`, if the folder is named that |
| a prefix | `hands` |
| one distinctive word | `fate` |

Results from every attempt are compared against the original title with
punctuation, spacing and case removed — so `Hordes of Fate` is recognised as an
exact match for `hordesoffate` and wins immediately. The search stops as soon
as something matches exactly, and only settles for a fuzzy match above 55%
similarity. Below that it reports no match rather than guessing.

The plural rule earns its keep. `handsoffate.exe` is **Hand of Fate** —
singular. Searching the plural finds only *MANOS: The Hands of Fate*, a
completely different game, so without the singular variant you get nothing:

```
#   SEARCHED FOR         HITS  CLOSEST TITLE                        SCORE
1   Handsoffate             0  -                                      0%
2   hands of fate           1  MANOS: The Hands of Fate ~ Directo…   52%
3   hand of fate            3  Hand of Fate                          95%
```

Two guards stop a near-miss becoming a wrong answer:

- Generic parent folders (`Games`, `Downloads`, `SteamLibrary`, …) are ignored
  for both searching and matching, so a game in `C:\Games` can't end up with
  artwork for something called "Games".
- A title that merely *contains* your name gets no credit for it when one
  dwarfs the other. *MANOS: The Hands of Fate ~ Director's Cut* contains
  "handsoffate" but is three times longer, so it scores 52% and is rejected
  rather than being handed a substring bonus.

## When some games come out blank

The library toolbar gives you three ways to work through the stragglers:

- **Get missing artwork** — fills every empty slot across the whole library and
  leaves anything already installed alone.
- **Retry N incomplete** — only the games that are not finished yet. Cheap to
  press repeatedly.
- **Show only games missing artwork** — hides the finished ones so you can see
  what is left. The counter reads `12 games · 3 incomplete · 7 slots empty`.

After a bulk run, anything that did not come out complete is listed underneath
with the reason, and **?** / **Pick** / **Rename** buttons beside it. The three
things that actually go wrong:

| Reason shown | What to do |
|---|---|
| `No SteamGridDB match for …` | The title is not recognisable from the filename. **Rename** it to the real title and retry. |
| `matched X, but SteamGridDB has no logo` | Genuinely nothing uploaded for that slot. Nothing to be done; the game still works. |
| `rate limit reached` | Too many requests too quickly. Wait a minute and press **Retry incomplete**. |

On rate limits: filling a library is hundreds of requests. SteamArt retries a
429 three times with backoff, honouring `Retry-After`, and asks for all grid
sizes in one request so the capsule and wide slots share it — four requests per
game rather than fifteen. A very large library can still hit the ceiling, which
is what **Retry incomplete** is for.

## Seeing what it searched for

Press **?** on any game card. It shows two tables:

1. **Every spelling it tried** — hits returned, closest title, score, with the
   winning row highlighted.
2. **What artwork exists** for the matched game, slot by slot, how many images
   SteamGridDB has, the size of the top-rated one, and whether it is installed
   on your machine yet.

If anything is available but not installed there's a button to fetch just
those. When nothing matched at all it says so and offers the manual picker.

From the terminal, the same thing without touching your library:

```bash
./run.sh match "C:/Games/hordesoffate.exe"
```

When the match isn't exact, the library card and the CLI both say so
(`→ Hand of Fate (best guess): added capsule, wide, hero`), so you can spot a
wrong guess and fix it with **Pick**.

Two things that improve matching a lot: keep each game in a folder named after
it, and edit the name box next to a game in the **Add games** list before
importing — that name is what gets searched.

## Command line

```bash
./run.sh info                        # what Steam install and profiles were found
./run.sh key YOUR_SGDB_KEY           # save your API key
./run.sh scan ~/Games                # list game executables it can see
./run.sh add ~/Games/Hades/Hades.exe # import (fetches art and sets Proton)
./run.sh list                        # every game and which slots are filled
./run.sh match hordesoffate.exe      # dry run: how a name would be searched
./run.sh install                     # desktop shortcut, no more terminal
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

## Working on it in VS Code

The workspace is pre-configured, so opening the folder is all the setup there is.

- **F5** → *SteamArt: web UI*. Other launch targets cover `info`, `list`, a
  verbose no-browser run, and the demo server that runs against a throwaway
  Steam tree instead of your real one.
- **Ctrl+Shift+P → Tasks: Run Test Task** runs all 32 tests. They also show up
  in the Testing panel.
- `files.eol` is pinned to LF in the workspace settings and enforced by
  `.gitattributes`, because a `run.sh` saved with CRLF fails on the Deck with
  `bad interpreter: /usr/bin/env bash^M`.

## Requirements

Python 3.7 or newer. That's it — SteamOS already has it.
