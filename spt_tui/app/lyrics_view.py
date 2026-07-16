"""Synced lyrics fetching, parsing and rendering."""

from __future__ import annotations

import re
import threading
from typing import List, Tuple

from textual.widgets import Static
from rich.markup import escape as rich_escape

try:
    import requests
except Exception:
    requests = None
try:
    import pyfiglet
except Exception:
    pyfiglet = None

from ..config import logger
from ..constants import WELCOME

class LyricsMixin:
    def action_toggle_lyrics(self):
        if self._lyrics_on:
            self._lyrics_on = False
            self._lyrics_loading = False
            self._lyrics_gen = getattr(self, "_lyrics_gen", 0) + 1  # invalidate in-flight loads
            if self._lyrics_interval:
                try:
                    self._lyrics_interval.pause()
                except Exception:
                    pass
                self._lyrics_interval = None
            try:
                self.right_panel.remove_class("lyrics-mode")
            except Exception:
                pass
            try:
                self._lyrics_track_id = None
                self.lyrics_box = None
            except Exception:
                pass
            try:
                self.action_escape_to_menu()
            except Exception:
                try:
                    self.right_panel.update(WELCOME)
                    self.level = self.LVL_SECTION_CONTENT
                except Exception:
                    pass
            return
        self._open_lyrics_view()

    def _open_lyrics_view(self):
        self._lyrics_on = True

        # Show the panel and a loading placeholder immediately so the UI never
        # freezes; the network fetch happens in a background thread below.
        right = self._clear_right()
        right.add_class("lyrics-mode")
        self.lyrics_box = Static("", id="lyrics_box")
        right.mount(self.lyrics_box)

        # Best-effort title from the cached now-playing track (no network call).
        item = getattr(self, "_bar_last_track", None) or {}
        name = item.get("name") or ""
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a)
        self._lyrics_title = f"{name} — {artists}" if name else ""
        self._lyrics_lines = []
        self.lyrics_box.update(self._render_header(big=True) + "\n[b]Lyrics loading…[/b]")

        try:
            self._lyrics_interval = self.set_interval(0.4, self._update_lyrics_highlight, pause=False)
        except Exception:
            logger.exception("Could not initialize lyrics range")

        self._start_lyrics_load()

    def _start_lyrics_load(self):
        """Fetch lyrics for the current track off the UI thread."""
        self._lyrics_gen = getattr(self, "_lyrics_gen", 0) + 1
        gen = self._lyrics_gen
        self._lyrics_loading = True

        def _worker():
            try:
                pb = self.spotify.get_playback() or {}
                item = pb.get("item") or getattr(self, "_bar_last_track", None) or {}
                name = item.get("name") or "(no title)"
                all_artists = [a.get("name", "") for a in (item.get("artists") or []) if a and a.get("name")]
                artists = ", ".join(all_artists)
                primary_artist = all_artists[0] if all_artists else artists
                album = (item.get("album") or {}).get("name", "") if isinstance(item.get("album"), dict) else ""
                duration_ms = int(item.get("duration_ms") or 0)
                track_id = item.get("id") or None
                title = f"{name} — {artists}" if artists else name
                lines = self._fetch_synced_lyrics(
                    title=name, artist=primary_artist, album=album, duration_ms=duration_ms,
                )
            except Exception:
                logger.exception("Lyrics background load failed")
                title, track_id, lines = self._lyrics_title, self._lyrics_track_id, []
            try:
                self.call_from_thread(self._apply_lyrics_result, gen, track_id, title, lines)
            except Exception:
                if getattr(self, '_closing', False):
                    logger.debug("lyrics result dropped during teardown")
                else:
                    logger.exception("lyrics result: call_from_thread failed")

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_lyrics_result(self, gen, track_id, title, lines):
        # Drop results from a stale request or after the view was closed.
        if not self._lyrics_on or gen != getattr(self, "_lyrics_gen", 0):
            return
        self._lyrics_loading = False
        self._lyrics_track_id = track_id
        self._lyrics_title = title or self._lyrics_title
        self._lyrics_lines = lines or []
        if self.lyrics_box is None:
            return
        header = self._render_header(big=True)
        if not self._lyrics_lines:
            self.lyrics_box.update(header + "\n[b]Lyrics not found[/b]")
        else:
            self.lyrics_box.update(header + "\n" + self._render_lyrics_at(self._cached_pos_ms()))

    # lrclib asks clients to identify themselves; a missing UA can get throttled.
    _LYRICS_UA = "spt-tui (terminal Spotify client)"

    def _clean_track_title(self, title: str) -> str:
        """Drop 'feat.'/‘with’ credits and remaster/live suffixes that otherwise
        make lrclib miss a track whose lyrics do exist."""
        t = title or ""
        t = re.sub(r"\s*[\(\[]\s*(feat|ft|featuring|with)\.?\s[^)\]]*[\)\]]", "", t, flags=re.IGNORECASE)
        t = re.sub(
            r"\s*-\s*(\d{0,4}\s*)?(re-?master(ed)?|remaster|live|mono|stereo|"
            r"radio edit|single version|album version|deluxe|bonus track)\b.*$",
            "", t, flags=re.IGNORECASE,
        )
        return t.strip() or (title or "").strip()

    def _pick_best_lyrics(self, arr, dur_s: int):
        """From an lrclib /search array, prefer a result that has synced lyrics
        and whose duration is closest to ours — avoids grabbing a wrong-length
        (e.g. remix/live) version that happens to rank first."""
        if not isinstance(arr, list):
            return None
        cand = [it for it in arr if it and (it.get("syncedLyrics") or it.get("plainLyrics"))]
        if not cand:
            return None

        def score(it):
            has_sync = 0 if (it.get("syncedLyrics") or "").strip() else 1
            try:
                dd = abs(int(it.get("duration") or 0) - dur_s) if dur_s else 0
            except (TypeError, ValueError):
                dd = 0
            return (has_sync, dd)

        cand.sort(key=score)
        return cand[0]

    def _fetch_synced_lyrics(
        self,
        *,
        title: str,
        artist: str,
        duration_ms: int = 0,
        album: str | None = None,
        **_ignored,
    ) -> list[tuple[int, str]]:
        try:
            if requests is None:
                # This runs on a background thread — never touch widgets directly.
                try:
                    self.call_from_thread(
                        lambda: self.right_panel.update("[i]Lyrics: install 'requests' to enable them (pip install requests)[/i]")
                    )
                except Exception:
                    pass
                return []

            base = "https://lrclib.net/api"
            headers = {"User-Agent": self._LYRICS_UA}
            timeout = 8
            dur_s = max(0, int(round((duration_ms or 0) / 1000)))
            clean = self._clean_track_title(title)

            def _from(data):
                if not isinstance(data, dict):
                    return None
                synced = (data.get("syncedLyrics") or "").strip()
                if synced:
                    return self._parse_lrc(synced)
                plain = (data.get("plainLyrics") or "").strip()
                if plain:
                    return self._plain_to_pseudo_lrc(plain, duration_ms)
                return None

            # 1) Exact-match endpoint (lrclib's best): needs track + artist;
            #    album + duration sharpen it and return duration-matched synced
            #    lyrics. (The old code queried /get?isrc=… which lrclib rejects
            #    with 400, so lyrics only ever came from the fragile search.)
            if clean and artist:
                try:
                    params = {"track_name": clean, "artist_name": artist}
                    if album:
                        params["album_name"] = album
                    if dur_s:
                        params["duration"] = dur_s
                    r = requests.get(f"{base}/get", params=params, timeout=timeout, headers=headers)
                    if r.status_code == 200:
                        res = _from(r.json() or {})
                        if res:
                            return res
                except Exception:
                    logger.debug("lyrics: /get exact failed", exc_info=True)

            # 2) Search fallbacks: track+artist, then free-text q. Pick the best
            #    candidate client-side (synced first, closest duration).
            queries = []
            if clean and artist:
                queries.append({"track_name": clean, "artist_name": artist})
            if clean:
                queries.append({"q": f"{clean} {artist}".strip()})
            for q in queries:
                try:
                    r = requests.get(f"{base}/search", params=q, timeout=timeout, headers=headers)
                    if r.status_code == 200:
                        best = self._pick_best_lyrics(r.json() or [], dur_s)
                        res = _from(best) if best else None
                        if res:
                            return res
                except Exception:
                    logger.debug("lyrics: /search failed", exc_info=True)

            return []
        except Exception:
            logger.exception("Lyrics: general failure in _fetch_synced_lyrics")
            return []

    def _plain_to_pseudo_lrc(self, plain: str, duration_ms: int) -> list[tuple[int, str]]:
        lines = [ln.strip() for ln in (plain or "").splitlines() if ln.strip()]
        if not lines:
            return []

        total = max(1, int(round((duration_ms or 0) / 1000)))
        gap = max(2, total // max(1, len(lines)))
        out = []
        t = 0
        for ln in lines:
            out.append((t * 1000, ln))
            t += gap
        return out

    def _parse_lrc(self, lrc_text: str) -> List[Tuple[int, str]]:
        if not lrc_text:
            return []
        lines: List[Tuple[int, str]] = []
        for raw in lrc_text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            parts = raw.split(']')
            stamps = []
            text = ""
            for p in parts:
                if p.startswith('['):
                    t = p[1:]
                    msec = None
                    try:
                        if t.count(':') == 2:
                            h, m, s = t.split(':')
                            sec = float(s)
                            msec = (int(h)*3600 + int(m)*60 + sec) * 1000
                        else:
                            m, s = t.split(':')
                            sec = float(s)
                            msec = (int(m)*60 + sec) * 1000
                        msec = int(msec)
                    except Exception:
                        msec = None
                    if msec is not None:
                        stamps.append(msec)
                else:
                    text = p.strip()
            if stamps:
                for ts in stamps:
                    lines.append((ts, text))
            else:
                lines.append((0, raw))
        lines.sort(key=lambda x: x[0])
        return lines

    def _render_lyrics_at(self, pos_ms: int, window: int = 5) -> str:
        if not self._lyrics_lines:
            return "[i]No Lyrics Found[/i]"
        idx = 0
        for i, (ts, _) in enumerate(self._lyrics_lines):
            if ts <= pos_ms:
                idx = i
            else:
                break
        start = max(0, idx - window)
        end = min(len(self._lyrics_lines), idx + window + 1)
        out: List[str] = []
        for i in range(start, idx):
            _, txt = self._lyrics_lines[i]
            safe = rich_escape(txt or "")
            out.append(f"[dim]{safe}[/dim]")
        _, cur_txt = self._lyrics_lines[idx]
        cur_safe = rich_escape(cur_txt or "")
        out.append("")
        out.append(f"[b][#e6ff71]{cur_safe}[/#e6ff71][/b]")
        out.append("")
        for i in range(idx + 1, end):
            _, txt = self._lyrics_lines[i]
            safe = rich_escape(txt or "")
            out.append(f"[dim]{safe}[/dim]")
        return "\n".join(out)

    def _update_lyrics_highlight(self):
        if not self._lyrics_on:
            return
        try:
            # Read the cached now-playing state (refreshed by the now-bar sync)
            # instead of hitting the network on every 0.4s tick.
            cur_id = getattr(self, "_now_internal_track_id", None)
            if cur_id and cur_id != self._lyrics_track_id and not getattr(self, "_lyrics_loading", False):
                self._lyrics_lines = []
                self._start_lyrics_load()
                return
            if self.lyrics_box is None:
                return
            header = self._render_header(big=True)
            if getattr(self, "_lyrics_loading", False) and not self._lyrics_lines:
                self.lyrics_box.update(header + "\n[b]Lyrics loading…[/b]")
                return
            block = self._render_lyrics_at(self._cached_pos_ms())
            self.lyrics_box.update(header + "\n" + block)
        except Exception:
            logger.exception("Error updating lyrics")
