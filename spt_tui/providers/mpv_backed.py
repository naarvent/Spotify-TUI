"""Shared playback for providers whose audio is played locally by ``mpv``.

Subsonic and YouTube Music have no remote "player" to control: we play their
streams ourselves through :class:`spt_tui.players.mpv.MpvPlayer`. This base
class turns the UI's Spotify-style playback calls (``start_playback`` with a
context URI or a list of URIs, ``pause``, ``next``, ``seek_ms``...) into mpv
commands, and turns mpv's state back into a Spotify-shaped ``get_playback()``
dict so the progress bar, the now-playing line and the lyrics view work as is.

Subclasses implement :meth:`stream_url`, :meth:`fetch_track` and
:meth:`context_tracks`, and call :meth:`_remember` on every track they hand to
the UI so a URI can later be resolved back to its metadata.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional

from ..config import logger
from ..players.mpv import MpvError, MpvPlayer
from .base import Capabilities, Provider, ProviderError, ProviderUnsupported

MPV_DEVICE_ID = "spt-tui-mpv"


class MpvBackedProvider(Provider):
    # Extra mpv command-line arguments (YouTube needs the ytdl format).
    MPV_ARGS: tuple = ()
    # This many tracks failing in a row (no track played in between) means the problem is the
    # network / yt-dlp / the service, not those tracks: none of them is marked unavailable.
    FAIL_STREAK_LIMIT = 3
    # How many upcoming queue entries get their direct stream URL resolved ahead (needs _resolver).
    PREFETCH_AHEAD = 3
    # queue(): how many upcoming tracks of the context ("Next from ...") are listed; the user's own
    # queued tracks are always listed in full.
    # None = `queue_context_limit` from spt_config.json (QUEUE_CONTEXT_DEFAULT,
    # clamped to 20–1000), so the Queue view can fill the panel.
    QUEUE_CONTEXT_LIMIT = None
    QUEUE_CONTEXT_DEFAULT = 300

    def _queue_context_limit(self) -> int:
        if self.QUEUE_CONTEXT_LIMIT is not None:
            return int(self.QUEUE_CONTEXT_LIMIT)
        from .. import config
        cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
        try:
            n = int(cfg.get("queue_context_limit") or self.QUEUE_CONTEXT_DEFAULT)
        except (TypeError, ValueError):
            n = self.QUEUE_CONTEXT_DEFAULT
        return max(20, min(1000, n))

    def __init__(self, *, player: Optional[MpvPlayer] = None) -> None:
        super().__init__()
        self._player = player if player is not None else MpvPlayer(extra_args=self.MPV_ARGS)
        self._known: Dict[str, dict] = {}      # uri -> track
        self._url_to_track: Dict[str, dict] = {}
        self._repeat = "off"
        self._shuffle = False
        self._meta_lock = threading.Lock()
        self._notices: List[str] = []
        self._last_started_id: Optional[str] = None
        self._fail_streak: List[str] = []      # uris of consecutive load failures (see on_track_error)
        # Optional LocalLibrary: play history, availability marks (and, for
        # account-less providers, the whole library; see LocalLibraryMixin).
        self.library = None
        # Optional StreamResolver (spt_tui/stream_cache.py), set by YouTube Music. When None,
        # nothing below about "direct" URLs runs and playback is exactly as without it.
        self._resolver = None
        self._direct_to_watch: Dict[str, str] = {}   # direct stream URL -> its watch URL
        self._queued_ids: List[int] = []            # mpv entry ids the user queued, not played yet
        self._playing_queued: List[int] = []        # queued entry ids that are (or were) playing
        self._queue_lock = threading.RLock()        # reorders vs. reaping played queued entries
        self._order: List[str] = []                 # watch URLs in unshuffled queue order
        self._queue_version = 0
        self._window_key = None
        self._window_pending: List[str] = []
        self._window_lock = threading.RLock()
        # Optional OfflineStore (spt_tui/offline.py), set by providers that can
        # download (YouTube Music, Subsonic). A track with an offline copy is
        # handed to mpv as its local file: no stream URL, no yt-dlp, no network.
        self.offline = None

    # ------------------------------------------------------------------ #
    # To implement
    # ------------------------------------------------------------------ #
    def stream_url(self, track: dict) -> str:
        raise NotImplementedError

    def fetch_track(self, track_id: str) -> dict:
        return {}

    def context_tracks(self, context_uri: str) -> List[dict]:
        return []

    def playback_unavailable_reason(self) -> Optional[str]:
        """Why local playback cannot work (missing mpv...), or None."""
        return self._player.unavailable_reason()

    # ------------------------------------------------------------------ #
    # Capabilities
    # ------------------------------------------------------------------ #
    def _playback_caps(self, base: Capabilities) -> Capabilities:
        if self.playback_unavailable_reason():
            return base.replace(can_play=False, can_control_playback=False, can_seek=False,
                                can_set_volume=False, can_shuffle_repeat=False,
                                can_control_queue=False, can_list_devices=False)
        return base

    def status_notice(self) -> Optional[str]:
        reason = self.playback_unavailable_reason()
        if reason:
            return f"{self.display_name}: playback disabled — {reason} Browsing still works."
        return None

    def unsupported_message(self, capability: str) -> str:
        reason = self.playback_unavailable_reason()
        if reason and capability in ("can_play", "can_control_playback", "can_seek",
                                     "can_set_volume", "can_shuffle_repeat",
                                     "can_control_queue", "can_list_devices"):
            return f"Playback is disabled: {reason}"
        return super().unsupported_message(capability)

    def close(self) -> None:
        r, self._resolver = self._resolver, None
        if r is not None:
            try:
                r.close()
            except Exception:
                logger.exception("closing the stream resolver failed")
        try:
            self._player.close()
        except Exception:
            logger.exception("closing mpv failed")

    # ------------------------------------------------------------------ #
    # Track bookkeeping
    # ------------------------------------------------------------------ #
    def _remember(self, track: dict) -> dict:
        if isinstance(track, dict) and track.get("uri"):
            with self._meta_lock:
                self._known[track["uri"]] = track
        return track

    def _lookup(self, uri: str) -> Optional[dict]:
        with self._meta_lock:
            t = self._known.get(uri)
        if t:
            return t
        tid = self._normalize_track_id(uri)
        if not tid:
            return None
        # An offline copy carries its own metadata: playing it after a restart
        # (or with no network) must not need fetch_track.
        st = self.offline
        if st is not None:
            try:
                t = st.track(tid)
                if t and t.get("uri") == uri:
                    return self._remember(t)
            except Exception:
                logger.exception("%s: offline metadata lookup failed", self.name)
        lib = self.library
        if lib is not None:
            stored = lib.find_track(tid)
            if stored:
                stored.pop("added_at", None)
                return self._remember(stored)
        try:
            t = self.fetch_track(tid) or None
        except Exception:
            logger.exception("%s: could not resolve %s", self.name, uri)
            t = None
        return self._remember(t) if t else None

    # ------------------------------------------------------------------ #
    # Offline copies
    # ------------------------------------------------------------------ #
    def _offline_path(self, track: dict) -> Optional[str]:
        st = self.offline
        if st is None or not isinstance(track, dict) or not track.get("id"):
            return None
        try:
            return st.path_for(track["id"])
        except Exception:
            logger.exception("%s: offline lookup failed", self.name)
            return None

    def is_downloaded(self, track_id) -> bool:
        st = self.offline
        try:
            return bool(st is not None and st.has(track_id))
        except Exception:
            return False

    def _download_file(self, track: dict, dest_base: str) -> str:
        """Download ``track``'s audio to ``dest_base`` + an extension and
        return the file written. Subclasses implement it."""
        raise ProviderUnsupported(self.unsupported_message("can_download"))

    def download_track(self, track: dict) -> str:
        """Make an offline copy (blocking; call from a worker). Returns the path."""
        st = self.offline
        if st is None:
            raise ProviderUnsupported(self.unsupported_message("can_download"))
        if not isinstance(track, dict) or not track.get("id"):
            raise ProviderError("This item cannot be downloaded.")
        have = st.path_for(track["id"])
        if have:
            return have
        track = self._complete_for_offline(track)
        base = st.new_partial(track["id"])
        try:
            produced = self._download_file(track, base)
        except Exception as exc:
            # Never leave half a file behind (.partial/<id>.*).
            import glob
            for f in glob.glob(glob.escape(base) + "*"):
                try: os.remove(f)
                except OSError: pass
            if isinstance(exc, (ProviderError, ProviderUnsupported)):
                raise
            raise ProviderError(f"Download failed ({type(exc).__name__}).") from exc
        # _download_file may return (path, extra metadata) — yt-dlp knows the
        # duration; otherwise the file itself is probed.
        extra = {}
        if isinstance(produced, tuple):
            produced, extra = produced[0], dict(produced[1] or {})
        if not int(track.get("duration_ms") or 0):
            ms = int(extra.get("duration_ms") or 0)
            if not ms:
                from ..offline import probe_duration_ms
                ms = probe_duration_ms(produced)
            if ms:
                track = dict(track, duration_ms=ms)
        try:
            from ..offline import OfflineError
            return st.add(track, produced)
        except OfflineError as exc:
            raise ProviderError(f"Could not keep the download: {exc}.") from exc

    def _complete_for_offline(self, track: dict) -> dict:
        """Fill what a table row lost (duration, album, artists) from the
        provider's own copy of the track, without any request."""
        with self._meta_lock:
            known = self._known.get(track.get("uri")) if track.get("uri") else None
        if not known:
            return track
        out = dict(track)
        for k in ("duration_ms", "album", "artists", "name"):
            if not out.get(k) or (k == "duration_ms" and not int(out.get(k) or 0)):
                if known.get(k):
                    out[k] = known[k]
        return out

    def offline_backfill(self, should_stop=None) -> int:
        """Durations for copies stored without one (probing the files)."""
        st = self.offline
        return st.backfill_durations(should_stop=should_stop) if st is not None else 0

    def offline_missing_durations(self) -> int:
        st = self.offline
        return len(st.missing_durations()) if st is not None else 0

    def remove_download(self, track_id) -> bool:
        st = self.offline
        return bool(st is not None and st.remove(track_id))

    def offline_tracks(self) -> List[dict]:
        st = self.offline
        return st.tracks() if st is not None else []

    def offline_entries(self) -> List[dict]:
        st = self.offline
        return st.entries() if st is not None else []

    def offline_usage(self):
        """(bytes used, cap in bytes, number of tracks) or None without offline support."""
        st = self.offline
        if st is None:
            return None
        return st.total_bytes(), st.limit, len(st.ids())

    def _url_for(self, track: dict) -> str:
        local = self._offline_path(track)
        if local:
            try:
                self.offline.touch(track["id"])
            except Exception:
                pass
            with self._meta_lock:
                self._url_to_track[local] = track
            return local
        url = self.stream_url(track)
        with self._meta_lock:
            self._url_to_track[url] = track
        return url

    # ------------------------------------------------------------------ #
    # Direct stream URLs (only with self._resolver)
    #
    # mpv's queue holds watch URLs (stable identity). When the resolver has the direct audio URL
    # of an UPCOMING entry, that entry is swapped in place for the direct URL (never the one
    # playing), so mpv skips yt-dlp when it gets there. _url_to_track maps both URLs to the
    # track, _direct_to_watch maps the direct one back. Any error here is logged and ignored:
    # playback then simply uses the watch URLs, as without a resolver.
    # ------------------------------------------------------------------ #
    def _playable(self, track: dict) -> str:
        url = self._url_for(track)
        r = self._resolver
        if r is None or os.path.isabs(url):
            return url          # no resolver, or an offline copy (a local file)
        try:
            direct = None if self._is_marked_unavailable(track) else r.get(url)
        except Exception:
            logger.exception("%s: stream cache lookup failed", self.name)
            direct = None
        if direct:
            self._register_direct(direct, url)
            return direct
        return url

    def _register_direct(self, direct: str, watch: str) -> None:
        with self._meta_lock:
            self._direct_to_watch[direct] = watch
            t = self._url_to_track.get(watch)
            if t is not None:
                self._url_to_track[direct] = t

    def _window_from(self, entries: List[dict]) -> List[str]:
        """Filenames of the next PREFETCH_AHEAD entries after the current one, in mpv's real
        order (so shuffle is respected), wrapping around with repeat=context."""
        cur = next((i for i, e in enumerate(entries) if e.get("current")), None)
        if cur is None or self._repeat == "track":
            return []
        n, out = len(entries), []
        for k in range(1, self.PREFETCH_AHEAD + 1):
            j = cur + k
            if j >= n:
                if self._repeat != "context":
                    break
                j %= n
            if j == cur:
                break
            fn = entries[j].get("filename")
            if fn:
                out.append(fn)
        return out

    def _substitute(self, watch: str, direct: str) -> None:
        replace = getattr(self._player, "replace_entry", None)
        if replace is None:
            return
        # Re-read just before: the user may have skipped since the window was computed.
        for i, e in enumerate(self._player.playlist_entries()):
            if e.get("filename") == watch and not e.get("current"):
                if replace(i, direct, watch):
                    self._register_direct(direct, watch)
                    self._remap_queued(e.get("id"), i)

    def _remap_queued(self, old_id, index: int) -> None:
        """replace_entry gives the entry a NEW mpv id: a queued entry must stay queued."""
        with self._meta_lock:
            if old_id not in self._queued_ids:
                return
        try:
            entries = self._player.playlist_entries()
            new_id = entries[index].get("id") if 0 <= index < len(entries) else None
        except Exception:
            logger.exception("%s: reading mpv playlist entries failed", self.name)
            new_id = None
        with self._meta_lock:
            self._queued_ids = [new_id if q == old_id else q for q in self._queued_ids
                                if not (q == old_id and new_id is None)]

    def _queue_changed(self) -> None:
        if self._resolver is None:
            return
        self._queue_version += 1
        self._prefetch_tick()

    def _prefetch_tick(self, st: Optional[dict] = None) -> None:
        """Recompute the prefetch window when the position / queue / repeat changed, and swap in
        any direct URL that became ready. Never raises."""
        r = self._resolver
        if r is None:
            return
        try:
            with self._window_lock:
                if st is None:
                    st = self._player.state()
                if not st.get("running") or st.get("idle") or st.get("paused"):
                    return
                key = (st.get("index"), self._repeat, self._queue_version)
                if key != self._window_key:
                    wanted = []
                    for fn in self._window_from(self._player.playlist_entries()):
                        with self._meta_lock:
                            direct_already = fn in self._direct_to_watch
                            track = self._url_to_track.get(fn)
                        if direct_already or track is None or self._is_marked_unavailable(track):
                            continue
                        if os.path.isabs(fn):
                            continue        # an offline copy: nothing to resolve
                        wanted.append(fn)
                    r.prefetch(wanted)
                    self._window_key = key
                    self._window_pending = wanted
                for watch in list(self._window_pending):
                    direct = r.get(watch)
                    if direct:
                        self._window_pending.remove(watch)
                        self._substitute(watch, direct)
        except Exception:
            logger.exception("%s: stream prefetch failed (playing without it)", self.name)

    def _direct_failed(self, entry_id, direct: str, watch: str) -> None:
        """mpv could not play a DIRECT URL (expired, 403, IP changed by a VPN...). That says
        nothing about the track: forget the URL and put the watch URL back in the same place.
        If mpv has just moved past it (to the following entry) or went idle, play it again from
        there, so the track the user expected still plays (via yt-dlp, as without prefetch)."""
        try:
            self._resolver.invalidate(watch, failed=True)
        except Exception:
            logger.exception("%s: stream cache invalidate failed", self.name)
        with self._meta_lock:
            self._direct_to_watch.pop(direct, None)
        try:
            entries = self._player.playlist_entries()
            idx = next((i for i, e in enumerate(entries) if e.get("id") == entry_id), None)
            if idx is None or not self._player.replace_entry(idx, watch, direct):
                return
            self._remap_queued(entry_id, idx)
            entries = self._player.playlist_entries()
            n = len(entries)
            cur = next((i for i, e in enumerate(entries) if e.get("current")), None)
            if cur is None or (n and cur == (idx + 1) % n and cur != idx):
                self._player.play_index(idx)
            self._queue_version += 1
        except Exception:
            logger.exception("%s: restoring the watch URL failed", self.name)

    def _fix_unshuffle(self) -> None:
        """mpv's playlist-unshuffle puts entries inserted after the shuffle (our swapped ones)
        first. Put everything back in the order the queue was built (self._order)."""
        try:
            entries = self._player.playlist_entries()
            with self._meta_lock:
                keys = [self._direct_to_watch.get(e.get("filename"), e.get("filename")) for e in entries]
            slots: Dict[str, List[int]] = {}
            for i, w in enumerate(self._order):
                slots.setdefault(w, []).append(i)
            ranked = []
            for i, (e, w) in enumerate(zip(entries, keys)):
                s = slots.get(w)
                ranked.append((s.pop(0) if s else len(self._order) + i, e.get("id")))
            ids = [eid for _, eid in sorted(ranked, key=lambda x: x[0])]
            if ids != [e.get("id") for e in entries]:
                self._player.reorder(ids)
        except Exception:
            logger.exception("%s: restoring the unshuffled order failed", self.name)

    # ------------------------------------------------------------------ #
    # Playback
    # ------------------------------------------------------------------ #
    def _notice(self, msg: str) -> None:
        with self._meta_lock:
            if self._notices and self._notices[-1] == msg:
                return          # the same message twice in a row says nothing new
            self._notices.append(msg)
            del self._notices[:-20]

    def take_notices(self) -> List[str]:
        with self._meta_lock:
            out, self._notices = self._notices, []
        return out

    def _is_marked_unavailable(self, track: dict) -> bool:
        # Only a FRESH mark counts. A stale one may have been a network hiccup, and a skipped
        # track never gets the chance to clear its own mark, so it would stay hidden forever.
        lib = self.library
        return bool(lib is not None and track.get("uri") and lib.unavailable_recent(track["uri"]))

    def on_track_started(self, track: dict) -> None:
        """A track really started playing: record it and clear any stale
        "unavailable" mark (it plays again, so it is available)."""
        lib = self.library
        with self._meta_lock:
            self._fail_streak.clear()
        if lib is None or not track:
            return
        try:
            lib.record_played(track)
            if track.get("uri"):
                lib.clear_unavailable(track["uri"])
        except Exception:
            logger.exception("%s: recording play history failed", self.name)

    def on_track_error(self, track: dict, reason: str) -> None:
        """mpv could not play ``track`` (removed / private video, server
        error) and moved on. Mark it, never delete it, and tell the user."""
        name = (track or {}).get("name") or "a track"
        uri = (track or {}).get("uri")
        lib = self.library
        with self._meta_lock:
            if uri and uri not in self._fail_streak:
                self._fail_streak.append(uri)
            n = len(self._fail_streak)
            streak = list(self._fail_streak)
        if n >= self.FAIL_STREAK_LIMIT:
            # Everything is failing at once: not the tracks' fault. Undo the marks this streak made
            # and stop marking until something plays again.
            if lib is not None:
                for u in streak:
                    try:
                        lib.clear_unavailable(u)
                    except Exception:
                        logger.exception("%s: clearing a streak mark failed", self.name)
            if n == self.FAIL_STREAK_LIMIT:
                self._notice(f"{n} tracks in a row failed to load, so they are not marked unavailable. "
                             "Check your connection (or update yt-dlp) and try again.")
            return
        self._notice(f"Skipped unavailable track: {name}")
        if lib is not None and uri:
            try:
                lib.mark_unavailable(uri, reason or "unavailable")
            except Exception:
                logger.exception("%s: marking unavailable failed", self.name)

    def recently_played(self, limit=50) -> dict:
        lib = self.library
        if lib is None:
            return {"items": []}
        return {"items": lib.recent(limit)}

    def _check_playback(self) -> None:
        reason = self.playback_unavailable_reason()
        if reason:
            raise ProviderError(f"Playback is disabled: {reason}")

    def start_playback(self, *, context_uri: Optional[str] = None,
                       uris: Optional[List[str]] = None, offset: Optional[dict] = None):
        self._check_playback()
        start = 0
        tracks: List[dict] = []
        if context_uri:
            tracks = [self._remember(t) for t in (self.context_tracks(context_uri) or []) if t]
            if isinstance(offset, dict):
                if isinstance(offset.get("position"), int):
                    start = offset["position"]
                elif offset.get("uri"):
                    start = next((i for i, t in enumerate(tracks)
                                  if t.get("uri") == offset["uri"]), 0)
        elif uris:
            for u in uris:
                t = self._lookup(u)
                if t:
                    tracks.append(t)
        if not tracks:
            raise ProviderError("Nothing playable was found.")
        # Tracks already known to be unavailable are skipped (with a notice)
        # when playing a list; a single explicit track is always retried.
        if len(tracks) > 1:
            chosen = tracks[start] if 0 <= start < len(tracks) else None
            kept = [t for t in tracks if not self._is_marked_unavailable(t) or t is chosen]
            skipped = len(tracks) - len(kept)
            if skipped:
                self._notice(f"Skipped {skipped} unavailable track(s).")
                start = kept.index(chosen) if chosen in kept else 0
                tracks = kept
        self._last_started_id = None
        if self._resolver is not None:
            # A new queue: drop pending resolutions and the old queue's direct entries (the
            # cache keeps the URLs themselves: still valid for the same tracks).
            try:
                self._resolver.cancel_pending()
            except Exception:
                logger.exception("%s: stream cache cancel failed", self.name)
            with self._meta_lock:
                for d in self._direct_to_watch:
                    self._url_to_track.pop(d, None)
                self._direct_to_watch.clear()
            self._window_pending = []
            urls = [self._playable(t) for t in tracks]
            with self._meta_lock:
                self._order = [self._direct_to_watch.get(u, u) for u in urls]
        else:
            urls = [self._url_for(t) for t in tracks]
        with self._meta_lock:
            self._queued_ids = []         # a new play replaces the whole queue, the user's too
            self._playing_queued = []
        try:
            self._player.load(urls, start)
        except MpvError as exc:
            raise ProviderError(f"mpv: {exc}") from exc
        self._queue_changed()

    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except MpvError:
            logger.exception("%s: mpv command failed", self.name)

    def pause(self) -> None:
        self._safe(self._player.pause)

    def resume(self) -> None:
        self._safe(self._player.resume)

    def next(self) -> None:
        r = self._resolver
        if r is not None:
            # The next entry is still a watch URL but its direct URL is cached: swap it now.
            try:
                nxt = self._window_from(self._player.playlist_entries())[:1]
                with self._meta_lock:
                    is_direct = bool(nxt) and nxt[0] in self._direct_to_watch
                direct = r.get(nxt[0]) if nxt and not is_direct else None
                if direct:
                    self._substitute(nxt[0], direct)
            except Exception:
                logger.exception("%s: swapping in the next stream failed", self.name)
        self._safe(self._player.next)
        self._queue_changed()

    def prev(self) -> None:
        self._safe(self._player.prev)
        self._queue_changed()

    def seek_ms(self, ms: int) -> None:
        self._safe(self._player.seek, max(0, int(ms or 0)) / 1000.0)

    def set_volume(self, volume: int) -> None:
        self._safe(self._player.set_volume, volume)

    def repeat(self, state: str) -> None:
        self._repeat = state if state in ("off", "context", "track") else "off"
        self._safe(self._player.set_loop, self._repeat)
        self._queue_changed()

    def shuffle(self, state: bool) -> None:
        self._shuffle = bool(state)
        with self._queue_lock:            # nobody reaps the queue while it is scattered
            self._prune_queue()
            self._safe(self._player.command, "playlist-shuffle" if state else "playlist-unshuffle")
            if self._resolver is not None and not state:
                self._fix_unshuffle()
            # mpv's shuffle moves the current entry too: with it in the middle, everything before it
            # would never play. Like Spotify, the current track stays first and the whole rest of
            # the context follows (the user's queue right after it).
            self._pin_queue(current_first=bool(state))
        if self._resolver is not None:
            self._queue_changed()

    # ------------------------------------------------------------------ #
    # "Add to queue" = play next, after the ones already queued (like Spotify), not at the very end.
    # A queued entry is consumed like in Spotify: once mpv has moved past it, it is REMOVED from
    # mpv's playlist, so shuffle / repeat=context never bring it back as part of the context.
    # ------------------------------------------------------------------ #
    def _entries(self) -> List[dict]:
        """mpv's playlist entries, or [] when mpv is not running / the player cannot list them."""
        fn = getattr(self._player, "playlist_entries", None)
        try:
            return list(fn()) if callable(fn) and self._player.is_running() else []
        except Exception:
            logger.exception("%s: reading mpv playlist entries failed", self.name)
            return []

    @staticmethod
    def _current_index(entries: List[dict]) -> Optional[int]:
        """Index of the entry mpv is on (its ``current`` flag): the real position, even when the
        same song is in the playlist more than once."""
        return next((i for i, e in enumerate(entries) if e.get("current")), None)

    def _enqueue_next(self, url: str) -> None:
        with self._queue_lock:
            self._prune_queue()
            entries = self._entries()
            cur = self._current_index(entries)
            insert_at = getattr(self._player, "insert_at", None)
            if cur is None or insert_at is None:
                self._player.append(url)          # nothing playing: this starts it
                return
            with self._meta_lock:
                ahead = [i for i, e in enumerate(entries)
                         if e.get("id") in self._queued_ids and i > cur]
            new_id = insert_at(url, (max(ahead) if ahead else cur) + 1)
            if new_id is not None:
                with self._meta_lock:
                    self._queued_ids.append(new_id)

    def _prune_queue(self) -> None:
        """Bring the user's queue up to date with mpv: a queued entry that is now current moves to
        "playing"; one mpv has moved past (or a "playing" one that is no longer current) is removed
        from mpv's playlist; ids that are gone are forgotten. Call it BEFORE reordering mpv's
        playlist. Does nothing while no entry is current (idle / between two entries). Never raises."""
        try:
            with self._queue_lock:
                with self._meta_lock:
                    if not self._queued_ids and not self._playing_queued:
                        return
                entries = self._entries()
                cur = self._current_index(entries)
                if cur is None:
                    return
                cur_id = entries[cur].get("id")
                pos = {e.get("id"): i for i, e in enumerate(entries)}
                with self._meta_lock:
                    pending, playing, played = [], [], []
                    for q in self._queued_ids:
                        i = pos.get(q)
                        if i is None:
                            continue
                        if q == cur_id:
                            playing.append(q)
                        elif i > cur:
                            pending.append(q)
                        else:
                            played.append(q)
                    for q in self._playing_queued:
                        if q == cur_id:
                            if q not in playing:
                                playing.append(q)
                        elif q in pos:
                            played.append(q)
                    self._queued_ids, self._playing_queued = pending, playing
                if played:
                    remove = getattr(self._player, "remove_ids", None)
                    if callable(remove) and remove(played):
                        self._queue_version += 1      # indexes moved: recompute the prefetch window
        except Exception:
            logger.exception("%s: updating the queue failed", self.name)

    def _pin_queue(self, current_first: bool = False) -> None:
        """Put the user's queued entries right after the current one, in the order they were added
        (shuffle / unshuffle scatter them). With ``current_first`` the current entry also moves to
        the top, so every other entry is still ahead of it. Never raises."""
        try:
            entries = self._entries()
            with self._meta_lock:
                queued = list(self._queued_ids)
            cur = self._current_index(entries)
            if cur is None or not (queued or (current_first and cur > 0)):
                return
            ids = [e.get("id") for e in entries]
            cur_id = ids[cur]
            q = [x for x in queued if x in ids and x != cur_id]
            if current_first:
                rest_before = [cur_id]
                rest_after = [x for x in ids if x != cur_id and x not in q]
            else:
                rest_before = [x for x in ids[:cur + 1] if x not in q]
                rest_after = [x for x in ids[cur + 1:] if x not in q]
            want = rest_before + q + rest_after
            if want != ids:
                self._player.reorder(want)
        except Exception:
            logger.exception("%s: re-pinning the queue failed", self.name)

    def add_to_queue(self, uri: str) -> None:
        self._check_playback()
        t = self._lookup(uri)
        if not t:
            raise ProviderError("Could not resolve that item.")
        try:
            url = self._playable(t)
            self._enqueue_next(url)
        except MpvError as exc:
            raise ProviderError(f"mpv: {exc}") from exc
        if self._resolver is not None:
            with self._meta_lock:
                self._order.append(self._direct_to_watch.get(url, url))
            self._queue_changed()

    def _current_track(self) -> Optional[dict]:
        path = self._player.get("path") if self._player.is_running() else None
        if not path:
            return None
        with self._meta_lock:
            return self._url_to_track.get(path)

    def on_playback_state(self, track: Optional[dict], position_ms: int,
                          duration_ms: int, is_playing: bool) -> None:
        """Hook called on every state read (Subsonic scrobbles from here)."""
        return None

    def _drain_player_errors(self) -> None:
        take = getattr(self._player, "take_errors", None)
        errs = take() if callable(take) else []
        if not errs:
            return
        by_id = {}
        try:
            by_id = {e.get("id"): e.get("filename") for e in self._player.playlist_entries()}
        except Exception:
            logger.exception("%s: reading mpv playlist entries failed", self.name)
        for err in errs:
            url = by_id.get(err.get("playlist_entry_id"))
            with self._meta_lock:
                track = self._url_to_track.get(url) if url else None
                watch = self._direct_to_watch.get(url) if url else None
            if watch is not None and self._resolver is not None:
                # A stale direct URL failed, not the track: no mark, no streak, no notice.
                self._direct_failed(err.get("playlist_entry_id"), url, watch)
                continue
            self.on_track_error(track or {}, str(err.get("file_error") or "unavailable"))

    def get_playback(self) -> dict:
        try:
            self._drain_player_errors()
        except Exception:
            logger.exception("%s: draining mpv errors failed", self.name)
        self._prune_queue()               # a queued track that already played leaves mpv's playlist
        st = self._player.state()
        if not st.get("running") or st.get("idle"):
            self.on_playback_state(None, 0, 0, False)
            return {}
        self._prefetch_tick(st)
        track = self._current_track()
        if not track:
            return {}
        mpv_dur = float(st.get("duration") or 0)
        dur_ms = int(round(mpv_dur * 1000)) or int(track.get("duration_ms") or 0)
        pos_ms = int(round(float(st.get("position") or 0) * 1000))
        # While mpv is still opening the stream (yt-dlp resolving, buffering)
        # there is no duration yet: report "not playing" so the bar does not
        # extrapolate a position that then jumps back.
        loading = mpv_dur <= 0 and pos_ms <= 0
        is_playing = not bool(st.get("paused")) and not loading
        if is_playing and track.get("id") != self._last_started_id:
            self._last_started_id = track.get("id")
            self.on_track_started(track)
        item = dict(track)
        if dur_ms:
            item["duration_ms"] = dur_ms
        try:
            self.on_playback_state(track, pos_ms, dur_ms, is_playing)
        except Exception:
            logger.exception("%s: playback hook failed", self.name)
        return {
            "is_playing": is_playing,
            "progress_ms": pos_ms,
            "item": item,
            "currently_playing_type": "track",
            "shuffle_state": self._shuffle,
            "repeat_state": self._repeat,
            "device": self._device(volume=st.get("volume")),
        }

    def play_queue_position(self, position: int) -> None:
        """Jump to the ``position``-th track (0-based) of what ``queue()`` lists, i.e. the one the
        user picked in the Queue view. Entries skipped on the way are dropped from the user's
        queue by ``_prune_queue`` like any played one."""
        self._check_playback()
        self._drain_player_errors()
        self._prune_queue()
        entries = self._entries()
        cur = self._current_index(entries)
        if cur is None:
            raise ProviderError("Nothing is playing.")
        n = len(entries)
        order = list(range(cur + 1, n))
        if self._repeat == "context":
            order += list(range(0, cur))
        with self._meta_lock:
            known = [i for i in order if self._url_to_track.get(entries[i].get("filename"))]
        if not (0 <= position < len(known)):
            raise ProviderError("That queue entry is no longer there.")
        self._safe(self._player.play_index, known[position])
        self._queue_changed()

    def queue(self) -> dict:
        """Spotify-shaped ``{"currently_playing": track, "queue": [track, ...]}`` in the order mpv
        will really play (shuffle and repeat=context included). The user's own queued tracks come
        first and carry ``"_queued": True`` (all of them); the rest of the context follows, at most
        QUEUE_CONTEXT_LIMIT. Nothing already played and never the current entry; with
        repeat=track only the user's queue is listed (the context does not advance)."""
        try:
            self._drain_player_errors()   # a failed direct URL is restored before the queue is read
        except Exception:
            logger.exception("%s: draining mpv errors failed", self.name)
        self._prune_queue()
        entries = self._entries()
        cur = self._current_index(entries)
        if cur is None:
            return {"currently_playing": self._current_track() if entries else None, "queue": []}
        n = len(entries)
        order = list(range(cur + 1, n))
        if self._repeat == "context":
            order += list(range(0, cur))
        with self._meta_lock:
            queued = set(self._queued_ids)
            current = self._url_to_track.get(entries[cur].get("filename"))
            look = [(self._url_to_track.get(entries[i].get("filename")), entries[i].get("id") in queued)
                    for i in order]
        mine, rest = [], []
        limit = self._queue_context_limit()
        for t, is_q in look:
            if not t:
                continue
            if is_q:
                item = dict(t)
                item["_queued"] = True
                mine.append(item)
            elif self._repeat != "track" and len(rest) < limit:
                rest.append(dict(t))
        return {"currently_playing": current, "queue": mine + rest}

    def _device(self, volume=None) -> dict:
        return {"id": MPV_DEVICE_ID, "name": f"mpv ({self.display_name})", "type": "Computer",
                "is_active": True, "volume_percent": int(volume if volume is not None else 0)}

    def devices(self) -> List[dict]:
        if self.playback_unavailable_reason():
            return []
        return [self._device(volume=getattr(self._player, "_volume", 0))]
