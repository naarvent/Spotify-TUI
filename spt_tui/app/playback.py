"""Playback control, now-playing bar and progress rendering."""

from __future__ import annotations

import time
import threading
from time import monotonic as _mono
from typing import Optional

from textual.widgets import Static, DataTable
from rich.markup import escape as rich_escape
from rich.text import Text

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
from ..constants import USE_ASCII
from ..spotify_client import SpotifyClient

class PlaybackMixin:
    # Only draw the ASCII-art artist banner when the panel is at least this
    # big; below it we fall back to the plain one-line header.
    _LYRICS_ART_MIN_W = 72
    _LYRICS_ART_MIN_H = 26
    _LYRICS_ART_MAX_CHARS = 16

    def _lyrics_panel_width(self) -> int:
        """Content width (columns) available for the lyrics text."""
        for widget_getter in (
            lambda: getattr(self, "lyrics_box", None),
            lambda: self.query_one("#right"),
        ):
            try:
                widget = widget_getter()
                if widget is not None:
                    cs = getattr(widget, "content_size", None)
                    w = int(getattr(cs, "width", 0) or 0) if cs else 0
                    if w > 0:
                        return w
            except Exception:
                pass
        try:
            return max(10, int(getattr(self.size, "width", 80) or 80) - 44)
        except Exception:
            return 60

    def _render_header(self, big: bool = True) -> str:
        title = self._lyrics_title or ""
        name = title
        artists = ""
        if " — " in title:
            try:
                name, artists = title.split(" — ", 1)
            except Exception:
                name, artists = title, ""
        artist_main = (artists or name or "").split(",")[0].strip()
        song = name.strip()

        w = self._lyrics_panel_width()
        try:
            h = int(getattr(self.size, "height", 0) or 0)
        except Exception:
            h = 0

        if big and pyfiglet is not None and w >= self._LYRICS_ART_MIN_W and h >= self._LYRICS_ART_MIN_H:
            # Song title in ASCII art (primary); artist in art too when the
            # title is short enough and there is vertical room, else as a line.
            title_art = self._ascii_art_centered(song, w) if song else None
            artist_art = self._ascii_art_centered(artist_main, w) if artist_main else None

            def _nlines(a):
                return (a.count("\n") + 1) if a else 0

            # Keep ~14 lines free for the lyrics themselves.
            room_for_both = (
                title_art and artist_art
                and (_nlines(title_art) + _nlines(artist_art)) <= max(0, h - 14)
            )
            if room_for_both:
                return (f"[b][#e6ff71]{title_art}[/#e6ff71][/b]\n"
                        f"[b][#27e1c1]{artist_art}[/#27e1c1][/b]\n")
            if title_art:
                sub = f"[dim]{rich_escape(artist_main)}[/dim]\n" if artist_main else ""
                return f"[b][#e6ff71]{title_art}[/#e6ff71][/b]\n{sub}"
            if artist_art:
                sub = f"[dim]{rich_escape(song)}[/dim]\n" if song else ""
                return f"[b][#27e1c1]{artist_art}[/#27e1c1][/b]\n{sub}"

        # Fallback (small terminal or no pyfiglet): plain one-line header.
        disp = title or artist_main
        return f"[b][underline]{rich_escape(disp)}[/underline][/b]"

    def _ascii_art_centered(self, text: str, width: int) -> Optional[str]:
        """Render `text` as figlet art, block-centered within `width`.

        Every line is padded to exactly `width` so the panel's per-line
        centering leaves the block undistorted. Returns None if it won't fit.
        """
        try:
            try:
                fig = pyfiglet.Figlet(font="small", width=max(20, width))
            except Exception:
                fig = pyfiglet.Figlet(width=max(20, width))
            raw = fig.renderText(text)
        except Exception:
            logger.exception("pyfiglet render failed")
            return None
        lines = [ln.rstrip() for ln in raw.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return None
        block_w = max(len(ln) for ln in lines)
        if block_w > width:
            return None  # too wide -> caller falls back to plain header
        shift = (width - block_w) // 2
        padded = []
        for ln in lines:
            ln = (" " * shift) + ln
            ln = ln + (" " * (width - len(ln)))
            padded.append(rich_escape(ln))
        return "\n".join(padded)

    def _np_full_bar_width(self) -> int:
        try:
            bar = self.query_one("#np_bar_full", Static)
            cs = getattr(bar, "content_size", None)
            if cs is not None:
                w = int(getattr(cs, "width", 0) or 0)
            else:
                w = int(getattr(bar.size, "width", 0) or 0)
            if w > 0: return max(1, w - 2)
        except Exception: pass
        try:
            scr_w = int(getattr(self.size, "width", 100) or 100)
        except Exception:
            scr_w = 100
        return max(10, scr_w - 20)

    def _progress_components(self, pos_ms: int, dur_ms: int, width: int) -> tuple[str, str, str]:
        width = max(1, int(width))
        if dur_ms <= 0:
            left, right = "0:00", "0:00"
            empty = "-" if USE_ASCII else "░"
            return left, f"[{(empty * width)[:width]}]", right
        frac = max(0.0, min(1.0, float(pos_ms) / float(dur_ms)))
        filled = max(0, min(width, int(round(frac * width))))
        fill  = "#" if USE_ASCII else "█"
        empty = "-" if USE_ASCII else "░"
        inside = (fill * filled) + (empty * (width - filled))
        if len(inside) != width:
            inside = (inside + empty * width)[:width]
        left = SpotifyClient.fmt_duration(pos_ms)
        right = SpotifyClient.fmt_duration(dur_ms)
        return left, f"[{inside}]", right

    def _cached_pos_ms(self) -> int:
        """Estimated playback position from cached state + wall clock — no
        network call. Used both by the now-bar tick and the lyrics view so the
        two never disagree and neither blocks on a fresh get_playback()."""
        base = int(getattr(self, "_now_internal_pos_ms", 0) or 0)
        if getattr(self, "_now_internal_is_playing", False):
            elapsed = int((_mono() * 1000.0) - float(getattr(self, "_now_internal_last_wall_ms", 0.0) or 0.0))
        else:
            elapsed = 0
        pos = max(0, base + elapsed)
        dur = int(getattr(self, "_now_internal_dur_ms", 0) or 0)
        if dur and pos > dur:
            pos = dur
        return pos

    def _update_now_bar(self) -> None:

        lock = getattr(self, "_now_worker_lock", None)
        if lock is None:
            self._now_worker_lock = threading.Lock(); lock = self._now_worker_lock

        if not lock.acquire(blocking=False):
            return

        def _now_worker():
            try:
                # Paint from cached state only. The authoritative network refresh
                # lives in _sync_playback (1.5s); this 0.5s tick must never do I/O
                # (it used to call get_playback() every tick — redundant traffic).
                try:
                    item = getattr(self, '_bar_last_track', None)
                    dur_ms = int(getattr(self, '_now_internal_dur_ms', 0) or 0)
                    is_playing = bool(getattr(self, '_now_internal_is_playing', False))
                    pos_ms = self._cached_pos_ms()
                except Exception:
                    logger.exception("Error computing internal now-bar state")
                    item, pos_ms, dur_ms, is_playing = None, 0, 0, False

                def paint():
                    try:
                        try:
                            w = int(getattr(self.size, "width", 100) or 100)
                        except Exception:
                            w = 100
                        avail = max(24, w - 20)
                        try:
                            bar_width = int(self._np_full_bar_width() or 0)
                        except Exception:
                            bar_width = 0
                        bar_right_len = max(0, bar_width + 2)
                        if item:
                            name = (item.get("name") or item.get("title") or
                                    (item.get("track") or {}).get("name") or
                                    (item.get("episode") or {}).get("name") or "(no title)")

                            artists = ""
                            try:
                                if (item.get("type") or "").lower() == "episode":
                                    show = (item.get("show") or {})
                                    artists = show.get("name") or item.get("publisher") or ""
                                else:
                                    if item.get("artists"):
                                        artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a)
                                    else:
                                        tr = item.get("track") or item.get("episode") or {}
                                        if tr.get("artists"):
                                            artists = ", ".join(a.get("name", "") for a in tr.get("artists", []) if a)
                            except Exception:
                                artists = ""

                            raw_title = f"Now Playing: {name}"
                            if artists:
                                raw_title = f"Now Playing: {name} — {artists}"
                            try:
                                repeat_state = getattr(self, '_now_repeat_state', 'off') or 'off'
                                shuffle_on = bool(getattr(self, '_now_shuffle', False))
                                if repeat_state == 'track':
                                    repeat_icon = '⟳'
                                elif repeat_state == 'context':
                                    repeat_icon = '↳↰'
                                else:
                                    repeat_icon = '↪'
                                shuffle_icon = '⇄' if shuffle_on else '↪'
                            except Exception:
                                repeat_icon = '↪'; shuffle_icon = '↪'

                            labeled = f"Repeat: {repeat_icon}  Shuffle: {shuffle_icon}"
                            icon_len = len(labeled)
                            target_right = max(avail, bar_right_len)
                            title_avail = max(1, target_right - icon_len - 1)
                            ell = self._ellipsis_middle(raw_title, title_avail)
                            esc = rich_escape(ell)
                            pad = max(1, target_right - len(ell) - icon_len - 1)
                            title_line = f"[b]{esc}[/b]" + (" " * pad) + labeled
                        else:
                            title_line = "[dim]Now Playing: —[/dim]"
                        if getattr(self, "np_title", None) is not None:
                            self.np_title.update(title_line)

                        bar_width = self._np_full_bar_width()
                        if dur_ms > 0:
                            _, bar_str, _ = self._progress_components(pos_ms, dur_ms, bar_width)
                        else:
                            empty = "-" if USE_ASCII else "░"
                            bar_str = "[" + (empty * bar_width) + "]"
                        if getattr(self, "np_bar_full", None) is not None:
                            try:
                                self.np_bar_full.update(str(bar_str))
                            except Exception:
                                try:
                                    self.np_bar_full.update(Text(bar_str))
                                except Exception:
                                    logger.exception("NowBar paint: failed to update np_bar_full")

                        left_time  = SpotifyClient.fmt_duration(pos_ms) if dur_ms > 0 else "--:--"
                        right_time = SpotifyClient.fmt_duration(dur_ms) if dur_ms > 0 else "--:--"
                        line_width = len(bar_str) if bar_str else max(20, w - 20)
                        spaces = max(1, line_width - len(left_time) - len(right_time))
                        times_line = left_time + (" " * spaces) + right_time
                        if getattr(self, "np_times", None) is not None:
                            try:
                                self.np_times.update(str(times_line))
                            except Exception:
                                try:
                                    self.np_times.update(Text(times_line))
                                except Exception:
                                    logger.exception("NowBar paint: failed to update np_times")

                        try:
                            wrap = self.query_one("#now_wrap")
                            if is_playing and dur_ms > 0: wrap.add_class("-playing")
                            else: wrap.remove_class("-playing")
                        except Exception: pass
                    except Exception:
                        logger.exception("The bottom bar could not be painted (Now Playing)")

                try:
                    self.call_from_thread(paint)
                except Exception:
                    if getattr(self, '_closing', False):
                        logger.debug("now-bar paint dropped during teardown")
                    else:
                        logger.exception("now-bar paint: call_from_thread failed")
            finally:
                try:
                    lock.release()
                except Exception:
                    pass

        threading.Thread(target=_now_worker, daemon=True).start()

    def _get_current_or_last_track(self) -> tuple[dict | None, int, int, bool]:
        try:
            pb = self.spotify.get_playback() or {}
            item = pb.get("item") or pb.get("track") or pb.get("episode") or None
            ctype = (pb.get("currently_playing_type") or "").lower()

            is_playing = False
            progress_ms = 0
            duration_ms = 0
            if not item and ctype == "episode":
                try:
                    try:
                        extra_pb = self.spotify.ensure().current_playback(additional_types='episode,track')
                    except TypeError:
                        try:
                            extra_pb = self.spotify.ensure().currently_playing()
                        except Exception:
                            extra_pb = None
                    except Exception:
                        extra_pb = None

                    if extra_pb:
                        ei = extra_pb.get('item') or extra_pb.get('track') or extra_pb.get('episode')
                        if ei:
                            item = ei
                            progress_ms = int(extra_pb.get('progress_ms') or progress_ms or 0)
                            duration_ms = int((item or {}).get('duration_ms') or duration_ms or 0)
                            is_playing = bool(extra_pb.get('is_playing') or is_playing)
                except Exception:
                    logger.exception("NowBar get_current: explicit current_playback attempt failed")
                if not item:
                    try:
                        placeholder = {
                            "type": "episode",
                            "name": "(Podcast episode)",
                            "publisher": None,
                            "duration_ms": 0,
                            "id": None,
                            "uri": None,
                        }
                        return placeholder, int(pb.get("progress_ms") or 0), 0, bool(pb.get("is_playing"))
                    except Exception:
                        pass
            try:
                self._now_shuffle = bool(pb.get("shuffle_state"))
                self._now_repeat_state = str(pb.get("repeat_state") or "off")
                self._now_volume = int((pb.get("device") or {}).get("volume_percent") or 0)
            except Exception:
                self._now_shuffle = False
                self._now_repeat_state = "off"
            is_playing = bool(pb.get("is_playing"))
            progress_ms = int(pb.get("progress_ms") or 0)
            duration_ms = int((item or {}).get("duration_ms") or 0)

            if item and duration_ms <= 0:
                try:
                    itid = item.get("id")
                    ittype = (item.get("type") or "").lower()
                    if itid:
                        if ittype == "episode":
                            try:
                                full = self.spotify.ensure().episode(itid) or {}
                                duration_ms = int(full.get("duration_ms") or 0)
                                item = {**item, **full}
                            except Exception:
                                pass
                        else:
                            try:
                                full = self.spotify.ensure().track(itid) or {}
                                duration_ms = int(full.get("duration_ms") or 0)
                                item = {**item, **full}
                            except Exception:
                                pass
                except Exception:
                    logger.exception("Could not fetch full item metadata for duration")

            if item:
                self._bar_last_track = item
                return item, progress_ms, duration_ms, is_playing
            try:
                rp = self.spotify.recently_played(limit=1) or {}
                arr = rp.get("items") or []
                if arr:
                    it = arr[0] or {}
                    last_track = it.get("track") or it.get("episode") or None
                    if last_track:
                        dur = int(last_track.get("duration_ms") or 0)
                        if dur <= 0:
                            try:
                                lid = last_track.get("id")
                                ltype = (last_track.get("type") or "").lower()
                                if lid:
                                    if ltype == "episode":
                                        try:
                                            full = self.spotify.ensure().episode(lid) or {}
                                            dur = int(full.get("duration_ms") or 0)
                                            last_track = {**last_track, **full}
                                        except Exception:
                                            pass
                                    else:
                                        try:
                                            full = self.spotify.ensure().track(lid) or {}
                                            dur = int(full.get("duration_ms") or 0)
                                            last_track = {**last_track, **full}
                                        except Exception:
                                            pass
                            except Exception:
                                logger.exception("Could not fetch duration for recently_played item")
                        self._bar_last_track = last_track
                        return last_track, 0, dur, False
            except Exception:
                logger.exception("recently_played failed on _get_current_or_last_track")
            if self._bar_last_track:
                lt = self._bar_last_track
                return lt, 0, int(lt.get("duration_ms") or 0), False
            return None, 0, 0, False
        except Exception:
            logger.exception("Error in _get_current_or_last_track")
            return None, 0, 0, False

    def _sync_playback(self) -> None:
        # Runs off a 1.5s timer (main thread); do the network fetch on a worker
        # so the event loop never blocks, then apply UI updates on the main thread.
        #
        # P4: a non-blocking guard keeps at most one sync worker in flight, so a
        # slow (>1.5s) fetch cannot pile up workers. A sequence number lets a
        # worker drop its result if a newer sync has meanwhile taken over, so an
        # old, slow worker can never overwrite a newer one's published state.
        lock = getattr(self, "_sync_worker_lock", None)
        if lock is None:
            self._sync_worker_lock = threading.Lock(); lock = self._sync_worker_lock
        if not lock.acquire(blocking=False):
            return  # a sync worker is already running; skip this tick

        self._sync_seq = int(getattr(self, "_sync_seq", 0)) + 1
        my_seq = self._sync_seq

        def worker():
            try:
                try:
                    item, pos_ms, dur_ms, is_playing = self._get_current_or_last_track()
                except Exception:
                    logger.exception("_sync_playback failed")
                    return
                # Drop stale results: a newer sync has superseded this one.
                if my_seq != getattr(self, "_sync_seq", my_seq):
                    return
                new_id = (item or {}).get('id') if item else None
                track_changed = new_id != getattr(self, "_now_internal_track_id", None)
                self._now_internal_track_id = new_id
                self._now_internal_pos_ms = int(pos_ms or 0)
                self._now_internal_dur_ms = int(dur_ms or 0)
                self._now_internal_is_playing = bool(is_playing)
                self._now_internal_last_wall_ms = _mono() * 1000.0
                if item:
                    self._bar_last_track = item

                def apply():
                    # Re-check under the same sequence before touching widgets.
                    if my_seq != getattr(self, "_sync_seq", my_seq):
                        return
                    if track_changed:
                        try:
                            self._refresh_playing_highlight()
                        except Exception:
                            pass
                    try:
                        self._update_now_bar()
                    except Exception:
                        pass
                try:
                    self.call_from_thread(apply)
                except Exception:
                    pass
            finally:
                lock.release()
        threading.Thread(target=worker, daemon=True).start()

    def _ellipsis_middle(self, text: str, max_len: int) -> str:
        s = str(text or "")
        if len(s) <= max_len: return s
        keep = max(1, max_len - 1)
        left = keep // 2
        right = keep - left
        return s[:left] + "…" + s[-right:]

    def _run_playback_call(self, fn, *, resync_after: float = None):
        """Fire a mutating Spotify call on a background thread so the single
        keypress that triggered it never blocks the UI on a network round
        trip. Optionally re-syncs cached state afterwards (for actions whose
        outcome isn't fully predictable locally, e.g. next/prev/seek)."""
        def worker():
            try:
                fn()
            except Exception:
                logger.exception("playback call failed")
            if resync_after is not None:
                try:
                    time.sleep(resync_after)
                    self._sync_playback()
                except Exception:
                    pass
        threading.Thread(target=worker, daemon=True).start()

    def action_next(self):
        self._run_playback_call(self.spotify.next, resync_after=0.4)

    def action_play_pause(self):
        # Toggle from cached state (refreshed at least every 1.5s by
        # _sync_playback) instead of blocking the UI on a fresh get_playback().
        was_playing = bool(getattr(self, '_now_internal_is_playing', False))
        new_playing = not was_playing
        self._now_internal_is_playing = new_playing
        self._now_internal_last_wall_ms = _mono() * 1000.0
        try:
            self._update_now_bar()
        except Exception:
            pass
        self._run_playback_call(self.spotify.resume if new_playing else self.spotify.pause, resync_after=0.4)

    def action_prev_logic(self):
        progress = self._cached_pos_ms()
        if progress > 3000:
            self._run_playback_call(lambda: self.spotify.seek_ms(0), resync_after=0.4)
        else:
            self._run_playback_call(self.spotify.prev, resync_after=0.4)

    def _seek_step_ms(self, typ: Optional[str]) -> int:
        try:
            track_s = int((config.LOCAL_CFG.get('seek_seconds_track') if isinstance(config.LOCAL_CFG, dict) else None) or 5)
        except (TypeError, ValueError):
            track_s = 5
        try:
            episode_s = int((config.LOCAL_CFG.get('seek_seconds_episode') if isinstance(config.LOCAL_CFG, dict) else None) or 15)
        except (TypeError, ValueError):
            episode_s = 15
        return (episode_s * 1000) if typ == "episode" else (track_s * 1000)

    def action_seek_back(self):
        item = getattr(self, '_bar_last_track', None) or {}
        delta_ms = self._seek_step_ms(item.get('type'))
        new_pos = max(0, self._cached_pos_ms() - delta_ms)
        self._now_internal_pos_ms = new_pos
        self._now_internal_last_wall_ms = _mono() * 1000.0
        try:
            self._update_now_bar()
        except Exception:
            pass
        self._run_playback_call(lambda: self.spotify.seek_ms(new_pos), resync_after=0.35)

    def action_seek_fwd(self):
        item = getattr(self, '_bar_last_track', None) or {}
        delta_ms = self._seek_step_ms(item.get('type'))
        dur = int(getattr(self, '_now_internal_dur_ms', 0) or 0)
        new_pos = self._cached_pos_ms() + delta_ms
        if dur and new_pos > dur:
            new_pos = max(0, dur - 200)
        self._now_internal_pos_ms = new_pos
        self._now_internal_last_wall_ms = _mono() * 1000.0
        try:
            self._update_now_bar()
        except Exception:
            pass
        self._run_playback_call(lambda: self.spotify.seek_ms(new_pos), resync_after=0.35)

    def action_toggle_shuffle(self):
        new_state = not bool(getattr(self, '_now_shuffle', False))
        self._now_shuffle = new_state
        self._run_playback_call(lambda: self.spotify.shuffle(new_state))

    def action_toggle_repeat(self):
        self.repeat_idx = (self.repeat_idx + 1) % len(self.repeat_state_cycle)
        new_state = self.repeat_state_cycle[self.repeat_idx]
        self._now_repeat_state = new_state
        self._run_playback_call(lambda: self.spotify.repeat(new_state))

    def action_volume_down(self):
        vol = max(0, min(100, int(getattr(self, '_now_volume', 50) or 50) - 5))
        self._now_volume = vol
        self._run_playback_call(lambda: self.spotify.set_volume(vol))

    def action_volume_up(self):
        vol = max(0, min(100, int(getattr(self, '_now_volume', 50) or 50) + 5))
        self._now_volume = vol
        self._run_playback_call(lambda: self.spotify.set_volume(vol))

    def action_toggle_mute(self):
        vol = int(getattr(self, '_now_volume', 0) or 0)
        if vol == 0:
            target = 50
            self._now_volume = target
            self._notify(f"[b]Volume restored to {target}%[/b]")
            self._run_playback_call(lambda: self.spotify.set_volume(target))
        else:
            self._now_volume = 0
            self._notify("[b]Muted[/b]")
            self._run_playback_call(lambda: self.spotify.set_volume(0))

    @staticmethod
    def _call_first(sp, names, *args):
        """Call the first available method name on `sp` (spotipy version drift)."""
        for nm in names:
            try:
                if hasattr(sp, nm):
                    return getattr(sp, nm)(*args)
            except Exception:
                logger.debug("method %s failed", nm)
        return None

    @staticmethod
    def _call_first_ok(sp, names, *args):
        """Like _call_first but returns True only if a matching method existed
        and ran without raising. Used by favourite toggles so a failed mutation
        is not reported as success (P6)."""
        for nm in names:
            if hasattr(sp, nm):
                try:
                    getattr(sp, nm)(*args)
                    return True
                except Exception:
                    logger.exception("method %s failed", nm)
                    return False
        return False

    def _fav_lock_for(self, rid):
        """Return a per-id lock so favourite read-modify-write on the SAME item
        is serialized (two quick presses can't both read the pre-state and issue
        duplicate ops), while different ids stay independent and concurrent (P7)."""
        guard = getattr(self, '_fav_locks_guard', None)
        if guard is None:
            self._fav_locks_guard = threading.Lock(); guard = self._fav_locks_guard
        with guard:
            locks = getattr(self, '_fav_locks', None)
            if locks is None:
                locks = self._fav_locks = {}
            lk = locks.get(rid)
            if lk is None:
                lk = locks[rid] = threading.Lock()
            return lk

    def _revalidate_saved_if_search(self, table):
        """After a favourite toggle in search results, re-check its saved column."""
        if table is None or getattr(table, 'id', '') != 'search_table':
            return
        try:
            threading.Thread(target=lambda: self._revalidate_saved_column(table, max_rows=200), daemon=True).start()
        except Exception:
            try:
                self._revalidate_saved_column(table, max_rows=200)
            except Exception:
                pass

    def _toggle_track_favorite(self, tid, table, row):
        def worker():
            # P7: serialize read-modify-write per track id.
            lock = self._fav_lock_for(tid)
            lock.acquire()
            try:
                try:
                    was_liked = bool(self.spotify.check_saved_tracks([tid])[0])
                except Exception:
                    was_liked = False
                if was_liked:
                    ok = self.spotify.remove_tracks([tid]); new_liked = False
                else:
                    ok = self.spotify.save_tracks([tid]); new_liked = True

                if not ok:
                    # P6: mutation failed -> never show a confirmed state.
                    try:
                        self.call_from_thread(lambda: self._notify('[b]Could not update Liked Songs[/b]', warn=True))
                    except Exception:
                        pass
                    return

                def paint():
                    try:
                        if table is not None and row is not None:
                            if not hasattr(table, '_liked_map'):
                                table._liked_map = {}
                            table._liked_map[row] = new_liked
                            try: self._set_heart_icon(table, row, new_liked)
                            except Exception: pass
                        try: self._sync_liked_state_across_tables(tid, new_liked)
                        except Exception: pass
                        self._notify(f"[b]{'Added to' if new_liked else 'Removed from'} Liked Songs[/b]")
                    except Exception:
                        logger.exception('paint after toggle favorite failed')
                try:
                    self.call_from_thread(paint)
                except Exception:
                    if getattr(self, '_closing', False):
                        logger.debug("toggle favorite paint dropped during teardown")
                    else:
                        logger.exception("toggle favorite paint: call_from_thread failed")

                # P6: revalidate the search saved-column only after success.
                self._revalidate_saved_if_search(table)
            except Exception:
                logger.exception('toggle track favorite failed')
            finally:
                lock.release()
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_saved_item(self, sp, item_id, *, contains_name, add_names, del_names, label, view_key, refresh_fn, table=None):
        """Add/remove an album, show or episode from the library (shared shape)."""
        def worker():
            # P7: serialize read-modify-write per item id.
            lock = self._fav_lock_for(item_id)
            lock.acquire()
            try:
                try:
                    c = self._call_first(sp, [contains_name], [item_id])
                    was = bool(c[0]) if isinstance(c, list) else bool(c)
                except Exception:
                    was = False
                if was:
                    ok = self._call_first_ok(sp, del_names, [item_id]); new = False
                else:
                    ok = self._call_first_ok(sp, add_names, [item_id]); new = True
                if not ok:
                    # P6: mutation failed -> never show a confirmed state.
                    try:
                        self.call_from_thread(lambda: self._notify(f"[b]Could not update {label}[/b]", warn=True))
                    except Exception:
                        pass
                    return
                try:
                    self.call_from_thread(lambda: self._notify(f"[b]{'Saved' if new else 'Removed from'} {label}[/b]"))
                except Exception:
                    pass
                try:
                    rv = getattr(self, '_right_view', None)
                    if rv and rv[0] == view_key:
                        threading.Thread(target=refresh_fn, daemon=True).start()
                except Exception:
                    pass
                # P6: revalidate the search saved-column only after success.
                self._revalidate_saved_if_search(table)
            except Exception:
                logger.exception('%s save/remove failed', label)
            finally:
                lock.release()
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_artist_favorite(self, sp, aid, table=None):
        def worker():
            # P7: serialize read-modify-write per artist id.
            lock = self._fav_lock_for(aid)
            lock.acquire()
            try:
                following = False
                try:
                    page = sp.current_user_followed_artists(limit=50) or {}
                    for a in (page.get('artists') or {}).get('items', []):
                        if a and a.get('id') == aid:
                            following = True; break
                except Exception:
                    following = False
                if following:
                    ok = self._call_first_ok(sp, ['current_user_unfollow_artists', 'user_unfollow_artists', 'unfollow_artists'], [aid]); new = False
                else:
                    ok = self._call_first_ok(sp, ['current_user_follow_artists', 'user_follow_artists', 'follow_artists'], [aid]); new = True
                if not ok:
                    # P6: mutation failed -> never show a confirmed state.
                    try:
                        self.call_from_thread(lambda: self._notify('[b]Could not update Artist follow[/b]', warn=True))
                    except Exception:
                        pass
                    return
                try:
                    self.call_from_thread(lambda: self._notify(f"[b]{'Followed' if new else 'Unfollowed'} Artist[/b]"))
                except Exception:
                    pass
                try:
                    rv = getattr(self, '_right_view', None)
                    if rv and rv[0] == 'artists':
                        threading.Thread(target=self._open_saved_artists, daemon=True).start()
                except Exception:
                    pass
                # P6: revalidate the search saved-column only after success.
                self._revalidate_saved_if_search(table)
            except Exception:
                logger.exception('artist follow/unfollow failed')
            finally:
                lock.release()
        threading.Thread(target=worker, daemon=True).start()

    def action_toggle_favorite(self):
        try:
            focused = getattr(self, 'focused', None)
            table = row = rtype = item_id = uri = None

            if isinstance(focused, DataTable):
                table = focused
                row = self._get_cursor_row(table)
                if row is not None:
                    try:
                        model_rows = getattr(table, '_model_rows', []) or []
                        obj = model_rows[row] if row < len(model_rows) else {}
                    except Exception:
                        obj = {}
                    rtype = getattr(table, 'row_to_type', {}).get(row) or (obj.get('type') if isinstance(obj, dict) else None)
                    item_id = getattr(table, 'row_to_id', {}).get(row) or (obj.get('id') if isinstance(obj, dict) else None)
                    uri = getattr(table, 'row_to_uri', {}).get(row) or (obj.get('uri') if isinstance(obj, dict) else None)

            # Resolved straight from the focused row: dispatch (mutation already
            # runs on its own worker).
            if item_id or uri:
                self._toggle_favorite_dispatch(rtype, item_id, uri, table, row)
                return

            # P5: nothing focused -> resolve the currently-playing item. Prefer
            # the cached now-playing state (kept fresh by _sync_playback) so the
            # UI thread never blocks; only if that is empty fall back to a
            # get_playback() lookup, and run it on a worker so this keypress
            # returns immediately.
            cached = getattr(self, '_bar_last_track', None) or {}
            c_id, c_uri = cached.get('id'), cached.get('uri')
            if c_id or c_uri:
                self._toggle_favorite_dispatch(cached.get('type'), c_id, c_uri, None, None)
                return

            def _resolve_worker():
                try:
                    item = (self.spotify.get_playback() or {}).get('item') or {}
                except Exception:
                    logger.exception('toggle favorite: get_playback failed')
                    item = {}
                r_type, r_id, r_uri = item.get('type'), item.get('id'), item.get('uri')
                if not r_id and not r_uri:
                    try:
                        self.call_from_thread(lambda: self._notify('[b]No item selected or playing[/b]', warn=True))
                    except Exception:
                        pass
                    return
                try:
                    self.call_from_thread(lambda: self._toggle_favorite_dispatch(r_type, r_id, r_uri, None, None))
                except Exception:
                    self._toggle_favorite_dispatch(r_type, r_id, r_uri, None, None)
            threading.Thread(target=_resolve_worker, daemon=True).start()
        except Exception:
            logger.exception("action_toggle_favorite failed")

    def _toggle_favorite_dispatch(self, rtype, item_id, uri, table, row):
        try:
            if not item_id and uri:
                try: item_id = self.spotify._normalize_track_id(uri)
                except Exception: pass
            if not item_id and not uri:
                try: self._notify('[b]No item selected or playing[/b]', warn=True)
                except Exception: pass
                return

            sp = self.spotify.ensure()
            typ = (str(rtype or '')).lower()
            if not typ and uri and 'track' in uri:
                typ = 'track'
            rid = item_id or (self.spotify._normalize_track_id(uri) if uri else None)

            if typ in ('track', 'single', ''):
                if not rid:
                    self._notify('[b]Could not determine track id[/b]', warn=True); return
                self._toggle_track_favorite(rid, table, row)
            elif typ == 'album':
                if not rid:
                    self._notify('[b]Could not determine album id[/b]', warn=True); return
                self._toggle_saved_item(sp, rid,
                    contains_name='current_user_saved_albums_contains',
                    add_names=['current_user_saved_albums_add', 'current_user_saved_albums_save'],
                    del_names=['current_user_saved_albums_delete', 'current_user_saved_albums_remove'],
                    label='Saved Albums', view_key='albums', refresh_fn=self._open_saved_albums, table=table)
            elif typ == 'artist':
                if not rid:
                    self._notify('[b]Could not determine artist id[/b]', warn=True); return
                self._toggle_artist_favorite(sp, rid, table=table)
            elif typ in ('podcast', 'show'):
                if not rid:
                    self._notify('[b]Could not determine show id[/b]', warn=True); return
                self._toggle_saved_item(sp, rid,
                    contains_name='current_user_saved_shows_contains',
                    add_names=['current_user_saved_shows_add', 'current_user_saved_shows_save'],
                    del_names=['current_user_saved_shows_delete', 'current_user_saved_shows_remove'],
                    label='Saved Podcasts', view_key='podcasts', refresh_fn=self._open_saved_podcasts, table=table)
            elif typ == 'episode':
                if not rid:
                    self._notify('[b]Could not determine episode id[/b]', warn=True); return
                self._toggle_saved_item(sp, rid,
                    contains_name='current_user_saved_episodes_contains',
                    add_names=['current_user_saved_episodes_add', 'current_user_saved_episodes_save'],
                    del_names=['current_user_saved_episodes_delete', 'current_user_saved_episodes_remove'],
                    label='Saved Episodes', view_key='episodes', refresh_fn=self._open_saved_episodes, table=table)
            else:
                try: self._notify('[b]Favorite action not supported for this item type[/b]')
                except Exception: pass
                return
            # P6: the search saved-column revalidation is now chained inside each
            # toggle worker, after the mutation succeeds (no longer fired here,
            # where it raced the mutation).
        except Exception:
            logger.exception("_toggle_favorite_dispatch failed")
