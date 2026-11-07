"""Provider mixin: likes, saved albums/artists and playlists in the local library.

For providers without an account or without remote playlist editing (YouTube
Music today; any future one the same way). Spotify never uses this.

The provider class supplies:

* ``self.library``: a :class:`spt_tui.local_library.LocalLibrary`;
* ``_resolve_meta(kind, item_id)``: metadata for an id about to be saved
  (from its cache or the network) — may return None when offline, in which
  case a minimal stub is saved and filled in later;
* optionally ``_remote_playlists()`` / ``_follow_remote(pid)`` /
  ``_unfollow_remote(pid)`` / ``_remote_playlist_ids()`` for followed
  ("imported") public playlists, which are listed together with the local ones.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..config import logger
from ..local_library import LocalLibrary, minimal_track
from .base import ProviderError, make_page

_KIND = {"album": "albums", "artist": "artists", "track": "tracks"}


class LocalLibraryMixin:
    library: LocalLibrary

    # -- hooks ----------------------------------------------------------- #
    def _resolve_meta(self, kind: str, item_id: str) -> Optional[dict]:
        return None

    def _remote_playlists(self) -> List[dict]:
        return []

    def _remote_playlist_ids(self) -> List[str]:
        return [p.get("id") for p in self._remote_playlists()]

    def _follow_remote(self, pid: str) -> bool:
        return False

    def _unfollow_remote(self, pid: str) -> bool:
        return False

    # -- helpers --------------------------------------------------------- #
    def _uri_for(self, kind: str, item_id: str) -> str:
        return f"{self.name}:{kind}:{item_id}"

    def _metas(self, kind: str, ids: List[str]) -> List[dict]:
        out = []
        for i in ids or []:
            i = self._normalize_track_id(i)
            if not i:
                continue
            meta = None
            try:
                meta = self._resolve_meta(kind, i)
            except Exception:
                logger.exception("%s: resolving %s %s failed", self.name, kind, i)
            # Offline / not found: keep a stub so the like is not lost.
            out.append(meta or {"id": i, "uri": self._uri_for(kind, i), "name": i})
        return out

    def _flag(self, item: dict) -> dict:
        """Copy of a stored item, marked if it is known to be unavailable."""
        it = dict(item)
        reason = self.library.unavailable_recent(it.get("uri") or "")   # a stale mark is not shown
        if reason:
            it["_unavailable"] = reason
            it["name"] = f"{it.get('name') or it.get('id')} (unavailable)"
        elif it.get("import_status") == "doubtful":
            it["name"] = f"{it.get('name') or it.get('id')} (? check match)"
        return it

    # -- liked songs ----------------------------------------------------- #
    def check_saved_tracks(self, ids: List[str], on_batch=None) -> List[bool]:
        out = self.library.contains("tracks", [self._normalize_track_id(i) for i in ids or []])
        if on_batch is not None and out:
            try:
                on_batch(0, list(out))
            except Exception:
                logger.exception("check_saved_tracks: on_batch callback failed")
        return out

    def save_tracks(self, ids: List[str]) -> bool:
        metas = self._metas("track", ids)
        if not metas:
            return False
        self.library.add("tracks", metas)
        return True

    def remove_tracks(self, ids: List[str]) -> bool:
        ids = [self._normalize_track_id(i) for i in ids or [] if i]
        if not ids:
            return False
        self.library.remove("tracks", ids)
        return True

    def saved_tracks(self, limit=50, offset=0) -> dict:
        entries = [{"added_at": t.get("added_at") or "", "track": self._flag(t)}
                   for t in self.library.items("tracks")]
        return make_page(entries, offset=offset, limit=limit)

    # -- saved albums / followed artists / followed playlists ------------ #
    def saved_contains(self, kind: str, ids: List[str]) -> List[bool]:
        ids = [self._normalize_track_id(i) for i in ids or []]
        if kind == "playlist":
            have = set(self._remote_playlist_ids()) | {p["id"] for p in self.library.playlists()}
            return [i in have for i in ids]
        if kind in _KIND:
            return self.library.contains(_KIND[kind], ids)
        return [False] * len(ids)

    def save_items(self, kind: str, ids: List[str]) -> bool:
        if kind == "playlist":
            return all(self._follow_remote(i) for i in ids or []) and bool(ids)
        if kind not in _KIND:
            return False
        metas = self._metas(kind, ids)
        if not metas:
            return False
        self.library.add(_KIND[kind], metas)
        return True

    def remove_items(self, kind: str, ids: List[str]) -> bool:
        if kind == "playlist":
            ok = False
            for i in ids or []:
                ok = (self.library.delete_playlist(i) if self.library.is_local_playlist(i)
                      else self._unfollow_remote(i)) or ok
            return ok
        if kind not in _KIND:
            return False
        self.library.remove(_KIND[kind], [self._normalize_track_id(i) for i in ids or []])
        return True

    def saved_albums(self, limit=50, offset=0) -> dict:
        entries = [{"added_at": a.get("added_at") or "", "album": self._flag(a)}
                   for a in self.library.items("albums")]
        return make_page(entries, offset=offset, limit=limit)

    def followed_artists(self, limit: int = 50) -> dict:
        return make_page([self._flag(a) for a in self.library.items("artists")],
                         offset=0, limit=limit)

    # -- playlists: followed public ones + local ones -------------------- #
    def _local_playlist_dict(self, p: dict) -> dict:
        n = len(p.get("tracks") or [])
        return {"id": p["id"], "uri": f"{self.name}:playlist:{p['id']}", "type": "playlist",
                "name": p.get("name") or "(no name)", "description": p.get("description") or "",
                "owner": {"id": "local", "display_name": "local library"},
                "tracks": {"total": n}, "items": {"total": n}, "images": [], "_local": True}

    def user_playlists(self, limit=50, offset=0) -> dict:
        items = [self._flag(p) for p in self._remote_playlists()]
        items += [self._local_playlist_dict(p) for p in self.library.playlists()]
        return make_page(items, offset=offset, limit=limit)

    def _pid(self, playlist_id: str) -> str:
        s = str(playlist_id or "")
        prefix = f"{self.name}:playlist:"
        return s[len(prefix):] if s.startswith(prefix) else s

    def local_playlist_entries(self, pid: str) -> Optional[List[dict]]:
        pl = self.library.playlist(self._pid(pid))
        if pl is None:
            return None
        return [{"added_at": t.get("added_at") or "", "item": self._flag(t), "track": self._flag(t)}
                for t in pl.get("tracks") or []]

    def create_playlist(self, name: str, public: bool = True, collaborative: bool = False,
                        description: str = ""):
        self._last_create_error = None
        try:
            return self._local_playlist_dict(self.library.create_playlist(name, description))
        except Exception as exc:
            self._last_create_error = str(exc)
            logger.exception("%s: creating a local playlist failed", self.name)
            return None

    def can_rename_playlist(self, playlist_id: str) -> bool:
        return self.library.is_local_playlist(self._pid(playlist_id))

    def rename_playlist(self, playlist_id: str, name: str) -> bool:
        pid = self._pid(playlist_id)
        if not self.library.is_local_playlist(pid):
            raise ProviderError("Only local playlists can be renamed.")
        return self.library.rename_playlist(pid, (name or "").strip())

    def add_items_to_playlist(self, playlist_id: str, uris: List[str]) -> bool:
        pid = self._pid(playlist_id)
        if not self.library.is_local_playlist(pid):
            # Followed public playlists belong to someone else.
            return False
        tracks = []
        for u in uris or []:
            t = self._lookup(u) if hasattr(self, "_lookup") else None
            tid = self._normalize_track_id(u)
            tracks.append(t or {"id": tid, "uri": self._uri_for("track", tid), "name": tid})
        return self.library.add_tracks(pid, tracks) > 0

    def remove_playlist_positions(self, playlist_id: str, positions: List[int]) -> bool:
        pid = self._pid(playlist_id)
        if not self.library.is_local_playlist(pid):
            return False
        return self.library.remove_positions(pid, positions) > 0

    def remove_playlist_uris(self, playlist_id: str, uris: List[str]) -> bool:
        pid = self._pid(playlist_id)
        pl = self.library.playlist(pid) if self.library.is_local_playlist(pid) else None
        if pl is None:
            return False
        want = {self._normalize_track_id(u) for u in uris or []}
        pos = [i for i, t in enumerate(pl.get("tracks") or []) if t.get("id") in want]
        return bool(pos) and self.library.remove_positions(pid, pos) > 0

    def delete_playlist(self, playlist_id: str) -> bool:
        pid = self._pid(playlist_id)
        if self.library.is_local_playlist(pid):
            return self.library.delete_playlist(pid)
        return self._unfollow_remote(pid)
