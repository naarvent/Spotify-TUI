"""Import a Spotify library into an account-less provider (YouTube Music).

Read-only on the Spotify side: the user's own playlists, Liked Songs, saved
albums and followed artists are read with the official Web API and the
user's own credentials/token, through :class:`ReadOnlySpotify`, which refuses
every method that is not a read. Nothing on Spotify is ever modified, no
cookies, no scraping.

Each Spotify track is matched to a YouTube Music song with the public
``ytmusicapi`` search (through the target provider's ``import_search``):

* query ``"<title> <first artist>"`` with ``filter="songs"``;
* candidates are scored on the normalised title (``(feat. …)``,
  ``- Remastered 2011``, ``(Live)``… removed), the artists, the duration
  (±3 s full marks) and the album;
* ``score >= ACCEPT`` is a match, ``>= DOUBT`` a doubtful match (imported but
  marked), below that a second, laxer search (no filter: videos too) is tried;
* results are cached (``import_match_cache.json``: spotify id -> video id),
  so a re-run does not search again and an interrupted import resumes. Network
  errors are never cached, so they are retried next time.

Playlists become local playlists (original name), Liked Songs go to the local
Liked Songs, saved albums / followed artists are matched by search into Saved
Albums / Saved Artists. A report (``import_report.json``) lists what was
doubtful or not found. The job runs on a worker thread, reports progress,
pauses between searches (rate limit) and can be cancelled.
"""

from __future__ import annotations

import difflib
import os
import re
import threading
import time
import unicodedata
from typing import Callable, Dict, List, Optional

from . import config
from .config import logger
from .local_library import atomic_write_json

ACCEPT = 0.78
DOUBT = 0.58
DURATION_TOLERANCE_MS = 3000
CACHE_FILE = "import_match_cache.json"
REPORT_FILE = "import_report.json"


# --------------------------------------------------------------------------- #
# Read-only Spotify access
# --------------------------------------------------------------------------- #
class ReadOnlySpotify:
    """Wrap a spotipy client so only read endpoints can be called."""

    ALLOWED = frozenset({
        "current_user", "current_user_playlists", "playlist", "playlist_items",
        "current_user_saved_tracks", "current_user_saved_albums",
        "current_user_followed_artists", "next",
    })

    def __init__(self, sp):
        self._sp = sp

    def __getattr__(self, name):
        if name not in self.ALLOWED:
            raise PermissionError(f"Spotify import is read-only: '{name}' is not allowed")
        return getattr(self._sp, name)


def spotify_credentials_configured() -> bool:
    return bool(config.CLIENT_ID and config.CLIENT_SECRET and config.REDIRECT_URI)


def spotify_token_cached() -> bool:
    try:
        return os.path.exists(config.CACHE_PATH) and os.path.getsize(config.CACHE_PATH) > 0
    except OSError:
        return False


NO_TOKEN_HELP = ("Spotify is not signed in on this computer yet. To import, sign in once: "
                 "press P, choose 'spotify', complete the browser sign-in, then press P again "
                 "and come back to this provider. Your library here is not touched.")
NO_CREDENTIALS_HELP = ("No Spotify credentials configured. Switch to 'spotify' with P once and "
                       "enter your Spotify app's Client ID / Secret / Redirect URI. Spotify only lets "
                       "Premium accounts create that developer app: with a Free account use "
                       "'Import Playlists' with a public playlist link instead (no credentials needed).")


# --------------------------------------------------------------------------- #
# "Import Playlists": what was pasted, and what to tell the user
# --------------------------------------------------------------------------- #
_SPOTIFY_ID_RE = re.compile(r"[0-9A-Za-z]{22}")
_YOUTUBE_HOSTS = ("music.youtube.com", "www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be")


def classify_import_input(raw: str):
    """``("spotify_playlist", id)`` | ``("spotify_other", kind)`` | ``("youtube", None)`` |
    ``("other", None)``. A bare 22-character id counts as a Spotify playlist id (YouTube playlist
    ids are longer: ``PL…`` has 34 characters)."""
    from urllib.parse import urlsplit
    from .scraper import parse_ref, URLError
    s = (raw or "").strip()
    if not s:
        return "other", None
    if s.startswith("ytmusic:"):
        return "youtube", None
    host = urlsplit(s if "://" in s else "https://" + s).hostname or ""
    if host in _YOUTUBE_HOSTS:
        return "youtube", None
    if s.startswith("spotify:") or host in ("open.spotify.com", "play.spotify.com"):
        try:
            kind, sid = parse_ref(s, "playlist")
        except URLError:
            return "other", None
        return ("spotify_playlist", sid) if kind == "playlist" else ("spotify_other", kind)
    if _SPOTIFY_ID_RE.fullmatch(s):
        return "spotify_playlist", s
    return "other", None


def import_playlists_text(provider_name: str, supports_spotify_import: bool) -> tuple:
    """``(title+help text, input placeholder)`` for the Library > Import Playlists view."""
    if supports_spotify_import:
        return ("[b]Import a playlist[/b]\n"
                "• Spotify link (public playlist): it is [b]copied[/b] into your local library and each song is "
                "matched on YouTube Music. No Spotify account needed.\n"
                "• YouTube Music link or ID: it is added as it is, and stays on YouTube.\n"
                "Paste it and press Enter.",
                "Spotify or YouTube Music playlist link…")
    if provider_name == "spotify":
        return ("[b]Add a playlist to your library[/b]\n"
                "Paste a Spotify playlist link, a spotify:playlist: URI or its ID. It is followed on your "
                "Spotify account (nothing is copied). Press Enter.",
                "Spotify playlist link, URI or ID…")
    return ("[b]Import a playlist[/b]\nPaste a playlist link or ID and press Enter.",
            "Paste the playlist link or ID and press Enter…")


def describe_link_error(exc: BaseException) -> str:
    """A plain-language reason for a failed link import (the technical detail goes to the log)."""
    from . import scraper
    if isinstance(exc, scraper.NotFoundError):
        return ("That Spotify playlist does not exist or is not public. Only public playlists can be "
                "imported by link.")
    if isinstance(exc, scraper.RateLimitedError):
        return "Spotify is limiting requests right now. Try again in a minute."
    if isinstance(exc, (scraper.ParsingError, scraper.TokenError)):
        return ("Spotify changed something and the link reader needs an update "
                "(spt_tui/scraper). Details are in the log.")
    if isinstance(exc, scraper.NetworkError):
        return "Could not reach Spotify (network problem). Try again."
    if isinstance(exc, scraper.URLError):
        return str(exc)
    return f"Could not read that playlist: {type(exc).__name__}"


def default_spotify_reader():
    """A read-only spotipy client from the user's cached token, independent of
    the active provider. Never opens a browser or prompts (token must exist)."""
    if not spotify_credentials_configured():
        raise RuntimeError(NO_CREDENTIALS_HELP)
    if not spotify_token_cached():
        raise RuntimeError(NO_TOKEN_HELP)
    from .providers.spotify import SpotifyProvider
    prov = SpotifyProvider()
    sp = prov.ensure()
    # spotipy cuts every request at 5 s by default, which aborted whole imports on one slow reply.
    try:
        getattr(sp, "_sp", sp).requests_timeout = READ_TIMEOUT
    except Exception:
        logger.debug("could not raise the Spotify read timeout")
    return ReadOnlySpotify(sp)


READ_TIMEOUT = 25           # seconds per Spotify request (spotipy's default is 5)
PAGE_RETRIES = 3            # extra tries for one page that timed out / dropped


def _is_transient(exc: Exception) -> bool:
    import requests
    return isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError))


def _fetch_page(fetch: Callable[[int], dict], offset: int,
                sleep: Callable[[float], None] = time.sleep) -> dict:
    """One page, retried with a growing pause when the network is slow or drops.
    Anything else (401, 404, a bug) is raised at once."""
    for attempt in range(PAGE_RETRIES + 1):
        try:
            return fetch(offset) or {}
        except Exception as exc:
            if not _is_transient(exc) or attempt == PAGE_RETRIES:
                raise
            logger.warning("Spotify page at offset %d failed (%s), retry %d",
                           offset, type(exc).__name__, attempt + 1)
            sleep(min(8.0, 1.0 * (2 ** attempt)))
    return {}


def _pages(fetch: Callable[[int], dict], limit: int, cap: int = 10000) -> List[dict]:
    out, offset = [], 0
    while offset < cap:
        page = _fetch_page(fetch, offset)
        items = page.get("items") or []
        out.extend(items)
        if not page.get("next") or not items:
            break
        offset += limit
    return out


class SpotifySource:
    """What can be imported from the user's Spotify account."""

    def __init__(self, sp):
        self.sp = sp

    def playlists(self) -> List[dict]:
        items = _pages(lambda o: self.sp.current_user_playlists(limit=50, offset=o), 50)
        out = []
        for p in items:
            if not p or not p.get("id"):
                continue
            total = ((p.get("items") or p.get("tracks") or {}).get("total")) or 0
            out.append({"id": p["id"], "name": p.get("name") or "(no name)", "total": int(total)})
        return out

    @staticmethod
    def _track(t: dict) -> Optional[dict]:
        if not t or (t.get("type") or "track") != "track" or not t.get("id"):
            return None
        return {"id": t["id"], "name": t.get("name") or "",
                "artists": [a.get("name") for a in t.get("artists") or [] if a and a.get("name")],
                "album": (t.get("album") or {}).get("name") or "",
                "duration_ms": int(t.get("duration_ms") or 0)}

    def playlist_tracks(self, pid: str) -> List[dict]:
        items = _pages(lambda o: self.sp.playlist_items(pid, limit=100, offset=o), 100)
        out = []
        for it in items:
            t = self._track((it or {}).get("item") or (it or {}).get("track") or {})
            if t:
                out.append(t)
        return out

    def liked_tracks(self) -> List[dict]:
        items = _pages(lambda o: self.sp.current_user_saved_tracks(limit=50, offset=o), 50)
        return [t for t in (self._track((it or {}).get("track") or {}) for it in items) if t]

    def saved_albums(self) -> List[dict]:
        items = _pages(lambda o: self.sp.current_user_saved_albums(limit=50, offset=o), 50)
        out = []
        for it in items:
            a = (it or {}).get("album") or {}
            if a.get("id"):
                out.append({"id": a["id"], "name": a.get("name") or "",
                            "artists": [x.get("name") for x in a.get("artists") or [] if x]})
        return out

    def followed_artists(self) -> List[dict]:
        out, after = [], None
        for _ in range(200):
            page = (self.sp.current_user_followed_artists(limit=50, after=after) or {}).get("artists") or {}
            items = page.get("items") or []
            out.extend({"id": a["id"], "name": a.get("name") or ""} for a in items if a and a.get("id"))
            after = (page.get("cursors") or {}).get("after")
            if not page.get("next") or not after or not items:
                break
        return out

    def summary(self) -> dict:
        """Counts for the selection screen (one cheap page each)."""
        def total(fn):
            try:
                return int((fn() or {}).get("total") or 0)
            except Exception:
                logger.exception("Spotify import: counting failed")
                return 0
        return {
            "liked": total(lambda: self.sp.current_user_saved_tracks(limit=1, offset=0)),
            "albums": total(lambda: self.sp.current_user_saved_albums(limit=1, offset=0)),
            "artists": total(lambda: (self.sp.current_user_followed_artists(limit=1) or {}).get("artists")),
            "playlists": self.playlists(),
        }


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
_NOISE_PARENS = re.compile(
    r"[\(\[][^\)\]]*\b(feat|ft|featuring|with|remaster(ed)?|version|edit|live|mono|stereo|"
    r"deluxe|bonus|explicit|clean|radio|mix|from|prod|official|audio|video|lyrics?)\b[^\)\]]*[\)\]]",
    re.I)
_NOISE_DASH = re.compile(
    r"\s+-\s+.*\b(remaster(ed)?|version|edit|live|mono|stereo|mix|bonus|single|demo|"
    r"from|soundtrack|ost|theme)\b.*$", re.I)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.I)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_title(s: str) -> str:
    s = _strip_accents(str(s or "")).lower()
    s = _NOISE_PARENS.sub(" ", s)
    s = _NOISE_DASH.sub(" ", s)
    s = _FEAT.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title_for_query(s: str) -> str:
    """The title as typed in a search box: "(feat. …)", "- Remastered 2011"
    and similar noise removed, case kept."""
    s = str(s or "")
    s = _NOISE_PARENS.sub(" ", s)
    s = _NOISE_DASH.sub(" ", s)
    s = _FEAT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_artist(s: str) -> str:
    s = _strip_accents(str(s or "")).lower()
    if s.endswith(" - topic"):
        s = s[:-8]
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def score_candidate(src: dict, cand: dict) -> float:
    """0..1: how likely ``cand`` (Spotify-shaped track from the target
    provider) is the same recording as ``src`` (Spotify track summary)."""
    t = _sim(normalize_title(src.get("name")), normalize_title(cand.get("name")))
    s_art = [normalize_artist(a) for a in src.get("artists") or [] if a]
    c_art = [normalize_artist(a.get("name")) for a in cand.get("artists") or [] if a and a.get("name")]
    if s_art and c_art:
        best = max(max(_sim(x, y) for y in c_art) for x in s_art[:2])
        # Nearly binary: a cover / another artist with the same title must not
        # reach the acceptance threshold on title + duration alone.
        art = 1.0 if best >= 0.85 else (0.5 if best >= 0.6 else 0.0)
        # Channel uploads often carry the artist in the title instead.
        if art < 0.5:
            ct = normalize_title(cand.get("name"))
            if any(x and x in ct for x in s_art[:2]):
                art = 0.8
    else:
        art = 0.5
    sd, cd = int(src.get("duration_ms") or 0), int(cand.get("duration_ms") or 0)
    if sd and cd:
        diff = abs(sd - cd)
        dur = 1.0 if diff <= DURATION_TOLERANCE_MS else (0.6 if diff <= 10000 else (0.2 if diff <= 30000 else 0.0))
    else:
        dur = 0.5
    s_alb = normalize_title(src.get("album"))
    c_alb = normalize_title((cand.get("album") or {}).get("name") if isinstance(cand.get("album"), dict) else "")
    alb = _sim(s_alb, c_alb) if s_alb and c_alb else 0.5
    return round(0.45 * t + 0.35 * art + 0.12 * dur + 0.08 * alb, 4)


class TrackMatcher:
    """Match Spotify tracks to the target provider's songs, with a persistent
    cache and a pause between searches."""

    def __init__(self, provider, *, cache_path: Optional[str] = None,
                 min_interval: float = 0.4, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.monotonic, retries: int = 2):
        self.provider = provider
        self.cache_path = cache_path or os.path.join(config.CACHE_DIR, CACHE_FILE)
        self.min_interval = float(min_interval)
        self._sleep = sleep
        self._now = now
        self._last = 0.0
        self.retries = int(retries)
        self.searches = 0
        self._lock = threading.Lock()
        self.cache = self._load()

    # -- cache ----------------------------------------------------------- #
    def _load(self) -> dict:
        import json
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("tracks", {}); d.setdefault("albums", {})
                d.setdefault("artists", {}); d.setdefault("playlists", {})
                return d
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("import match cache unreadable; starting a new one")
        return {"tracks": {}, "albums": {}, "artists": {}, "playlists": {}}

    def save(self) -> None:
        with self._lock:
            try:
                atomic_write_json(self.cache_path, self.cache)
            except Exception:
                logger.exception("could not save the import match cache")

    # -- searching ------------------------------------------------------- #
    def _throttle(self) -> None:
        wait = self.min_interval - (self._now() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = self._now()

    def _search(self, query: str, kind: str, lax: bool) -> List[dict]:
        last_exc = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                self.searches += 1
                return list(self.provider.import_search(query, kind, lax=lax) or [])
            except Exception as exc:
                last_exc = exc
                logger.warning("import search failed (%s), attempt %d", type(exc).__name__, attempt + 1)
                self._sleep(min(4.0, 0.5 * (2 ** attempt)))
        raise ConnectionError(f"search failed: {type(last_exc).__name__}") from last_exc

    @staticmethod
    def _best(src: dict, cands: List[dict]):
        best, best_s = None, -1.0
        for c in cands:
            s = score_candidate(src, c)
            if s > best_s:
                best, best_s = c, s
        return best, max(best_s, 0.0)

    def match_track(self, src: dict, *, retry_not_found: bool = False) -> dict:
        """``{"status": matched|doubtful|not_found, "track": <target track>|None,
        "score": float}``. Raises ConnectionError when the search itself keeps
        failing (not cached, so a later run retries it)."""
        sid = src.get("id")
        hit = self.cache["tracks"].get(sid) if sid else None
        if hit and (hit.get("status") != "not_found" or not retry_not_found):
            return dict(hit)
        artist = (src.get("artists") or [""])[0]
        q = f"{clean_title_for_query(src.get('name', ''))} {artist}".strip()
        best, score = self._best(src, self._search(q, "track", lax=False))
        if score < DOUBT:
            b2, s2 = self._best(src, self._search(q, "track", lax=True))
            if s2 > score:
                best, score = b2, s2
        status = "matched" if score >= ACCEPT else ("doubtful" if score >= DOUBT else "not_found")
        res = {"status": status, "score": score,
               "track": _min_track(best) if best is not None and status != "not_found" else None}
        if sid:
            self.cache["tracks"][sid] = res
        return dict(res)

    def match_named(self, kind: str, src: dict) -> dict:
        """Albums (name + artist) and artists (name) by search."""
        key = "albums" if kind == "album" else "artists"
        sid = src.get("id")
        hit = self.cache[key].get(sid) if sid else None
        if hit:
            return dict(hit)
        artist = (src.get("artists") or [""])[0] if kind == "album" else ""
        q = f"{src.get('name', '')} {artist}".strip()
        cands = self._search(q, kind, lax=False)
        best, score = None, 0.0
        for c in cands:
            s = _sim(normalize_title(src.get("name")), normalize_title(c.get("name")))
            if kind == "album" and artist:
                ca = [normalize_artist(a.get("name")) for a in c.get("artists") or [] if a and a.get("name")]
                a_s = max((_sim(normalize_artist(artist), x) for x in ca), default=0.5)
                s = 0.7 * s + 0.3 * a_s
            if s > score:
                best, score = c, s
        threshold = 0.85 if kind == "artist" else 0.75
        status = "matched" if score >= threshold else ("doubtful" if score >= threshold - 0.15 else "not_found")
        res = {"status": status, "score": round(score, 4),
               "item": dict(best) if best is not None and status != "not_found" else None}
        if sid:
            self.cache[key][sid] = res
        return dict(res)


def _min_track(t: dict) -> dict:
    return {"id": t.get("id"), "uri": t.get("uri"), "type": "track", "name": t.get("name"),
            "artists": [{"name": a.get("name"), "id": a.get("id")} for a in t.get("artists") or [] if a],
            "album": {"name": ((t.get("album") or {}).get("name") or "") if isinstance(t.get("album"), dict) else "",
                      "id": (t.get("album") or {}).get("id") if isinstance(t.get("album"), dict) else None},
            "duration_ms": int(t.get("duration_ms") or 0)}


# --------------------------------------------------------------------------- #
# The import job
# --------------------------------------------------------------------------- #
class ImportCancelled(Exception):
    pass


class SpotifyImporter:
    """Run an import of the selected items. ``selection`` is a list of
    ``{"kind": "playlist", "id", "name"}`` / ``{"kind": "liked"}`` /
    ``{"kind": "albums"}`` / ``{"kind": "artists"}``."""

    MAX_CONSECUTIVE_ERRORS = 5

    def __init__(self, source: SpotifySource, provider, *, matcher: Optional[TrackMatcher] = None,
                 progress: Callable[[str], None] = lambda m: None,
                 cancel: Optional[threading.Event] = None,
                 report_path: Optional[str] = None):
        self.source = source
        self.provider = provider
        self.library = provider.library
        self.matcher = matcher or TrackMatcher(provider)
        self.progress = progress
        self.cancel = cancel or threading.Event()
        self.report_path = report_path or os.path.join(config.CACHE_DIR, REPORT_FILE)
        self.summary = {"matched": 0, "doubtful": 0, "not_found": 0, "errors": 0}
        self.items: List[dict] = []
        self.cancelled = False
        self.aborted: Optional[str] = None
        self._consecutive_errors = 0

    def _check_cancel(self):
        if self.cancel.is_set():
            raise ImportCancelled()

    def _match_tracks(self, label: str, tracks: List[dict], entry: dict) -> List[dict]:
        out = []
        n = len(tracks)
        for i, src in enumerate(tracks, 1):
            self._check_cancel()
            self.progress(f"Importing '{label}' {i}/{n}…")
            try:
                res = self.matcher.match_track(src)
                self._consecutive_errors = 0
            except ConnectionError as exc:
                self.summary["errors"] += 1
                entry["errors"].append(_describe(src))
                self._consecutive_errors += 1
                if self._consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    self.aborted = f"network problem ({exc}); run the import again to resume"
                    raise ImportCancelled()
                continue
            status = res["status"]
            self.summary[status] += 1
            if status == "not_found" or not res.get("track"):
                entry["not_found"].append(_describe(src))
                continue
            t = dict(res["track"])
            if status == "doubtful":
                t["import_status"] = "doubtful"
                entry["doubtful"].append({**_describe(src), "matched": _describe_target(t),
                                          "score": res.get("score")})
            out.append(t)
            if i % 20 == 0:
                self.matcher.save()
        self.matcher.save()
        return out

    def _import_playlist(self, sel: dict) -> None:
        name = sel.get("name") or "Spotify playlist"
        entry = self._entry("playlist", name)
        self.progress(f"Reading '{name}' from Spotify…")
        tracks = self.source.playlist_tracks(sel["id"])
        matched = self._match_tracks(name, tracks, entry)
        # Re-importing the same Spotify playlist refreshes its local copy.
        local_id = self.matcher.cache["playlists"].get(sel["id"])
        if not local_id or self.library.playlist(local_id) is None:
            local_id = self.library.create_playlist(name, "Imported from Spotify")["id"]
            self.matcher.cache["playlists"][sel["id"]] = local_id
        self.library.set_tracks(local_id, matched)
        self.matcher.save()
        entry["imported"] = len(matched)

    def _import_liked(self) -> None:
        entry = self._entry("liked", "Liked Songs")
        self.progress("Reading Liked Songs from Spotify…")
        matched = self._match_tracks("Liked Songs", self.source.liked_tracks(), entry)
        # Spotify lists newest first; add oldest first so the order is kept.
        added = self.library.add("tracks", list(reversed(matched)))
        entry["imported"] = len(matched)
        # Several Spotify songs (album + single, remasters...) can match the same YouTube video, and
        # songs already liked here are not added twice: say how many really were new.
        entry["added_new"] = added
        entry["merged_or_already_there"] = len(matched) - added

    def _import_named(self, kind: str) -> None:
        label = "Saved Albums" if kind == "album" else "Followed Artists"
        entry = self._entry(kind + "s", label)
        self.progress(f"Reading {label} from Spotify…")
        srcs = self.source.saved_albums() if kind == "album" else self.source.followed_artists()
        found = []
        for i, src in enumerate(srcs, 1):
            self._check_cancel()
            self.progress(f"Importing '{label}' {i}/{len(srcs)}…")
            try:
                res = self.matcher.match_named(kind, src)
                self._consecutive_errors = 0
            except ConnectionError:
                self.summary["errors"] += 1
                entry["errors"].append({"name": src.get("name")})
                self._consecutive_errors += 1
                if self._consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    self.aborted = "network problem; run the import again to resume"
                    raise ImportCancelled()
                continue
            self.summary[res["status"]] += 1
            if res["status"] == "not_found" or not res.get("item"):
                entry["not_found"].append({"name": src.get("name"), "artists": src.get("artists", [])})
                continue
            if res["status"] == "doubtful":
                entry["doubtful"].append({"name": src.get("name"), "matched": res["item"].get("name"),
                                          "score": res.get("score")})
            found.append(res["item"])
        self.matcher.save()
        self.library.add("albums" if kind == "album" else "artists", list(reversed(found)))
        entry["imported"] = len(found)

    def _entry(self, kind: str, name: str) -> dict:
        e = {"kind": kind, "name": name, "imported": 0, "doubtful": [], "not_found": [], "errors": []}
        self.items.append(e)
        return e

    def run(self, selection: List[dict]) -> dict:
        try:
            for sel in selection:
                self._check_cancel()
                k = sel.get("kind")
                if k == "playlist":
                    self._import_playlist(sel)
                elif k == "liked":
                    self._import_liked()
                elif k == "albums":
                    self._import_named("album")
                elif k == "artists":
                    self._import_named("artist")
        except ImportCancelled:
            self.cancelled = not self.aborted
        finally:
            self.matcher.save()
            self.write_report()
        return self.result()

    def result(self) -> dict:
        return {"summary": dict(self.summary), "cancelled": self.cancelled, "aborted": self.aborted,
                "report": self.report_path, "items": self.items}

    def write_report(self) -> None:
        data = {"finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "provider": getattr(self.provider, "name", "?"),
                "cancelled": self.cancelled, "aborted": self.aborted,
                "summary": self.summary, "items": self.items,
                "note": "Read-only import from Spotify. 'doubtful' entries were imported and are "
                        "marked '(? check match)'; 'not_found' were not imported."}
        try:
            atomic_write_json(self.report_path, data, indent=1)
        except Exception:
            logger.exception("could not write the import report")


def _describe(src: dict) -> dict:
    return {"spotify_id": src.get("id"), "name": src.get("name"),
            "artists": src.get("artists", []), "album": src.get("album"),
            "duration_ms": src.get("duration_ms")}


def _describe_target(t: dict) -> dict:
    return {"id": t.get("id"), "name": t.get("name"),
            "artists": [a.get("name") for a in t.get("artists") or [] if a]}
