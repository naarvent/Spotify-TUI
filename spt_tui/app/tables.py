"""DataTable building, rendering and liked/saved column upkeep."""

from __future__ import annotations

import time
import threading
import unicodedata
from typing import Dict, List, Optional

from textual.widgets import DataTable
from ..widgets import WrapTable
from textual.css.query import NoMatches
from textual.coordinate import Coordinate
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

from ..config import logger
from ..constants import GLYPHS

class _SkipRow(Exception):
    """Internal: a row the view filter hides (see _append_track_rows)."""


class TablesMixin:
    # Colour used to mark the row that is currently playing.
    PLAYING_STYLE = "bold #b388ff"

    # Column profiles for tracks tables: (labels, fields). Widths come from the
    # field catalogue below, so the same column means the same thing in every
    # table and the fitter can reason about it by name.
    _TRACKS_PROFILES = {
        # "dl": the 1-cell offline column (↓ downloaded, ⇣ downloading,
        # · queued), always before the heart (app/offline.py).
        "playlist": (["↓", "♥", "Title", "Artist", "Album", "Duration", "Added"],
                     ["dl", "heart", "title", "artist", "album", "dur", "added"]),
        "recent":   (["↓", "♥", "Title", "Artist", "Album", "Duration", "Played"],
                     ["dl", "heart", "title", "artist", "album", "dur", "added"]),
        "album":    (["↓", "♥", "Title", "Artist", "Duration"],
                     ["dl", "heart", "title", "artist", "dur"]),
        "radio":    (["↓", "♥", "Title", "Artist", "Album", "Duration"],
                     ["dl", "heart", "title", "artist", "album", "dur"]),
    }

    def _track_cells(self, r: Dict, liked: bool, playing: bool, fields):
        """Build the cells for a tracks_table row (purple if playing) for the
        given field list (see _TRACKS_PROFILES)."""
        heart = Text("❤", style="bold red") if liked else Text("")
        style = self.PLAYING_STYLE if playing else None

        def c(val):
            val = val or ""
            return Text(val, style=style) if style else val

        cell_map = {
            "dl": self._dl_cell(r),
            "heart": heart,
            "title": c(r.get("title", "")),
            "artist": c(r.get("artist", "")),
            "album": c(r.get("album", "")),
            "dur": c(r.get("dur", "")),
            "added": c(r.get("added", "")),
            "source": c(r.get("source", "")),
        }
        return [cell_map[f] for f in fields]

    def _refresh_playing_highlight(self):
        """Re-paint the visible tracks table so the playing row is marked purple."""
        try:
            table = self.query_one("#tracks_table", DataTable)
        except NoMatches:
            return
        try:
            if getattr(table, "_model_rows", None):
                self._repaint_rows_from_model(table)
        except Exception:
            logger.exception("_refresh_playing_highlight failed")

    def _content_title(self, title) -> str:
        """Plain-text (markup-stripped) title for a content table's integrated
        border header — e.g. '[b]Album:[/b] X' -> 'Album: X'."""
        try:
            return Text.from_markup(str(title)).plain
        except Exception:
            return str(title)

    def _set_table_title(self, table_id: str, title) -> None:
        """Update the content view's title, shown on #right's frame (streaming
        progress). No-op if the given content table is not the one on screen."""
        try:
            tables = self.query(f"#{table_id}")
            if len(tables) > 0:
                table = tables.first()
                table._view_title = title
                self._refresh_table_title(table)
        except Exception:
            pass

    def _render_tracks_table(self, title: str, rows: List[Dict], liked_bools: Optional[List[bool]] = None, *, context_uri: Optional[str] = None, context_uris: Optional[List[str]] = None, profile: str = "playlist"):
        right = self._clear_right()
        col_labels, fields = self._TRACKS_PROFILES.get(
            profile, self._TRACKS_PROFILES["playlist"])
        table = self._create_table_with_full_width(
            col_labels, fields, widget_id="tracks_table", fields_attr="_track_fields",
        )
        # The heart's column index depends on whether the narrow fit kept the
        # "dl" column; _heart_col looks it up from the surviving fields.
        fields = getattr(table, "_track_fields", fields)
        table._col_heart = self._heart_col(table)
        table.row_to_uri = {}; table.row_to_title = {}; table.row_to_id = {}
        playing_id = getattr(self, "_now_internal_track_id", None)
        for i, r in enumerate(rows):
            liked = bool(liked_bools and i < len(liked_bools) and liked_bools[i])
            playing = bool(r.get("id") and r.get("id") == playing_id)
            table.add_row(*self._track_cells(r, liked, playing, fields), key=i)
            table.row_to_uri[i] = r["uri"]
            table.row_to_title[i] = f"{r['title']} {GLYPHS['sep']} {r['artist']}"
            if r.get("id"): table.row_to_id[i] = r["id"]
        table._context_uri = context_uri
        table._context_uris = list(context_uris) if context_uris else None
        table._model_rows = rows
        table._liked_map = {i: (bool(liked_bools[i]) if liked_bools and i < len(liked_bools) else False) for i in range(len(rows))}
        # The borderless table borrows #right's frame (single clean border, no
        # grey bleed): #right shows the title and drops its padding.
        self.right_panel.add_class("table-view")
        table._view_title = title
        self._refresh_table_title(table)
        def _after_mount():
            table.focus()
            self._refit_after_mount(table)
        self._mount_in_right(right, table, _after_mount)
        self.level = self.LVL_VIEW
        return table

    def _append_track_rows(self, table: DataTable, new_rows: List[Dict]) -> None:
        """Append a page of tracks to an already-rendered tracks table.

        Progressive loaders call this once per fetched page. Going through
        _repaint_rows_from_model instead would re-add every earlier row on each
        page (O(n²) add_row calls on a long playlist) and clear/restore the
        cursor each time; appending only touches the new rows.
        """
        if not new_rows:
            return
        for attr in ("row_to_uri", "row_to_title", "row_to_id"):
            if not hasattr(table, attr):
                setattr(table, attr, {})
        liked_map = getattr(table, "_liked_map", None)
        if liked_map is None:
            liked_map = table._liked_map = {}
        model = getattr(table, "_model_rows", None)
        if model is None:
            model = table._model_rows = []
        start = len(model)
        playing_id = getattr(self, "_now_internal_track_id", None)
        fields = getattr(table, "_track_fields",
                         ["dl", "heart", "title", "artist", "album", "dur", "added"])
        tokens = self._filter_tokens(getattr(table, "_filter_query", "") or "")
        vis = getattr(table, "_filter_rows", None)
        if tokens and vis is None:
            vis = table._filter_rows = []
        for j, r in enumerate(new_rows):
            i = start + j
            liked = bool(liked_map.get(i, False))
            playing = bool(r.get("id") and r.get("id") == playing_id)
            shown = (not tokens) or self._row_matches_filter(r, tokens)
            if shown and tokens:
                vis.append(i)
            try:
                if not shown:
                    raise _SkipRow
                table.add_row(*self._track_cells(r, liked, playing, fields), key=i)
            except _SkipRow:
                pass
            except Exception:
                heart = Text("❤", style="bold red") if liked else Text("")
                try:
                    table.add_row(heart, r.get("title", ""), key=i)
                except Exception:
                    pass
            table.row_to_uri[i] = r.get("uri")
            table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
            if r.get("id"):
                table.row_to_id[i] = r.get("id")
            liked_map.setdefault(i, False)
        model.extend(new_rows)
        ctx = getattr(table, "_context_uris", None)
        if ctx is not None:
            ctx.extend(r.get("uri") for r in new_rows)
        if tokens:
            self._refresh_table_title(table)
        try: table.refresh()
        except Exception: pass

    # --- "/" filter over the rows already loaded ---------------------------- #
    # Row keys and every row_to_* map stay keyed by the MODEL index; the filter
    # only decides which of those rows are added to the DataTable. The display
    # row -> model index translation lives in _get_cursor_row (navigation.py),
    # so Enter, f, Ctrl+D, Multi-Add… keep addressing the real track and a
    # filtered row plays from its real position in the playlist/album.
    FILTERABLE_TABLES = ("tracks_table", "search_table", "queue_table", "devices_table")

    def _normalize_id_safe(self, uri):
        try:
            return self.spotify._normalize_track_id(uri) if uri else None
        except Exception:
            return None
    # Typing repaints after this pause, not on every key (a long playlist
    # re-adds thousands of rows per repaint).
    FILTER_DEBOUNCE_S = 0.12

    @staticmethod
    def _fold_text(s) -> str:
        """Lower-case, accent-free text for matching ("Beyoncé" ~ "beyonce")."""
        s = unicodedata.normalize("NFKD", str(s or ""))
        return "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()

    @classmethod
    def _filter_tokens(cls, query) -> List[str]:
        return cls._fold_text(query).split()

    @classmethod
    def _row_matches_filter(cls, row, tokens) -> bool:
        """Every word of the filter appears in the row's title, artist or album."""
        if not tokens:
            return True
        if not isinstance(row, dict):
            return False
        hay = cls._fold_text(" ".join(str(row.get(k) or "") for k in ("title", "artist", "album")))
        return all(t in hay for t in tokens)

    def _visible_indices(self, table, rows) -> Optional[List[int]]:
        """Model indices that pass the table's filter, or None when unfiltered."""
        tokens = self._filter_tokens(getattr(table, "_filter_query", "") or "")
        if not tokens:
            return None
        return [i for i, r in enumerate(rows or []) if self._row_matches_filter(r, tokens)]

    def _model_to_display_row(self, table, model_idx) -> Optional[int]:
        """Where model row ``model_idx`` sits on screen (None if filtered out)."""
        vis = getattr(table, "_filter_rows", None)
        if vis is None:
            return model_idx
        try:
            return vis.index(model_idx)
        except ValueError:
            return None

    def _filterable_table(self, widget=None):
        """The content table "/" filters: the focused one, else the one on screen."""
        w = widget if widget is not None else getattr(self, "focused", None)
        if isinstance(w, DataTable) and getattr(w, "id", "") in self.FILTERABLE_TABLES \
                and hasattr(w, "_model_rows"):
            return w
        if isinstance(w, DataTable):
            return None
        for tid in self.FILTERABLE_TABLES:
            try:
                t = self.query_one(f"#{tid}", DataTable)
            except Exception:
                continue
            if hasattr(t, "_model_rows") and getattr(t, "parent", None) is getattr(self, "right_panel", None):
                return t
        return None

    def _filter_input_for(self, table):
        inp = getattr(table, "_filter_input", None)
        if inp is not None and getattr(inp, "parent", None) is not None:
            return inp
        return None

    def _open_view_filter(self, table=None) -> bool:
        """`/` on a content table: show (or re-focus) its filter box."""
        table = table if table is not None else self._filterable_table()
        if table is None:
            return False
        from ..widgets import ViewFilterInput
        inp = self._filter_input_for(table)
        if inp is None:
            inp = ViewFilterInput(placeholder="Filter loaded rows (title, artist, album)  ·  "
                                              "Enter/↓ keep  ·  Esc clear",
                                  classes="view-filter")
            inp._filter_table = table
            table._filter_input = inp
            parent = getattr(table, "parent", None)
            if parent is None:
                return False
            try:
                parent.mount(inp, before=table)
            except Exception:
                logger.exception("mounting the view filter failed")
                return False
            inp.value = getattr(table, "_filter_query", "") or ""
        try:
            inp.focus()
        except Exception:
            pass
        self._notify("[b]Filter:[/b] type to narrow the loaded rows. Enter/↓ back to the list, Esc clears.")
        return True

    def _schedule_view_filter(self, inp, value: str) -> None:
        """Input.Changed on the filter box: repaint after a short pause."""
        table = getattr(inp, "_filter_table", None)
        if table is None:
            return
        old = getattr(self, "_filter_timer", None)
        if old is not None:
            try: old.stop()
            except Exception: pass
        self._filter_timer = self.set_timer(
            self.FILTER_DEBOUNCE_S, lambda: self._apply_view_filter(table, value))

    def _apply_view_filter(self, table, query: str, *, announce: bool = True) -> None:
        """Set the filter text of ``table`` and repaint only the matching rows."""
        self._filter_timer = None
        query = (query or "").strip()
        if (getattr(table, "_filter_query", "") or "") == query and \
                getattr(table, "_filter_applied", False):
            return
        table._filter_query = query
        table._filter_applied = True
        self._repaint_rows_from_model(table)
        try:
            if table.row_count:
                col = int(getattr(getattr(table, "cursor_coordinate", None), "column", 0) or 0)
                table.move_cursor(row=0, column=col, animate=False, scroll=True)
        except Exception:
            pass
        self._refresh_table_title(table)
        if announce and query:
            shown = len(getattr(table, "_filter_rows", None) or [])
            total = len(getattr(table, "_model_rows", None) or [])
            if shown == 0:
                self._notify(f"[b]Filter:[/b] no loaded row matches “{rich_escape(query)}”.", warn=True)
            else:
                self._notify(f"[b]Filter:[/b] {shown} of {total} rows.")

    def _filter_hint(self, table) -> str:
        # Plain text: the border title is parsed as markup, so "[/ …]" would be
        # read as a closing tag and swallow the hint.
        query = getattr(table, "_filter_query", "") or ""
        vis = getattr(table, "_filter_rows", None)
        if not query or vis is None:
            return ""
        total = len(getattr(table, "_model_rows", None) or [])
        return f"   · filter “{rich_escape(query)}”: {len(vis)} of {total}"

    def _leave_view_filter(self) -> None:
        """Enter/↓ in the filter box: apply now and go back to the table."""
        inp = self.focused
        table = getattr(inp, "_filter_table", None)
        if table is None:
            return
        old = getattr(self, "_filter_timer", None)
        if old is not None:
            try: old.stop()
            except Exception: pass
        value = getattr(inp, "value", "") or ""
        self._apply_view_filter(table, value, announce=False)
        if not value.strip():
            self._close_view_filter(table)
            return
        try:
            table.focus()
        except Exception:
            pass

    def _close_view_filter(self, table=None) -> bool:
        """Esc: drop the filter, remove its box and show every row again.

        Returns False when there was nothing to clear, so Esc keeps its usual
        meaning (back to the menu)."""
        if table is None:
            w = getattr(self, "focused", None)
            table = getattr(w, "_filter_table", None) or self._filterable_table(w)
        if table is None:
            return False
        inp = self._filter_input_for(table)
        had = bool(getattr(table, "_filter_query", "")) or inp is not None
        if not had:
            return False
        old = getattr(self, "_filter_timer", None)
        if old is not None:
            try: old.stop()
            except Exception: pass
            self._filter_timer = None
        # Keep the highlighted track highlighted once every row is back.
        cur_model = None
        try:
            cur_model = self._get_cursor_row(table)
        except Exception:
            cur_model = None
        table._filter_query = ""
        table._filter_applied = False
        table._filter_input = None
        if inp is not None:
            try: inp.remove()
            except Exception: pass
        self._repaint_rows_from_model(table)
        if cur_model is not None:
            try:
                col = int(getattr(getattr(table, "cursor_coordinate", None), "column", 0) or 0)
                table.move_cursor(row=int(cur_model), column=col, animate=False, scroll=True)
            except Exception:
                pass
        self._refresh_table_title(table)
        try:
            table.focus()
        except Exception:
            pass
        self._notify("[b]Filter cleared.[/b]")
        return True

    def _fill_track_likes(self, table, rows, still_current) -> None:
        """Paint the hearts of a tracks table whose rows came without liked
        state (Downloaded, Radio). One ``check_saved_tracks`` call for all ids
        (the provider batches it). A local library (YouTube Music) answers from
        memory, so it runs right away; otherwise on a worker thread. Skipped
        when the provider has no likes. ``still_current()`` guards a late
        answer against a newer view."""
        try:
            if not self._cap("can_like"):
                return
        except Exception:
            pass
        ids = [r.get("id") for r in rows if isinstance(r, dict) and r.get("id")]
        if not ids:
            return

        def apply(id_to_liked):
            # Not "is the same rows list": s/S replace the model; the map is by id.
            if not still_current() or getattr(table, "parent", None) is None:
                return
            # Map by id, so it holds whatever order s/S or the "/" filter put the rows in.
            table._liked_map = {i: bool(id_to_liked.get(r.get("id"), False))
                                for i, r in enumerate(table._model_rows)}
            self._repaint_rows_from_model(table)

        def lookup():
            try:
                return dict(zip(ids, self.spotify.check_saved_tracks(ids) or []))
            except Exception:
                logger.debug("liked lookup failed")
                return None

        caps = getattr(getattr(self, "spotify", None), "capabilities", None)
        if getattr(caps, "local_library", False) is True:
            res = lookup()
            if res is not None:
                apply(res)
            return

        def worker():
            res = lookup()
            if res is None:
                return
            try:
                self.call_from_thread(apply, res)
            except Exception:
                if not getattr(self, "_closing", False):
                    logger.debug("liked repaint dropped")
        threading.Thread(target=worker, daemon=True).start()

    def _open_album_table(self, album_item: dict, push_stack: bool = True):
        try:
            album_id = album_item.get("id")
            album_name = album_item.get("name")
            if push_stack:
                token = self._new_view_token("album", album_id)

                rv = getattr(self, "_right_view", None)
                if rv and len(rv) >= 3:
                    self._right_view = (rv[0], rv[1], rv[2], album_item)
            else:

                self._view_counter += 1
                token = self._view_counter
                self._right_view = ("album", album_id, token, album_item)
            right = self._clear_right()
            right.update(f"[b]Loading album:[/b] {rich_escape(album_name)} …")
            def worker():
                rows = []
                tracks_page = self.spotify.ensure().album_tracks(album_id)
                for tr in tracks_page.get("items", []):
                    rows.append({
                        "id": tr.get("id"),
                        "uri": tr.get("uri"),
                        "title": tr.get("name"),
                        "artist": ", ".join(a.get("name", "") for a in tr.get("artists", [])),
                        "album": album_name,
                        "dur": self.spotify.fmt_duration(tr.get("duration_ms") or 0),
                        "raw": tr,
                    })
                # Stage 1: show the album immediately (hearts blank), guarded by the
                # view token so a slow album cannot paint over a newer view.
                def paint():
                    if not self._is_current_view("album", album_id, token):
                        return
                    title = f"[b]Album:[/b] {rich_escape(album_name)}"
                    self._render_tracks_table(title, rows, None, context_uris=[r["uri"] for r in rows], profile="album")
                self.call_from_thread(paint)

                # Stage 2: resolve liked state off the UI thread. album_tracks never
                # carries saved-state, so check_saved_tracks (id-aligned, never
                # raises) is the only source. Map liked by id — not row index — so
                # tracks without an id (local/unavailable) stay blank and a partial
                # failure only blanks its own id, never the whole album.
                ids = [r["id"] for r in rows if r.get("id")]
                liked_list = self.spotify.check_saved_tracks(ids) if ids else []
                id_to_liked = dict(zip(ids, liked_list))

                def apply_likes():
                    if not self._is_current_view("album", album_id, token):
                        return
                    try:
                        table = self.query_one("#tracks_table", DataTable)
                    except NoMatches:
                        return
                    # Only touch the table that still belongs to THIS album render.
                    if getattr(table, "_model_rows", None) is not rows:
                        return
                    table._liked_map = {i: bool(id_to_liked.get(r.get("id"), False))
                                        for i, r in enumerate(rows)}
                    self._repaint_rows_from_model(table)
                try:
                    self.call_from_thread(apply_likes)
                except Exception:
                    if getattr(self, "_closing", False):
                        logger.debug("album liked repaint dropped during teardown")
                    else:
                        logger.exception("album liked repaint: call_from_thread failed")
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            logger.exception("_open_album_table failed")

    def _open_artist_table(self, artist_item: dict, push_stack: bool = True):
        try:
            artist_id = artist_item.get("id")
            artist_name = artist_item.get("name")
            if push_stack:
                token = self._new_view_token("artist", artist_id)
                rv = getattr(self, "_right_view", None)
                if rv and len(rv) >= 3:
                    self._right_view = (rv[0], rv[1], rv[2], artist_item)
            else:
                self._view_counter += 1
                token = self._view_counter
                self._right_view = ("artist", artist_id, token, artist_item)
            right = self._clear_right()
            right.update(f"[b]Loading artist:[/b] {rich_escape(artist_name)} …")
            def worker():
                ov = self._artist_overview(artist_id, artist_name)
                rows = self._artist_page_rows(ov)
                rows.extend(self._artist_owned_playlists(artist_id, artist_name))
                n_top = sum(1 for r in rows if r.get("_section") == "top")

                def paint():
                    if not self._is_current_view("artist", artist_id, token): return
                    title = f"[b]Artist:[/b] {rich_escape(artist_name)}" + self._artist_counts(ov)
                    table = self._render_search_table(title, rows, layout="artist_page")
                    # Top tracks come first, so their model indices ARE their
                    # positions in this list: Enter on one plays the top tracks
                    # from there (see _play_row's range guard for other rows).
                    table._context_uris = [r["uri"] for r in rows[:n_top]] if n_top else None
                    table._artist_overview_notes = list(ov.get("notes") or [])
                    table._queue_name = f"{artist_name} · top tracks"
                    if not rows:
                        self._notify(f"[b]Nothing to show for[/b] {rich_escape(artist_name)}.", warn=True)
                    if ov.get("notes"):
                        self._notify("  ·  ".join(rich_escape(n) for n in ov["notes"]), warn=True,
                                     seconds=7.0)

                self.call_from_thread(paint)
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            logger.exception("_open_artist_table failed")

    # --- Artist page ------------------------------------------------------- #
    # Section label shown in the Type column of the artist page.
    _ARTIST_SECTION_LABELS = {"top": "TOP", "album": "ALB", "single": "SNG",
                              "playlist": "PLY", "related": "REL"}

    def _artist_overview(self, artist_id, artist_name) -> dict:
        """The provider's artist_overview, or the generic per-section build
        for objects without one (test fakes, adapters)."""
        from ..providers.base import build_artist_overview
        fn = getattr(self.spotify, "artist_overview", None)
        try:
            ov = fn(artist_id, artist_name=artist_name) if callable(fn) else \
                build_artist_overview(self.spotify, artist_id, artist_name)
        except Exception as exc:
            logger.exception("artist overview failed")
            ov = {"notes": [f"The artist page could not be loaded ({type(exc).__name__})."]}
        for k in ("top_tracks", "albums", "singles", "related", "notes"):
            ov.setdefault(k, [])
        return ov

    @staticmethod
    def _artist_counts(ov) -> str:
        parts = []
        for key, label in (("top_tracks", "top tracks"), ("albums", "albums"),
                           ("singles", "singles"), ("related", "related")):
            n = len(ov.get(key) or [])
            if n:
                parts.append(f"{n} {label}")
        return ("  —  " + " · ".join(parts)) if parts else ""

    def _artist_page_rows(self, ov) -> List[Dict]:
        """Rows for the artist page, in section order: top tracks, albums,
        singles, related artists. Each row keeps its real type (Enter plays a
        track, opens an album or another artist) and a section label."""
        rows: List[Dict] = []
        fmt = getattr(self.spotify, "fmt_duration", None)
        seen = set()
        for t in ov.get("top_tracks") or []:
            if not isinstance(t, dict) or not t.get("uri") or t.get("uri") in seen:
                continue
            seen.add(t.get("uri"))
            rows.append({
                "type": "track", "_section": "top", "type_label": self._ARTIST_SECTION_LABELS["top"],
                "id": t.get("id"), "uri": t.get("uri"), "title": t.get("name", "(no title)"),
                "artist": ", ".join(a.get("name", "") for a in (t.get("artists") or []) if isinstance(a, dict)),
                "album": (t.get("album") or {}).get("name", "") if isinstance(t.get("album"), dict) else "",
                "dur": fmt(t.get("duration_ms") or 0) if callable(fmt) else "",
                "raw": t,
            })
        for key, rtype in (("albums", "album"), ("singles", "single")):
            for a in ov.get(key) or []:
                if not isinstance(a, dict) or not a.get("id") or ("a", a.get("id")) in seen:
                    continue
                seen.add(("a", a.get("id")))
                year = str(a.get("release_date") or "")[:4]
                rows.append({
                    "type": rtype, "_section": rtype, "type_label": self._ARTIST_SECTION_LABELS[rtype],
                    "id": a.get("id"), "uri": a.get("uri"), "title": a.get("name", "(no title)"),
                    "artist": ", ".join(x.get("name", "") for x in (a.get("artists") or []) if isinstance(x, dict)),
                    "album": year, "dur": "", "raw": a,
                })
        for a in ov.get("related") or []:
            if not isinstance(a, dict) or not a.get("id") or ("r", a.get("id")) in seen:
                continue
            seen.add(("r", a.get("id")))
            rows.append({
                "type": "artist", "_section": "related", "type_label": self._ARTIST_SECTION_LABELS["related"],
                "id": a.get("id"), "uri": a.get("uri"), "title": a.get("name", "(no name)"),
                "artist": "", "album": a.get("_info", "") or ", ".join((a.get("genres") or [])[:2]),
                "dur": "", "raw": a,
            })
        return rows

    def _artist_owned_playlists(self, artist_id, artist_name) -> List[Dict]:
        """Playlists published by the artist's own account (Spotify-style
        search filtered by owner). Best effort: any failure is just no rows.
        limit=10: the February 2026 Web API change caps search at 10 for
        development-mode apps (a bigger limit is a 400)."""
        try:
            pls_res = self.spotify.ensure().search(artist_name, type="playlist", limit=10) or {}
            pls = (pls_res.get("playlists") or {}).get("items", []) or []
        except Exception:
            return []

        def _norm(s: Optional[str]) -> str:
            return (s or "").strip().lower()

        name_n, id_n = _norm(artist_name), _norm(artist_id)
        out = []
        for p in pls:
            if not p:
                continue
            owner = p.get("owner") or {}
            oid = _norm(owner.get("id"))
            oname = _norm(owner.get("display_name") or owner.get("id"))
            if (oid and id_n and oid == id_n) or (oname and name_n and oname == name_n) \
                    or (oid and name_n and name_n.replace(" ", "") in oid):
                out.append({
                    "type": "playlist", "_section": "playlist",
                    "type_label": self._ARTIST_SECTION_LABELS["playlist"],
                    "id": p.get("id"), "uri": p.get("uri"), "title": p.get("name", "(no name)"),
                    "artist": owner.get("display_name", ""), "album": "", "dur": "", "raw": p,
                })
        return out

    def _open_podcast_table(self, show_item: dict, push_stack: bool = True):
        try:
            show_id = show_item.get("id")
            show_name = show_item.get("name")
            if push_stack:
                token = self._new_view_token("podcast", show_id)
                rv = getattr(self, "_right_view", None)
                if rv and len(rv) >= 3:
                    self._right_view = (rv[0], rv[1], rv[2], show_item)
            else:
                self._view_counter += 1
                token = self._view_counter
                self._right_view = ("podcast", show_id, token, show_item)
            right = self._clear_right()
            right.update(f"[b]Loading podcast:[/b] {rich_escape(show_name)} …")

            def worker():
                rows = []
                try:
                    try:
                        eps_page = self.spotify.ensure().show_episodes(show_id, limit=50)
                        eps = eps_page.get("items", [])
                    except Exception:
                        eps = []

                    for ep in eps:
                        if not ep: continue
                        rows.append({
                            "id": ep.get("id"),
                            "uri": ep.get("uri"),
                            "title": ep.get("name", "(Podcast episode)"),
                            "artist": (show_name or ""),
                            "album": (show_name or ""),
                            "dur": self.spotify.fmt_duration(ep.get("duration_ms") or 0),
                            "raw": ep,
                            "type": "episode",
                        })
                except Exception:
                    logger.exception("_open_podcast_table worker failed")

                def paint():
                    if not self._is_current_view("podcast", show_id, token): return
                    title = f"[b]Podcast:[/b] {rich_escape(show_name)}"
                    table = self._render_tracks_table(title, rows, None, context_uris=[r["uri"] for r in rows], profile="album")
                    self._revalidate_liked_column(table, max_rows=200)

                self.call_from_thread(paint)

            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            logger.exception("_open_podcast_table failed")

    def _revalidate_liked_column(self, table: DataTable, max_rows: int = 100, force: bool = False):
        try:
            if getattr(table, "id", "") == "search_table":
                return
            if not force and not getattr(self, '_allow_auto_revalidate_likes', False) and not getattr(self, '_force_revalidate_likes_once', False):
                return
            if getattr(self, '_force_revalidate_likes_once', False):
                try: self._force_revalidate_likes_once = False
                except Exception: pass
            row_keys = list(getattr(table, "row_to_id", {}).keys())[:max_rows]
            ids = [table.row_to_id[k] for k in row_keys if table.row_to_id.get(k)]
            if not ids:
                return
            bools = self.spotify.check_saved_tracks(ids)
            for rk, liked in zip(row_keys, bools):
                self._set_heart_icon(table, rk, bool(liked))
            table.refresh()
        except Exception:
            logger.exception("The favorites column could not be revalidated.")

    def _revalidate_saved_column(self, table: DataTable, max_rows: int = 100):
        try:
            if getattr(table, "id", "") != "search_table":
                return
            row_keys = list(getattr(table, "row_to_id", {}).keys())[:max_rows]
            if not row_keys:
                return
            sp = self.spotify.ensure()

            by_type = {"artist": [], "album": [], "episode": [], "podcast": [], "track": [], "playlist": []}
            row_for = {}
            for rk in row_keys:
                rtype = getattr(table, 'row_to_type', {}).get(rk) or ''
                rid = getattr(table, 'row_to_id', {}).get(rk)
                if not rid:
                    try:
                        mr = getattr(table, '_model_rows', [])[rk]
                        rid = (mr or {}).get('id') if isinstance(mr, dict) else None
                    except Exception:
                        rid = None
                if not rid:
                    continue
                rtype = (rtype or '').lower()
                if rtype == 'album':
                    by_type['album'].append(rid); row_for.setdefault(('album', rid), []).append(rk)
                elif rtype in ('episode',):
                    by_type['episode'].append(rid); row_for.setdefault(('episode', rid), []).append(rk)
                elif rtype in ('podcast', 'show'):
                    by_type['podcast'].append(rid); row_for.setdefault(('podcast', rid), []).append(rk)
                elif rtype in ('artist',):
                    by_type['artist'].append(rid); row_for.setdefault(('artist', rid), []).append(rk)
                elif rtype in ('track', 'single'):
                    by_type['track'].append(rid); row_for.setdefault(('track', rid), []).append(rk)
                elif rtype == 'playlist' and hasattr(sp, 'current_user_follows_playlists_contains'):
                    by_type['playlist'].append(rid); row_for.setdefault(('playlist', rid), []).append(rk)

            results = {}
            logger.debug("_revalidate_saved_column: checking %d rows", len(row_keys))
            try:
                if by_type['album']:
                    vals = sp.current_user_saved_albums_contains(by_type['album'])
                    for rid, val in zip(by_type['album'], vals or []):
                        results[('album', rid)] = bool(val)
            except Exception:
                logger.exception('_revalidate_saved_column: albums contains failed')
            try:
                if by_type['podcast']:
                    vals = sp.current_user_saved_shows_contains(by_type['podcast'])
                    for rid, val in zip(by_type['podcast'], vals or []):
                        results[('podcast', rid)] = bool(val)
            except Exception:
                logger.exception('_revalidate_saved_column: shows contains failed')
            try:
                if by_type['track']:
                    vals = sp.current_user_saved_tracks_contains(by_type['track'])
                    logger.debug("_revalidate_saved_column: tracks contains returned %s", str(vals)[:200])
                    for rid, val in zip(by_type['track'], vals or []):
                        results[('track', rid)] = bool(val)
            except Exception:
                logger.exception('_revalidate_saved_column: tracks contains failed')
            try:
                if by_type['episode']:
                    vals = sp.current_user_saved_episodes_contains(by_type['episode'])
                    for rid, val in zip(by_type['episode'], vals or []):
                        results[('episode', rid)] = bool(val)
            except Exception:
                logger.exception('_revalidate_saved_column: episodes contains failed')
            try:
                if by_type['playlist']:
                    vals = sp.current_user_follows_playlists_contains(by_type['playlist'])
                    for rid, val in zip(by_type['playlist'], vals or []):
                        results[('playlist', rid)] = bool(val)
            except Exception:
                logger.exception('_revalidate_saved_column: playlists contains failed')
            try:
                if by_type['artist']:
                    followed = set()
                    try:
                        page = sp.current_user_followed_artists(limit=50) or {}
                        arts = (page.get('artists') or {}).get('items', [])
                        for a in arts:
                            if a and a.get('id'):
                                followed.add(a.get('id'))
                    except Exception:
                        pass
                    for rid in by_type['artist']:
                        results[('artist', rid)] = (rid in followed)
            except Exception:
                logger.exception('_revalidate_saved_column: artists check failed')

            def paint():
                try:
                    # P1: if this table came from a token-guarded search, drop
                    # the update when a newer search has superseded it or the
                    # table was removed from the tree. Tables without a search
                    # token (e.g. artist-profile view) are unaffected. A detached
                    # DataTable keeps is_mounted == True, so we test .parent.
                    tok = getattr(table, '_search_token', None)
                    if tok is not None:
                        if tok != getattr(self, '_last_search_worker', None):
                            return
                        if getattr(table, 'parent', None) is None:
                            return
                    col = getattr(table, '_col_saved', 0)
                    try:
                        model_rows = getattr(table, '_model_rows', None)
                        if isinstance(model_rows, list):
                            for key, val in results.items():
                                rows = row_for.get(key, [])
                                for rk in rows:
                                    try:
                                        if rk is not None and rk < len(model_rows) and isinstance(model_rows[rk], dict):
                                            model_rows[rk]['saved'] = bool(val)
                                            logger.debug("_revalidate_saved_column: marked model_rows[%s] saved=%s", rk, bool(val))
                                    except Exception:
                                        logger.exception('_revalidate_saved_column: could not set model_rows saved flag for rk=%s', rk)
                            try:
                                self._repaint_rows_from_model(table)
                            except Exception:
                                logger.exception('_revalidate_saved_column: repaint rows failed')
                        else:
                            for key, val in results.items():
                                rows = row_for.get(key, [])
                                for rk in rows:
                                    cell = Text("❤", style="bold red") if val else Text("")
                                    try:
                                        table.update_cell(rk, col, cell)
                                        logger.debug("_revalidate_saved_column: painted fallback row=%s col=%s val=%s", rk, col, val)
                                    except Exception:
                                        logger.exception('_revalidate_saved_column: fallback paint failed for row=%s col=%s', rk, col)
                    except Exception:
                        logger.exception('_revalidate_saved_column: paint loop failed')
                except Exception:
                    logger.exception('_revalidate_saved_column: paint failed')

            try:
                self.call_from_thread(paint)
            except Exception:
                if getattr(self, '_closing', False):
                    logger.debug("saved-column repaint dropped during teardown")
                else:
                    logger.exception("saved-column repaint: call_from_thread failed")
        except Exception:
            logger.exception('_revalidate_saved_column failed')

    def _set_heart_icon(self, table: DataTable, row_key, liked: bool):
        """Repaint one row's heart in place.

        Addressed by *coordinate*: `update_cell` takes a column **key**, and
        this passed it the column index instead, so both the call and its
        fallback raised and were swallowed — the function silently did nothing
        and every in-place heart update (per-batch playlist hearts, the `f`
        toggle, liked-column revalidation) left the row unchanged until some
        later full repaint happened to fix it.
        """
        if getattr(table, "id", "") == "search_table":
            return
        cell = Text("❤", style="bold red") if liked else Text("")
        col = self._heart_col(table)
        try:
            row = table.get_row_index(row_key)
        except Exception:
            # Rows are keyed by their position, so the key doubles as the index
            # — unless the "/" filter hides rows: then a missing key is a row
            # that is not on screen, and there is nothing to repaint.
            if getattr(table, "_filter_rows", None) is not None:
                key = getattr(row_key, "value", row_key)
                row = self._model_to_display_row(table, key) if isinstance(key, int) else None
            else:
                row = row_key if isinstance(row_key, int) else None
        if row is None:
            return
        try:
            table.update_cell_at(Coordinate(row, col), cell)
        except Exception:
            logger.debug("could not repaint the heart at row %s", row)
            return
        try: table.refresh()
        except Exception: pass

    def _restore_table_view(self, table: DataTable, cur_coord, saved_y: int, n_rows: int):
        """Restore the cursor row + vertical scroll after a full repaint. Textual
        resets both to the top on ``clear()``; without this the user is yanked
        back to the start whenever likes load, a like is toggled, or the playing
        row highlight moves. ``cursor_row`` has no setter (assigning it silently
        fails) and ``scroll_to_row`` does not exist in this Textual, so we use
        ``move_cursor(scroll=False)`` + ``scroll_to(animate=False)``."""
        if n_rows <= 0:
            return
        try:
            if cur_coord is not None:
                row = max(0, min(int(getattr(cur_coord, "row", 0) or 0), n_rows - 1))
                col = int(getattr(cur_coord, "column", 0) or 0)
                table.move_cursor(row=row, column=col, animate=False, scroll=False)
        except Exception:
            logger.debug("restore cursor failed")
        try:
            table.scroll_to(y=saved_y, animate=False)
        except Exception:
            logger.debug("restore scroll failed")

    def _repaint_rows_from_model(self, table: DataTable):
        if not hasattr(table, "_model_rows"):
            return
        cur_coord = None
        try:
            cur_coord = getattr(table, "cursor_coordinate", None)
        except Exception:
            cur_coord = None
        saved_y = 0
        try:
            saved_y = int(getattr(getattr(table, "scroll_offset", None), "y", 0) or 0)
        except Exception:
            saved_y = 0

        table.clear()
        rows = getattr(table, "_model_rows", [])
        liked_map = getattr(table, "_liked_map", {})
        if not hasattr(table, "row_to_uri"):
            table.row_to_uri = {}
        if not hasattr(table, "row_to_title"):
            table.row_to_title = {}
        if not hasattr(table, "row_to_id"):
            table.row_to_id = {}
        if not hasattr(table, "row_to_type"):
            table.row_to_type = {}
        if not hasattr(table, "row_to_obj"):
            table.row_to_obj = {}

        if getattr(table, "id", "") == "search_table":
            fields = getattr(table, "_search_fields",
                             ["saved", "type", "title", "artist", "album", "dur", "source"])
            vis = table._filter_rows = self._visible_indices(table, rows)
            shown = None if vis is None else set(vis)
            for i, r in enumerate(rows):
                try:
                    if shown is not None and i not in shown:
                        raise _SkipRow
                    table.add_row(*self._search_cells(r, fields), key=i)
                except _SkipRow:
                    pass
                except Exception:
                    saved_cell = Text("❤", style="bold red") if r.get('saved', False) else Text("")
                    try:
                        table.add_row(saved_cell, (r.get("type") or "").upper(), r.get("title", ""), key=i)
                    except Exception:
                        pass
                table.row_to_uri[i] = r.get("uri")
                table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
                if r.get("id"):
                    table.row_to_id[i] = r.get("id")
                table.row_to_type[i] = r.get("type")
                table.row_to_obj[i] = r.get("raw")
            try: table.refresh()
            except Exception: pass
            self._restore_table_view(table, cur_coord, saved_y, len(rows) if vis is None else len(vis))
            return

        if getattr(table, "id", "") == "devices_table":
            fields = getattr(table, "_device_fields", None) or ["mark", "title", "device_type"]
            vis = table._filter_rows = self._visible_indices(table, rows)
            shown = None if vis is None else set(vis)
            for i, r in enumerate(rows):
                if shown is None or i in shown:
                    try:
                        table.add_row(*[r.get(f, "") for f in fields], key=i)
                    except Exception:
                        pass
            try: table.refresh()
            except Exception: pass
            self._restore_table_view(table, cur_coord, saved_y, len(rows) if vis is None else len(vis))
            return

        if getattr(table, "id", "") == "queue_table":
            fields = getattr(table, "_queue_fields", None) or [
                "num", "dl", "heart", "title", "artist", "album", "dur", "source"]
            # Position among the real tracks (headers excluded) = what the
            # provider's queue() lists; keyed by model index like every map.
            table.row_to_qpos = {}
            n = 0
            for i, r in enumerate(rows):
                if not r.get("_header"):
                    table.row_to_qpos[i] = n
                    n += 1
            vis = table._filter_rows = self._visible_indices(table, rows)
            shown = None if vis is None else set(vis)
            liked_map = getattr(table, "_liked_map", {}) or {}
            for i, r in enumerate(rows):
                if liked_map.get(i) is not None and not r.get("_header"):
                    r["liked"] = bool(liked_map[i])
                if shown is None or i in shown:
                    try:
                        table.add_row(*self._queue_cells(r, i, fields), key=i)
                    except Exception:
                        pass
                table.row_to_uri[i] = r.get("uri")
                table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
                tid = r.get("id") or self._normalize_id_safe(r.get("uri"))
                if tid and not r.get("_header"):
                    table.row_to_id[i] = tid
            try: table.refresh()
            except Exception: pass
            self._restore_table_view(table, cur_coord, saved_y, len(rows) if vis is None else len(vis))
            return

        if getattr(table, "_panel_key", None) == "songs":
            # Combined-search Songs panel: 2 columns (heart + "Title — Artist").
            # Kept in sync here so a favourite toggle / liked lookup repaints its
            # heart without mangling the compact single-line layout.
            for i, r in enumerate(rows):
                liked = bool(liked_map.get(i, False))
                heart = Text("❤", style="bold red") if liked else Text("")
                title = r.get("title", "") or ""
                artist = r.get("artist", "") or ""
                line = f"{title} {GLYPHS['sep']} {artist}" if artist else title
                try:
                    table.add_row(heart, line, key=i)
                except Exception:
                    pass
                table.row_to_uri[i] = r.get("uri")
                table.row_to_title[i] = f"{title} {GLYPHS['sep']} {artist}"
                if r.get("id"):
                    table.row_to_id[i] = r.get("id")
                table.row_to_type[i] = r.get("type")
            try: table.refresh()
            except Exception: pass
            self._restore_table_view(table, cur_coord, saved_y, len(rows))
            return

        if getattr(table, "_panel_key", None) in ("artists", "albums", "playlists"):
            # Combined-search Artists / Albums / Playlists panels: ONE column with the name (no heart).
            # They used to fall through to the generic track layout below, whose several cells did not
            # fit the single column: every row failed to add and the panel came back EMPTY after a
            # like / follow / save.
            pk = table._panel_key
            for i, r in enumerate(rows):
                try:
                    table.add_row(self._grid_line(pk, r), key=i)
                except Exception:
                    logger.exception("search panel row repaint failed")
                table.row_to_uri[i] = r.get("uri")
                table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
                if r.get("id"):
                    table.row_to_id[i] = r.get("id")
                table.row_to_type[i] = r.get("type")
                table.row_to_obj[i] = r.get("raw")
            try: table.refresh()
            except Exception: pass
            self._restore_table_view(table, cur_coord, saved_y, len(rows))
            return

        playing_id = getattr(self, "_now_internal_track_id", None)
        fields = getattr(table, "_track_fields", ["dl", "heart", "title", "artist", "album", "dur", "added"])
        vis = table._filter_rows = self._visible_indices(table, rows)
        shown = None if vis is None else set(vis)
        for i, r in enumerate(rows):
            liked = bool(liked_map.get(i, False))
            playing = bool(r.get("id") and r.get("id") == playing_id)
            try:
                if shown is not None and i not in shown:
                    raise _SkipRow
                table.add_row(*self._track_cells(r, liked, playing, fields), key=i)
            except _SkipRow:
                pass
            except Exception:
                heart = Text("❤", style="bold red") if liked else Text("")
                try:
                    table.add_row(heart, r.get("title", ""), key=i)
                except Exception:
                    pass
            table.row_to_uri[i] = r.get("uri")
            table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
            if r.get("id"):
                table.row_to_id[i] = r.get("id")

        self._restore_table_view(table, cur_coord, saved_y, len(rows) if vis is None else len(vis))

        try: table.refresh()
        except Exception: pass

    # --- Column catalogue -------------------------------------------------- #
    # One definition per column, shared by every table (tracks, search, queue,
    # devices), keyed by field name rather than by position.

    # Identity columns: their content has a known size, so they never stretch.
    _FIXED_W = {"dl": 1, "heart": 3, "saved": 3, "num": 3, "mark": 3, "type": 7,
                "dur": 9, "added": 12, "device_type": 14}
    # Everything else splits what is left. Title carries the most weight.
    _FLEX_WEIGHT = {"title": 1.4}
    _FLEX_DEFAULT_WEIGHT = 1.0
    # Narrower than this a column shows nothing useful, so the fitter drops a
    # column instead of squeezing them all.
    _FLEX_MIN = {"title": 16, "artist": 12, "album": 12, "source": 12}
    _FLEX_DEFAULT_MIN = 10
    # Which columns may be dropped on a narrow terminal, least useful first.
    # Anything absent here (heart, Title, Duration, Type) always survives.
    # The 1-cell download column goes first: on a very narrow terminal a
    # title is worth more than the ↓ mark.
    _DROP_ORDER = ("dl", "source", "added", "album", "artist")

    def _panel_width(self) -> int:
        """Width available to a content table (the right panel's content area)."""
        right = getattr(self, "right_panel", None)
        w = 0
        if right is not None:
            cs = getattr(right, "content_size", None)
            if cs is not None:
                w = int(getattr(cs, "width", 0) or 0)
            if not w:
                sz = getattr(right, "size", None)
                if sz is not None:
                    w = int(getattr(sz, "width", 0) or 0)
        if not w:
            try:
                w = int(getattr(self, "size").width or 80)
            except Exception:
                w = 80
        return w

    # Chrome around the columns: the round border (2) plus room for the vertical
    # scrollbar (2), so a list growing past the viewport never pushes a
    # horizontal one into view. The search-grid panels are borderless and pass
    # their own value.
    _CHROME = 4
    _CHROME_BORDERLESS = 2

    def _fit_columns(self, fields: List[str], col_labels: List[str],
                     panel_w: int | None = None, chrome: int | None = None):
        """Fit a column set to the panel: drop what no longer fits, then hand
        every remaining cell to the flexible columns.

        Returns ``(kept_fields, kept_labels, widths)`` with
        ``sum(widths) == panel_w - overhead``, so the table always reaches the
        right edge — no gap, and never a horizontal scrollbar.
        """
        panel_w = int(panel_w if panel_w else self._panel_width())
        chrome = self._CHROME if chrome is None else int(chrome)
        keep = list(fields)
        keep_labels = list(col_labels)
        while True:
            # A DataTable renders each column as width + 2*cell_padding
            # (cell_padding defaults to 1), on top of the widget's own chrome.
            avail = max(12, panel_w - (chrome + 2 * len(keep)))
            fixed_total = sum(self._FIXED_W[f] for f in keep if f in self._FIXED_W)
            flex = [f for f in keep if f not in self._FIXED_W]
            rem = avail - fixed_total
            need = sum(self._FLEX_MIN.get(f, self._FLEX_DEFAULT_MIN) for f in flex)
            if flex and rem < need and len(keep) > 2:
                drop = next((f for f in self._DROP_ORDER if f in keep), None)
                if drop is not None:
                    i = keep.index(drop)
                    keep.pop(i); keep_labels.pop(i)
                    continue
            break
        return keep, keep_labels, self._spread_widths(keep, flex, avail, rem)

    def _spread_widths(self, keep: List[str], flex: List[str], avail: int, rem: int) -> List[int]:
        """Give each flexible column its minimum, then split the rest by weight.
        The rounding remainder goes to the heaviest column so the widths add up
        to `avail` exactly."""
        widths = [self._FIXED_W.get(f, 0) for f in keep]
        if not flex:
            return widths
        rem = max(0, rem)
        bases = [self._FLEX_MIN.get(f, self._FLEX_DEFAULT_MIN) for f in flex]
        base_total = sum(bases)
        if rem < base_total and base_total:
            # Nothing left to drop and still no room (a terminal narrower than
            # the app really supports): scale the minimums down. Overflowing
            # would show a horizontal scrollbar, which is worse than a cramped
            # column.
            scale = rem / float(base_total)
            bases = [max(1, int(b * scale)) for b in bases]
            while sum(bases) > rem and max(bases) > 1:
                bases[bases.index(max(bases))] -= 1
            base_total = sum(bases)
        extra = max(0, rem - base_total)
        wts = [self._FLEX_WEIGHT.get(f, self._FLEX_DEFAULT_WEIGHT) for f in flex]
        total_w = sum(wts) or 1.0
        share = [int(extra * (wt / total_w)) for wt in wts]
        share[wts.index(max(wts))] += extra - sum(share)
        for f, b, s in zip(flex, bases, share):
            widths[keep.index(f)] = b + s
        return widths

    def _hidden_columns_hint(self, table: DataTable) -> str:
        """' +Album, Added' for the columns the narrow fit dropped, else ''.

        Without it a column simply vanishes on a narrow terminal and there is no
        way to tell it ever existed."""
        spec = getattr(table, "_width_spec", None)
        if not spec or len(spec) != 2:
            return ""
        col_labels, fields = spec
        kept = set(getattr(table, "_fit_fields", []) or [])
        # The 1-cell download column is the first to go and says nothing as a
        # hint ("hidden: ↓"), so it is left out of it.
        hidden = [lbl for lbl, f in zip(col_labels, fields) if f not in kept and f != "dl"]
        return f"   (hidden: {', '.join(hidden)})" if hidden else ""

    def _refresh_table_title(self, table: DataTable) -> None:
        """Put the view's title on #right's frame, plus the hidden-column hint.
        Re-run whenever the fit changes so the hint follows the terminal size."""
        title = getattr(table, "_view_title", None)
        if title is None:
            return
        try:
            self.right_panel.border_title = (self._content_title(title)
                                             + self._hidden_columns_hint(table)
                                             + self._filter_hint(table))
        except Exception:
            logger.debug("refreshing the table title failed")

    DL_LABEL = "↓"

    def _heart_col(self, table) -> int:
        """Index of the heart / saved column among the columns on screen."""
        attr = getattr(table, "_fields_attr", None)
        fields = getattr(table, attr, None) if attr else None
        fields = fields or getattr(table, "_fit_fields", None) or []
        for name in ("heart", "saved"):
            if name in fields:
                return fields.index(name)
        return int(getattr(table, "_col_heart", 0) or 0)

    def _apply_columns(self, table: DataTable, keep_labels: List[str], widths: List[int]) -> None:
        # The download column's header is dimmed when the provider cannot
        # download (the column stays, the action explains why on the status line).
        can_dl = True
        try:
            can_dl = bool(self._cap("can_download"))
        except Exception:
            pass
        # Same for the heart when the provider has no likes: dimmed, and its
        # cells stay empty (no lookups) instead of reading as "not liked".
        can_like = True
        try:
            can_like = bool(self._cap("can_like"))
        except Exception:
            pass
        keep_labels = [Text(lbl, style=("bold cyan" if can_dl else "#555555")) if lbl == self.DL_LABEL
                       else (Text(lbl, style="#555555") if lbl == "♥" and not can_like else lbl)
                       for lbl in keep_labels]
        for lbl, wd in zip(keep_labels, widths):
            try:
                table.add_column(lbl, width=int(wd))
            except Exception:
                try: table.add_column(lbl)
                except Exception: pass

    def _refit_after_mount(self, table: DataTable) -> None:
        """Re-fit a freshly mounted table once the layout has settled.

        The right panel's content_size still holds its pre-mount value while the
        table is being built (mounting it also drops the panel's padding), so the
        first fit is a few cells short and would leave a gap on the right."""
        try:
            self.call_after_refresh(lambda: self._recompute_table_widths(table))
        except Exception:
            logger.exception("_refit_after_mount failed")

    def _create_table_with_full_width(self, col_labels: list, fields: List[str],
                                      widget_id: Optional[str] = None,
                                      fields_attr: Optional[str] = None) -> DataTable:
        try:
            keep_fields, keep_labels, widths = self._fit_columns(fields, col_labels)
            table = WrapTable(zebra_stripes=True, id=(widget_id or "table"))
            table.show_cursor = True; table.cursor_type = "row"
            # Remember the *full* column set so a widening resize can bring a
            # dropped column back without the caller being involved.
            table._width_spec = (list(col_labels), list(fields))
            table._fit_fields = list(keep_fields)
            # Where the rendering code looks the surviving fields up.
            table._fields_attr = fields_attr
            if fields_attr:
                setattr(table, fields_attr, list(keep_fields))
            self._apply_columns(table, keep_labels, widths)
            return table
        except Exception:
            logger.exception("_create_table_with_full_width failed")
            try:
                t = WrapTable(zebra_stripes=True, id=(widget_id or "table"))
                for lbl in col_labels:
                    try: t.add_column(lbl)
                    except Exception: pass
                return t
            except Exception:
                raise

    def _recompute_table_widths(self, table) -> None:
        """Re-fit a table's columns to the current panel size (on resize),
        preserving cursor and scroll. When the fit drops or restores a column the
        header is rebuilt and the rows repainted from the model; otherwise only
        the widths change. No-op if nothing changed."""
        spec = getattr(table, "_width_spec", None)
        if not spec or len(spec) != 2:
            return
        col_labels, fields = spec
        keep_fields, keep_labels, widths = self._fit_columns(fields, col_labels)
        if list(keep_fields) != list(getattr(table, "_fit_fields", []) or []):
            self._rebuild_columns(table, keep_fields, keep_labels, widths)
            return
        try:
            cols = list(table.ordered_columns)
        except Exception:
            return
        if len(cols) != len(widths):
            return
        changed = False
        for col, wd in zip(cols, widths):
            try:
                if int(getattr(col, "width", -1)) != int(wd):
                    col.width = int(wd); changed = True
            except Exception:
                pass
        if changed:
            try:
                table.refresh(layout=True)
            except Exception:
                try: table.refresh()
                except Exception: pass

    def _recompute_all_table_widths(self) -> None:
        """Re-fit every visible table (deferred resize handler, see on_resize)."""
        try:
            for t in self.query(DataTable):
                self._recompute_table_widths(t)
        except Exception:
            logger.exception("table resize recompute failed")

    def _rebuild_columns(self, table: DataTable, keep_fields: List[str],
                         keep_labels: List[str], widths: List[int]) -> None:
        """Swap a table's header for a different column set and repaint the rows.

        clear(columns=True) drops rows and columns together, so the widget is
        reused (no remount, no DuplicateIds). Cursor and scroll are captured
        before the clear wipes them and restored after the repaint."""
        try:
            try:
                cur_coord = getattr(table, "cursor_coordinate", None)
            except Exception:
                cur_coord = None
            try:
                saved_y = int(getattr(getattr(table, "scroll_offset", None), "y", 0) or 0)
            except Exception:
                saved_y = 0
            table.clear(columns=True)
            self._apply_columns(table, keep_labels, widths)
            table._fit_fields = list(keep_fields)
            attr = getattr(table, "_fields_attr", None)
            if attr:
                setattr(table, attr, list(keep_fields))
            self._repaint_rows_from_model(table)
            self._restore_table_view(table, cur_coord, saved_y,
                                     len(getattr(table, "_model_rows", []) or []))
            self._refresh_table_title(table)
        except Exception:
            logger.exception("_rebuild_columns failed")

    def _open_url_in_browser(self, url: str) -> bool:
        """Open ``url`` with the shared opener (open_command / SPT_OPEN_COMMAND,
        default `abrir`; webbrowser as a last resort). False (logged) if
        nothing could open it — callers then show the URL instead."""
        if not url:
            return False
        from ..opener import OpenURLError, open_url
        try:
            open_url(url)
            return True
        except OpenURLError as exc:
            logger.warning("could not open %s: %s", url.split("?")[0], exc)
            self._last_open_error = str(exc)
            return False

    def _play_row(self, row_key, table: DataTable):
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_play"):
            return
        try:
            ctx_uri = getattr(table, "_context_uri", None)
            ctx_list = getattr(table, "_context_uris", None)

            def resolve_index(rk):
                # DataTable.RowSelected hands a RowKey; its value is the model
                # index (rows are keyed by it, filtered or not).
                rk = getattr(rk, "value", rk)
                try:
                    return int(rk)
                except Exception:
                    pass
                try:
                    for k in (getattr(table, 'row_to_uri', {}) or {}).keys():
                        if str(k) == str(rk):
                            try:
                                return int(k)
                            except Exception:
                                return k
                except Exception:
                    pass
                try:
                    cur = self._get_cursor_row(table)
                    if cur is not None:
                        return int(cur)
                except Exception:
                    pass
                try:
                    u = getattr(table, 'row_to_uri', {}).get(rk) or getattr(table, 'row_to_id', {}).get(rk)
                    if u and ctx_list and u in ctx_list:
                        return int(ctx_list.index(u))
                except Exception:
                    pass
                return rk

            resolved_key = resolve_index(row_key)
            uri = getattr(table, 'row_to_uri', {}).get(resolved_key)
            if not uri and ctx_list is not None:
                try:
                    if isinstance(resolved_key, int) and 0 <= resolved_key < len(ctx_list):
                        uri = ctx_list[resolved_key]
                except Exception:
                    uri = None
            title = getattr(table, 'row_to_title', {}).get(resolved_key, '') or getattr(table, 'row_to_title', {}).get(row_key, "")

            # Debounce accidental double-triggers (mouse + Enter) on the same row.
            try:
                last = getattr(self, '_last_played', None) or {}
                if uri and last.get('uri') and str(uri) == str(last.get('uri')) and (time.time() - float(last.get('ts') or 0)) < 0.8:
                    return
            except Exception:
                pass

            # Cached shuffle state — no blocking get_playback() on the UI thread.
            was_shuffle = bool(getattr(self, '_now_shuffle', False))
            idx = resolve_index(row_key)
            # A playlist row knows its position in the playlist; once the view is
            # sorted that differs from the row index, and the context offset
            # must use the playlist position.
            ctx_pos = idx
            try:
                mr = (getattr(table, '_model_rows', None) or [])[int(idx)]
                if isinstance(mr, dict) and isinstance(mr.get('_pos'), int):
                    ctx_pos = mr['_pos']
            except Exception:
                pass

            def announce(msg, warn=False):
                try:
                    self.call_from_thread(lambda: self._notify(msg, warn=warn))
                except Exception:
                    pass

            # What the queue view calls the context ("Next from: <name>", its Source label).
            try:
                # A view can name itself for the queue ("Downloaded", "Radio: X",
                # the artist) instead of its full frame title with counters.
                ctx_name = getattr(table, '_queue_name', None) or self._content_title(
                    getattr(table, '_pl_base_title', None) or getattr(table, '_view_title', None) or '')
                play_context = {'name': ctx_name, 'source': self._queue_source_label(table) or 'Playlist'}
            except Exception:
                play_context = None

            def worker():
                if play_context is not None:
                    self._play_context = play_context
                try:
                    if ctx_uri is not None:
                        try:
                            self.spotify.start_playback(context_uri=ctx_uri, offset={"position": int(ctx_pos)})
                            self._last_played = {'uri': getattr(table, 'row_to_uri', {}).get(row_key) or None, 'ts': time.time()}
                            announce(f"[b]Playing from playlist:[/b] {rich_escape(title)}")
                            return
                        except Exception:
                            logger.exception("start_playback with context_uri failed; falling back")

                    # A context list that does not cover this row (the artist
                    # page: only its top tracks form the list) plays just the row.
                    in_ctx = True
                    try:
                        in_ctx = 0 <= int(idx) < len(ctx_list or [])
                    except Exception:
                        in_ctx = True
                    if ctx_list and in_ctx:
                        try:
                            ordered = ctx_list[int(idx):] + ctx_list[:int(idx)]
                        except Exception:
                            u = getattr(table, 'row_to_uri', {}).get(row_key)
                            if u and u in ctx_list:
                                i2 = ctx_list.index(u); ordered = ctx_list[i2:] + ctx_list[:i2]
                            else:
                                ordered = list(ctx_list)
                        if was_shuffle: self.spotify.shuffle(False)
                        try:
                            self.spotify.start_playback(uris=ordered)
                            self._last_played = {'uri': ordered[0] if ordered else None, 'ts': time.time()}
                        finally:
                            if was_shuffle: self.spotify.shuffle(True)
                        announce(f"[b]Playing list:[/b] {rich_escape(title)}")
                        return

                    if uri:
                        if was_shuffle: self.spotify.shuffle(False)
                        try:
                            self.spotify.start_playback(uris=[uri])
                            self._last_played = {'uri': uri, 'ts': time.time()}
                        finally:
                            if was_shuffle: self.spotify.shuffle(True)
                        announce(f"[b]Playing:[/b] {rich_escape(title)}")
                except Exception as err:
                    logger.exception("_play_row worker failed")
                    announce(f"[b]Could not play:[/b] {rich_escape(str(err))}")

            threading.Thread(target=worker, daemon=True).start()
        except Exception as err:
            try:
                self._notify(f"[b]Could not play:[/b] {rich_escape(str(err, warn=True))}")
            except Exception:
                pass
            logger.exception("_play_row failed")

    def _sync_liked_state_across_tables(self, track_id: str, liked: bool):
        try:
            tables = list(self.query(DataTable))
        except Exception:
            tables = []
        for table in tables:
            try:
                row_to_id = getattr(table, "row_to_id", {}) or {}

                changed = False
                for rk, tid in list(row_to_id.items()):
                    if tid == track_id:

                        try:
                            if not hasattr(table, '_liked_map') or table._liked_map is None:
                                table._liked_map = {}
                            table._liked_map[rk] = bool(liked)
                            changed = True
                        except Exception:
                            pass

                if changed:
                    try:

                        self._repaint_rows_from_model(table)
                    except Exception:
                        try: table.refresh()
                        except Exception: pass
            except Exception:
                logger.exception("Error updating table for track %s", track_id)
