"""SteamGridDB API client built on urllib, so there is nothing to pip install.

Get a free key at https://www.steamgriddb.com/profile/preferences/api
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://www.steamgriddb.com/api/v2"
USER_AGENT = "SteamArt/1.0 (+https://github.com/)"

# Ideal pixel size per slot. Assets that match exactly are ranked first, since
# Steam scales anything else and soft box art is the main thing people notice.
IDEAL_SIZE = {
    "capsule": (600, 900),
    "wide": (920, 430),
    "hero": (1920, 620),
    "logo": None,
    "icon": (256, 256),
}


class SGDBError(RuntimeError):
    """A SteamGridDB request failed."""


class SGDBAuthError(SGDBError):
    """The API key is missing or rejected."""


class Client:
    def __init__(self, api_key, timeout=25, cache_ttl=300):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._cache = {}
        self._cache_ttl = cache_ttl

    # -- plumbing ---------------------------------------------------------

    def _request(self, path, params=None, use_cache=True):
        if not self.api_key:
            raise SGDBAuthError(
                "No SteamGridDB API key set. Get one at "
                "https://www.steamgriddb.com/profile/preferences/api"
            )
        query = urllib.parse.urlencode(
            {k: v for k, v in (params or {}).items() if v not in (None, "")}
        )
        url = "%s%s%s" % (API_BASE, path, ("?" + query) if query else "")

        if use_cache:
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < self._cache_ttl:
                return hit[1]

        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer %s" % self.api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise SGDBAuthError("SteamGridDB rejected the API key (HTTP %d)" % exc.code)
            if exc.code == 404:
                return {"success": True, "data": []}
            if exc.code == 429:
                raise SGDBError("SteamGridDB rate limit hit; wait a moment and retry")
            raise SGDBError("SteamGridDB returned HTTP %d for %s" % (exc.code, path))
        except urllib.error.URLError as exc:
            raise SGDBError("Could not reach SteamGridDB: %s" % exc.reason)
        except json.JSONDecodeError:
            raise SGDBError("SteamGridDB returned a response that was not JSON")

        if not payload.get("success", False):
            errors = payload.get("errors") or ["unknown error"]
            raise SGDBError("SteamGridDB: %s" % "; ".join(str(e) for e in errors))

        data = payload.get("data", [])
        if use_cache:
            self._cache[url] = (time.time(), data)
        return data

    def check_key(self):
        """Cheap call that proves the key works."""
        self._request("/search/autocomplete/portal", use_cache=False)
        return True

    # -- games ------------------------------------------------------------

    def search(self, term):
        term = (term or "").strip()
        if not term:
            return []
        return self._request("/search/autocomplete/%s" % urllib.parse.quote(term, safe=""))

    def best_game(self, name):
        """The single most likely game for a title, or None.

        SteamGridDB's autocomplete is already relevance-ordered, but an exact
        title match further down the list beats a fuzzy one at the top.
        """
        results = self.search(name)
        if not results:
            # Retry without bracketed junk and edition suffixes, which are the
            # usual reason a folder name fails to match.
            cleaned = _simplify(name)
            if cleaned and cleaned.lower() != name.strip().lower():
                results = self.search(cleaned)
        if not results:
            return None
        target = _normalize(name)
        for game in results:
            if _normalize(game.get("name", "")) == target:
                return game
        for game in results:
            if _normalize(game.get("name", "")).startswith(target):
                return game
        return results[0]

    # -- assets -----------------------------------------------------------

    def assets(self, game_id, kind, nsfw=False, humor=False, animated=False):
        """Artwork candidates for one slot, best first."""
        from .steam import ART_KINDS

        spec = ART_KINDS[kind]
        params = {
            "nsfw": "false" if not nsfw else "any",
            "humor": "false" if not humor else "any",
            "types": "static,animated" if animated else "static",
        }
        if spec["dimensions"]:
            params["dimensions"] = spec["dimensions"]

        try:
            data = self._request("/%s/game/%s" % (spec["endpoint"], game_id), params)
        except SGDBError:
            if not spec["dimensions"]:
                raise
            # A game may have art in an unusual size; better an odd size than none.
            params.pop("dimensions", None)
            data = self._request("/%s/game/%s" % (spec["endpoint"], game_id), params)

        if not data and spec["dimensions"]:
            params.pop("dimensions", None)
            data = self._request("/%s/game/%s" % (spec["endpoint"], game_id), params)

        return rank_assets(data, kind)

    def best_asset(self, game_id, kind, **kwargs):
        candidates = self.assets(game_id, kind, **kwargs)
        return candidates[0] if candidates else None

    def download(self, url):
        """Fetch an image, returning ``(bytes, extension)``."""
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout, 60)) as response:
                data = response.read()
                content_type = (response.headers.get("Content-Type") or "").split(";")[0]
        except urllib.error.HTTPError as exc:
            raise SGDBError("Image download failed (HTTP %d)" % exc.code)
        except urllib.error.URLError as exc:
            raise SGDBError("Image download failed: %s" % exc.reason)
        if not data:
            raise SGDBError("Image download returned an empty file")
        return data, _extension_for(url, content_type)


def rank_assets(assets, kind):
    """Sort candidates so the best one for this slot comes first.

    SteamGridDB returns things roughly by popularity. We keep that as the
    backbone and lift exact-size, well-voted, non-animated art above it.
    """
    ideal = IDEAL_SIZE.get(kind)
    ranked = []
    for position, asset in enumerate(assets or []):
        if not isinstance(asset, dict) or not asset.get("url"):
            continue
        width = asset.get("width") or 0
        height = asset.get("height") or 0
        exact = 0
        if ideal and (width, height) == ideal:
            exact = 2
        elif ideal and height and abs((width / height) - (ideal[0] / ideal[1])) < 0.02:
            exact = 1
        votes = (asset.get("upvotes") or 0) - (asset.get("downvotes") or 0)
        score = asset.get("score")
        score = score if isinstance(score, (int, float)) else votes
        animated = 1 if str(asset.get("mime", "")).endswith(("webm", "apng", "gif")) else 0
        ranked.append((-exact, animated, -score, position, asset))
    ranked.sort(key=lambda item: item[:4])
    return [item[4] for item in ranked]


def _extension_for(url, content_type):
    by_mime = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
    }
    if content_type in by_mime:
        return by_mime[content_type]
    extension = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return extension if extension in (".png", ".jpg", ".jpeg", ".webp", ".ico") else ".png"


_EDITION_NOISE = re.compile(
    r"\b(goty|game of the year|definitive|deluxe|ultimate|complete|remastered|"
    r"enhanced|anniversary|collectors?|special|standard|edition|repack|"
    r"early access|demo|beta|v?\d+[\d.]*)\b",
    re.IGNORECASE,
)


def _simplify(name):
    """Drop bracketed tags and edition words from a scraped folder name."""
    text = re.sub(r"[\[(<{].*?[\])>}]", " ", name or "")
    text = _EDITION_NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" -_:")


def _normalize(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())
