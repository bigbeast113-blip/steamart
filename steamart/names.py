"""Turning messy executable filenames into titles worth searching for.

Game executables are named every which way: ``hordesoffate.exe``,
``HordesOfFate.exe``, ``hordes_of_fate.exe``, ``HoF-Win64-Shipping.exe``. This
module does two separate jobs with that mess:

* :func:`nice_name_for` picks a tidy **display** name, staying conservative so
  the label in Steam never looks mangled;
* :func:`query_variants` generates several **search** spellings to try against
  SteamGridDB, where being wrong costs nothing but a lookup.
"""

from __future__ import annotations

import os
import re

# Words that are part of the build, not the title.
NOISE = (
    "setup", "launcher", "launch", "start", "game", "play", "win64", "win32",
    "x64", "x86", "shipping", "release", "final", "retail", "steam", "client",
    "bin", "app",
)

# Small joining words that show up inside squished titles: "hordesoffate".
# Longest first so the alternation cannot match a prefix of a longer word.
CONNECTORS = ("versus", "under", "into", "from", "over", "with", "the",
              "and", "for", "of", "vs")

# Only split on a connector when there are at least three letters either side,
# so "professor" and "software" survive intact.
_CONNECTOR_RE = re.compile(
    r"(?<=[a-z0-9]{3})(%s)(?=[a-z0-9]{3})" % "|".join(CONNECTORS))

# Folders that tell you nothing about the title. Searching for these, or
# treating them as an acceptable match, is how you end up with art for a game
# literally called "Games".
GENERIC_FOLDERS = frozenset("""
    games game downloads download desktop documents home users public temp tmp
    programfiles programfilesx86 steam steamlibrary steamapps common apps
    applications bin install installed portable emulation emulators roms
    newfolder mygames goggames epicgames launcher library
""".split())


def is_generic_folder(name):
    normalized = normalize(name)
    return len(normalized) < 3 or normalized in GENERIC_FOLDERS


_EDITION_NOISE = re.compile(
    r"\b(goty|game of the year|definitive|deluxe|ultimate|complete|remastered|"
    r"enhanced|anniversary|collectors?|special|standard|edition|repack|"
    r"early access|demo|beta|v?\d+[\d.]*)\b",
    re.IGNORECASE)


def normalize(text):
    """Strip everything but letters and digits, lowercased.

    This is the yardstick for "same title, different spelling":
    ``hordesoffate`` and ``Hordes of Fate`` both normalize to the same string.
    """
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def simplify(text):
    """Drop bracketed tags and edition words from a scraped name."""
    out = re.sub(r"[\[(<{].*?[\])>}]", " ", text or "")
    out = _EDITION_NOISE.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip(" -_:")


def split_camel(text, split_digits=True):
    """``HordesOfFate`` -> ``Hordes Of Fate``, ``HUDManager`` -> ``HUD Manager``.

    ``split_digits`` also breaks ``Portal2`` into ``Portal 2``. Turn it off when
    the result still has to be checked against :data:`NOISE`, or ``Win64`` gets
    torn into ``Win`` + ``64`` and stops looking like build junk.
    """
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    out = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", out)
    if split_digits:
        out = re.sub(r"(?<=[a-zA-Z])(?=\d)", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def split_connectors(text):
    """``hordesoffate`` -> ``hordes of fate``.

    Only used to build search queries. It is a guess, and a wrong guess just
    returns no results, but it would be embarrassing on a visible label:
    ``brotherhood`` splits to ``bro the rhood``.
    """
    return re.sub(r"\s+", " ", _CONNECTOR_RE.sub(r" \1 ", text or "")).strip()


def split_separators(text):
    return re.sub(r"[_.\-]+", " ", text or "")


def strip_noise(words):
    """Drop build junk, and any bare version number trailing it.

    ``Deep Rock 64 Shipping`` keeps the 64, but ``FSD Win 64 Shipping`` drops it
    along with the ``Win`` it belonged to.
    """
    kept, dropped_previous = [], False
    for word in words:
        if not word:
            continue
        if word.lower() in NOISE:
            dropped_previous = True
            continue
        if dropped_previous and word.isdigit():
            continue
        dropped_previous = False
        kept.append(word)
    return kept or [w for w in words if w]


def pretty(text):
    """Title-case without wrecking acronyms, lowercasing joining words."""
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    out = []
    for index, word in enumerate(words):
        if word.isupper() and len(word) > 1:
            out.append(word)                      # FSD, HUD, XIII
        elif index and word.lower() in CONNECTORS:
            out.append(word.lower())              # "Hordes of Fate"
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def is_noise(text):
    """True when a name is nothing but build junk, e.g. ``launcher`` or ``start``."""
    cleaned = split_separators(text or "").lower()
    words = cleaned.split()
    return bool(words) and all(w in NOISE or w.isdigit() for w in words)


def nice_name_for(exe):
    """Pick the display name for an executable.

    The containing folder usually holds the real title, so it wins whenever the
    filename is junk or is just the same words squished together.
    """
    stem = os.path.splitext(os.path.basename(exe))[0]
    folder = os.path.basename(os.path.dirname(exe))

    # "Hordes of Fate/hordesoffate.exe" - same title, better spelling upstairs.
    if folder and not is_generic_folder(folder):
        if normalize(stem) == normalize(folder):
            return pretty(split_camel(split_separators(folder)))
        if is_noise(stem) and not is_noise(folder):
            return pretty(split_camel(split_separators(folder)))

    # Noise is filtered before digits are split off, so "Win64" is still
    # recognisable as junk rather than becoming "Win" + "64".
    words = strip_noise(split_camel(split_separators(stem), split_digits=False).split())
    words = strip_noise(split_camel(" ".join(words)).split())
    return pretty(" ".join(words)) or stem


def query_variants(name, exe=None, limit=7):
    """Search spellings to try, best guess first.

    Every entry is a different theory about how the title was mangled. They are
    cheap: a wrong one simply returns nothing useful.
    """
    seen = set()
    out = []

    def add(value):
        value = re.sub(r"\s+", " ", (value or "")).strip()
        if len(value) < 2:
            return
        key = value.lower()
        if key not in seen:
            seen.add(key)
            out.append(value)

    add(name)
    add(simplify(name))
    add(split_camel(split_separators(name)))
    # The squished form is what lets "hordesoffate" become "hordes of fate".
    add(split_connectors(normalize(name)))

    if exe:
        stem = os.path.splitext(os.path.basename(exe))[0]
        folder = os.path.basename(os.path.dirname(exe))
        if not is_generic_folder(folder):
            add(pretty(split_camel(split_separators(folder))))
        add(split_camel(split_separators(stem)))
        add(split_connectors(normalize(stem)))

    # Last resort: a prefix. SteamGridDB's autocomplete turns "hordes" into
    # "Hordes of Fate" even when the full squished string matched nothing.
    # Cutting at the first joining word beats a blind six characters:
    # "call" finds Call of Duty where "callof" finds nothing.
    squished = normalize(name)
    if len(squished) >= 9:
        head = split_connectors(squished).split()
        add(head[0] if len(head) > 1 and len(head[0]) >= 4 else squished[:6])

    return out[:limit]
