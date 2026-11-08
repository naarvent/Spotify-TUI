"""Offline downloads in the UI: `o` / `O`, the ↓ mark and Library > Downloaded.

* `o` on a track row: download it (or, if it already has an offline copy,
  delete the copy). `O`: download every track row of the open list — only the
  rows on screen when the "/" filter is on.
* Downloads run one at a time on a single background worker (`_dl_worker`), so
  a big `O` never floods the network; the status line shows progress.
* Every track table (tracks, search results, artist page, queue) has a 1-cell
  download column before the heart (``_dl_cell``): ↓ downloaded, ⇣ being
  downloaded, · waiting; empty otherwise. It exists for EVERY provider; where
  the provider cannot download, its header is dimmed and `o`/`O` explain why.
* Library > Downloaded lists the offline copies; playing them needs no network
  (the provider hands mpv the local files, metadata comes from the store).

Only providers with ``can_download`` (YouTube Music, Subsonic) offer any of
this; elsewhere `o`/`O` show the usual "not available" notice. Storage, cap and
pruning: spt_tui/offline.py.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Dict, List, Optional

from textual.widgets import DataTable
from rich.markup import escape as rich_escape
from rich.text import Text

from .. import config
from ..config import logger

DOWNLOADED_ENTRY = "Downloaded"
OFFLINE_GLYPH = "↓"


class OfflineMixin:
    # ------------------------------------------------------------------ #
    # The ↓ mark
    # ------------------------------------------------------------------ #
    def _is_offline(self, row) -> bool:
        """Whether a table row (or track dict) has an offline copy. Cheap: a
        dict lookup in the provider's store; False for providers without it."""
        if not isinstance(row, dict) or not row.get("id"):
            return False
        if (row.get("type") or "track") != "track":
            return False
        fn = getattr(getattr(self, "spotify", None), "is_downloaded", None)
        if not callable(fn):
            return False
        try:
            return bool(fn(row["id"]))
        except Exception:
            return False

    # Download column states (1 cell, before the heart): downloaded, being
    # downloaded, waiting in the download queue, nothing.
    DL_DONE = OFFLINE_GLYPH
    DL_ACTIVE = "⇣"
    DL_QUEUED = "·"

    def _dl_state(self, row) -> str:
        """'done' / 'active' / 'queued' / '' for a table row or track dict."""
        if not isinstance(row, dict) or not row.get("id") or (row.get("type") or "track") != "track":
            return ""
        if self._is_offline(row):
            return "done"
        tid = row.get("id")
        if tid and tid == getattr(self, "_dl_active_id", None):
            return "active"
        if tid and tid in (getattr(self, "_dl_queued_ids", None) or ()):
            return "queued"
        return ""

    def _dl_cell(self, row) -> Text:
        """The cell of the download column. Empty for providers that cannot
        download (their column header is dimmed, see TablesMixin._apply_columns)."""
        st = self._dl_state(row)
        if st == "done":
            return Text(self.DL_DONE, style="bold cyan")
        if st == "active":
            return Text(self.DL_ACTIVE, style="bold #ffb454")
        if st == "queued":
            return Text(self.DL_QUEUED, style="#8a8a8a")
        return Text("")

    def _repaint_offline_marks(self) -> None:
        """After a download / removal: repaint the visible track tables."""
        try:
            tables = list(self.query(DataTable))
        except Exception:
            return
        for t in tables:
            if getattr(t, "id", "") in ("tracks_table", "search_table") and getattr(t, "_model_rows", None):
                try:
                    self._repaint_rows_from_model(t)
                except Exception:
                    logger.exception("offline mark repaint failed")

    # ------------------------------------------------------------------ #
    # Which tracks
    # ------------------------------------------------------------------ #
    @staticmethod
    def _track_from_row(r: Dict) -> Optional[Dict]:
        if not isinstance(r, dict) or not r.get("id") or (r.get("type") or "track") != "track":
            return None
        raw = r.get("raw")
        if isinstance(raw, dict) and raw.get("id") == r.get("id") and raw.get("uri"):
            return raw
        # Rows only keep display strings: carry the duration over (playlist rows
        # have `_dur_ms`, the others "m:ss") — without it every offline copy was
        # stored with duration_ms 0 and Downloaded showed 0:00.
        from ..offline import parse_clock
        ms = int(r.get("_dur_ms") or 0) or parse_clock(r.get("dur"))
        return {"id": r["id"], "uri": r.get("uri"), "type": "track", "name": r.get("title") or "",
                "artists": [{"name": a.strip()} for a in str(r.get("artist") or "").split(",") if a.strip()],
                "album": {"name": r.get("album") or ""}, "duration_ms": ms}

    def _focused_track(self) -> Optional[Dict]:
        t = getattr(self, "focused", None)
        if not isinstance(t, DataTable):
            return None
        row = self._get_cursor_row(t)
        model = getattr(t, "_model_rows", None) or []
        if not isinstance(row, int) or not (0 <= row < len(model)):
            return None
        return self._track_from_row(model[row])

    def _visible_tracks(self, table) -> List[Dict]:
        model = getattr(table, "_model_rows", None) or []
        vis = getattr(table, "_filter_rows", None)
        idx = vis if vis is not None else range(len(model))
        out = []
        for i in idx:
            t = self._track_from_row(model[i]) if 0 <= i < len(model) else None
            if t:
                out.append(t)
        return out

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def action_toggle_offline(self) -> None:
        """`o`: download the highlighted track, or delete its offline copy."""
        if not self._require_cap("can_download"):
            return
        track = self._focused_track()
        if not track:
            self._notify("[b]Highlight a song to download it[/b] (o), or open a list and press O for all.",
                         warn=True)
            return
        if self._is_offline(track):
            try:
                self.spotify.remove_download(track["id"])
            except Exception:
                logger.exception("removing an offline copy failed")
            self._notify(f"[b]Offline copy deleted:[/b] {rich_escape(track.get('name') or '')}")
            self._repaint_offline_marks()
            return
        self._enqueue_downloads([track])

    def action_download_all(self) -> None:
        """`O`: download every track row of the open list (the filtered ones
        when "/" is on)."""
        if not self._require_cap("can_download"):
            return
        t = getattr(self, "focused", None)
        if not isinstance(t, DataTable):
            t = self._filterable_table() if hasattr(self, "_filterable_table") else None
        tracks = self._visible_tracks(t) if t is not None else []
        todo = [x for x in tracks if not self._is_offline(x)]
        if not tracks:
            self._notify("[b]Open a list of songs to download them all.[/b]", warn=True)
            return
        if not todo:
            self._notify(f"[b]All {len(tracks)} songs are already downloaded.[/b]")
            return
        self._enqueue_downloads(todo)

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #
    def _enqueue_downloads(self, tracks: List[Dict]) -> None:
        q = getattr(self, "_dl_queue", None)
        if q is None:
            q = self._dl_queue = deque()
            self._dl_lock = threading.Lock()
            self._dl_done = 0
            self._dl_failed = 0
        with self._dl_lock:
            have = {t.get("id") for t in q}
            added = [t for t in tracks if t.get("id") not in have]
            q.extend(added)
            self._dl_queued_ids = {t.get("id") for t in q}
            self._dl_total = getattr(self, "_dl_total", 0) + len(added)
            running = getattr(self, "_dl_thread", None)
            if running is None or not running.is_alive():
                self._dl_thread = threading.Thread(target=self._dl_worker, name="spt-offline", daemon=True)
                self._dl_thread.start()
        self._notify(f"[b]Downloading[/b] {len(added)} song(s)… (offline copies, see Library > {DOWNLOADED_ENTRY})")

    def _dl_call(self, fn, *args) -> None:
        try:
            self.call_from_thread(fn, *args)
        except Exception:
            if not getattr(self, "_closing", False):
                logger.debug("offline: UI update dropped")

    def _dl_worker(self) -> None:
        prov = self.spotify
        while True:
            with self._dl_lock:
                stop = getattr(self, "_closing", False) or prov is not self.spotify
                if stop or not self._dl_queue:
                    if stop:
                        self._dl_queue.clear()   # provider switched / closing: drop the rest
                    self._dl_active_id = None
                    self._dl_queued_ids = set()
                    done, failed = self._dl_done, self._dl_failed
                    self._dl_total = self._dl_done = self._dl_failed = 0
                    break
                track = self._dl_queue.popleft()
                n = self._dl_done + self._dl_failed + 1
                total = self._dl_total
                self._dl_active_id = track.get("id")
                self._dl_queued_ids = {t.get("id") for t in self._dl_queue}
            self._dl_call(self._repaint_offline_marks)
            name = track.get("name") or track.get("id")
            self._dl_call(self._notify, f"[b]Downloading {n}/{total}:[/b] {rich_escape(str(name))}…")
            try:
                prov.download_track(track)
                with self._dl_lock:
                    self._dl_done += 1
                self._dl_call(self._repaint_offline_marks)
            except Exception as exc:
                logger.warning("offline download failed for %s: %s", track.get("id"), exc)
                with self._dl_lock:
                    self._dl_failed += 1
                self._dl_call(self._notify, f"[b]Download failed:[/b] {rich_escape(str(name))} — "
                                            f"{rich_escape(str(exc))}")
        if getattr(self, "_closing", False) or prov is not self.spotify:
            return
        self._dl_call(self._repaint_offline_marks)
        usage = None
        try:
            usage = prov.offline_usage()
        except Exception:
            pass
        msg = f"[b]Downloads finished:[/b] {done} ok"
        if failed:
            msg += f", {failed} failed"
        if usage:
            used, cap, count = usage
            msg += f". Offline: {count} songs, {config.format_size(used)} of {config.format_size(cap)}"
        self._dl_call(lambda: self._notify(msg + ".", warn=bool(failed), seconds=7.0))

    # ------------------------------------------------------------------ #
    # Library > Downloaded
    # ------------------------------------------------------------------ #
    def _start_offline_backfill(self) -> None:
        """Once per provider and session: probe the files of copies stored
        without a duration (older downloads), in the background, and repaint
        Downloaded when done. Never re-downloads."""
        prov = self.spotify
        done = getattr(self, "_backfill_done_for", None)
        if done is None:
            done = self._backfill_done_for = set()
        if id(prov) in done:
            return
        try:
            missing = int(prov.offline_missing_durations() or 0)
        except Exception:
            missing = 0
        if not missing:
            return
        done.add(id(prov))

        def work():
            try:
                n = prov.offline_backfill(should_stop=lambda: getattr(self, "_closing", False)
                                          or prov is not self.spotify)
            except Exception:
                logger.exception("offline duration backfill failed")
                return

            def after():
                rv = getattr(self, "_right_view", None)
                if n and rv and rv[0] == "downloaded":
                    self._open_downloaded_table(push_stack=False)
                if n:
                    self._notify(f"[b]Downloaded:[/b] durations filled in for {n} song(s).")
            self._dl_call(after)
        threading.Thread(target=work, name="spt-offline-backfill", daemon=True).start()

    def _open_downloaded_table(self, push_stack: bool = True) -> None:
        if push_stack:
            token = self._new_view_token("downloaded", "")
        else:
            self._view_counter += 1
            token = self._view_counter
            self._right_view = ("downloaded", "", token, None)
        self._start_offline_backfill()
        rows = self._downloaded_rows()
        if not self._is_current_view("downloaded", "", token):
            return
        if not rows:
            self._clear_right().update("[b]No downloaded songs yet.[/b]\n\nHighlight a song and press "
                                       "[b]o[/b], or open a list and press [b]O[/b] to download it all.")
            return
        usage = None
        try:
            usage = self.spotify.offline_usage()
        except Exception:
            pass
        extra = ""
        if usage:
            extra = f"  [dim]({config.format_size(usage[0])} of {config.format_size(usage[1])})[/dim]"
        base = f"[b]{DOWNLOADED_ENTRY}[/b] · {len(rows)} songs{extra}"
        # Sorted like a playlist (s / S), with its own remembered order.
        srows, _ = self._pl_sorted(rows, None, "downloaded")
        table = self._render_tracks_table(self._pl_title(base, "downloaded"), srows, None,
                                          context_uris=[r["uri"] for r in srows], profile="playlist")
        table._pl_base_title = base
        table._sort_profile = "downloaded"
        table._queue_name = DOWNLOADED_ENTRY
        # The rows come from the offline index, without liked state: look it up
        # like a playlist does (it used to stay blank).
        self._fill_track_likes(table, table._model_rows,
                               lambda: self._is_current_view("downloaded", "", token))

    def _downloaded_rows(self) -> List[Dict]:
        """Rows of Library > Downloaded with the hidden sort keys (``_pos`` =
        newest download first, ``_dur_ms``, ``_added_at`` ISO date, ``_size``)."""
        import datetime
        fn = getattr(self.spotify, "offline_entries", None)
        entries = fn() if callable(fn) else []
        if not entries:
            # Providers / fakes that only list tracks.
            tf = getattr(self.spotify, "offline_tracks", None)
            entries = [{"track": t, "added": 0, "size": 0} for t in (tf() if callable(tf) else [])]
        fmt = getattr(self.spotify, "fmt_duration", None)
        rows = []
        for e in entries:
            t = e.get("track") or {}
            if not t.get("uri"):
                continue
            artists = [a.get("name", "") for a in (t.get("artists") or []) if isinstance(a, dict)]
            dur = int(t.get("duration_ms") or 0)
            added = float(e.get("added") or 0)
            rows.append({"_pos": len(rows), "id": t.get("id"), "uri": t.get("uri"), "type": "track",
                         "title": t.get("name", ""), "artist": ", ".join(artists),
                         "_artist0": artists[0] if artists else "",
                         "album": (t.get("album") or {}).get("name", "") if isinstance(t.get("album"), dict) else "",
                         # Unknown duration shows blank (not 0:00) until the backfill fills it.
                         "dur": (fmt(dur) if callable(fmt) else "") if dur else "",
                         "_dur_ms": dur,
                         "_added_at": (datetime.datetime.fromtimestamp(added).isoformat(timespec="seconds")
                                       if added else ""),
                         "added": (datetime.date.fromtimestamp(added).isoformat() if added else ""),
                         "_size": int(e.get("size") or 0), "raw": t})
        return rows
