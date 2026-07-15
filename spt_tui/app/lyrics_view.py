"""Synced lyrics fetching, parsing and rendering."""

from __future__ import annotations

import threading
from typing import List, Optional, Tuple

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
                artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a)
                isrc = self._get_isrc_for_item(item)
                duration_ms = int(item.get("duration_ms") or 0)
                track_id = item.get("id") or None
                title = f"{name} — {artists}"
                lines = self._fetch_synced_lyrics(
                    title=name, artist=artists, duration_ms=duration_ms,
                    track_id=track_id, isrc=isrc,
                )
            except Exception:
                logger.exception("Lyrics background load failed")
                title, track_id, lines = self._lyrics_title, self._lyrics_track_id, []
            try:
                self.call_from_thread(self._apply_lyrics_result, gen, track_id, title, lines)
            except Exception:
                self._apply_lyrics_result(gen, track_id, title, lines)

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

    def _get_isrc_for_item(self, item: dict) -> Optional[str]:
        isrc = (item.get("external_ids", {}) or {}).get("isrc")
        if isrc:
            return isrc
        try:
            spid = item.get("id")
            if spid:
                full = self.spotify.ensure().track(spid) or {}
                isrc2 = (full.get("external_ids", {}) or {}).get("isrc")
                if isrc2:
                    return isrc2
        except Exception:
            logger.exception("Could not obtain ISRC via sp.track()")
        return None

    def _fetch_synced_lyrics(
        self,
        *,
        title: str,
        artist: str,
        duration_ms: int,
        track_id: str | None = None,
        isrc: str | None = None,
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
            timeout = 6

            if isrc:
                try:
                    r = requests.get(f"{base}/get", params={"isrc": isrc}, timeout=timeout)
                    if r.status_code == 200:
                        data = r.json() or {}
                        synced = (data.get("syncedLyrics") or "").strip()
                        plain = (data.get("plainLyrics") or "").strip()
                        if synced:
                            return self._parse_lrc(synced)
                        if plain:
                            return self._plain_to_pseudo_lrc(plain, duration_ms)
                except Exception:
                    pass

            try:
                q = {
                    "track_name": title or "",
                    "artist_name": artist or "",

                    "duration": max(0, int(round((duration_ms or 0) / 1000))),
                }
                r = requests.get(f"{base}/search", params=q, timeout=timeout)
                if r.status_code == 200:
                    arr = r.json() or []

                    best = None
                    for it in arr:
                        if (it.get("syncedLyrics") or it.get("plainLyrics")):
                            best = it
                            break
                    if best:
                        synced = (best.get("syncedLyrics") or "").strip()
                        plain = (best.get("plainLyrics") or "").strip()
                        if synced:
                            return self._parse_lrc(synced)
                        if plain:
                            return self._plain_to_pseudo_lrc(plain, best.get("duration") and int(best["duration"]*1000) or duration_ms)
            except Exception:
                pass

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
