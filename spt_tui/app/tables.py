"""DataTable building, rendering and liked/saved column upkeep."""

from __future__ import annotations

import time
import threading
from typing import Dict, List, Optional

from textual.widgets import DataTable
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

class TablesMixin:
    # Colour used to mark the row that is currently playing.
    PLAYING_STYLE = "bold #b388ff"

    # Column profiles for tracks tables: (labels, fields). Widths come from the
    # field catalogue below, so the same column means the same thing in every
    # table and the fitter can reason about it by name.
    _TRACKS_PROFILES = {
        "playlist": (["♥", "Title", "Artist", "Album", "Duration", "Added"],
                     ["heart", "title", "artist", "album", "dur", "added"]),
        "recent":   (["♥", "Title", "Artist", "Album", "Duration", "Played"],
                     ["heart", "title", "artist", "album", "dur", "added"]),
        "album":    (["♥", "Title", "Artist", "Duration"],
                     ["heart", "title", "artist", "dur"]),
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
        table._col_heart = 0
        # The heart never gets dropped, so the surviving set still starts with it.
        fields = getattr(table, "_track_fields", fields)
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
        right.mount(table)
        table.focus()
        self._refit_after_mount(table)
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
                         ["heart", "title", "artist", "album", "dur", "added"])
        for j, r in enumerate(new_rows):
            i = start + j
            liked = bool(liked_map.get(i, False))
            playing = bool(r.get("id") and r.get("id") == playing_id)
            try:
                table.add_row(*self._track_cells(r, liked, playing, fields), key=i)
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
        try: table.refresh()
        except Exception: pass

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
                rows = []

                try:
                    albums_page = self.spotify.ensure().artist_albums(artist_id, album_type="album", limit=50)
                    albums = albums_page.get("items", [])
                except Exception:
                    albums = []

                seen_albums = set()
                for a in albums:
                    if not a: continue
                    aid = a.get("id")
                    if not aid or aid in seen_albums: continue
                    seen_albums.add(aid)
                    rows.append({
                        "type": "album",
                        "id": aid,
                        "uri": a.get("uri"),
                        "title": a.get("name","(no title)"),
                        "artist": ", ".join([x.get("name","") for x in (a.get("artists") or [])]),
                        "album": "",
                        "dur": "",
                        "raw": a,
                    })

                try:
                    pls_res = self.spotify.ensure().search(artist_name, type="playlist", limit=50) or {}
                    pls = (pls_res.get("playlists") or {}).get("items", [])
                except Exception:
                    pls = []

                def _norm(s: Optional[str]) -> str:
                    return (s or "").strip().lower()

                filtered = []
                artist_name_norm = _norm(artist_name)
                artist_id_norm = _norm(artist_id)
                for p in pls:
                    if not p:
                        continue
                    owner = p.get("owner") or {}
                    owner_id = _norm(owner.get("id"))
                    owner_name = _norm(owner.get("display_name") or owner.get("id"))

                    if owner_id and artist_id_norm and owner_id == artist_id_norm:
                        filtered.append(p); continue

                    if owner_name and artist_name_norm and owner_name == artist_name_norm:
                        filtered.append(p); continue

                    if owner_id and artist_name_norm.replace(" ", "") in owner_id:
                        filtered.append(p); continue

                pls_to_add = filtered
                for p in pls_to_add:
                    rows.append({
                        "type": "playlist",
                        "id": p.get("id"),
                        "uri": p.get("uri"),
                        "title": p.get("name","(sin nombre)"),
                        "artist": (p.get("owner") or {}).get("display_name", ""),
                        "album": "",
                        "dur": "",
                        "raw": p,
                    })

                try:
                    singles_page = self.spotify.ensure().artist_albums(artist_id, album_type="single", limit=50)
                    singles = singles_page.get("items", [])
                except Exception:
                    singles = []
                seen_singles = set()
                for s in singles:
                    if not s: continue
                    sid = s.get("id")
                    if not sid or sid in seen_singles: continue
                    seen_singles.add(sid)
                    rows.append({
                        "type": "single",
                        "id": sid,
                        "uri": s.get("uri"),
                        "title": s.get("name","(no title)"),
                        "artist": ", ".join([x.get("name","") for x in (s.get("artists") or [])]),
                        "album": "",
                        "dur": "",
                        "raw": s,
                    })

                def paint():
                    if not self._is_current_view("artist", artist_id, token): return
                    title = f"[b]Profile of:[/b] {rich_escape(artist_name)}"
                    # Every item here is by this one artist, so the full search
                    # layout left Artist/Owner redundant and Album/Duration blank.
                    # Use the compact heart/type/name layout instead.
                    self._render_search_table(title, rows, layout="artists")

                self.call_from_thread(paint)
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            logger.exception("_open_artist_table failed")

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

            by_type = {"artist": [], "album": [], "episode": [], "podcast": [], "track": []}
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
        col = int(getattr(table, "_col_heart", 0) or 0)
        try:
            row = table.get_row_index(row_key)
        except Exception:
            # Rows are keyed by their position, so the key doubles as the index.
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
            for i, r in enumerate(rows):
                try:
                    table.add_row(*self._search_cells(r, fields), key=i)
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
            self._restore_table_view(table, cur_coord, saved_y, len(rows))
            return

        if getattr(table, "id", "") == "queue_table":
            fields = getattr(table, "_queue_fields", None) or [
                "num", "heart", "title", "artist", "album", "dur", "source"]
            for i, r in enumerate(rows):
                try:
                    table.add_row(*self._queue_cells(r, i, fields), key=i)
                except Exception:
                    pass
                table.row_to_uri[i] = r.get("uri")
                table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
            try: table.refresh()
            except Exception: pass
            self._restore_table_view(table, cur_coord, saved_y, len(rows))
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

        playing_id = getattr(self, "_now_internal_track_id", None)
        fields = getattr(table, "_track_fields", ["heart", "title", "artist", "album", "dur", "added"])
        for i, r in enumerate(rows):
            liked = bool(liked_map.get(i, False))
            playing = bool(r.get("id") and r.get("id") == playing_id)
            try:
                table.add_row(*self._track_cells(r, liked, playing, fields), key=i)
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

        self._restore_table_view(table, cur_coord, saved_y, len(rows))

        try: table.refresh()
        except Exception: pass

    # --- Column catalogue -------------------------------------------------- #
    # One definition per column, shared by every table (tracks, search, queue,
    # devices), keyed by field name rather than by position.

    # Identity columns: their content has a known size, so they never stretch.
    _FIXED_W = {"heart": 3, "saved": 3, "num": 3, "mark": 3, "type": 7,
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
    _DROP_ORDER = ("source", "added", "album", "artist")

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
        hidden = [lbl for lbl, f in zip(col_labels, fields) if f not in kept]
        return f"   (hidden: {', '.join(hidden)})" if hidden else ""

    def _refresh_table_title(self, table: DataTable) -> None:
        """Put the view's title on #right's frame, plus the hidden-column hint.
        Re-run whenever the fit changes so the hint follows the terminal size."""
        title = getattr(table, "_view_title", None)
        if title is None:
            return
        try:
            self.right_panel.border_title = (self._content_title(title)
                                             + self._hidden_columns_hint(table))
        except Exception:
            logger.debug("refreshing the table title failed")

    def _apply_columns(self, table: DataTable, keep_labels: List[str], widths: List[int]) -> None:
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
            table = DataTable(zebra_stripes=True, id=(widget_id or "table"))
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
                t = DataTable(zebra_stripes=True, id=(widget_id or "table"))
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
        if not url:
            return False
        try:
            import webbrowser, os, subprocess, platform
            try:
                opened = webbrowser.open(url, new=2)
                if opened:
                    return True
            except Exception:
                logger.exception("webbrowser.open failed")
            plat = platform.system()
            if plat == 'Windows':
                try:
                    os.startfile(url)
                    return True
                except Exception:
                    logger.exception("os.startfile failed to open URL")
            elif plat == 'Darwin':
                try:
                    subprocess.Popen(['open', url])
                    return True
                except Exception:
                    logger.exception("open failed to open URL")
            else:
                try:
                    subprocess.Popen(['xdg-open', url])
                    return True
                except Exception:
                    logger.exception("xdg-open failed to open URL")
            return False
        except Exception:
            logger.exception("_open_url_in_browser failed")
            return False

    def _play_row(self, row_key, table: DataTable):
        try:
            ctx_uri = getattr(table, "_context_uri", None)
            ctx_list = getattr(table, "_context_uris", None)

            def resolve_index(rk):
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

            def announce(msg, warn=False):
                try:
                    self.call_from_thread(lambda: self._notify(msg, warn=warn))
                except Exception:
                    pass

            def worker():
                try:
                    if ctx_uri is not None:
                        try:
                            self.spotify.start_playback(context_uri=ctx_uri, offset={"position": int(idx)})
                            self._last_played = {'uri': getattr(table, 'row_to_uri', {}).get(row_key) or None, 'ts': time.time()}
                            announce(f"[b]Playing from playlist:[/b] {rich_escape(title)}")
                            return
                        except Exception:
                            logger.exception("start_playback with context_uri failed; falling back")

                    if ctx_list:
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
