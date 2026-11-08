"""Local library: likes, saved albums/artists, playlists and history stored on
disk for providers that have no account (YouTube Music) or lack a feature.

One JSON file per provider, next to ``spt_config.json``
(``local_library_<provider>.json``), so YouTube Music and Subsonic data never
mix with each other or with Spotify. Every entry keeps a **minimal copy of its
metadata** (id, uri, name, artists, album, duration) so the library can be
shown without the network.

Rules (they matter when things disappear upstream):

* nothing is ever deleted because of a network error or "not available":
  the entry is only *marked* unavailable (``mark_unavailable``) and shown as
  such; a later successful load clears the mark;
* only an explicit user action (unlike, remove from playlist, delete) removes
  an entry, and it is removed from this local store only;
* writes are atomic (unique temp file in the same directory + ``os.replace``)
  and serialised by a lock, so concurrent saves cannot corrupt the file;
* a corrupt file is kept aside as ``*.corrupt-<time>`` instead of being lost.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

from . import config
from .config import logger

LOCAL_PLAYLIST_PREFIX = "local:"
RECENT_MAX = 50
UNAVAILABLE_TTL = 24 * 3600   # a "unavailable" mark only hides a track for a day
KINDS = ("tracks", "albums", "artists")


def atomic_write_json(path: str, data, *, indent: Optional[int] = None) -> None:
    """Write ``data`` to ``path`` atomically with a unique temp file, so two
    concurrent writers can never rename each other's half-written file."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Minimal metadata copies
# --------------------------------------------------------------------------- #
def _artists_min(raw) -> List[dict]:
    out = []
    for a in raw or []:
        if isinstance(a, dict) and a.get("name"):
            out.append({"name": a.get("name"), "id": a.get("id"), "uri": a.get("uri")})
    return out


def minimal_track(t: dict) -> dict:
    alb = t.get("album") if isinstance(t.get("album"), dict) else {"name": t.get("album") or ""}
    out = {"id": t.get("id"), "uri": t.get("uri"), "type": "track",
           "name": t.get("name") or "(no title)", "artists": _artists_min(t.get("artists")),
           "album": {"name": (alb or {}).get("name") or "", "id": (alb or {}).get("id")},
           "duration_ms": int(t.get("duration_ms") or 0)}
    if t.get("import_status"):
        # Set by the Spotify import for doubtful matches, so they can be found.
        out["import_status"] = t["import_status"]
    return out


def minimal_album(a: dict) -> dict:
    return {"id": a.get("id"), "uri": a.get("uri"), "type": "album",
            "name": a.get("name") or "(no title)", "artists": _artists_min(a.get("artists")),
            "release_date": a.get("release_date") or "", "total_tracks": a.get("total_tracks")}


def minimal_artist(a: dict) -> dict:
    return {"id": a.get("id"), "uri": a.get("uri"), "type": "artist",
            "name": a.get("name") or "(no name)"}


MINIMAL = {"tracks": minimal_track, "albums": minimal_album, "artists": minimal_artist}


class LocalLibrary:
    def __init__(self, provider: str, *, path: Optional[str] = None,
                 now: Callable[[], float] = time.time):
        self.provider = provider
        self._path = path
        self._now = now
        self._lock = threading.RLock()
        self._data: Optional[dict] = None

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    @property
    def path(self) -> str:
        # Resolved lazily: tests (and SPT_TUI_CACHE_DIR) move config.CACHE_DIR.
        return self._path or os.path.join(config.CACHE_DIR, f"local_library_{self.provider}.json")

    @staticmethod
    def _empty() -> dict:
        return {"version": 1, "tracks": [], "albums": [], "artists": [],
                "playlists": [], "recent": [], "unavailable": {}, "unavailable_at": {}}

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        data = self._empty()
        p = self.path
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    raise ValueError("not an object")
                for k, v in self._empty().items():
                    if isinstance(raw.get(k), type(v)):
                        data[k] = raw[k]
            except Exception:
                logger.exception("local library %s is unreadable; keeping it aside", p)
                try:
                    os.replace(p, f"{p}.corrupt-{int(self._now())}")
                except OSError:
                    logger.exception("could not move the corrupt local library aside")
        self._data = data
        return data

    def _save(self) -> None:
        try:
            atomic_write_json(self.path, self._load(), indent=1)
        except Exception:
            logger.exception("could not save the local library")

    # ------------------------------------------------------------------ #
    # Likes / saved albums / followed artists
    # ------------------------------------------------------------------ #
    def contains(self, kind: str, ids: List[str]) -> List[bool]:
        with self._lock:
            have = {e.get("id") for e in self._load()[kind]}
        return [i in have for i in ids or []]

    def add(self, kind: str, items: List[dict]) -> int:
        """Add (newest first); already-saved ids are left where they are."""
        n = 0
        with self._lock:
            lst = self._load()[kind]
            have = {e.get("id") for e in lst}
            for it in items or []:
                if not it or not it.get("id") or it["id"] in have:
                    continue
                entry = MINIMAL[kind](it)
                entry["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))
                lst.insert(0, entry)
                have.add(it["id"])
                n += 1
            if n:
                self._save()
        return n

    def remove(self, kind: str, ids: List[str]) -> int:
        ids = set(ids or [])
        with self._lock:
            lst = self._load()[kind]
            kept = [e for e in lst if e.get("id") not in ids]
            n = len(lst) - len(kept)
            if n:
                self._load()[kind] = kept
                self._save()
        return n

    def items(self, kind: str) -> List[dict]:
        with self._lock:
            return [dict(e) for e in self._load()[kind]]

    # ------------------------------------------------------------------ #
    # Local playlists
    # ------------------------------------------------------------------ #
    @staticmethod
    def is_local_playlist(pid: str) -> bool:
        return isinstance(pid, str) and pid.startswith(LOCAL_PLAYLIST_PREFIX)

    def playlists(self) -> List[dict]:
        with self._lock:
            return [dict(p, tracks=list(p.get("tracks") or [])) for p in self._load()["playlists"]]

    def playlist(self, pid: str) -> Optional[dict]:
        return next((p for p in self.playlists() if p.get("id") == pid), None)

    def create_playlist(self, name: str, description: str = "") -> dict:
        pl = {"id": f"{LOCAL_PLAYLIST_PREFIX}{uuid.uuid4()}", "name": name or "New playlist",
              "description": description or "", "tracks": [],
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))}
        with self._lock:
            self._load()["playlists"].append(pl)
            self._save()
        return dict(pl)

    def _find(self, pid: str) -> Optional[dict]:
        return next((p for p in self._load()["playlists"] if p.get("id") == pid), None)

    def rename_playlist(self, pid: str, name: str) -> bool:
        if not name:
            return False
        with self._lock:
            pl = self._find(pid)
            if pl is None:
                return False
            pl["name"] = name
            self._save()
        return True

    def delete_playlist(self, pid: str) -> bool:
        with self._lock:
            lst = self._load()["playlists"]
            kept = [p for p in lst if p.get("id") != pid]
            if len(kept) == len(lst):
                return False
            self._load()["playlists"] = kept
            self._save()
        return True

    def add_tracks(self, pid: str, tracks: List[dict]) -> int:
        with self._lock:
            pl = self._find(pid)
            if pl is None:
                return 0
            added = [minimal_track(t) for t in tracks or [] if t and t.get("id")]
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))
            for t in added:
                t["added_at"] = stamp
            pl.setdefault("tracks", []).extend(added)
            if added:
                self._save()
        return len(added)

    def set_tracks(self, pid: str, tracks: List[dict]) -> bool:
        """Replace a local playlist's tracks (used when re-importing it)."""
        with self._lock:
            pl = self._find(pid)
            if pl is None:
                return False
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))
            pl["tracks"] = [dict(minimal_track(t), added_at=stamp) for t in tracks or [] if t and t.get("id")]
            self._save()
        return True

    def remove_positions(self, pid: str, positions: List[int]) -> int:
        drop = {int(p) for p in positions or []}
        with self._lock:
            pl = self._find(pid)
            if pl is None:
                return 0
            tracks = pl.get("tracks") or []
            kept = [t for i, t in enumerate(tracks) if i not in drop]
            n = len(tracks) - len(kept)
            if n:
                pl["tracks"] = kept
                self._save()
        return n

    def find_track(self, track_id: str) -> Optional[dict]:
        """Stored metadata for a track id (liked, in a playlist or in the
        history), so a saved track can be resolved without the network."""
        if not track_id:
            return None
        with self._lock:
            d = self._load()
            for t in d["tracks"]:
                if t.get("id") == track_id:
                    return dict(t)
            for p in d["playlists"]:
                for t in p.get("tracks") or []:
                    if t.get("id") == track_id:
                        return dict(t)
            for e in d["recent"]:
                t = e.get("track") or {}
                if t.get("id") == track_id:
                    return dict(t)
        return None

    # ------------------------------------------------------------------ #
    # Recently played (local history)
    # ------------------------------------------------------------------ #
    def record_played(self, track: dict) -> None:
        if not track or not track.get("id"):
            return
        with self._lock:
            rec = self._load()["recent"]
            if rec and (rec[0].get("track") or {}).get("id") == track["id"]:
                return
            rec.insert(0, {"track": minimal_track(track),
                           "played_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))})
            del rec[RECENT_MAX:]
            self._save()

    def recent(self, limit: int = RECENT_MAX) -> List[dict]:
        with self._lock:
            return [dict(e) for e in self._load()["recent"][:max(0, int(limit or 0))]]

    # ------------------------------------------------------------------ #
    # Availability marks (never deletion)
    # ------------------------------------------------------------------ #
    def mark_unavailable(self, uri: str, reason: str = "unavailable") -> None:
        if not uri:
            return
        with self._lock:
            data = self._load()
            data["unavailable"][uri] = reason
            data["unavailable_at"][uri] = self._now()
            self._save()

    def clear_unavailable(self, uri: str) -> None:
        with self._lock:
            data = self._load()
            if uri in data["unavailable"] or uri in data["unavailable_at"]:
                data["unavailable"].pop(uri, None)
                data["unavailable_at"].pop(uri, None)
                self._save()

    def unavailable_reason(self, uri: str) -> Optional[str]:
        with self._lock:
            return self._load()["unavailable"].get(uri)

    def unavailable_recent(self, uri: str, ttl: float = UNAVAILABLE_TTL) -> Optional[str]:
        """The mark's reason only while it is *fresh* (set within ``ttl`` seconds).
        Marks without a date (written by older versions) count as stale: a failure
        that may have been a hiccup must not hide a track forever."""
        with self._lock:
            data = self._load()
            reason = data["unavailable"].get(uri)
            at = data["unavailable_at"].get(uri)
            if not reason or not isinstance(at, (int, float)):
                return None
            return reason if (self._now() - at) < ttl else None
