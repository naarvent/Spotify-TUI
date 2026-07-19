"""Synced lyrics fetching, parsing and rendering."""

from __future__ import annotations

import os
import re
import json
import time
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

from .. import config
from ..config import logger
from ..constants import WELCOME

class LyricsMixin:
    def _leave_lyrics_mode(self):
        """Idempotent teardown of the lyrics view: stop the 0.4s tick, invalidate
        any in-flight load (so a late worker can't paint), and drop the lyrics
        widget/CSS state. Does NOT navigate anywhere — callers decide the next
        view. Safe to call twice and safe to call when lyrics were never open."""
        if (not getattr(self, "_lyrics_on", False)
                and getattr(self, "_lyrics_interval", None) is None
                and getattr(self, "lyrics_box", None) is None):
            return  # already clean
        self._lyrics_on = False
        self._lyrics_loading = False
        self._lyrics_gen = getattr(self, "_lyrics_gen", 0) + 1   # invalidate in-flight loads
        it = getattr(self, "_lyrics_interval", None)
        if it is not None:
            try:
                it.pause()
            except Exception:
                pass
            self._lyrics_interval = None
        try:
            self.right_panel.remove_class("lyrics-mode")
        except Exception:
            pass
        self._lyrics_track_id = None
        self.lyrics_box = None

    def action_toggle_lyrics(self):
        if self._lyrics_on:
            self._leave_lyrics_mode()
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
        self.lyrics_box.update(self._render_header(big=True) + self._lyrics_gap() + "[b]Lyrics loading…[/b]")

        try:
            self._lyrics_interval = self.set_interval(0.4, self._update_lyrics_highlight, pause=False)
        except Exception:
            logger.exception("Could not initialize lyrics range")

        self._start_lyrics_load()

    def _lyrics_gap(self) -> str:
        """Vertical separation between the header, the status line and the lyric
        body. Shrinks on short terminals so nothing overflows."""
        try:
            h = int(getattr(self.size, "height", 0) or 0)
        except Exception:
            h = 0
        if h and h < 18:
            return "\n"
        if h and h < 26:
            return "\n\n"
        return "\n\n\n"

    def _start_lyrics_load(self, force: bool = False):
        """Switch the lyrics view to the current track. Metadata (title/artist)
        comes from the cached now-playing state so the header updates instantly;
        the LRCLIB request (or cache hit) fills the lyric body afterwards. A gen
        counter invalidates in-flight loads so a late A never paints over B."""
        self._lyrics_gen = getattr(self, "_lyrics_gen", 0) + 1
        gen = self._lyrics_gen
        self._lyrics_loading = True

        # --- immediate metadata, no network ---
        item = getattr(self, "_bar_last_track", None) or {}
        now_id = getattr(self, "_now_internal_track_id", None)
        name = item.get("name") or "(no title)"
        all_artists = [a.get("name", "") for a in (item.get("artists") or []) if a and a.get("name")]
        artists = ", ".join(all_artists)
        primary_artist = all_artists[0] if all_artists else artists
        album = (item.get("album") or {}).get("name", "") if isinstance(item.get("album"), dict) else ""
        duration_ms = int(item.get("duration_ms") or 0)
        track_id = item.get("id") or now_id

        # Track by the now-playing id so the 0.4s tick sees the change is handled
        # (prevents a re-trigger loop when the cached item lags the id).
        self._lyrics_track_id = now_id or track_id
        self._lyrics_title = f"{name} — {artists}" if artists else name
        self._lyrics_lines = []
        if self.lyrics_box is not None:
            self.lyrics_box.update(self._render_header(big=True) + self._lyrics_gap() + "[b]Lyrics loading…[/b]")

        # --- cache first (a Ctrl+R forces a fresh fetch, ignoring negatives) ---
        if not force:
            cached = self._lyrics_cache_get(track_id, name, primary_artist, duration_ms, album)
            if cached is not None:
                self._apply_lyrics_result(gen, track_id, self._lyrics_title, cached,
                                          "found" if cached else "notfound")
                return

        key = self._lyrics_cache_key(track_id, name, primary_artist, duration_ms, album)
        title = self._lyrics_title

        def _worker():
            try:
                lines, status = self._fetch_synced_lyrics(
                    title=name, artist=primary_artist, album=album, duration_ms=duration_ms,
                )
            except Exception:
                logger.exception("Lyrics background load failed")
                lines, status = [], "error"
            # Persist real results only; a timeout/connection error is never
            # cached as a permanent absence.
            if status in ("found", "notfound"):
                self._lyrics_cache_put(key, lines if status == "found" else [], status)
            try:
                self.call_from_thread(self._apply_lyrics_result, gen, track_id, title, lines, status)
            except Exception:
                if getattr(self, '_closing', False):
                    logger.debug("lyrics result dropped during teardown")
                else:
                    logger.exception("lyrics result: call_from_thread failed")

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_lyrics_result(self, gen, track_id, title, lines, status="found"):
        # Drop results from a stale request (A→B→C keeps only the newest) or
        # after the view was closed.
        if not self._lyrics_on or gen != getattr(self, "_lyrics_gen", 0):
            return
        self._lyrics_loading = False
        self._lyrics_track_id = track_id
        self._lyrics_title = title or self._lyrics_title
        self._lyrics_lines = lines or []
        if self.lyrics_box is None:
            return
        header = self._render_header(big=True)
        gap = self._lyrics_gap()
        if self._lyrics_lines:
            self.lyrics_box.update(header + gap + self._render_lyrics_at(self._cached_pos_ms()))
        elif status == "error":
            # Keep the (correct) song header; a transient failure is retryable.
            self.lyrics_box.update(header + gap + "[b]Lyrics unavailable.[/b] Press Ctrl+R to retry.")
        else:
            self.lyrics_box.update(header + gap + "[b]Lyrics not found[/b]")

    # ------------------------------------------------------------------ #
    # Lyrics cache (in-memory + best-effort JSON persistence)
    # ------------------------------------------------------------------ #
    _LYRICS_CACHE_MAX = 500
    _LYRICS_CACHE_MAX_BYTES = 2 * 1024 * 1024   # hard 2 MiB cap on the file
    _LYRICS_NEG_TTL = 24 * 3600      # re-try a 'not found' after a day

    def _lyrics_cache_path(self) -> str:
        return os.path.join(config.CACHE_DIR, "lyrics_cache.json")

    def _lyrics_cache(self) -> dict:
        c = getattr(self, "_lyrics_cache_data", None)
        if c is None:
            c = self._lyrics_cache_load()
            self._lyrics_cache_data = c
        return c

    def _lyrics_cache_load(self) -> dict:
        try:
            p = self._lyrics_cache_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            # Corrupt or unreadable cache must never block startup.
            logger.exception("lyrics cache load failed (ignoring)")
        return {}

    def _lyrics_cache_save(self):
        try:
            c = getattr(self, "_lyrics_cache_data", None) or {}
            self._lyrics_cache_trim(c)
            p = self._lyrics_cache_path()
            tmp = f"{p}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False)
            os.replace(tmp, p)       # atomic swap; no half-written file
        except OSError:
            logger.exception("lyrics cache save failed")

    def _lyrics_cache_trim(self, c: dict) -> None:
        """Bound the cache so it can never grow without end: evict the oldest
        entries (by timestamp) until it is within BOTH the entry-count cap and
        the on-disk byte cap. Mutates c in place."""
        def _ts(kv):
            return (kv[1] or {}).get("ts", 0) if isinstance(kv[1], dict) else 0

        # 1) entry-count cap
        if len(c) > self._LYRICS_CACHE_MAX:
            for k, _ in sorted(c.items(), key=_ts)[: len(c) - self._LYRICS_CACHE_MAX]:
                c.pop(k, None)

        # 2) byte cap on the serialised file — drop oldest until it fits (an
        # over-estimate of the freed size is fine; it only trims a little extra).
        def _blen(obj):
            return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        total = _blen(c)
        if total > self._LYRICS_CACHE_MAX_BYTES:
            for k, v in sorted(c.items(), key=_ts):
                if total <= self._LYRICS_CACHE_MAX_BYTES or len(c) <= 1:
                    break
                total -= _blen({k: v})
                c.pop(k, None)

    def _lyrics_cache_key(self, track_id, title, artist, duration_ms, album) -> str:
        if track_id:
            return f"id:{track_id}"
        dur = int(round((duration_ms or 0) / 1000))
        bucket = dur // 5            # tolerate tiny duration differences
        ct = self._clean_track_title(title).lower().strip()
        return f"q:{ct}|{(artist or '').lower().strip()}|{bucket}"

    def _lyrics_cache_get(self, track_id, title, artist, duration_ms, album):
        """Return parsed lines (a positive hit), [] (a cached 'not found' still
        within its TTL) or None (miss / expired negative -> fetch)."""
        try:
            key = self._lyrics_cache_key(track_id, title, artist, duration_ms, album)
            e = self._lyrics_cache().get(key)
            if not isinstance(e, dict):
                return None
            if e.get("status") == "found":
                lines = e.get("lines") or []
                return [tuple(x) for x in lines] if lines else None
            if e.get("status") == "notfound":
                if (int(time.time()) - int(e.get("ts", 0))) < self._LYRICS_NEG_TTL:
                    return []
        except Exception:
            logger.exception("lyrics cache get failed")
        return None

    def _lyrics_cache_put(self, key, lines, status):
        try:
            c = self._lyrics_cache()
            if status == "found":
                c[key] = {"status": "found", "lines": [list(x) for x in (lines or [])], "ts": int(time.time())}
            elif status == "notfound":
                c[key] = {"status": "notfound", "lines": [], "ts": int(time.time())}
            else:
                return               # never cache transient errors
            self._lyrics_cache_save()
        except Exception:
            logger.exception("lyrics cache put failed")

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
    ) -> tuple[list, str]:
        """Return ``(lines, status)`` where status is 'found', 'notfound' (LRCLIB
        answered with no lyrics) or 'error' (timeout / connection / rate-limit /
        invalid response). Only 'found'/'notfound' are safe to cache."""
        try:
            if requests is None:
                try:
                    self.call_from_thread(
                        lambda: self.right_panel.update("[i]Lyrics: install 'requests' to enable them (pip install requests)[/i]")
                    )
                except Exception:
                    pass
                return [], "error"

            base = "https://lrclib.net/api"
            headers = {"User-Agent": self._LYRICS_UA}
            timeout = 8
            dur_s = max(0, int(round((duration_ms or 0) / 1000)))
            clean = self._clean_track_title(title)
            errored = False

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
            #    lyrics. A 404 just means "not in the DB" (not an error).
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
                            return res, "found"
                    elif r.status_code == 429:
                        errored = True
                except requests.exceptions.RequestException:
                    errored = True
                except Exception:
                    errored = True
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
                            return res, "found"
                    elif r.status_code == 429:
                        errored = True
                except requests.exceptions.RequestException:
                    errored = True
                except Exception:
                    errored = True
                    logger.debug("lyrics: /search failed", exc_info=True)

            return [], ("error" if errored else "notfound")
        except Exception:
            logger.exception("Lyrics: general failure in _fetch_synced_lyrics")
            return [], "error"

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
            if cur_id and cur_id != self._lyrics_track_id:
                # Song changed: switch the header to the new song immediately (from
                # cached metadata, no network on this tick). A previous in-flight
                # load is invalidated by the gen counter, so A→B→C only shows C.
                self._start_lyrics_load()
                return
            if self.lyrics_box is None:
                return
            header = self._render_header(big=True)
            gap = self._lyrics_gap()
            if getattr(self, "_lyrics_loading", False) and not self._lyrics_lines:
                self.lyrics_box.update(header + gap + "[b]Lyrics loading…[/b]")
                return
            block = self._render_lyrics_at(self._cached_pos_ms())
            self.lyrics_box.update(header + gap + block)
        except Exception:
            logger.exception("Error updating lyrics")
