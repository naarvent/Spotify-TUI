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
from .. import karaoke
from .. import spicy_lyrics
from ..config import logger
from ..local_library import atomic_write_json
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
                self._go_to_menu()
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
        # A class, not an id: Widget.remove() is asynchronous, so an old box can
        # still be mounted when a new one arrives (DuplicateIds crash).
        self.lyrics_box = Static("", classes="lyrics_box")
        right.mount(self.lyrics_box)

        # Best-effort title from the cached now-playing track (no network call).
        item = getattr(self, "_bar_last_track", None) or {}
        name = item.get("name") or ""
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a)
        self._lyrics_title = f"{name} — {artists}" if name else ""
        self._lyrics_lines = []
        self.lyrics_box.update(self._render_header(big=True) + self._lyrics_gap() + "[b]Lyrics loading…[/b]")

        self._lyrics_frame = None
        self._start_lyrics_interval()
        self._start_lyrics_load()

    # ------------------------------------------------------------------ #
    # Karaoke mode (word by word) and the live offset
    # ------------------------------------------------------------------ #
    # Line mode keeps its historical 0.4 s tick. Karaoke needs a finer one for
    # the word wipe, but only repaints when the frame really changes
    # (karaoke.frame_key_fixed), so most ticks cost a few comparisons.
    LYRICS_TICK_LINES = 0.4
    LYRICS_TICK_KARAOKE = 0.1
    LYRICS_MODES = ("lines", "karaoke")
    OFFSET_STEP_MS = 250
    OFFSET_BIG_STEP_MS = 1000

    def _lyrics_mode(self) -> str:
        m = getattr(self, "_lyrics_mode_value", None)
        if m is None:
            cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
            m = cfg.get("lyrics_mode") if cfg.get("lyrics_mode") in self.LYRICS_MODES else "lines"
            self._lyrics_mode_value = m
        return m

    def _start_lyrics_interval(self) -> None:
        it = getattr(self, "_lyrics_interval", None)
        if it is not None:
            try: it.stop()
            except Exception: pass
            self._lyrics_interval = None
        period = self._lyrics_tick_period()
        self._lyrics_period = period
        try:
            self._lyrics_interval = self.set_interval(period, self._update_lyrics_highlight, pause=False)
        except Exception:
            logger.exception("Could not initialize lyrics range")

    def _lyrics_tick_period(self) -> float:
        # Karaoke only needs 10 Hz while the song moves: paused, the 0.4 s tick
        # of the line view is enough (a 10 Hz timer costs a few % CPU just by
        # waking Textual up, even when nothing is repainted).
        if self._lyrics_mode() == "karaoke" and getattr(self, "_now_internal_is_playing", False):
            return self.LYRICS_TICK_KARAOKE
        return self.LYRICS_TICK_LINES

    def action_lyrics_mode(self) -> None:
        """`k` in the lyrics view: line-by-line ⇄ karaoke (remembered)."""
        if not getattr(self, "_lyrics_on", False):
            return
        m = "karaoke" if self._lyrics_mode() == "lines" else "lines"
        self._lyrics_mode_value = m
        try:
            if not isinstance(config.LOCAL_CFG, dict):
                config.LOCAL_CFG = {}
            config.LOCAL_CFG["lyrics_mode"] = m
            config.save_local_config(config.LOCAL_CFG)
        except Exception:
            logger.exception("could not save lyrics_mode")
        self._lyrics_frame = None
        self._start_lyrics_interval()
        self._update_lyrics_highlight()
        self._notify("[b]Lyrics:[/b] " + ("karaoke (word by word)" if m == "karaoke" else "line by line"))

    def _lyrics_offset(self) -> int:
        offs = getattr(self, "_lyrics_offsets", None) or {}
        return int(offs.get(getattr(self, "_lyrics_track_id", None) or "", 0))

    def _shift_lyrics_offset(self, delta_ms: int) -> None:
        """`]`/`[` (±250 ms) and `}`/`{` (±1 s): lyrics later/earlier for THIS
        song (on top of the file's own [offset:]). Positive = lyrics earlier."""
        if not getattr(self, "_lyrics_on", False):
            return
        offs = getattr(self, "_lyrics_offsets", None)
        if offs is None:
            offs = self._lyrics_offsets = {}
        key = getattr(self, "_lyrics_track_id", None) or ""
        offs[key] = int(offs.get(key, 0)) + int(delta_ms)
        self._lyrics_frame = None
        self._update_lyrics_highlight()
        self._notify(f"[b]Lyrics offset:[/b] {offs[key] / 1000:+.2f} s  [dim]([ ] ±0.25 s · {{ }} ±1 s)[/dim]")

    def action_lyrics_offset_up(self): self._shift_lyrics_offset(+self.OFFSET_STEP_MS)
    def action_lyrics_offset_down(self): self._shift_lyrics_offset(-self.OFFSET_STEP_MS)
    def action_lyrics_offset_up_big(self): self._shift_lyrics_offset(+self.OFFSET_BIG_STEP_MS)
    def action_lyrics_offset_down_big(self): self._shift_lyrics_offset(-self.OFFSET_BIG_STEP_MS)

    def _lyrics_pos(self) -> int:
        return max(0, self._cached_pos_ms() + self._lyrics_offset())

    def _karaoke_lines(self):
        kl = getattr(self, "_lyrics_klines", None)
        if kl is None:
            dur = int(getattr(self, "_now_internal_dur_ms", 0) or 0)
            kl = self._lyrics_klines = karaoke.from_lines(self._lyrics_lines or [], dur)
        return kl

    def _karaoke_status(self, kl) -> str:
        src = getattr(self, "_lyrics_source", "") or ""
        if karaoke.has_real_words(kl):
            sync = f"word-synced{' · ' + src if src else ''}"
            n = karaoke.approximate_count(kl)
            if n:
                sync += f" (≈ {n} line{'s' if n != 1 else ''} approximate)"
        else:
            sync = "≈ approximate word timing (from line times)"
        off = self._lyrics_offset()
        off_s = f" · offset {off / 1000:+.2f} s" if off else ""
        return f"[dim]karaoke · {sync}{off_s} · k: line view · [ ]: offset[/dim]"

    # Body rows the karaoke view never goes below (on a tiny panel the big
    # title is dropped for a one-line one first).
    KARAOKE_MIN_BODY = 7

    def _lyrics_box_size(self):
        """(width, height) of the lyrics box's content area, with a guess
        before the first layout."""
        w = h = 0
        try:
            cs = self.lyrics_box.content_size
            w, h = int(cs.width or 0), int(cs.height or 0)
        except Exception:
            pass
        if not w:
            w = self._lyrics_panel_width()
        if not h:
            try:
                h = max(10, int(self.size.height) - 16)
            except Exception:
                h = 20
        return w, h

    @staticmethod
    def _fixed_rows(markup: str, width: int):
        """One no-wrap, cropped Text per line of ``markup`` (a header can never
        wrap into extra rows)."""
        from rich.text import Text
        out = []
        # Parse the whole markup first: a tag (the title colour) spans lines.
        whole = Text.from_markup((markup or "").rstrip("\n"))
        for t in whole.split("\n", allow_blank=True):
            t.justify = "center"
            t.no_wrap = True
            t.overflow = "crop"
            out.append(t)
        return out

    def _karaoke_layout(self):
        """Rows of the header block (title, gap, status, spacer) and how many
        rows the body gets. Fixed for a given song and panel size."""
        from rich.text import Text
        w, h = self._lyrics_box_size()
        kl = self._karaoke_lines()
        status = self._fixed_rows(self._karaoke_status(kl), w)[:1]
        gap = max(0, self._lyrics_gap().count("\n") - 1)
        head = self._header_rows(big=True, width=w)
        if len(head) + gap + 2 + self.KARAOKE_MIN_BODY > h:
            head = self._header_rows(big=False, width=w)
            gap = min(gap, 1)
        top = head + [Text("") for _ in range(gap)] + status + [Text("")]
        if len(top) + self.KARAOKE_MIN_BODY > h:
            top = status + [Text("")]
        return top, max(1, h - len(top)), w

    def _render_karaoke_view(self, force: bool = False) -> bool:
        """Paint the karaoke frame; returns False when nothing changed.

        Fixed geometry: the frame is EXACTLY the box height, one row per Text
        (no wrapping anywhere), so the title and the status line never move
        and the current line always starts on the middle row of the body."""
        from rich.console import Group
        from rich.text import Text
        kl = self._karaoke_lines()
        pos = self._lyrics_pos()
        top, body_rows, width = self._karaoke_layout()
        # Credit rows at the bottom of the box (fixed: the geometry never changes).
        credit = spicy_lyrics.attribution_rows(getattr(self, "_lyrics_attr", None), width)
        if credit and body_rows - len(credit) - 1 >= 3:
            body_rows -= len(credit) + 1
        else:
            credit = []
        sig = (karaoke.frame_key_fixed(kl, pos), tuple(t.plain for t in top), body_rows, width,
               self._lyrics_offset(), tuple(t.plain for t in credit))
        if not force and sig == getattr(self, "_lyrics_frame", None):
            return False
        self._lyrics_frame = sig
        body = karaoke.render_fixed(kl, pos, width=width, rows=body_rows, accent=self._karaoke_accent())
        self._karaoke_rows = top + body + ([Text("")] + credit if credit else [])
        self._karaoke_credit_rows = (len(credit) + 1) if credit else 0
        self._karaoke_header_rows = len(top)
        # .lyrics_box is 1fr × 1fr: its size never depends on the text, so a
        # frame only needs a repaint, not a layout pass (that pass was ~30% of
        # the cost of each karaoke frame).
        self.lyrics_box.update(Group(*self._karaoke_rows), layout=force)
        self._lyrics_paints = getattr(self, "_lyrics_paints", 0) + 1
        return True

    # Wipe colour: the same yellow as the current line of the line view.
    KARAOKE_ACCENT = "#e6ff71"

    def _karaoke_accent(self) -> str:
        return self.KARAOKE_ACCENT

    # ------------------------------------------------------------------ #
    # Spicy Lyrics source (optional, needs the user's own key)
    # ------------------------------------------------------------------ #
    def _spicy_enabled(self) -> bool:
        """Settings flag (on by default). Whether a key exists is checked in the worker;
        without one the source is skipped silently and LRCLIB works as before."""
        return spicy_lyrics.enabled_in(config.LOCAL_CFG)

    def _spicy_service(self):
        svc = getattr(self, "_spicy_svc", None)
        if svc is None:
            cache = spicy_lyrics.SpicyCache(os.path.join(self._local_lyrics_dir(), "spicy_cache"))
            svc = self._spicy_svc = spicy_lyrics.SpicyService(cache)
            try:
                cache.prune()
            except Exception:
                logger.exception("spicy cache prune failed")
        return svc

    def _spicy_notice(self, kind: str, text: str) -> None:
        """One discreet line per kind of problem every 10 minutes (from the worker)."""
        now = time.monotonic()
        seen = getattr(self, "_spicy_notices", None)
        if seen is None:
            seen = self._spicy_notices = {}
        if now - seen.get(kind, -1e9) < 600:
            return
        seen[kind] = now
        try:
            self.call_from_thread(self._notify, rich_escape(text), warn=True)
        except Exception:
            logger.debug("spicy notice dropped", exc_info=True)

    def _lyrics_footer(self) -> str:
        """Credit under the lines of the line-by-line view."""
        m = spicy_lyrics.attribution_markup(getattr(self, "_lyrics_attr", None))
        return ("\n\n" + m) if m else ""

    # ------------------------------------------------------------------ #
    # Your own lyrics files (TTML or LRC) — checked before the cache/LRCLIB
    # ------------------------------------------------------------------ #
    LOCAL_LYRICS_EXT = (".ttml", ".lrc")

    def _local_lyrics_dir(self) -> str:
        return os.path.join(config.CACHE_DIR, "lyrics")

    @staticmethod
    def _lyrics_file_stem(s: str) -> str:
        return re.sub(r'[\\/:*?"<>|]+', "_", (s or "").strip()).lower()

    def _find_local_lyrics(self, track_id, title, artist):
        """Path of <lyrics dir>/<track id>.(ttml|lrc) or "<Artist> - <Title>"."""
        d = self._local_lyrics_dir()
        if not os.path.isdir(d):
            return None
        try:
            names = {f.lower(): f for f in os.listdir(d)}
        except OSError:
            return None
        stems = []
        if track_id:
            stems.append(self._lyrics_file_stem(str(track_id)))
        if title:
            if artist:
                stems.append(self._lyrics_file_stem(f"{artist} - {title}"))
                stems.append(self._lyrics_file_stem(f"{artist} - {self._clean_track_title(title)}"))
        for stem in stems:
            for ext in self.LOCAL_LYRICS_EXT:
                hit = names.get(stem + ext)
                if hit:
                    return os.path.join(d, hit)
        return None

    def _load_local_lyrics(self, path):
        """(line tuples, karaoke lines or None, source label) from a file."""
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if path.lower().endswith(".ttml"):
            kl = karaoke.parse_ttml(text)
            return karaoke.to_line_tuples(kl), kl, "TTML"
        lines = self._parse_lrc(text)
        enhanced = any(len(x) > 2 and x[2] for x in lines)
        return lines, None, ("enhanced LRC" if enhanced else "LRC")

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
        self._lyrics_klines = None
        self._lyrics_source = ""
        self._lyrics_attr = None
        self._lyrics_frame = None
        if self.lyrics_box is not None:
            self.lyrics_box.update(self._render_header(big=True) + self._lyrics_gap() + "[b]Lyrics loading…[/b]")

        # --- your own file (TTML / LRC in <data>/lyrics/) beats everything ---
        try:
            local = self._find_local_lyrics(track_id, name, primary_artist)
        except Exception:
            local = None
        if local:
            try:
                lines, kl, src = self._load_local_lyrics(local)
                self._apply_lyrics_result(gen, track_id, self._lyrics_title, lines, "found",
                                          klines=kl, source=src,
                                          attribution=spicy_lyrics.Attribution("Local file", "local"))
                return
            except Exception as exc:
                logger.warning("lyrics file %s unusable: %s", local, exc)
                self._notify(f"[b]Lyrics file ignored:[/b] {rich_escape(os.path.basename(local))} "
                             f"({rich_escape(str(exc))})", warn=True)

        use_spicy = self._spicy_enabled()
        provider = self._current_provider_name() or ""

        # --- cache first (a Ctrl+R forces a fresh fetch, ignoring negatives) ---
        # With the Spicy Lyrics source on, its cache/request goes BEFORE the LRCLIB
        # cache, so that check moves into the worker (the key lookup may touch the keyring).
        if not force and not use_spicy:
            cached = self._lyrics_cache_get(track_id, name, primary_artist, duration_ms, album)
            if cached is not None:
                self._apply_lyrics_result(gen, track_id, self._lyrics_title, cached,
                                          "found" if cached else "notfound")
                return

        key = self._lyrics_cache_key(track_id, name, primary_artist, duration_ms, album)
        title = self._lyrics_title

        def _deliver(*a, **kw):
            try:
                self.call_from_thread(self._apply_lyrics_result, gen, track_id, title, *a, **kw)
            except Exception:
                if getattr(self, '_closing', False):
                    logger.debug("lyrics result dropped during teardown")
                else:
                    logger.exception("lyrics result: call_from_thread failed")

        def _worker():
            static_fallback = None
            if use_spicy:
                out = self._spicy_service().fetch(
                    provider=provider, track_id=track_id, title=name, artist=primary_artist,
                    album=album, duration_ms=duration_ms, force=force,
                    is_current=lambda: gen == getattr(self, "_lyrics_gen", 0) and self._lyrics_on)
                if out.notice:
                    self._spicy_notice(out.notice_kind, out.notice)
                res = out.result
                if out.status == "found" and res is not None:
                    if res.kind in ("Syllable", "Line"):
                        label = "Spicy Lyrics" if res.kind == "Syllable" else ""
                        _deliver(res.lines, "found", klines=res.klines, source=label, attribution=res.attribution)
                        return
                    static_fallback = res
                if gen != getattr(self, "_lyrics_gen", 0):
                    return
                if not force:
                    cached = self._lyrics_cache_get(track_id, name, primary_artist, duration_ms, album)
                    if cached:
                        _deliver(cached, "found")
                        return
                    if cached is not None and static_fallback is None:
                        _deliver(cached, "notfound")
                        return
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
            if not lines and static_fallback is not None:
                # Only unsynced text exists: pseudo-timed like LRCLIB's plain lyrics, credited to its source.
                texts = [t[1] for t in static_fallback.lines]
                _deliver(self._plain_to_pseudo_lrc("\n".join(texts), duration_ms), "found",
                         attribution=static_fallback.attribution)
                return
            _deliver(lines, status)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_lyrics_result(self, gen, track_id, title, lines, status="found", *,
                             klines=None, source="", attribution=None):
        # Drop results from a stale request (A→B→C keeps only the newest) or
        # after the view was closed.
        if not self._lyrics_on or gen != getattr(self, "_lyrics_gen", 0):
            return
        self._lyrics_loading = False
        self._lyrics_track_id = track_id
        self._lyrics_title = title or self._lyrics_title
        self._lyrics_lines = lines or []
        self._lyrics_klines = klines
        self._lyrics_source = source or ("enhanced LRC" if any(len(x) > 2 and x[2] for x in self._lyrics_lines) else "")
        # The credit always goes with the lyrics (provider; for a community sync also
        # uploader and maker). Lyrics that came from LRCLIB say so.
        self._lyrics_attr = (attribution or spicy_lyrics.Attribution("LRCLIB", "lrclib")) if self._lyrics_lines else None
        self._lyrics_frame = None
        if self.lyrics_box is None:
            return
        header = self._render_header(big=True)
        gap = self._lyrics_gap()
        if self._lyrics_lines and self._lyrics_mode() == "karaoke":
            self._render_karaoke_view(force=True)
        elif self._lyrics_lines:
            self.lyrics_box.update(header + gap + self._render_lyrics_at(self._lyrics_pos()) + self._lyrics_footer())
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
            # Atomic swap with a unique temp file (no half-written file, and
            # two threads saving at once cannot clobber each other's temp).
            atomic_write_json(self._lyrics_cache_path(), c)
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
            # Plain (unsynced) lyrics only get evenly spread fake timestamps, so
            # they never follow the song. Keep them as a last resort and go on
            # looking for a synced version (the /get match can be plain-only
            # while /search has a synced entry for the same song).
            plain_fallback = []

            def _from(data):
                if not isinstance(data, dict):
                    return None
                synced = (data.get("syncedLyrics") or "").strip()
                if synced:
                    return self._parse_lrc(synced)
                plain = (data.get("plainLyrics") or "").strip()
                if plain and not plain_fallback:
                    plain_fallback.extend(self._plain_to_pseudo_lrc(plain, duration_ms))
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

            if plain_fallback:
                return plain_fallback, "found"
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
        # LRC "[offset:+/-ms]": positive shows the lyrics earlier. It used to be
        # ignored (and shown as a lyric line at 0:00 along with [ar:]/[ti:] tags).
        offset_ms = 0
        for raw in lrc_text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            tag = re.fullmatch(r"\[([A-Za-z]+):([^\]]*)\]", raw)
            if tag:
                if tag.group(1).lower() == "offset":
                    try:
                        offset_ms = int(tag.group(2).strip())
                    except ValueError:
                        pass
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
                    # Enhanced LRC: "<mm:ss.xx>" word stamps inside the text.
                    # The line view shows the clean text; karaoke uses the times.
                    clean, words = karaoke.parse_enhanced_words(text, ts)
                    lines.append((ts, clean, words) if words else (ts, text))
            else:
                lines.append((0, raw))
        if offset_ms:
            lines = [(max(0, x[0] - offset_ms), x[1]) + (
                ([[max(0, int(w[0]) - offset_ms), w[1]] for w in x[2]],) if len(x) > 2 else ())
                for x in lines]
        lines.sort(key=lambda x: x[0])
        return lines

    def _render_lyrics_at(self, pos_ms: int, window: int = 5) -> str:
        if not self._lyrics_lines:
            return "[i]No Lyrics Found[/i]"
        idx = 0
        for i, line in enumerate(self._lyrics_lines):
            if line[0] <= pos_ms:
                idx = i
            else:
                break
        start = max(0, idx - window)
        end = min(len(self._lyrics_lines), idx + window + 1)
        out: List[str] = []
        for i in range(start, idx):
            txt = self._lyrics_lines[i][1]
            safe = rich_escape(txt or "")
            out.append(f"[dim]{safe}[/dim]")
        cur_txt = self._lyrics_lines[idx][1]
        cur_safe = rich_escape(cur_txt or "")
        out.append("")
        out.append(f"[b][#e6ff71]{cur_safe}[/#e6ff71][/b]")
        out.append("")
        for i in range(idx + 1, end):
            txt = self._lyrics_lines[i][1]
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
            if self._lyrics_lines and self._lyrics_mode() == "karaoke":
                self._render_karaoke_view()
                if self._lyrics_tick_period() != getattr(self, "_lyrics_period", None):
                    self._start_lyrics_interval()     # play/pause changed the rate
                return
            block = self._render_lyrics_at(self._lyrics_pos()) + self._lyrics_footer()
            self.lyrics_box.update(header + gap + block)
        except Exception:
            logger.exception("Error updating lyrics")
