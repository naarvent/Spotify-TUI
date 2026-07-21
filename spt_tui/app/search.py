"""Search: query building, dispatch and result rendering."""

from __future__ import annotations

import time
import threading
from typing import Dict, List, Optional

from textual.widgets import DataTable
from textual.containers import Container
from textual.css.query import NoMatches
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
from ..widgets import SearchPanel

class SearchMixin:
    # Per-panel cap for the combined (no-prefix) 2x2 dashboard: each of Songs /
    # Artists / Albums / Playlists is filled with up to this many results.
    GRID_LIMIT = 20

    def _build_search_query(self, raw: str) -> str:
        parts = raw.split()
        tokens = []
        for p in parts:
            if ":" in p:
                k, v = p.split(":", 1)
                k = k.lower()
                if k in {"artist","album","title","year","label","isrc"} and v:
                    tokens.append(f"{k}:{v}")
                else:
                    tokens.append(p)
            else:
                tokens.append(p)
        return " ".join(tokens)

    def _parse_forced_type(self, raw: str):
        """Pull a leading /TRK, /ART, ... type filter off the query. Returns
        (forced_type or None, cleaned_query)."""
        mapping = {'TRK': 'track', 'ART': 'artist', 'ALB': 'album', 'PLY': 'playlist',
                   'SNG': 'single', 'EPS': 'episode', 'PDC': 'podcast'}
        try:
            parts = raw.split(None, 1)
            if parts and parts[0].startswith('/') and len(parts[0]) > 1:
                code = parts[0][1:].upper()
                if code in mapping:
                    return mapping[code], (parts[1] if len(parts) > 1 else '')
        except Exception:
            pass
        return None, raw

    def _begin_search_feedback(self, raw: str, token: int):
        """Immediate on-submit feedback (UI thread): drop the previous view so no
        stale table hides the state, show a clear 'Searching for: <query>', and
        schedule a one-shot 'taking longer' update tied to this token via a
        Textual timer (no extra thread; stops on teardown)."""
        try:
            # If a reusable results widget is already up (the 2x2 grid or the
            # single table), keep it mounted — the upcoming render refills it in
            # place. Clearing it here would schedule an async removal that races
            # the re-fill; the grid's larger widget tree removes over several
            # cycles and would vanish on a Ctrl+R / back-to-back search. When
            # nothing reusable is up (Welcome / another view), clear and show the
            # immediate 'Searching for:' feedback. _clear_right() resets
            # _searching_token, so mark this search active right after either way.
            reusable = (len(self.query("#search_grid")) > 0
                        or len(self.query("#search_table")) > 0)
            if reusable:
                # Keep the current results mounted (clearing would race the
                # re-fill and blank the grid) but show a searching indicator on
                # the frame, so a search started from within a results view still
                # gives immediate visual feedback.
                try: self.right_panel.border_title = f"Searching: {raw} …"
                except Exception: pass
            else:
                self._clear_right().update(f"[b]Searching for:[/b] {rich_escape(raw)}")
            self._searching_token = token
        except Exception:
            logger.exception("_begin_search_feedback failed")

        def _slow():
            if (getattr(self, "_closing", False)
                    or getattr(self, "_searching_token", None) != token
                    or getattr(self, "_last_search_worker", None) != token
                    or getattr(self, "_search_rendered_token", None) == token):
                return
            try:
                if (len(self.query("#search_grid")) > 0
                        or len(self.query("#search_table")) > 0):
                    self.right_panel.border_title = f"Searching: {raw} (taking longer) …"
                else:
                    self.right_panel.update(
                        f"[b]Searching for:[/b] {rich_escape(raw)}\n"
                        f"[dim]This is taking longer than expected…[/dim]")
            except Exception:
                pass
        try:
            self.set_timer(6.0, _slow)
        except Exception:
            pass

    def _dispatch_search(self, raw: str, forced_type: Optional[str] = None):
        """Start a search from the UI thread (submit or Ctrl+R): assign the token
        synchronously so submit order wins, show immediate feedback, then run the
        query on a worker with that token."""
        raw = (raw or "").strip()
        if not raw:
            return
        self._leave_lyrics_mode()   # running a search exits lyrics
        cleaned = raw
        if forced_type is None:
            forced_type, cleaned = self._parse_forced_type(raw)
        self._search_seq = int(getattr(self, "_search_seq", 0)) + 1
        token = self._search_seq
        self._last_search_worker = token
        self._search_rendered_token = None
        self._begin_search_feedback(raw, token)
        q = self._build_search_query(cleaned)
        threading.Thread(target=self._do_search, args=(q, raw, forced_type, token), daemon=True).start()

    def _do_search(self, q: str, raw: str, force_type: Optional[str] = None, my_search_token: Optional[int] = None):
        try:
            # The token is normally assigned on the UI thread at submit (so submit
            # order == token order); _dispatch_search also sets _searching_token.
            # Assign both here for any direct caller that bypasses that path.
            if my_search_token is None:
                self._search_seq = int(getattr(self, "_search_seq", 0)) + 1
                my_search_token = self._search_seq
                self._last_search_worker = my_search_token
                self._searching_token = my_search_token
            if force_type == 'track':
                res = self.spotify.ensure().search(q, type='track', limit=25) or {}
                res = {'tracks': res.get('tracks') or {}}
            elif force_type == 'artist':
                res = self.spotify.ensure().search(q, type='artist', limit=25) or {}
                res = {'artists': res.get('artists') or {}}
            elif force_type == 'album' or force_type == 'single':
                res = self.spotify.ensure().search(q, type='album', limit=25) or {}
                res = {'albums': res.get('albums') or {}}
            elif force_type == 'playlist':
                res = self.spotify.ensure().search(q, type='playlist', limit=25) or {}
                res = {'playlists': res.get('playlists') or {}}
            elif force_type == 'episode':
                res = self.spotify.ensure().search(q, type='episode', limit=25) or {}
                res = {'episodes': res.get('episodes') or {}}
            elif force_type == 'podcast':
                res = self.spotify.ensure().search(q, type='show', limit=25) or {}
                res = {'shows': res.get('shows') or {}}
            else:
                # Combined (no-prefix) search: fill each of the four panels
                # (Songs / Artists / Albums / Playlists) with up to GRID_LIMIT
                # results each. Podcasts/episodes are intentionally not requested
                # here; they remain reachable via /PDC and /EPS. The calls stay
                # sequential on the shared spotipy session (no parallel calls on
                # one session).
                gl = self.GRID_LIMIT
                try:
                    tr = self.spotify.ensure().search(q, type='track', limit=gl) or {}
                    al = self.spotify.ensure().search(q, type='album', limit=gl) or {}
                    ar = self.spotify.ensure().search(q, type='artist', limit=gl) or {}
                    pl = self.spotify.ensure().search(q, type='playlist', limit=gl) or {}

                    res = {
                        'tracks': tr.get('tracks') or {'items': []},
                        'albums': al.get('albums') or {'items': []},
                        'artists': ar.get('artists') or {'items': []},
                        'playlists': pl.get('playlists') or {'items': []},
                    }
                except Exception:
                    fail = 0
                    try:
                        tr = self.spotify.ensure().search(q, type='track', limit=gl) or {}
                    except Exception:
                        tr = {'tracks': {'items': []}}; fail += 1
                    try:
                        al = self.spotify.ensure().search(q, type='album', limit=gl) or {}
                    except Exception:
                        al = {'albums': {'items': []}}; fail += 1
                    try:
                        ar = self.spotify.ensure().search(q, type='artist', limit=gl) or {}
                    except Exception:
                        ar = {'artists': {'items': []}}; fail += 1
                    try:
                        pl = self.spotify.ensure().search(q, type='playlist', limit=gl) or {}
                    except Exception:
                        pl = {'playlists': {'items': []}}; fail += 1

                    # Every request failed -> this is an error, not zero results.
                    if fail >= 4:
                        raise RuntimeError("all search requests failed")

                    res = {
                        'tracks': tr.get('tracks') or {'items': []},
                        'albums': al.get('albums') or {'items': []},
                        'artists': ar.get('artists') or {'items': []},
                        'playlists': pl.get('playlists') or {'items': []},
                    }
        except Exception:
            logger.exception("search failed")
            def show_err():
                # Superseded by a newer search, or the user left Search: don't paint.
                if (getattr(self, '_last_search_worker', None) != my_search_token
                        or getattr(self, '_searching_token', None) != my_search_token):
                    return
                self._search_rendered_token = my_search_token
                self._clear_right().update(
                    f"[b]Search failed for:[/b] {rich_escape(raw)}\n"
                    f"[dim]Press Enter to try again.[/dim]")
            try:
                self.call_from_thread(show_err)
            except Exception:
                if getattr(self, '_closing', False):
                    logger.debug("search error paint dropped during teardown")
                else:
                    logger.exception("search error paint: call_from_thread failed")
            return

        # Drop the render if the app is closing: scheduling a callback on a
        # torn-down loop otherwise raises an unhandled RuntimeError out of this
        # worker (and leaks an un-awaited coroutine). P9.
        if getattr(self, '_closing', False):
            return
        try:
            self.call_from_thread(lambda: self._render_search_results(res, raw, force_type, my_search_token))
        except Exception:
            logger.debug("search render dropped during teardown")

    def _render_search_results(self, res, raw, force_type, my_search_token):
        # Drop stale renders before touching any widget. A newer search advances
        # _last_search_worker; leaving Search (escape / opening another view)
        # clears _searching_token via _clear_right. Either means this render must
        # not paint (never over a newer search or over the menu).
        try:
            if my_search_token is not None and (
                    getattr(self, '_last_search_worker', None) != my_search_token
                    or getattr(self, '_searching_token', None) != my_search_token):
                return
        except Exception:
            pass
        try:
            # Do NOT clear the right panel here: the render methods below reuse an
            # existing #search_grid / #search_table in place (else they clear and
            # mount fresh). Clearing first would schedule the reusable widget for
            # async removal and race the re-fill (the grid would vanish on a
            # Ctrl+R / back-to-back search).
            if not res:
                self._clear_right().update(f"[b]No results:[/b] {rich_escape(raw)}")
                # Mark rendered so the 8s watchdog won't overwrite this message.
                try:
                    self._search_rendered_token = my_search_token
                except Exception:
                    pass
                return
            rows = []
            ids = []
            artist_rows = []
            album_rows = []
            track_rows = []
            playlist_rows = []
            episode_rows = []
            show_rows = []

            tracks = (res.get("tracks") or {}).get("items", [])
            for tr in tracks:
                if not tr:
                    continue
                item = {
                    "type": "track",
                    "id": tr.get("id"),
                    "uri": tr.get("uri"),
                    "title": tr.get("name", "(sin título)"),
                    "artist": ", ".join(a.get("name", "") for a in tr.get("artists", [])),
                    "album": (tr.get("album") or {}).get("name", ""),
                    "dur": self.spotify.fmt_duration(tr.get("duration_ms") or 0),
                    "raw": tr,
                }
                track_rows.append(item)
                if tr.get("id"): ids.append(tr.get("id"))

            albums = (res.get("albums") or {}).get("items", [])
            for a in albums:
                if not a:
                    continue
                item = {
                    "type": "album",
                    "id": a.get("id"),
                    "uri": a.get("uri"),
                    "title": a.get("name"),
                    "artist": ", ".join([x.get("name", "") for x in (a.get("artists") or [])]),
                    "album": "",
                    "dur": "",
                    "raw": a,
                }
                album_rows.append(item)

            artists = (res.get("artists") or {}).get("items", [])
            for ar in artists:
                if not ar:
                    continue
                item = {
                    "type": "artist",
                    "id": ar.get("id"),
                    "uri": ar.get("uri"),
                    "title": ar.get("name"),
                    "artist": "",
                    "album": "",
                    "dur": "",
                    "raw": ar,
                }
                artist_rows.append(item)

            plays = (res.get("playlists") or {}).get("items", [])
            for p in plays:
                if not p:
                    continue
                item = {
                    "type": "playlist",
                    "id": p.get("id"),
                    "uri": p.get("uri"),
                    "title": p.get("name"),
                    "artist": (p.get("owner") or {}).get("display_name", ""),
                    "album": "",
                    "dur": "",
                    "raw": p,
                }
                playlist_rows.append(item)

            episodes = (res.get("episodes") or {}).get("items", [])
            for ep in episodes:
                if not ep:
                    continue
                try:
                    show = (ep.get('show') or {})
                except Exception:
                    show = {}
                item = {
                    "type": "episode",
                    "id": ep.get("id"),
                    "uri": ep.get("uri"),
                    "title": ep.get("name", "(sin título)"),
                    "artist": (show.get("name") or ""),
                    "album": (show.get("name") or ""),
                    "dur": self.spotify.fmt_duration(ep.get("duration_ms") or 0),
                    "raw": ep,
                }
                episode_rows.append(item)

            shows = (res.get("shows") or {}).get("items", [])
            for s in shows:
                if not s:
                    continue
                item = {
                    "type": "podcast",
                    "id": s.get("id"),
                    "uri": s.get("uri"),
                    "title": s.get("name"),
                    "artist": s.get("publisher", ""),
                    "album": "",
                    "dur": "",
                    "raw": s,
                }
                show_rows.append(item)

            if force_type:
                # A prefix search shows one specialized type and keeps the larger
                # fetched limit (25); the tight combined caps must not apply here.
                pass
            else:
                # Combined (no-prefix) view: fill each panel up to GRID_LIMIT,
                # and no podcasts/episodes (reachable only via /PDC and /EPS).
                gl = self.GRID_LIMIT
                artist_rows = artist_rows[:gl]
                album_rows = album_rows[:gl]
                track_rows = track_rows[:gl]
                playlist_rows = playlist_rows[:gl]
                show_rows = []
                episode_rows = []

            exact_artist_ids = [ar['raw'].get('id') for ar in artist_rows if (ar.get('title','').strip().lower() == (raw or '').strip().lower()) and ar.get('raw')]
            if exact_artist_ids:
                ea_set = set(x for x in exact_artist_ids if x)
                ordered = []

                for ar in artist_rows:
                    if ar.get('raw') and ar['raw'].get('id') in ea_set:
                        ordered.append(ar)

                for al in album_rows:
                    try:
                        a_artists = [x.get('id') for x in (al.get('raw',{}).get('artists') or [])]
                    except Exception:
                        a_artists = []
                    if any(aid in ea_set for aid in a_artists):
                        ordered.append(al)

                for tr in track_rows:
                    try:
                        t_artists = [x.get('id') for x in (tr.get('raw',{}).get('artists') or [])]
                    except Exception:
                        t_artists = []
                    if any(tid in ea_set for tid in t_artists):
                        ordered.append(tr)

                for ar in artist_rows:
                    if not (ar.get('raw') and ar['raw'].get('id') in ea_set):
                        ordered.append(ar)

                for al in album_rows:
                    try:
                        a_artists = [x.get('id') for x in (al.get('raw',{}).get('artists') or [])]
                    except Exception:
                        a_artists = []
                    if not any(aid in ea_set for aid in a_artists):
                        ordered.append(al)
                for tr in track_rows:
                    try:
                        t_artists = [x.get('id') for x in (tr.get('raw',{}).get('artists') or [])]
                    except Exception:
                        t_artists = []
                    if not any(tid in ea_set for tid in t_artists):
                        ordered.append(tr)
                ordered.extend(show_rows)
                ordered.extend(episode_rows)
                ordered.extend(playlist_rows)
                rows = ordered
            else:

                rows = artist_rows + show_rows + album_rows + episode_rows + track_rows + playlist_rows

            if force_type:
                ft = force_type
                if ft == 'track':
                    rows = track_rows
                elif ft == 'album':
                    rows = album_rows
                elif ft == 'artist':
                    rows = artist_rows
                elif ft == 'playlist':
                    rows = playlist_rows
                elif ft == 'single':

                    single_rows = []
                    for a in album_rows:
                        try:
                            if (a.get('raw') or {}).get('album_type') == 'single':
                                nr = dict(a)
                                nr['type'] = 'single'
                                single_rows.append(nr)
                        except Exception:
                            pass
                    rows = single_rows

            if not rows:
                # Zero results is a distinct, clear state (not an error, not an
                # empty table).
                self._clear_right().update(f"[b]No results for:[/b] {rich_escape(raw)}")
                self._search_rendered_token = my_search_token
                return

            title = f"[b]Results for:[/b] {rich_escape(raw)}"

            try:

                self._new_view_token("search", raw)

                rv = getattr(self, "_right_view", None)
                if rv and len(rv) >= 3:
                    self._right_view = (rv[0], rv[1], rv[2], rows)
            except Exception:
                logger.exception("_do_search: failed setting search view token")
            if not force_type:
                # A combined (no-prefix) search shows the 2x2 dashboard: Songs /
                # Artists on top, Albums / Playlists below. Each panel is a
                # single specialized type (no mixed-type table).
                self._render_search_grid(track_rows, artist_rows, album_rows,
                                         playlist_rows, my_search_token)
            else:
                # A forced single-type search keeps the specialized single-type
                # table with a layout tailored to that type.
                layout = "full"
                if force_type in ("album", "single"):
                    layout = "albums"
                elif force_type == "playlist":
                    layout = "playlists"
                elif force_type == "artist":
                    layout = "artists"
                table = self._render_search_table(title, rows, layout=layout)
                # Stamp the token so late saved/liked updates (this render's own
                # worker, and post-favourite revalidation) can verify the table
                # is still the current search before painting.
                try:
                    table._search_token = my_search_token
                except Exception:
                    pass

                # P2: the saved-state lookup is a network call — never run it on
                # the UI thread. The table is already rendered above; fetch liked
                # flags on a worker and apply them only if this search is current.
                self._fetch_liked_for_search(table, rows, my_search_token)

            # Mark this token as rendered (suppresses the 8s watchdog nag).
            try:
                self._search_rendered_token = my_search_token
            except Exception:
                pass
        except Exception:
            # Log the traceback; never show internals to the user. We are already
            # on the UI thread here (called via call_from_thread), so update directly.
            logger.exception("paint search failed")
            try:
                self._clear_right().update(
                    f"[b]Could not display results for:[/b] {rich_escape(raw)}\n"
                    f"[dim]Press Enter to try again.[/dim]")
            except Exception:
                pass

    _TYPE_LABELS = {"track": "TRK", "album": "ALB", "artist": "ART", "playlist": "PLY",
                    "single": "SNG", "episode": "EPS", "podcast": "PDC"}

    # Column layouts for the shared search_table: (labels, fields). `fields` maps
    # 1:1 to the cells built by _search_cells, and their widths come from the
    # shared catalogue in TablesMixin. Source is dropped from every content table
    # (only the Queue keeps it).
    # First column shows the saved/liked/followed state as a heart (its exact
    # semantics stay per-type: liked track, saved album, followed artist, saved
    # show/episode — see _revalidate_saved_column). The glyph is unified, the
    # endpoints are not.
    _SEARCH_LAYOUTS = {
        "full": (["♥", "Type", "Title", "Artist/Owner", "Album", "Duration"],
                 ["saved", "type", "title", "artist", "album", "dur"]),
        "artists": (["♥", "Type", "Name"], ["saved", "type", "title"]),
        "podcasts": (["♥", "Type", "Name", "Owner"], ["saved", "type", "title", "artist"]),
        # Search albums / playlists have no meaningful Duration.
        "albums": (["♥", "Type", "Name", "Artist"], ["saved", "type", "title", "artist"]),
        "playlists": (["♥", "Type", "Name", "Owner"], ["saved", "type", "title", "artist"]),
    }

    def _search_cells(self, r: Dict, fields: List[str]):
        """Build search_table cells for the given field list (see _SEARCH_LAYOUTS)."""
        t = (r.get("type") or "")
        cell_map = {
            "saved": Text("❤", style="bold red") if r.get('saved', False) else Text(""),
            "type": self._TYPE_LABELS.get(t, t.upper()),
            "title": r.get("title", ""),
            "artist": r.get("artist", ""),
            "album": r.get("album", ""),
            "dur": r.get("dur", ""),
            "source": r.get("source", ""),
        }
        return [cell_map[f] for f in fields]

    def _render_search_table(self, title: str, rows: List[Dict], check_saved: bool = True, layout: str = "full"):
        # Reuse an already-mounted search_table in place. Textual's remove() is
        # async, so calling _clear_right() and immediately mounting a second
        # widget with the same id in the same callback raises DuplicateIds (seen
        # in the log on saved-view reopen and back-to-back searches). The id is
        # also a behaviour discriminator elsewhere, so it must stay stable.
        col_labels, fields = self._SEARCH_LAYOUTS.get(layout, self._SEARCH_LAYOUTS["full"])
        try:
            table = self.query_one("#search_table", DataTable)
        except NoMatches:
            table = None
        reused = table is not None
        if reused:
            try:
                # Reset columns to this layout (also refreshes widths for the
                # current size); clear(columns=True) drops both rows and columns,
                # so a layout change reuses the widget without a remount.
                table.clear(columns=True)
                keep_fields, keep_labels, widths = self._fit_columns(fields, col_labels)
                table._width_spec = (list(col_labels), list(fields))
                table._fit_fields = list(keep_fields)
                table._fields_attr = "_search_fields"
                fields = keep_fields
                self._apply_columns(table, keep_labels, widths)
            except Exception:
                reused = False
                table = None
        if not reused:
            right = self._clear_right()
            table = self._create_table_with_full_width(
                col_labels, fields, widget_id="search_table", fields_attr="_search_fields",
            )
            fields = getattr(table, "_search_fields", fields)
        table.row_to_uri = {}; table.row_to_title = {}; table.row_to_id = {}; table.row_to_type = {}; table.row_to_obj = {}
        table._col_saved = 0
        table._saved_check_done = False
        table._search_fields = list(fields)
        for i, r in enumerate(rows):
            table.add_row(*self._search_cells(r, fields), key=i)
            table.row_to_uri[i] = r.get("uri")
            table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
            if r.get("id"): table.row_to_id[i] = r.get("id")
            table.row_to_type[i] = r.get("type")
            table.row_to_obj[i] = r.get("raw")
        table._model_rows = rows
        # The borderless table borrows #right's frame (single clean border, no
        # grey bleed); #right shows the title and drops its padding.
        self.right_panel.add_class("table-view")
        self.right_panel.border_title = self._content_title(title)
        if not reused:
            right.mount(table)
        table.focus()
        self._refit_after_mount(table)
        self.level = self.LVL_VIEW
        try:
            if check_saved and not getattr(table, '_saved_check_done', False):
                table._saved_check_done = True
                def _one_shot():
                    try:
                        time.sleep(0.05)
                        self._revalidate_saved_column(table, max_rows=200)
                    except Exception:
                        try: self._revalidate_saved_column(table, max_rows=200)
                        except Exception: pass
                threading.Thread(target=_one_shot, daemon=True).start()
        except Exception:
            pass
        return table

    def _fetch_liked_for_search(self, table, rows, my_search_token):
        """Look up liked/saved state for a rendered search table off the UI
        thread (P2). The table is already on screen; we only fill in the liked
        map. Results are applied via call_from_thread and only if this search is
        still the current one and the table is still mounted, so a table that
        was unmounted (view changed / newer search) never causes an error and a
        failed lookup never wipes the already-rendered results."""
        track_ids = [r["id"] for r in rows if r.get("type") == "track" and r.get("id")]

        def worker():
            try:
                liked = self.spotify.check_saved_tracks(track_ids) if track_ids else []
            except Exception:
                # Leave the already-rendered results untouched on failure.
                logger.exception("search liked lookup failed")
                return

            liked_map = {}
            li = 0
            for i, r in enumerate(rows):
                if r.get("type") == "track":
                    liked_map[i] = bool(liked[li]) if li < len(liked) else False
                    li += 1
                else:
                    liked_map[i] = False

            def apply():
                # Re-check the search token and that the table is still on
                # screen before mutating any state tied to a widget. Note: a
                # detached DataTable keeps is_mounted == True in Textual, so we
                # test .parent (None once the table has been removed from the
                # tree) as the reliable "still current view" signal.
                if getattr(self, '_last_search_worker', None) != my_search_token:
                    return
                if getattr(table, '_search_token', None) != my_search_token:
                    return
                if getattr(table, 'parent', None) is None:
                    return
                try:
                    table._liked_map = liked_map
                except Exception:
                    logger.exception("applying liked map to search table failed")

            try:
                self.call_from_thread(apply)
            except Exception:
                # Loop gone (shutdown) — nothing to paint.
                pass

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Combined-search 2x2 dashboard (Songs / Artists / Albums / Playlists)
    # ------------------------------------------------------------------ #
    _GRID_SPECS = [("songs", "Songs"), ("artists", "Artists"),
                   ("albums", "Albums"), ("playlists", "Playlists")]
    _GRID_ORDER = ["songs", "artists", "albums", "playlists"]
    _GRID_POS = {"songs": (0, 0), "artists": (0, 1), "albums": (1, 0), "playlists": (1, 1)}
    _GRID_POS_INV = {(0, 0): "songs", (0, 1): "artists", (1, 0): "albums", (1, 1): "playlists"}

    def _grid_avail_width(self) -> int:
        """Inner width available to the results grid, derived from the TERMINAL
        width (right content ~= terminal - 44: the 38-col left column plus grid
        and panel borders). Deliberately not the right panel's content_size —
        that is inflated by the panels' current column widths, so on a shrink it
        would stay wide and never re-flow. Falls back to content_size only if the
        terminal size is not yet known."""
        try:
            tw = int(self.size.width or 0)
        except Exception:
            tw = 0
        if tw > 0:
            return max(0, tw - 44)
        right = getattr(self, "right_panel", None)
        cs = getattr(right, "content_size", None) if right is not None else None
        if cs is not None:
            return max(0, int(getattr(cs, "width", 0) or 0))
        return 40

    def _grid_is_stacked(self) -> bool:
        """Below this width a side-by-side 2x2 leaves each panel too narrow, so
        stack the four panels vertically (single column)."""
        return self._grid_avail_width() < 46

    def _grid_col_width(self, stacked: bool) -> int:
        aw = self._grid_avail_width() or 40
        panel_w = aw if stacked else max(8, (aw - 1) // 2)
        # Leave room for the panel border + the DataTable's cell padding.
        return max(8, panel_w - 4)

    # Grid panels are borderless and carry one flexible column (plus the heart on
    # Songs), so they go through the same fitter as the full-width tables.
    def _grid_panel_columns(self, panel_key: str):
        if panel_key == "songs":
            return ["heart", "title"], ["♥", "Songs"]
        return ["title"], [panel_key.capitalize()]

    def _grid_panel_width(self, table, stacked: bool | None = None) -> int:
        w = 0
        for attr in ("content_size", "size"):
            sz = getattr(table, attr, None)
            w = int(getattr(sz, "width", 0) or 0) if sz is not None else 0
            if w:
                break
        if not w:
            # Not laid out yet (first render): approximate from the grid, and let
            # the deferred relayout correct it once the panels have a size.
            if stacked is None:
                stacked = self._grid_is_stacked()
            w = self._grid_col_width(stacked) + 4
        return w

    def _fit_grid_panel(self, table, panel_key: str, stacked: bool | None = None):
        """Column widths for one grid panel, fitted to the quadrant it occupies."""
        fields, labels = self._grid_panel_columns(panel_key)
        return self._fit_columns(fields, labels,
                                 panel_w=self._grid_panel_width(table, stacked),
                                 chrome=self._CHROME_BORDERLESS)

    def _refit_grid_panel(self, table) -> None:
        """Re-fit one panel's columns to its current width. Called by the panel
        itself on resize (mount, terminal resize, 2x2 <-> stacked reflow)."""
        panel_key = (getattr(table, "id", "") or "").replace("_table", "")
        if not panel_key:
            return
        try:
            cols = list(table.ordered_columns)
        except Exception:
            return
        _, _, widths = self._fit_grid_panel(table, panel_key)
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
                pass

    def _grid_song_line(self, r: Dict) -> str:
        title = r.get("title", "") or ""
        artist = r.get("artist", "") or ""
        return f"{title} {GLYPHS['sep']} {artist}" if artist else title

    def _grid_line(self, panel_key: str, r: Dict) -> str:
        title = r.get("title", "") or ""
        if panel_key == "albums":
            artist = r.get("artist", "") or ""
            return f"{title} {GLYPHS['sep']} {artist}" if artist else title
        # artists / playlists: just the name (owner omitted to stay compact in a
        # narrow panel). Overlong text is truncated to the column width by the
        # DataTable, never producing a horizontal scrollbar.
        return title

    def _fill_grid_panel(self, table, panel_key: str, rows: List[Dict]) -> None:
        """(Re)build one panel's columns and rows. Reused in place on a repeated
        search / resize so no widget is remounted (no DuplicateIds)."""
        try:
            table.clear(columns=True)
        except Exception:
            pass
        # Fresh results start in panel-selection mode: no row cursor shown.
        try:
            table.show_cursor = False
        except Exception:
            pass
        is_songs = (panel_key == "songs")
        _, keep_labels, widths = self._fit_grid_panel(table, panel_key)
        self._apply_columns(table, keep_labels, widths)
        if is_songs:
            table._col_heart = 0
        table.row_to_uri = {}; table.row_to_id = {}; table.row_to_title = {}
        table.row_to_type = {}; table.row_to_obj = {}
        table._model_rows = rows
        table._panel_key = panel_key
        liked_map = {}
        for i, r in enumerate(rows):
            if is_songs:
                liked_map[i] = False
                table.add_row(Text(""), self._grid_song_line(r), key=i)
            else:
                table.add_row(self._grid_line(panel_key, r), key=i)
            table.row_to_uri[i] = r.get("uri")
            if r.get("id"):
                table.row_to_id[i] = r.get("id")
            table.row_to_type[i] = r.get("type")
            table.row_to_obj[i] = r.get("raw")
            table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
        if is_songs:
            table._liked_map = liked_map

    def _render_search_grid(self, track_rows, artist_rows, album_rows, playlist_rows, my_search_token):
        rows_by = {"songs": track_rows, "artists": artist_rows,
                   "albums": album_rows, "playlists": playlist_rows}
        stacked = self._grid_is_stacked()
        try:
            grid = self.query_one("#search_grid", Container)
        except NoMatches:
            grid = None

        if grid is None:
            right = self._clear_right()
            panel_widgets = []
            for pk, title in self._GRID_SPECS:
                tbl = SearchPanel(id=f"{pk}_table", zebra_stripes=True)
                tbl.show_cursor = False
                tbl.cursor_type = "row"
                tbl.show_header = False
                self._fill_grid_panel(tbl, pk, rows_by[pk])
                try: tbl._search_token = my_search_token
                except Exception: pass
                panel = Container(tbl, id=f"panel_{pk}", classes="search-panel")
                panel_widgets.append((panel, title))
            grid = Container(*[p for p, _ in panel_widgets], id="search_grid")
            grid.set_class(stacked, "-stacked")
            right.mount(grid)
            for panel, title in panel_widgets:
                try: panel.border_title = title
                except Exception: pass
        else:
            grid.set_class(stacked, "-stacked")
            for pk, _ in self._GRID_SPECS:
                t = self._grid_panel_table(pk)
                if t is not None:
                    self._fill_grid_panel(t, pk, rows_by[pk])
                    try: t._search_token = my_search_token
                    except Exception: pass

        self.level = self.LVL_VIEW
        # Fresh results begin at the panel-selection level, not inside the rows.
        self._grid_mode = "select"
        # Results are up: clear any 'Searching…' indicator on the frame.
        try: self.right_panel.border_title = ""
        except Exception: pass

        # Focus and the liked lookup must wait until the freshly-mounted panels
        # are actually in the tree (a just-mounted widget can't take focus, and
        # a fast liked lookup could otherwise apply() before .parent is set and
        # get dropped). call_after_refresh runs once the DOM has settled.
        def _post():
            # The panels only get their real width once mounted, so the first
            # fit used an approximation: re-fit now that they have a size.
            self._relayout_search_grid()
            self._grid_initial_focus()
            songs = self._grid_panel_table("songs")
            if songs is not None:
                self._fetch_liked_for_grid(songs, track_rows, my_search_token)
        try:
            self.call_after_refresh(_post)
        except Exception:
            _post()
        return grid

    def _grid_panel_table(self, panel_key: str):
        try:
            return self.query_one(f"#{panel_key}_table", SearchPanel)
        except NoMatches:
            return None

    def _grid_nonempty(self) -> List[str]:
        out = []
        for pk in self._GRID_ORDER:
            t = self._grid_panel_table(pk)
            if t is not None and int(getattr(t, "row_count", 0) or 0) > 0:
                out.append(pk)
        return out

    def _focus_grid_panel(self, panel_key: str) -> bool:
        """Focus a panel for SELECTION (no row cursor). Used when moving the
        selection across the grid; entering a panel's rows is _grid_enter_content."""
        t = self._grid_panel_table(panel_key)
        if t is None or int(getattr(t, "row_count", 0) or 0) == 0:
            return False
        try:
            t.show_cursor = (getattr(self, "_grid_mode", "select") == "content")
        except Exception:
            pass
        try:
            t.focus()
        except Exception:
            try: self.set_focus(t)
            except Exception: pass
        # Focusing a panel means we are inside the results view; keep the level
        # in sync so the app's edge handlers (e.g. Right on a right-edge panel)
        # behave as a view, not as the section menu.
        self.level = self.LVL_VIEW
        self._grid_focus_key = panel_key
        return True

    def _grid_enter_content(self, panel_key: str) -> None:
        """Enter a selected panel's rows (Enter in select mode): show the row
        cursor and let Up/Down navigate results."""
        t = self._grid_panel_table(panel_key)
        if t is None or int(getattr(t, "row_count", 0) or 0) == 0:
            return
        self._grid_mode = "content"
        self._grid_focus_key = panel_key
        try:
            t.show_cursor = True
            row = getattr(t, "cursor_row", None)
            row = 0 if row is None else max(0, min(int(row), int(t.row_count) - 1))
            t.move_cursor(row=row, column=0, animate=False)
        except Exception:
            pass
        try:
            t.focus()
        except Exception:
            pass

    def _grid_exit_to_select(self, panel_key: str) -> None:
        """Step back out of a panel's rows to panel selection (Left/Right in
        content mode): hide the row cursor, keep this panel selected."""
        self._grid_mode = "select"
        t = self._grid_panel_table(panel_key)
        if t is not None:
            try: t.show_cursor = False
            except Exception: pass
            try: t.focus()
            except Exception: pass

    def _grid_initial_focus(self) -> None:
        nonempty = self._grid_nonempty()
        if not nonempty:
            return
        self._grid_mode = "select"
        target = "songs" if "songs" in nonempty else nonempty[0]
        self._focus_grid_panel(target)

    def _search_grid_key(self, panel_key: str, direction: str) -> bool:
        """Move focus between panels. Called from SearchPanel.on_key so the app's
        global on_key is untouched. Returns True if it acted."""
        nonempty = self._grid_nonempty()
        if not nonempty:
            return False
        if direction in ("next", "prev"):
            if panel_key not in nonempty:
                return self._focus_grid_panel(nonempty[0])
            i = nonempty.index(panel_key)
            j = (i + (1 if direction == "next" else -1)) % len(nonempty)
            return self._focus_grid_panel(nonempty[j])
        pos = self._GRID_POS.get(panel_key)
        if pos is None:
            return False
        r, c = pos
        target = None
        if direction == "left":
            if c == 0:
                # Left-edge panel: let the event fall through to the app's
                # existing 'back to the main menu' handler (same as the
                # single-table search view), so the behaviour stays consistent.
                return False
            target = self._GRID_POS_INV.get((r, c - 1))
        elif direction == "right":
            target = self._GRID_POS_INV.get((r, c + 1))
        elif direction == "up":
            target = self._GRID_POS_INV.get((r - 1, c))
        elif direction == "down":
            target = self._GRID_POS_INV.get((r + 1, c))
        if target:
            return self._focus_grid_panel(target)
        return False

    def _fetch_liked_for_grid(self, songs_table, song_rows, my_search_token):
        """Resolve liked hearts for the Songs panel off the UI thread (like
        _fetch_liked_for_search) and paint them only if this search is still
        current and the panel is still mounted."""
        track_ids = [r["id"] for r in song_rows if r.get("id")]

        def worker():
            try:
                liked = self.spotify.check_saved_tracks(track_ids) if track_ids else []
            except Exception:
                logger.exception("grid liked lookup failed")
                return
            id_to = {}
            li = 0
            for r in song_rows:
                if r.get("id"):
                    id_to[r["id"]] = bool(liked[li]) if li < len(liked) else False
                    li += 1

            def apply():
                if getattr(self, "_last_search_worker", None) != my_search_token:
                    return
                if getattr(songs_table, "_search_token", None) != my_search_token:
                    return
                if getattr(songs_table, "parent", None) is None:
                    return
                liked_map = {i: bool(id_to.get(r.get("id"), False)) for i, r in enumerate(song_rows)}
                try:
                    songs_table._liked_map = liked_map
                    self._repaint_rows_from_model(songs_table)
                except Exception:
                    logger.exception("applying grid liked hearts failed")

            try:
                self.call_from_thread(apply)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _relayout_search_grid(self) -> None:
        """Resize hook: re-flow the 2x2 <-> stacked layout and resize the panel
        columns in place, preserving rows / cursor / focus (no re-query)."""
        try:
            grid = self.query_one("#search_grid", Container)
        except NoMatches:
            return
        stacked = self._grid_is_stacked()
        grid.set_class(stacked, "-stacked")
        for pk, _ in self._GRID_SPECS:
            t = self._grid_panel_table(pk)
            if t is None:
                continue
            self._refit_grid_panel(t)
