"""YouTube Music provider: anonymous ``ytmusicapi`` + ``mpv``/``yt-dlp`` audio.

No account and no credentials: search and *public* playlists come from
``ytmusicapi.YTMusic()`` without authentication, and audio is played by mpv,
which resolves ``https://music.youtube.com/watch?v=<id>`` through its yt-dlp
hook. There is no account, so likes (Liked Songs), saved albums, followed
artists, your own playlists and the play history live in the **local
library** (``local_library_ytmusic.json``, see :mod:`spt_tui.local_library`).
Public playlists are followed with ``f`` or **Import Playlists** (a
``music.youtube.com/playlist?list=...`` URL or id) and stored as
``ytmusic_playlists`` in ``spt_config.json``.

When something disappears upstream (a video removed or made private, an album
gone, a playlist turned private, no network) nothing saved is deleted: it is
marked unavailable, shown as such, and skipped with a notice when playing.

Optional dependencies: ``pip install "Spotify-TUI[ytmusic]"`` for ytmusicapi
and yt-dlp, plus the ``mpv`` binary. Anything missing disables the provider
(or just its playback) with a clear message instead of a crash.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import threading
from typing import Callable, List, Optional
from urllib.parse import parse_qs, urlparse

from .. import config
from ..config import logger
from ..local_library import LocalLibrary
from .base import ProviderUnsupported, Capabilities, ProviderError, make_page
from .local_store import LocalLibraryMixin
from .mpv_backed import MpvBackedProvider

WATCH_URL = "https://music.youtube.com/watch?v={}"


def _import_ytmusicapi():
    try:
        import ytmusicapi  # type: ignore
        return ytmusicapi
    except Exception:
        return None


def parse_playlist_id(raw: str) -> Optional[str]:
    """Playlist id from a YouTube (Music) URL, a ``ytmusic:playlist:`` URI or a
    bare id."""
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("ytmusic:playlist:"):
        return s.split(":", 2)[2] or None
    if "://" in s or s.startswith(("music.youtube.com", "www.youtube.com", "youtube.com")):
        q = parse_qs(urlparse(s if "://" in s else "https://" + s).query)
        return (q.get("list") or [None])[0]
    return s


TOPIC_SUFFIX = " - Topic"


def clean_artist_name(name: str) -> str:
    """YouTube's auto-generated artist channels are named "<Artist> - Topic"."""
    n = (name or "").strip()
    return n[: -len(TOPIC_SUFFIX)].strip() if n.endswith(TOPIC_SUFFIX) else n


def split_channel_upload(title: str, artists: List[dict], video_type: Optional[str]):
    """A user upload (``MUSIC_VIDEO_TYPE_UGC``) is attributed to the uploading
    channel, and its title usually carries the real artist:
    ``"JOJI - I Don't Feel Like ..."`` by channel ``2Restarted``. Return the
    cleaned ``(title, artists)``: the artist from the title, the channel
    dropped. Songs and official videos are left alone."""
    if video_type != "MUSIC_VIDEO_TYPE_UGC" or " - " not in (title or ""):
        return title, artists
    left, right = title.split(" - ", 1)
    left = left.strip().strip("[]() ").strip()   # "[ Joji ] - Song" -> "Joji"
    if not left or not right.strip():
        return title, artists
    return right.strip(), [{"name": left, "id": None, "uri": None}]



def _is_network_error(exc: BaseException) -> bool:
    """Timeouts, dropped connections, DNS: the item itself may be fine."""
    try:
        import requests
        if isinstance(exc, requests.exceptions.RequestException) and \
                not isinstance(exc, requests.exceptions.HTTPError):
            return True
    except ImportError:
        pass
    return isinstance(exc, (OSError, TimeoutError))

def _default_resolver():
    try:
        import importlib.util
        if importlib.util.find_spec("yt_dlp") is None:
            return None
        from ..stream_cache import StreamResolver
        return StreamResolver()
    except Exception:
        logger.exception("ytmusic: stream resolver unavailable")
        return None


class YTMusicProvider(LocalLibraryMixin, MpvBackedProvider):
    name = "ytmusic"
    display_name = "YouTube Music"
    MPV_ARGS = ("--ytdl=yes", "--ytdl-format=bestaudio/best")

    def __init__(self, *, client=None, player=None, library: Optional[LocalLibrary] = None,
                 module_loader: Callable = _import_ytmusicapi,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 stream_resolver="auto", offline="auto", downloader=None):
        super().__init__(player=player)
        # Offline copies: "auto" = the default store under CACHE_DIR/offline/ytmusic
        # (only if yt_dlp can be imported, or a downloader is injected); None disables.
        self._downloader = downloader
        if offline == "auto":
            offline = None
            if downloader is not None or importlib.util.find_spec("yt_dlp") is not None:
                from ..offline import OfflineStore
                offline = OfflineStore(self.name)
        self.offline = offline
        # Direct stream URLs resolved ahead so skipping is near-instant (see mpv_backed.py).
        # "auto": only with the real mpv player and the yt_dlp module; None disables it.
        if stream_resolver == "auto":
            stream_resolver = _default_resolver() if player is None else None
        self._resolver = stream_resolver
        self._which = which
        self._module = None
        self._client = client
        if client is None:
            self._module = module_loader()
        self._client_lock = threading.Lock()
        self.library = library if library is not None else LocalLibrary(self.name)
        self._albums_seen: dict = {}
        self._artists_seen: dict = {}

    # ------------------------------------------------------------------ #
    # Availability / capabilities
    # ------------------------------------------------------------------ #
    def unavailable_reason(self) -> Optional[str]:
        if self._client is None and self._module is None:
            return ("the optional 'ytmusicapi' package is not installed "
                    "(pip install \"Spotify-TUI[ytmusic]\").")
        return None

    def playback_unavailable_reason(self) -> Optional[str]:
        reason = super().playback_unavailable_reason()
        if reason:
            return reason
        if not self._which("yt-dlp"):
            return "yt-dlp was not found on PATH (pip install \"Spotify-TUI[ytmusic]\" or install yt-dlp)."
        return None

    @property
    def capabilities(self) -> Capabilities:
        # Likes and playlists work through the local library (no account).
        return self._playback_caps(Capabilities(
            can_like=True, can_edit_playlists=True, can_podcasts=False, local_library=True,
            can_import_from_spotify=True, can_artist_top_tracks=True, can_related_artists=True,
            can_radio=True, can_download=self.offline is not None,
            playlist_visibility=False))

    def _yt(self):
        with self._client_lock:
            if self._client is None:
                if self._module is None:
                    raise ProviderError(self.unavailable_reason())
                self._client = self._module.YTMusic()
            return self._client

    # ------------------------------------------------------------------ #
    # Mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _artists(raw) -> List[dict]:
        out = []
        for a in raw or []:
            if isinstance(a, dict) and a.get("name"):
                aid = a.get("id")
                out.append({"name": clean_artist_name(a["name"]), "id": aid,
                            "uri": f"ytmusic:artist:{aid}" if aid else None})
        return out

    def _map_song(self, s: dict, album: Optional[dict] = None) -> Optional[dict]:
        vid = (s or {}).get("videoId")
        if not vid:
            return None
        alb = s.get("album") or album or {}
        if isinstance(alb, str):
            # get_album() returns the track's album as a plain name string.
            alb = {"name": alb, "id": (album or {}).get("id")}
        dur = s.get("duration_seconds")
        if dur is None and isinstance(s.get("duration"), str):
            dur = 0
            for part in s["duration"].split(":"):
                try:
                    dur = dur * 60 + int(part)
                except ValueError:
                    dur = 0
                    break
        title, artists = split_channel_upload(s.get("title") or "(no title)",
                                              self._artists(s.get("artists")), s.get("videoType"))
        t = {"id": vid, "uri": f"ytmusic:track:{vid}", "type": "track",
             "name": title,
             "artists": artists,
             "album": {"name": (alb or {}).get("name") or (alb or {}).get("title") or "",
                       "id": (alb or {}).get("id") or (alb or {}).get("browseId"),
                       "images": []},
             "duration_ms": int(dur or 0) * 1000}
        if s.get("isAvailable") is False:
            t["_unavailable"] = "not available"
        return self._remember(t)

    def _map_album(self, a: dict, artist: Optional[dict] = None) -> dict:
        bid = a.get("browseId") or a.get("id")
        artists = self._artists(a.get("artists"))
        if not artists and artist:
            # get_artist()'s album/single lists carry no artist: it is the
            # artist whose page this is.
            artists = [artist]
        out = {"id": bid, "uri": f"ytmusic:album:{bid}", "type": "album",
               "name": a.get("title") or "(no title)", "artists": artists,
               "release_date": str(a.get("year") or ""), "images": []}
        if bid:
            self._albums_seen[bid] = out
        return out

    def _map_artist(self, a: dict) -> dict:
        aid = a.get("browseId") or a.get("channelId") or a.get("id")
        out = {"id": aid, "uri": f"ytmusic:artist:{aid}", "type": "artist",
               "name": clean_artist_name(a.get("artist") or a.get("title") or a.get("name") or "(no name)"),
               "genres": [], "images": []}
        if aid:
            self._artists_seen[aid] = out
        return out

    @staticmethod
    def _map_playlist(p: dict) -> dict:
        pid = p.get("playlistId") or p.get("id") or p.get("browseId")
        if isinstance(pid, str) and pid.startswith("VL"):
            pid = pid[2:]
        author = p.get("author")
        if isinstance(author, list):
            author = ", ".join(a.get("name", "") for a in author if isinstance(a, dict))
        elif isinstance(author, dict):
            author = author.get("name")
        n = p.get("trackCount") or p.get("itemCount") or 0
        try:
            n = int(str(n).split()[0].replace(",", ""))
        except (TypeError, ValueError):
            n = 0
        return {"id": pid, "uri": f"ytmusic:playlist:{pid}", "type": "playlist",
                "name": p.get("title") or p.get("name") or "(no name)",
                "owner": {"id": None, "display_name": author or ""},
                "tracks": {"total": n}, "items": {"total": n}, "images": []}

    # ------------------------------------------------------------------ #
    # Search and catalogue
    # ------------------------------------------------------------------ #
    _FILTERS = {"track": "songs", "album": "albums", "artist": "artists",
                "playlist": "community_playlists"}

    def search(self, query: str, kind: str = "track", limit: int = 10, offset: int = 0) -> dict:
        flt = self._FILTERS.get(kind)
        if not flt:
            return make_page([])
        limit, offset = int(limit or 10), int(offset or 0)
        try:
            raw = self._yt().search(query or "", filter=flt, limit=offset + limit) or []
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"YouTube Music search failed: {type(exc).__name__}") from exc
        mapper = {"track": self._map_song, "album": self._map_album,
                  "artist": self._map_artist, "playlist": self._map_playlist}[kind]
        rows = [r for r in raw if isinstance(r, dict)]
        if kind == "track":
            # filter="songs" should only return songs; keep it strict anyway so
            # a video never shows up as a song.
            rows = [r for r in rows if r.get("resultType", "song") == "song"]
        items = [x for x in (mapper(r) for r in rows) if x]
        return make_page(items, offset=offset, limit=limit)

    def import_search(self, query: str, kind: str = "track", *, lax: bool = False) -> List[dict]:
        """Candidates for the Spotify import. Strict: the songs filter. Lax
        (second chance for tracks): no filter, keeping songs and videos.
        Errors propagate (the importer retries and never caches them)."""
        if kind == "track" and lax:
            raw = self._yt().search(query or "", limit=20) or []
            rows = [r for r in raw if isinstance(r, dict) and r.get("videoId")
                    and r.get("resultType") in ("song", "video")]
            return [x for x in (self._map_song(r) for r in rows) if x]
        flt = self._FILTERS.get(kind)
        if not flt:
            return []
        raw = self._yt().search(query or "", filter=flt, limit=10) or []
        mapper = {"track": self._map_song, "album": self._map_album, "artist": self._map_artist}[kind]
        rows = [r for r in raw if isinstance(r, dict)]
        return [x for x in (mapper(r) for r in rows) if x]

    def track(self, track_id: str) -> dict:
        try:
            data = self._yt().get_song(track_id) or {}
        except Exception:
            logger.exception("ytmusic get_song failed")
            return {}
        vd = data.get("videoDetails") or {}
        if not vd.get("videoId"):
            return {}
        return self._map_song({"videoId": vd.get("videoId"), "title": vd.get("title"),
                               "artists": [{"name": vd.get("author"), "id": vd.get("channelId")}],
                               "duration_seconds": int(vd.get("lengthSeconds") or 0)}) or {}

    def fetch_track(self, track_id: str) -> dict:
        return self.track(track_id)

    def _remote(self, what: str, uri: str, fn):
        """Call ytmusicapi; on failure mark ``uri`` unavailable (never delete)
        and raise a ProviderError the UI can show. Success clears the mark."""
        try:
            out = fn()
        except ProviderError:
            raise
        except Exception as exc:
            if not _is_network_error(exc):      # no network says nothing about the item
                self.library.mark_unavailable(uri, type(exc).__name__)
            raise ProviderError(f"Could not load the YouTube Music {what} "
                                f"(unavailable or no network): {type(exc).__name__}") from exc
        self.library.clear_unavailable(uri)
        return out

    def album_tracks(self, album_id: str, limit: int = 50, offset: int = 0) -> dict:
        data = self._remote("album", f"ytmusic:album:{album_id}",
                            lambda: self._yt().get_album(album_id) or {})
        alb = {"name": data.get("title"), "id": album_id}
        songs = [x for x in (self._map_song(t, alb) for t in data.get("tracks") or []) if x]
        return make_page(songs, offset=offset, limit=limit)

    def artist_albums(self, artist_id: str, album_type: Optional[str] = None,
                      limit: int = 50, offset: int = 0) -> dict:
        data = self._remote("artist", f"ytmusic:artist:{artist_id}",
                            lambda: self._yt().get_artist(artist_id) or {})
        me = {"name": clean_artist_name(data.get("name") or ""), "id": artist_id,
              "uri": f"ytmusic:artist:{artist_id}"} if data.get("name") else None
        key = "singles" if album_type and "single" in str(album_type) else "albums"
        res = (data.get(key) or {}).get("results") or []
        return make_page([self._map_album(a, artist=me) for a in res], offset=offset, limit=limit)

    def _artist_data(self, artist_id: str) -> dict:
        return self._remote("artist", f"ytmusic:artist:{artist_id}",
                            lambda: self._yt().get_artist(artist_id) or {})

    def _artist_releases(self, data: dict, key: str, me: Optional[dict]) -> List[dict]:
        """Albums or singles of an artist page. get_artist() only carries the
        first ~10; when the section has a browseId + params, get_artist_albums
        gives the full list (falls back to the short one on any error)."""
        sec = data.get(key) or {}
        res = sec.get("results") or []
        if sec.get("browseId") and sec.get("params"):
            try:
                full = self._yt().get_artist_albums(sec["browseId"], sec["params"], limit=None) or []
                if full:
                    res = full
            except Exception as exc:
                logger.debug("ytmusic get_artist_albums failed: %s", type(exc).__name__)
        return [self._map_album(a, artist=me) for a in res if isinstance(a, dict)]

    def _map_related(self, a: dict) -> Optional[dict]:
        aid = a.get("browseId") or a.get("channelId") or a.get("id")
        if not aid:
            return None
        out = self._map_artist(a)
        if a.get("subscribers"):
            out["_info"] = f"{a['subscribers']} subscribers"
        return out

    def artist_top_tracks(self, artist_id, artist_name=None, limit=10):
        data = self._artist_data(artist_id)
        songs = (data.get("songs") or {}).get("results") or []
        return [x for x in (self._map_song(s) for s in songs if isinstance(s, dict)) if x][:int(limit or 10)]

    def related_artists(self, artist_id, artist_name=None, limit=20):
        data = self._artist_data(artist_id)
        rel = (data.get("related") or {}).get("results") or []
        return [x for x in (self._map_related(a) for a in rel if isinstance(a, dict)) if x][:int(limit or 20)]

    def artist_overview(self, artist_id, artist_name=None) -> dict:
        """One get_artist() call for the whole page (the generic version
        would make one per section)."""
        out = {"top_tracks": [], "albums": [], "singles": [], "related": [], "notes": []}
        try:
            data = self._artist_data(artist_id)
        except ProviderError as exc:
            out["notes"].append(str(exc))
            return out
        me = {"name": clean_artist_name(data.get("name") or artist_name or ""), "id": artist_id,
              "uri": f"ytmusic:artist:{artist_id}"} if (data.get("name") or artist_name) else None
        songs = (data.get("songs") or {}).get("results") or []
        out["top_tracks"] = [x for x in (self._map_song(s) for s in songs if isinstance(s, dict)) if x]
        out["albums"] = self._artist_releases(data, "albums", me)
        out["singles"] = self._artist_releases(data, "singles", me)
        rel = (data.get("related") or {}).get("results") or []
        out["related"] = [x for x in (self._map_related(a) for a in rel if isinstance(a, dict)) if x]
        if not out["top_tracks"]:
            out["notes"].append("YouTube Music lists no top songs for this artist.")
        if not out["related"]:
            out["notes"].append("YouTube Music lists no related artists for this artist.")
        return out

    # ------------------------------------------------------------------ #
    # Radio (get_watch_playlist: YouTube Music's own "Start radio")
    # ------------------------------------------------------------------ #
    def _watch_tracks(self, **kw) -> List[dict]:
        try:
            data = self._yt().get_watch_playlist(**kw) or {}
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"YouTube Music radio failed: {type(exc).__name__}") from exc
        out = []
        for t in data.get("tracks") or []:
            if not isinstance(t, dict):
                continue
            t = dict(t)
            # Watch-playlist entries carry the duration as "length" ("3:05").
            if t.get("duration") is None and t.get("length"):
                t["duration"] = t["length"]
            m = self._map_song(t)
            if m:
                out.append(m)
        return out

    def radio(self, seed_kind, seed_id, *, seed=None, limit=50) -> dict:
        limit = int(limit or 50)
        notes: List[str] = []
        if seed_kind == "track":
            tracks = self._watch_tracks(videoId=seed_id, radio=True, limit=limit)
        elif seed_kind == "playlist":
            pid = str(seed_id)
            if pid.startswith("VL"):
                pid = pid[2:]
            local = self.local_playlist_entries(pid)
            if local is not None:
                # A local playlist has no YouTube id: radio from its first song.
                first = next((e.get("item") or e.get("track") for e in local
                              if (e.get("item") or e.get("track") or {}).get("id")), None)
                if not first:
                    raise ProviderUnsupported("This playlist is empty: nothing to start a radio from.")
                notes.append("Local playlist: radio based on its first song.")
                tracks = self._watch_tracks(videoId=first["id"], radio=True, limit=limit)
            else:
                tracks = self._watch_tracks(playlistId=pid, radio=True, limit=limit)
        elif seed_kind == "artist":
            data = self._artist_data(seed_id)
            rid = data.get("radioId")
            if rid:
                tracks = self._watch_tracks(playlistId=rid, limit=limit)
            else:
                songs = (data.get("songs") or {}).get("results") or []
                vid = next((x.get("videoId") for x in songs if isinstance(x, dict) and x.get("videoId")), None)
                if not vid:
                    raise ProviderUnsupported("YouTube Music has no radio for this artist.")
                notes.append("No artist radio on YouTube Music: based on their top song.")
                tracks = self._watch_tracks(videoId=vid, radio=True, limit=limit)
        else:
            raise ProviderUnsupported("Radio starts from a track, an artist or a playlist.")
        return {"tracks": tracks[:limit], "notes": notes}

    def _get_playlist(self, playlist_id: str) -> dict:
        return self._remote("playlist", f"ytmusic:playlist:{playlist_id}",
                            lambda: self._yt().get_playlist(playlist_id, limit=None) or {})

    def playlist(self, playlist_id: str) -> dict:
        pid = parse_playlist_id(self._pid(playlist_id))
        if not pid:
            raise ProviderError("Not a YouTube Music playlist id or URL.")
        if self.library.is_local_playlist(pid):
            pl = self.library.playlist(pid)
            if pl is None:
                raise ProviderError("That local playlist no longer exists.")
            return self._local_playlist_dict(pl)
        data = self._get_playlist(pid)
        return self._map_playlist({**data, "playlistId": data.get("id") or pid})

    # ------------------------------------------------------------------ #
    # Local library hooks
    # ------------------------------------------------------------------ #
    def _resolve_meta(self, kind: str, item_id: str) -> Optional[dict]:
        if kind == "track":
            return self._lookup(f"ytmusic:track:{item_id}")
        if kind == "album":
            if item_id in self._albums_seen:
                return self._albums_seen[item_id]
            data = self._yt().get_album(item_id) or {}
            return {"id": item_id, "uri": f"ytmusic:album:{item_id}", "type": "album",
                    "name": data.get("title") or item_id, "artists": self._artists(data.get("artists")),
                    "release_date": str(data.get("year") or "")}
        if kind == "artist":
            if item_id in self._artists_seen:
                return self._artists_seen[item_id]
            data = self._yt().get_artist(item_id) or {}
            return {"id": item_id, "uri": f"ytmusic:artist:{item_id}", "type": "artist",
                    "name": clean_artist_name(data.get("name") or item_id)}
        return None

    @staticmethod
    def _saved_entries() -> List[dict]:
        cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
        out = []
        for e in cfg.get("ytmusic_playlists") or []:
            if isinstance(e, str) and e:
                out.append({"id": e, "name": e})
            elif isinstance(e, dict) and e.get("id"):
                out.append({"id": e["id"], "name": e.get("name") or e["id"]})
        return out

    def _remote_playlists(self) -> List[dict]:
        return [{"id": e["id"], "uri": f"ytmusic:playlist:{e['id']}", "type": "playlist",
                 "name": e["name"], "owner": {"display_name": ""},
                 "tracks": {"total": 0}, "items": {"total": 0}}
                for e in self._saved_entries()]

    def _remote_playlist_ids(self) -> List[str]:
        return [e["id"] for e in self._saved_entries()]

    def _store_entries(self, entries: List[dict]) -> None:
        if not isinstance(config.LOCAL_CFG, dict):
            config.LOCAL_CFG = {}
        config.LOCAL_CFG["ytmusic_playlists"] = entries
        config.save_local_config(config.LOCAL_CFG)

    def _follow_remote(self, playlist_id: str) -> bool:
        pid = parse_playlist_id(playlist_id)
        if not pid or self.library.is_local_playlist(pid):
            return False
        try:
            name = self.playlist(pid).get("name") or pid
        except ProviderError:
            logger.exception("ytmusic: could not read playlist %s", pid)
            return False
        entries = [e for e in self._saved_entries() if e["id"] != pid]
        entries.append({"id": pid, "name": name})
        self._store_entries(entries)
        return True

    def follow_playlist(self, playlist_id: str) -> bool:
        return self._follow_remote(playlist_id)

    def _unfollow_remote(self, playlist_id: str) -> bool:
        """Forget a followed playlist (only the local reference goes)."""
        pid = parse_playlist_id(playlist_id)
        entries = self._saved_entries()
        kept = [e for e in entries if e["id"] != pid]
        if len(kept) == len(entries):
            return False
        self._store_entries(kept)
        return True

    def _playlist_songs(self, playlist_id: str) -> List[dict]:
        pid = self._pid(playlist_id)
        local = self.local_playlist_entries(pid)
        if local is not None:
            return [e["track"] for e in local]
        data = self._get_playlist(pid)
        return [x for x in (self._map_song(t) for t in data.get("tracks") or []) if x]

    def playlist_items(self, playlist_id, limit=100, offset=0, fields=None) -> dict:
        local = self.local_playlist_entries(playlist_id)
        if local is not None:
            return make_page(local, offset=offset, limit=limit)
        entries = [{"added_at": "", "item": t, "track": t} for t in self._playlist_songs(playlist_id)]
        return make_page(entries, offset=offset, limit=limit)

    # ------------------------------------------------------------------ #
    # Playback (mpv + yt-dlp)
    # ------------------------------------------------------------------ #
    def stream_url(self, track: dict) -> str:
        return WATCH_URL.format(track.get("id"))

    def _download_file(self, track: dict, dest_base: str) -> str:
        """Best audio of the video through yt-dlp (no re-encode: usually Opus
        in .webm, ~137 kbps, the most YouTube gives without an account)."""
        url = WATCH_URL.format(track.get("id"))
        if self._downloader is not None:
            return self._downloader(url, dest_base)
        import glob
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                "format": "bestaudio/best", "outtmpl": dest_base + ".%(ext)s",
                "retries": 2, "socket_timeout": 20, "cachedir": False, "noprogress": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True) or {}
        extra = {"duration_ms": int(round(float(info.get("duration") or 0) * 1000))}
        for d in info.get("requested_downloads") or []:
            fp = d.get("filepath")
            if fp and os.path.isfile(fp):
                return fp, extra
        found = [f for f in glob.glob(glob.escape(dest_base) + ".*") if not f.endswith(".part")]
        if not found:
            raise ProviderError("yt-dlp finished without writing a file.")
        return found[0], extra

    def context_tracks(self, context_uri: str) -> List[dict]:
        parts = str(context_uri or "").split(":", 2)
        if len(parts) < 3:
            return []
        kind, cid = parts[1], parts[2]
        if kind == "playlist":
            return self._playlist_songs(cid)
        if kind == "album":
            return self.album_tracks(cid, limit=None).get("items") or []
        return []
