"""Search: query building, dispatch and result rendering."""

from __future__ import annotations

import time
import threading
from typing import Dict, List, Optional

from textual.widgets import Static, DataTable
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

class SearchMixin:
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
            # _clear_right() resets _searching_token; mark this search active
            # right after so the watchdog can tell it is still the current state.
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
                try:
                    tr = self.spotify.ensure().search(q, type='track', limit=10) or {}
                    al = self.spotify.ensure().search(q, type='album', limit=5) or {}
                    ar = self.spotify.ensure().search(q, type='artist', limit=3) or {}
                    pl = self.spotify.ensure().search(q, type='playlist', limit=5) or {}
                    eps = self.spotify.ensure().search(q, type='episode', limit=5) or {}
                    sh = self.spotify.ensure().search(q, type='show', limit=3) or {}

                    res = {
                        'tracks': tr.get('tracks') or {'items': []},
                        'albums': al.get('albums') or {'items': []},
                        'artists': ar.get('artists') or {'items': []},
                        'playlists': pl.get('playlists') or {'items': []},
                        'episodes': eps.get('episodes') or {'items': []},
                        'shows': sh.get('shows') or {'items': []},
                    }
                except Exception:
                    fail = 0
                    try:
                        tr = self.spotify.ensure().search(q, type='track', limit=10) or {}
                    except Exception:
                        tr = {'tracks': {'items': []}}; fail += 1
                    try:
                        al = self.spotify.ensure().search(q, type='album', limit=5) or {}
                    except Exception:
                        al = {'albums': {'items': []}}; fail += 1
                    try:
                        ar = self.spotify.ensure().search(q, type='artist', limit=3) or {}
                    except Exception:
                        ar = {'artists': {'items': []}}; fail += 1
                    try:
                        pl = self.spotify.ensure().search(q, type='playlist', limit=5) or {}
                    except Exception:
                        pl = {'playlists': {'items': []}}; fail += 1
                    try:
                        eps = self.spotify.ensure().search(q, type='episode', limit=5) or {}
                    except Exception:
                        eps = {'episodes': {'items': []}}; fail += 1
                    try:
                        sh = self.spotify.ensure().search(q, type='show', limit=3) or {}
                    except Exception:
                        sh = {'shows': {'items': []}}; fail += 1

                    # Every request failed -> this is an error, not zero results.
                    if fail >= 6:
                        raise RuntimeError("all search requests failed")

                    res = {
                        'tracks': tr.get('tracks') or {'items': []},
                        'albums': al.get('albums') or {'items': []},
                        'artists': ar.get('artists') or {'items': []},
                        'playlists': pl.get('playlists') or {'items': []},
                        'episodes': eps.get('episodes') or {'items': []},
                        'shows': sh.get('shows') or {'items': []},
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
            right = self._clear_right()
            try:
                right.update(f"[b]Rendering results for:[/b] {rich_escape(raw)} …")
            except Exception:
                pass
            if not res:
                right.update(f"[b]No results:[/b] {rich_escape(raw)}")
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

            artist_rows = artist_rows[:3]
            show_rows = show_rows[:3]
            album_rows = album_rows[:5]
            episode_rows = episode_rows[:5]
            track_rows = track_rows[:10]
            playlist_rows = playlist_rows[:5]

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
            # A forced single-type search gets a layout tailored to that type;
            # a combined search keeps the full (mixed-type) layout.
            layout = "full"
            if force_type in ("album", "single"):
                layout = "albums"
            elif force_type == "playlist":
                layout = "playlists"
            elif force_type == "artist":
                layout = "artists"
            table = self._render_search_table(title, rows, layout=layout)
            # Stamp the token so late saved/liked updates (this render's own
            # worker, and post-favourite revalidation) can verify the table is
            # still the current search before painting.
            try:
                table._search_token = my_search_token
            except Exception:
                pass

            # P2: the saved-state lookup is a network call — never run it on the
            # UI thread. The table is already rendered above; fetch liked flags
            # on a worker and apply them only if this search is still current.
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

    # Column layouts for the shared search_table. Each entry is
    # (labels, fixed_widths, fields). `fields` maps 1:1 to the cells built by
    # _search_cells; flexible columns (not in fixed_widths) share the rest.
    # Column profiles for the shared search_table: (labels, fixed_widths, fields,
    # weights). Source is dropped from every content table (only the Queue keeps
    # it). Name/Title carries the highest weight so it takes the most free space.
    _SEARCH_LAYOUTS = {
        "full": (["S", "Type", "Title", "Artist/Owner", "Album", "Duration"],
                 {0: 3, 1: 7, 5: 9},
                 ["saved", "type", "title", "artist", "album", "dur"], {2: 1.4}),
        "artists": (["S", "Type", "Name"], {0: 3, 1: 7}, ["saved", "type", "title"], {}),
        "podcasts": (["S", "Type", "Name", "Owner"], {0: 3, 1: 7}, ["saved", "type", "title", "artist"], {2: 1.4}),
        # Search albums / playlists have no meaningful Duration.
        "albums": (["S", "Type", "Name", "Artist"], {0: 3, 1: 7}, ["saved", "type", "title", "artist"], {2: 1.4}),
        "playlists": (["S", "Type", "Name", "Owner"], {0: 3, 1: 7}, ["saved", "type", "title", "artist"], {2: 1.4}),
    }

    def _search_cells(self, r: Dict, fields: List[str]):
        """Build search_table cells for the given field list (see _SEARCH_LAYOUTS)."""
        t = (r.get("type") or "")
        cell_map = {
            "saved": Text(GLYPHS['disk']) if r.get('saved', False) else Text(""),
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
        col_labels, fixed_widths, fields, weights = self._SEARCH_LAYOUTS.get(layout, self._SEARCH_LAYOUTS["full"])
        max_widths = {i: self._FLEX_MAX[f] for i, f in enumerate(fields) if f in self._FLEX_MAX}
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
                table._width_spec = (list(col_labels), dict(fixed_widths), dict(weights), dict(max_widths))
                for lbl, w in zip(col_labels, self._column_widths(col_labels, fixed_widths, weights, max_widths)):
                    try: table.add_column(lbl, width=int(w))
                    except Exception:
                        try: table.add_column(lbl)
                        except Exception: pass
            except Exception:
                reused = False
                table = None
        if not reused:
            right = self._clear_right()
            table = self._create_table_with_full_width(
                col_labels, fixed_widths=fixed_widths, widget_id="search_table", weights=weights, max_widths=max_widths,
            )
        table.row_to_uri = {}; table.row_to_title = {}; table.row_to_id = {}; table.row_to_type = {}; table.row_to_obj = {}
        table._col_saved = 0
        table._saved_check_done = False
        table._search_fields = fields
        for i, r in enumerate(rows):
            table.add_row(*self._search_cells(r, fields), key=i)
            table.row_to_uri[i] = r.get("uri")
            table.row_to_title[i] = f"{r.get('title','')} {GLYPHS['sep']} {r.get('artist','')}"
            if r.get("id"): table.row_to_id[i] = r.get("id")
            table.row_to_type[i] = r.get("type")
            table.row_to_obj[i] = r.get("raw")
        table._model_rows = rows
        if not reused:
            right.mount(Static(title, markup=True, id="results_title")); right.mount(table)
        else:
            try:
                self.query_one("#results_title", Static).update(title)
            except NoMatches:
                pass
        table.focus()
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
