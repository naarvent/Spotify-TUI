"""A public Spotify playlist, read anonymously by link, shaped like ``SpotifySource`` so the
existing ``SpotifyImporter`` (matching + local playlist) can use it unchanged.

No Spotify account, credentials or Premium needed: it uses ``spt_tui.scraper``. Only public
playlists. Songs only: episodes and local files are skipped (``skipped`` says how many)."""
from __future__ import annotations

from typing import Dict, List, Optional

from .spotify_import import SpotifySource


class ScraperSource:
    def __init__(self, client):
        self.client = client
        self._playlists: Dict[str, List[dict]] = {}
        self.info: Dict[str, dict] = {}

    def load(self, value: str, *, max_tracks: Optional[int] = None) -> dict:
        """Read a playlist (URL / URI / id) once. Returns ``{"id", "name", "total", "read",
        "skipped", "partial"}``; raises the scraper's errors (``NotFoundError`` for a private or
        missing playlist)."""
        pl = self.client.playlist(value, max_tracks=max_tracks)
        tracks = []
        for it in pl["tracks"]["items"]:
            t = SpotifySource._track(it.get("track") or {})
            if t:
                tracks.append(t)
        total = int(pl["tracks"].get("total") or 0)
        info = {"id": pl["id"], "name": pl.get("name") or pl["id"], "total": total,
                "read": len(tracks), "skipped": max(0, total - len(tracks)),
                "partial": bool(pl["tracks"].get("partial"))}
        self._playlists[pl["id"]] = tracks
        self.info[pl["id"]] = info
        return info

    # --- the part of SpotifySource the importer uses --------------------- #
    def playlist_tracks(self, pid: str) -> List[dict]:
        return list(self._playlists.get(pid, []))

    def playlists(self) -> List[dict]:
        return [{"id": i["id"], "name": i["name"], "total": i["total"]} for i in self.info.values()]

    def liked_tracks(self) -> List[dict]:
        return []

    def saved_albums(self) -> List[dict]:
        return []

    def followed_artists(self) -> List[dict]:
        return []

    def summary(self) -> dict:
        return {"liked": 0, "albums": 0, "artists": 0, "playlists": self.playlists()}
