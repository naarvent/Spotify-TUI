"""Subsonic / OpenSubsonic provider (Navidrome, Airsonic, Gonic, ...).

Talks to the server's REST API (``<url>/rest/<endpoint>``) with token
authentication: every request carries ``u`` (user), ``t = md5(password +
salt)``, ``s`` (a fresh random salt), ``v`` (API version), ``c`` (client name)
and ``f=json``. The clear-text password never goes on the wire and is never
written to ``spt_config.json``: it comes from ``SPT_SUBSONIC_PASSWORD`` or the
system keyring (see :mod:`spt_tui.secret_store`).

Features: ``search3`` search, playlists (list / open / create / add / remove /
delete), favourites through ``star`` / ``unstar`` (shown as Liked Songs),
``scrobble`` (now-playing on track start, a submission once a track has been
played for half its length or 4 minutes) and audio through the ``stream``
endpoint played by mpv.

Configuration (``spt_config.json``; env vars override):
``subsonic_url`` / ``SPT_SUBSONIC_URL``, ``subsonic_user`` / ``SPT_SUBSONIC_USER``.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode

try:
    import requests
except Exception:  # pragma: no cover - requests is a core dependency
    requests = None

from .. import config
from ..config import logger
from ..local_library import LocalLibrary
from ..secret_store import SecretStore, default_store
from .base import Capabilities, ProviderError, ProviderUnsupported, make_page
from .mpv_backed import MpvBackedProvider

API_VERSION = "1.16.1"
CLIENT_NAME = "spt-tui"
PASSWORD_ENV = "SPT_SUBSONIC_PASSWORD"


def password_account(url: str, user: str) -> str:
    """Keyring account name for one server + user."""
    return f"subsonic:{user}@{(url or '').rstrip('/')}"


def _cfg(key: str, env: str) -> Optional[str]:
    v = os.getenv(env)
    if v and v.strip():
        return v.strip()
    cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
    val = cfg.get(key)
    return val.strip() if isinstance(val, str) and val.strip() else None


class SubsonicError(ProviderError):
    pass


class SubsonicProvider(MpvBackedProvider):
    name = "subsonic"
    display_name = "Subsonic"

    # Scrobble once a track has played this long (Last.fm's rule).
    SCROBBLE_MIN_MS = 4 * 60 * 1000
    STARRED_TTL = 20.0

    def __init__(self, *, url: Optional[str] = None, user: Optional[str] = None,
                 password: Optional[str] = None, session=None, player=None,
                 secret_store: Optional[SecretStore] = None,
                 salt_factory: Callable[[], str] = lambda: secrets.token_hex(6),
                 now: Callable[[], float] = time.monotonic,
                 library: Optional[LocalLibrary] = None, offline="auto"):
        super().__init__(player=player)
        # Subsonic keeps likes and playlists on the server; only the play
        # history (Recently Played) and availability marks are local.
        self.library = library if library is not None else LocalLibrary(self.name)
        self.url = (url or _cfg("subsonic_url", "SPT_SUBSONIC_URL") or "").rstrip("/")
        self.user = user or _cfg("subsonic_user", "SPT_SUBSONIC_USER") or ""
        store = secret_store or default_store()
        if password is None and self.url and self.user:
            try:
                password = store.get(password_account(self.url, self.user), env=PASSWORD_ENV)
            except Exception:
                logger.exception("Subsonic: password lookup failed")
                password = os.getenv(PASSWORD_ENV)
        self._password = password or None
        if session is None and requests is not None:
            session = requests.Session()
        self._session = session
        self._salt = salt_factory
        self._now = now
        self._playlists_cache: Optional[List[dict]] = None
        self._starred: Optional[Tuple[float, set]] = None
        self._lock = threading.Lock()
        # Offline copies, one folder per server + user (ids are only unique per
        # server): CACHE_DIR/offline/subsonic/<host>_<user>-<hash>/. No secret
        # goes into the path.
        if offline == "auto":
            offline = None
            if self.url and self.user:
                from ..offline import OfflineStore, safe_name
                host = urlparse(self.url).netloc or self.url
                offline = OfflineStore(f"{self.name}/{safe_name(host + '_' + self.user)}")
        self.offline = offline
        # Scrobble bookkeeping for the track currently playing.
        self._scrobble_id: Optional[str] = None
        self._scrobble_max_ms = 0
        self._scrobble_dur_ms = 0
        self._scrobble_done = False

    # ------------------------------------------------------------------ #
    # Availability / capabilities
    # ------------------------------------------------------------------ #
    def unavailable_reason(self) -> Optional[str]:
        if self._session is None:
            return "the 'requests' package is missing."
        if not self.url or not self.user:
            return ("no server configured (set the Subsonic URL and user in Settings, "
                    "or SPT_SUBSONIC_URL / SPT_SUBSONIC_USER).")
        if not self._password:
            return (f"no password (save it in Settings to store it in the system keyring, "
                    f"or export {PASSWORD_ENV}).")
        return None

    @property
    def capabilities(self) -> Capabilities:
        return self._playback_caps(Capabilities(can_podcasts=False, can_import_playlists=False,
                                                can_artist_top_tracks=True, can_related_artists=True,
                                                can_radio=True, can_download=self.offline is not None,
                                                playlist_visibility=False, playlist_description=False))

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #
    def auth_params(self) -> List[Tuple[str, str]]:
        salt = self._salt()
        token = hashlib.md5(((self._password or "") + salt).encode("utf-8")).hexdigest()
        return [("u", self.user), ("t", token), ("s", salt), ("v", API_VERSION),
                ("c", CLIENT_NAME), ("f", "json")]

    def _call(self, endpoint: str, params=None) -> dict:
        """GET one endpoint and return its ``subsonic-response`` body."""
        if self.unavailable_reason():
            raise SubsonicError(self.unavailable_reason())
        extra = list(params.items()) if isinstance(params, dict) else list(params or [])
        query = self.auth_params() + [(k, v) for k, v in extra if v is not None]
        try:
            r = self._session.get(f"{self.url}/rest/{endpoint}", params=query, timeout=15)
            r.raise_for_status()
            body = r.json()
        except SubsonicError:
            raise
        except Exception as exc:
            # Never log the query string: it carries the token and salt.
            raise SubsonicError(f"Subsonic request '{endpoint}' failed: {type(exc).__name__}") from exc
        data = (body or {}).get("subsonic-response") or {}
        if data.get("status") != "ok":
            err = data.get("error") or {}
            raise SubsonicError(f"Subsonic error {err.get('code', '?')}: "
                                f"{err.get('message', 'unknown error')}")
        return data

    def ping(self) -> bool:
        try:
            self._call("ping")
            return True
        except SubsonicError:
            logger.exception("Subsonic ping failed")
            return False

    # ------------------------------------------------------------------ #
    # Mapping to the Spotify-shaped dicts the UI renders
    # ------------------------------------------------------------------ #
    @staticmethod
    def _uri(kind: str, item_id) -> str:
        return f"subsonic:{kind}:{item_id}"

    def _map_song(self, s: dict) -> dict:
        sid = str(s.get("id"))
        artist_id = s.get("artistId")
        t = {
            "id": sid,
            "uri": self._uri("track", sid),
            "type": "track",
            "name": s.get("title") or "(no title)",
            "artists": [{"name": s.get("artist") or "", "id": artist_id,
                         "uri": self._uri("artist", artist_id) if artist_id else None}],
            "album": {"name": s.get("album") or "", "id": s.get("albumId"),
                      "uri": self._uri("album", s.get("albumId")) if s.get("albumId") else None,
                      "images": []},
            "duration_ms": int(s.get("duration") or 0) * 1000,
            "track_number": s.get("track"),
            "_suffix": s.get("suffix"),
            "_starred": bool(s.get("starred")),
        }
        return self._remember(t)

    def _map_album(self, a: dict) -> dict:
        aid = str(a.get("id"))
        return {"id": aid, "uri": self._uri("album", aid), "type": "album",
                "name": a.get("name") or a.get("title") or "(no title)",
                "artists": [{"name": a.get("artist") or "", "id": a.get("artistId")}],
                "total_tracks": a.get("songCount"),
                "release_date": str(a.get("year") or ""), "images": []}

    def _map_artist(self, a: dict) -> dict:
        aid = str(a.get("id"))
        return {"id": aid, "uri": self._uri("artist", aid), "type": "artist",
                "name": a.get("name") or "(no name)", "genres": [], "images": []}

    def _map_playlist(self, p: dict) -> dict:
        pid = str(p.get("id"))
        n = p.get("songCount") or 0
        return {"id": pid, "uri": self._uri("playlist", pid), "type": "playlist",
                "name": p.get("name") or "(no name)",
                "owner": {"id": p.get("owner"), "display_name": p.get("owner") or ""},
                "description": p.get("comment") or "",
                "public": bool(p.get("public")),
                "tracks": {"total": n}, "items": {"total": n}, "images": []}

    # ------------------------------------------------------------------ #
    # Search and catalogue
    # ------------------------------------------------------------------ #
    def search(self, query: str, kind: str = "track", limit: int = 10, offset: int = 0) -> dict:
        limit, offset = int(limit or 10), int(offset or 0)
        if kind == "playlist":
            q = (query or "").strip().lower()
            items = [p for p in self._all_playlists() if q in (p.get("name") or "").lower()]
            return make_page(items, offset=offset, limit=limit)
        counts = {"songCount": 0, "albumCount": 0, "artistCount": 0}
        key = {"track": "songCount", "album": "albumCount", "artist": "artistCount"}.get(kind)
        if key is None:
            return make_page([])
        counts[key] = limit
        off_key = key.replace("Count", "Offset")
        data = self._call("search3", {"query": query or "", **counts, off_key: offset})
        res = data.get("searchResult3") or {}
        if kind == "track":
            items = [self._map_song(s) for s in res.get("song") or []]
        elif kind == "album":
            items = [self._map_album(a) for a in res.get("album") or []]
        else:
            items = [self._map_artist(a) for a in res.get("artist") or []]
        more = len(items) >= limit
        return {"items": items, "total": offset + len(items) + (1 if more else 0),
                "offset": offset, "limit": limit, "next": "more" if more else None}

    def track(self, track_id: str) -> dict:
        data = self._call("getSong", {"id": track_id})
        return self._map_song(data.get("song") or {}) if data.get("song") else {}

    fetch_track = track

    def album_tracks(self, album_id: str, limit: int = 50, offset: int = 0) -> dict:
        data = self._call("getAlbum", {"id": album_id})
        songs = [self._map_song(s) for s in (data.get("album") or {}).get("song") or []]
        return make_page(songs, offset=offset, limit=limit)

    # OpenSubsonic `releaseTypes` values shown under "Singles" on the artist page.
    SINGLE_RELEASE_TYPES = ("single", "ep")

    @classmethod
    def _is_single(cls, a: dict) -> bool:
        types = a.get("releaseTypes") or []
        if isinstance(types, str):
            types = [types]
        return any(str(t).strip().lower() in cls.SINGLE_RELEASE_TYPES for t in types)

    def _split_releases(self, raw_albums) -> Tuple[List[dict], List[dict]]:
        """(albums, singles). Plain Subsonic has no release type, so without
        OpenSubsonic `releaseTypes` everything is an album."""
        albums, singles = [], []
        for a in raw_albums or []:
            if isinstance(a, dict):
                (singles if self._is_single(a) else albums).append(self._map_album(a))
        return albums, singles

    def artist_albums(self, artist_id: str, album_type: Optional[str] = None,
                      limit: int = 50, offset: int = 0) -> dict:
        data = self._call("getArtist", {"id": artist_id})
        albums, singles = self._split_releases((data.get("artist") or {}).get("album"))
        want = singles if album_type and "single" in str(album_type) else albums
        if album_type and "album" not in str(album_type) and "single" not in str(album_type):
            want = []
        return make_page(want, offset=offset, limit=limit)

    def _artist_name(self, artist_id: str, artist_name: Optional[str]) -> str:
        if artist_name:
            return artist_name
        data = self._call("getArtist", {"id": artist_id})
        return (data.get("artist") or {}).get("name") or ""

    def artist_top_tracks(self, artist_id, artist_name=None, limit=10):
        # getTopSongs (API 1.13) is by artist NAME; Navidrome fills it from Last.fm.
        name = self._artist_name(artist_id, artist_name)
        if not name:
            return []
        data = self._call("getTopSongs", {"artist": name, "count": int(limit or 10)})
        songs = (data.get("topSongs") or {}).get("song") or []
        return [self._map_song(x) for x in songs if isinstance(x, dict)]

    def related_artists(self, artist_id, artist_name=None, limit=20):
        data = self._call("getArtistInfo2", {"id": artist_id, "count": int(limit or 20)})
        sim = (data.get("artistInfo2") or {}).get("similarArtist") or []
        # Servers list similar artists that are not in the library without an id.
        return [self._map_artist(a) for a in sim if isinstance(a, dict) and a.get("id")]

    # ------------------------------------------------------------------ #
    # Radio: getSimilarSongs (song/album/artist id) and getSimilarSongs2
    # (artist id, ID3). Navidrome answers from Last.fm similar artists, so an
    # empty answer usually means no Last.fm key on the server.
    # ------------------------------------------------------------------ #
    RADIO_PLAYLIST_SEEDS = 3

    def _similar(self, endpoint: str, key: str, item_id: str, count: int) -> List[dict]:
        data = self._call(endpoint, {"id": item_id, "count": int(count)})
        songs = (data.get(key) or {}).get("song") or []
        return [self._map_song(x) for x in songs if isinstance(x, dict)]

    def radio(self, seed_kind, seed_id, *, seed=None, limit=50) -> dict:
        limit = int(limit or 50)
        notes: List[str] = []
        try:
            if seed_kind == "artist":
                tracks = self._similar("getSimilarSongs2", "similarSongs2", seed_id, limit)
            elif seed_kind == "track":
                tracks = self._similar("getSimilarSongs", "similarSongs", seed_id, limit)
                if tracks and not any(t["id"] == str(seed_id) for t in tracks):
                    # Start the radio with the seed itself, like YouTube Music.
                    try:
                        tracks = [self.track(seed_id)] + tracks
                    except SubsonicError:
                        pass
            elif seed_kind == "playlist":
                songs = self._playlist_songs(seed_id)
                if not songs:
                    raise ProviderUnsupported("This playlist is empty: nothing to start a radio from.")
                seeds = songs[: self.RADIO_PLAYLIST_SEEDS]
                in_pl = {t.get("id") for t in songs}
                tracks, seen = [], set()
                per = max(10, limit // len(seeds))
                for sd in seeds:
                    for t in self._similar("getSimilarSongs", "similarSongs", sd["id"], per):
                        if t["id"] not in seen and t["id"] not in in_pl:
                            seen.add(t["id"]); tracks.append(t)
                notes.append(f"Playlist radio: songs similar to its first {len(seeds)} tracks.")
            else:
                raise ProviderUnsupported("Radio starts from a track, an artist or a playlist.")
        except SubsonicError as exc:
            raise ProviderError(f"Subsonic radio failed ({exc}).") from exc
        tracks = [t for t in tracks if t and t.get("id")]
        if not tracks:
            notes.append("The server found no similar songs (Navidrome needs Last.fm for this).")
        return {"tracks": tracks[:limit], "notes": notes}

    def artist_overview(self, artist_id, artist_name=None) -> dict:
        out = {"top_tracks": [], "albums": [], "singles": [], "related": [], "notes": []}
        try:
            data = self._call("getArtist", {"id": artist_id})
        except SubsonicError as exc:
            out["notes"].append(str(exc))
            return out
        art = data.get("artist") or {}
        out["albums"], out["singles"] = self._split_releases(art.get("album"))
        name = artist_name or art.get("name") or ""
        try:
            out["top_tracks"] = self.artist_top_tracks(artist_id, artist_name=name)
            if not out["top_tracks"]:
                out["notes"].append("The server has no top songs for this artist "
                                    "(Navidrome takes them from Last.fm).")
        except SubsonicError as exc:
            out["notes"].append(f"Top songs not available from this server ({exc}).")
        try:
            out["related"] = self.related_artists(artist_id, artist_name=name)
            if not out["related"]:
                out["notes"].append("The server lists no similar artists in your library.")
        except SubsonicError as exc:
            out["notes"].append(f"Similar artists not available from this server ({exc}).")
        return out

    def playlist(self, playlist_id: str) -> dict:
        data = self._call("getPlaylist", {"id": playlist_id})
        return self._map_playlist(data.get("playlist") or {})

    # ------------------------------------------------------------------ #
    # Library and playlists
    # ------------------------------------------------------------------ #
    def _all_playlists(self, force: bool = False) -> List[dict]:
        with self._lock:
            cached = self._playlists_cache
        if cached is not None and not force:
            return cached
        data = self._call("getPlaylists")
        items = [self._map_playlist(p)
                 for p in (data.get("playlists") or {}).get("playlist") or []]
        with self._lock:
            self._playlists_cache = items
        return items

    def user_playlists(self, limit=50, offset=0) -> dict:
        # offset 0 is a fresh load (startup / Ctrl+R): refetch.
        return make_page(self._all_playlists(force=(int(offset or 0) == 0)),
                         offset=offset, limit=limit)

    def _playlist_songs(self, playlist_id: str) -> List[dict]:
        data = self._call("getPlaylist", {"id": playlist_id})
        return [self._map_song(s) for s in (data.get("playlist") or {}).get("entry") or []]

    def playlist_items(self, playlist_id, limit=100, offset=0, fields=None) -> dict:
        entries = [{"added_at": "", "item": t, "track": t}
                   for t in self._playlist_songs(playlist_id)]
        return make_page(entries, offset=offset, limit=limit)

    def _starred2(self) -> dict:
        return self._call("getStarred2").get("starred2") or {}

    def saved_tracks(self, limit=50, offset=0) -> dict:
        songs = self._starred2().get("song") or []
        entries = [{"added_at": s.get("starred") or "", "track": self._map_song(s)} for s in songs]
        with self._lock:
            self._starred = (self._now(), {e["track"]["id"] for e in entries})
        return make_page(entries, offset=offset, limit=limit)

    def saved_albums(self, limit=50, offset=0) -> dict:
        albums = self._starred2().get("album") or []
        entries = [{"added_at": a.get("starred") or "", "album": self._map_album(a)} for a in albums]
        return make_page(entries, offset=offset, limit=limit)

    def _starred_ids(self) -> set:
        with self._lock:
            cached = self._starred
        if cached and (self._now() - cached[0]) < self.STARRED_TTL:
            return cached[1]
        ids = {str(s.get("id")) for s in self._starred2().get("song") or []}
        with self._lock:
            self._starred = (self._now(), ids)
        return ids

    def check_saved_tracks(self, ids: List[str], on_batch=None) -> List[bool]:
        try:
            starred = self._starred_ids()
        except Exception:
            logger.exception("Subsonic: getStarred2 failed")
            starred = set()
        out = [str(i) in starred for i in (ids or [])]
        if on_batch is not None and out:
            try:
                on_batch(0, list(out))
            except Exception:
                logger.exception("check_saved_tracks: on_batch callback failed")
        return out

    def _star(self, endpoint: str, ids: List[str], add: bool) -> bool:
        ids = [self._normalize_track_id(i) for i in ids or [] if i]
        if not ids:
            return False
        try:
            self._call(endpoint, [("id", i) for i in ids])
        except SubsonicError:
            logger.exception("Subsonic %s failed", endpoint)
            return False
        with self._lock:
            if self._starred:
                ts, s = self._starred
                s = set(s) | set(ids) if add else set(s) - set(ids)
                self._starred = (ts, s)
        return True

    def save_tracks(self, ids: List[str]) -> bool:
        return self._star("star", ids, True)

    # Albums and artists are starred on the server too (albumId / artistId).
    _STAR_PARAM = {"album": "albumId", "artist": "artistId"}

    def saved_contains(self, kind: str, ids: List[str]) -> List[bool]:
        key = {"album": "album", "artist": "artist"}.get(kind)
        if not key:
            return [False] * len(ids or [])
        try:
            have = {str(x.get("id")) for x in self._starred2().get(key) or []}
        except SubsonicError:
            logger.exception("Subsonic getStarred2 failed")
            have = set()
        return [str(self._normalize_track_id(i)) in have for i in ids or []]

    def _star_kind(self, endpoint: str, kind: str, ids: List[str]) -> bool:
        param = self._STAR_PARAM.get(kind)
        ids = [self._normalize_track_id(i) for i in ids or [] if i]
        if not param or not ids:
            return False
        try:
            self._call(endpoint, [(param, i) for i in ids])
            return True
        except SubsonicError:
            logger.exception("Subsonic %s %s failed", endpoint, kind)
            return False

    def save_items(self, kind: str, ids: List[str]) -> bool:
        return self._star_kind("star", kind, ids)

    def remove_items(self, kind: str, ids: List[str]) -> bool:
        return self._star_kind("unstar", kind, ids)

    def followed_artists(self, limit: int = 50) -> dict:
        try:
            arts = [self._map_artist(a) for a in self._starred2().get("artist") or []]
        except SubsonicError:
            logger.exception("Subsonic getStarred2 failed")
            arts = []
        return make_page(arts, offset=0, limit=limit)

    def remove_tracks(self, ids: List[str]) -> bool:
        return self._star("unstar", ids, False)

    def create_playlist(self, name: str, public: bool = True, collaborative: bool = False,
                        description: str = ""):
        self._last_create_error = None
        try:
            data = self._call("createPlaylist", {"name": name})
            with self._lock:
                self._playlists_cache = None
            pl = data.get("playlist")
            if pl:
                return self._map_playlist(pl)
            # Servers before API 1.14 answer with an empty body: look it up.
            return next((p for p in self._all_playlists(force=True) if p.get("name") == name),
                        {"name": name, "id": None})
        except SubsonicError as exc:
            self._last_create_error = str(exc)
            logger.exception("Subsonic createPlaylist failed")
            return None

    def add_items_to_playlist(self, playlist_id: str, uris: List[str]) -> bool:
        ids = [self._normalize_track_id(u) for u in uris or [] if u]
        if not ids:
            return False
        try:
            self._call("updatePlaylist", [("playlistId", playlist_id)] +
                       [("songIdToAdd", i) for i in ids])
            with self._lock:
                self._playlists_cache = None
            return True
        except SubsonicError:
            logger.exception("Subsonic updatePlaylist (add) failed")
            return False

    def remove_playlist_positions(self, playlist_id: str, positions: List[int]) -> bool:
        try:
            self._call("updatePlaylist", [("playlistId", playlist_id)] +
                       [("songIndexToRemove", int(p)) for p in positions])
            return True
        except SubsonicError:
            logger.exception("Subsonic updatePlaylist (remove) failed")
            return False

    def delete_playlist(self, playlist_id: str) -> bool:
        try:
            self._call("deletePlaylist", {"id": playlist_id})
            with self._lock:
                self._playlists_cache = None
            return True
        except SubsonicError:
            logger.exception("Subsonic deletePlaylist failed")
            return False

    # ------------------------------------------------------------------ #
    # Playback (mpv) and scrobbling
    # ------------------------------------------------------------------ #
    def stream_url(self, track: dict) -> str:
        params = self.auth_params() + [("id", str(track.get("id")))]
        return f"{self.url}/rest/stream?{urlencode(params)}"

    _CT_EXT = {"audio/mpeg": ".mp3", "audio/flac": ".flac", "audio/x-flac": ".flac",
               "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/mp4": ".m4a",
               "audio/aac": ".aac", "audio/x-m4a": ".m4a", "audio/wav": ".wav"}

    def _fetch_to(self, endpoint: str, track_id: str, dest_base: str, suffix: Optional[str]) -> str:
        if self.unavailable_reason():
            raise SubsonicError(self.unavailable_reason())
        query = self.auth_params() + [("id", str(track_id))]
        try:
            r = self._session.get(f"{self.url}/rest/{endpoint}", params=query, timeout=60, stream=True)
            r.raise_for_status()
        except Exception as exc:
            # Never log the query: it carries the token and salt.
            raise SubsonicError(f"Subsonic '{endpoint}' failed: {type(exc).__name__}") from exc
        ctype = str((getattr(r, "headers", None) or {}).get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype in ("application/json", "text/xml", "application/xml"):
            # A Subsonic error answer (e.g. the user has no download permission).
            raise SubsonicError(f"Subsonic '{endpoint}' refused the download")
        ext = ("." + suffix.strip(".").lower()) if suffix else self._CT_EXT.get(ctype, ".audio")
        path = dest_base + ext
        with open(path, "wb") as fh:
            it = getattr(r, "iter_content", None)
            if callable(it):
                for chunk in it(chunk_size=256 * 1024):
                    if chunk:
                        fh.write(chunk)
            else:
                fh.write(r.content or b"")
        return path

    def _download_file(self, track: dict, dest_base: str) -> str:
        """The original file (`download`); if the server refuses it (no
        download permission), the transcoded `stream` instead."""
        tid = track.get("id")
        try:
            return self._fetch_to("download", tid, dest_base, track.get("_suffix"))
        except SubsonicError as exc:
            logger.info("Subsonic download refused (%s); using stream", exc)
            return self._fetch_to("stream", tid, dest_base, None)

    def context_tracks(self, context_uri: str) -> List[dict]:
        parts = str(context_uri or "").split(":")
        if len(parts) < 3:
            return []
        kind, cid = parts[1], ":".join(parts[2:])
        if kind == "playlist":
            return self._playlist_songs(cid)
        if kind == "album":
            return self.album_tracks(cid, limit=None).get("items") or []
        return []

    def scrobble(self, track_id: str, *, submission: bool) -> bool:
        try:
            params = {"id": track_id, "submission": "true" if submission else "false"}
            if submission:
                params["time"] = int(time.time() * 1000)
            self._call("scrobble", params)
            return True
        except SubsonicError:
            logger.exception("Subsonic scrobble failed")
            return False

    def _scrobble_due(self) -> bool:
        if self._scrobble_done or not self._scrobble_id:
            return False
        dur = self._scrobble_dur_ms
        threshold = min(self.SCROBBLE_MIN_MS, dur // 2) if dur else self.SCROBBLE_MIN_MS
        return self._scrobble_max_ms >= max(1, threshold)

    def on_playback_state(self, track, position_ms, duration_ms, is_playing) -> None:
        tid = (track or {}).get("id")
        if tid != self._scrobble_id:
            # Track changed (or playback stopped): submit the previous one if it
            # was played enough, then announce the new one as "now playing".
            if self._scrobble_due():
                self.scrobble(self._scrobble_id, submission=True)
            self._scrobble_id = tid
            self._scrobble_max_ms = 0
            self._scrobble_dur_ms = int(duration_ms or 0)
            self._scrobble_done = False
            if tid:
                self.scrobble(tid, submission=False)
            return
        if not tid:
            return
        self._scrobble_dur_ms = int(duration_ms or self._scrobble_dur_ms or 0)
        self._scrobble_max_ms = max(self._scrobble_max_ms, int(position_ms or 0))
        if self._scrobble_due():
            self._scrobble_done = self.scrobble(tid, submission=True)
