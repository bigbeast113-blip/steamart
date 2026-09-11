"""SteamGridDB API client built on urllib, so there is nothing to pip install.

Get a free key at https://www.steamgriddb.com/profile/preferences/api
"""

from __future__ import annotations

import difflib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import names

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

    def best_game(self, name, exe=None, report=None):
        """The single most likely game for a title, or None.

        A filename like ``hordesoffate.exe`` will not match anything as typed,
        so several spellings are tried in turn -- the name as given, with the
        edition junk stripped, split on camel case, split on joining words
        (``hordes of fate``), the parent folder's name, and finally a prefix.

        Results from every attempt are scored against the original title with
        punctuation and case ignored, so the correct game is recognised however
        it was spelled. The search stops early once something matches exactly.

        ``report``, if given, is called with each query tried, which is how the
        CLI explains what it did.
        """
        targets = _match_targets(name, exe)
        if not targets:
            return None

        best, best_score, tried = None, 0.0, []
        for query in names.query_variants(name, exe):
            tried.append(query)
            if report:
                report(query)
            try:
                results = self.search(query)
            except SGDBAuthError:
                raise
            except SGDBError:
                continue
            for game in results:
                score = _similarity(game.get("name", ""), targets)
                if score >= 0.999:
                    game = dict(game)
                    game["_matched_by"] = query
                    return game
                if score > best_score:
                    best, best_score = game, score
            # A near-miss is good enough to stop burning API calls on guesses.
            if best_score >= 0.9:
                break

        if best is None or best_score < 0.55:
            return None
        best = dict(best)
        best["_matched_by"] = tried[-1] if tried else name
        best["_confidence"] = round(best_score, 3)
        return best

    def search_all(self, name, exe=None, limit=12):
        """Merged results from every spelling, most likely first.

        The manual picker uses this so a squished filename still puts the right
        game at the front of the list instead of returning nothing at all.
        """
        targets = _match_targets(name, exe)
        merged, seen = [], set()
        for query in names.query_variants(name, exe):
            try:
                results = self.search(query)
            except SGDBAuthError:
                raise
            except SGDBError:
                continue
            for position, game in enumerate(results):
                if game.get("id") in seen:
                    continue
                seen.add(game.get("id"))
                merged.append((-_similarity(game.get("name", ""), targets),
                               position, game))
            if merged and -merged[0][0] >= 0.999 and len(merged) >= limit:
                break

        merged.sort(key=lambda item: item[:2])
        return [game for _, _, game in merged[:limit]]

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


def _match_targets(name, exe=None):
    """Normalized spellings that would each count as the right game.

    A generic parent folder is deliberately excluded: treating ``Games`` as an
    acceptable title is how you end up installing art for some other game that
    happens to be called that.
    """
    targets = {names.normalize(name)}
    if exe:
        stem = os.path.splitext(os.path.basename(exe))[0]
        folder = os.path.basename(os.path.dirname(exe))
        targets.add(names.normalize(stem))
        if not names.is_generic_folder(folder):
            targets.add(names.normalize(folder))
    targets.discard("")
    return targets


def _similarity(candidate, targets):
    """How well a SteamGridDB result matches any spelling of what we wanted.

    Compared on the normalized forms, so ``Hordes of Fate`` scores a perfect
    match against ``hordesoffate``.
    """
    normalized = names.normalize(candidate)
    if not normalized:
        return 0.0
    best = 0.0
    for target in targets:
        if normalized == target:
            return 1.0
        score = difflib.SequenceMatcher(None, normalized, target).ratio()
        # Short targets match too many things by accident to trust containment.
        if len(target) >= 5:
            if normalized.startswith(target) or target.startswith(normalized):
                score = max(score, 0.9)
            elif target in normalized or normalized in target:
                score = max(score, 0.8)
        best = max(best, score)
    return best
