"""Queue management and playback device selection."""

from __future__ import annotations

import time
import threading

from textual.widgets import Input, Static, ListView, ListItem, Label, DataTable
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

from textual.css.query import NoMatches

from .. import config
from ..config import logger
from ..constants import GLYPHS
from ..providers.base import Provider
from ..providers.spotify import SpotifyClient
from ..local_player import LOCAL_START_SENTINEL

class QueueDevicesMixin:
    # The widget id used to leak into the queue's Source column ("tracks_table").
    _QUEUE_SOURCE_LABELS = {
        "tracks_table": "Playlist",
        "search_table": "Search",
        "songs_table": "Search",
        "albums_table": "Album",
        "artists_table": "Artist",
        "playlists_table": "Playlist",
        "queue_table": "Queue",
    }

    # #tracks_table is shared by playlists, Liked Songs, Recently Played, albums
    # and podcasts (and #search_table by the saved-library lists), so the view
    # kind (_right_view[0]) decides the label; the widget id is the fallback.
    _QUEUE_SOURCE_VIEW_LABELS = {
        "playlist": "Playlist",
        "liked": "Liked Songs",
        "recent": "Recently Played",
        "album": "Album",
        "artist": "Artist",
        "podcast": "Podcast",
        "search": "Search",
        "albums": "Saved Albums",
        "artists": "Saved Artists",
        "podcasts": "Saved Podcasts",
        "episodes": "Saved Episodes",
        "queue": "Queue",
        "downloaded": "Downloaded",
        "radio": "Radio",
    }

    def _queue_source_label(self, table) -> str:
        wid = getattr(table, "id", "") or ""
        rv = getattr(self, "_right_view", None)
        kind = rv[0] if rv else None
        if wid != "queue_table" and kind in self._QUEUE_SOURCE_VIEW_LABELS:
            return self._QUEUE_SOURCE_VIEW_LABELS[kind]
        return self._QUEUE_SOURCE_LABELS.get(wid, "")

    def action_queue_track(self):
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_control_queue"):
            return
        try:
            focused = self.focused
            if not isinstance(focused, DataTable):
                return
            row = self._get_cursor_row(focused)
            if row is None:
                return
            uri = getattr(focused, 'row_to_uri', {}).get(row) or getattr(focused, 'row_to_id', {}).get(row)
            title = getattr(focused, 'row_to_title', {}).get(row, "")

            fid = getattr(focused, 'id', '')
            if fid == 'search_table':
                rtype = getattr(focused, 'row_to_type', {}).get(row)
                if rtype not in (None, 'track', 'single'):
                    try:
                        self._notify('[b]Only tracks can be added to the queue from search results.[/b]', warn=True)
                    except Exception:
                        pass
                    return

            if not uri:
                return

            try:
                s = str(uri)
                if s.startswith('http') and 'open.spotify.com' in s:
                    norm_id = s.split('/')[-1].split('?')[0]
                    uri = f'spotify:track:{norm_id}'
            except Exception:
                pass

            try:
                model_rows = getattr(focused, "_model_rows", []) or []
                row_obj = model_rows[row] if row is not None and row < len(model_rows) else {}
            except Exception:
                row_obj = {}
            # Resolved now: by the time add_to_queue returns the user may be
            # in another view, and the label follows _right_view.
            source_label = self._queue_source_label(focused)

            def worker():
                try:
                    self.spotify.add_to_queue(uri)
                except Exception as e:
                    err_msg = rich_escape(str(e))
                    try:
                        self.call_from_thread(lambda: self._notify(f"[b]Could not add to queue:[/b] {err_msg}", warn=True))
                    except Exception:
                        pass
                    logger.exception("queue_track failed")
                    return
                try:
                    qitem = {
                        "uri": uri,
                        "title": title or row_obj.get("title") or "(unknown)",
                        "artist": row_obj.get("artist") or "",
                        "album": row_obj.get("album") or "",
                        "dur": row_obj.get("dur") or "",
                        "source": source_label or row_obj.get('source', '') or '',
                        "added_at": int(time.time() * 1000),
                    }
                    self._local_queue.append(qitem)
                except Exception:
                    logger.exception("Could not record local queue item")
                try:
                    self.call_from_thread(lambda: self._notify(f"[b]Added to queue:[/b] {rich_escape(title)}"))
                except Exception:
                    pass
                rv = getattr(self, "_right_view", None)
                if rv and rv[0] == "queue":
                    try:
                        self.call_from_thread(self._refresh_queue_table)
                    except Exception:
                        pass
            threading.Thread(target=worker, daemon=True).start()
        except Exception as e:
            err_msg = rich_escape(str(e))
            try:
                self._notify(f"[b]Could not add to queue:[/b] {err_msg}", warn=True)
            except Exception:
                pass
            logger.exception("queue_track failed")

    def action_add_to_playlist(self):
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_edit_playlists"):
            return
        try:
            focused = self.focused
            if isinstance(focused, ListView) and getattr(focused, "id", "") == "multi_review_list":
                try:
                    sel_all = set(range(len(focused.children)))
                    self._multi_review_selected = sel_all
                    for i in range(len(focused.children)):
                        try:
                            li: ListItem = focused.children[i]
                            base = getattr(li, 'data', {}) or {}
                            liked = base.get('liked', False)
                            marker_like = '♥' if liked else ' '
                            marker_sel = '◉' if i in sel_all else ' '
                            text = f"[{marker_sel}][{marker_like}] {base.get('title','')} — {base.get('artist','')}"
                            if li.children:
                                li.children[0].update(text)
                            else:
                                li.update(Label(text))
                        except Exception:
                            continue
                    try:
                        self._notify(f"[b]Selected {len(sel_all)} items.[/b]")
                    except Exception:
                        pass
                except Exception:
                    logger.exception('action_add_to_playlist (select-all) failed')
                return
            if not isinstance(focused, DataTable):
                self._notify("[b]Select a track or episode in the list first.[/b]")
                return
            row = self._get_cursor_row(focused)
            if row is None:
                self._notify("[b]No row selected. Move to a track and try again.[/b]", warn=True)
                return
            rtype, item_id, uri, name = self._row_add_target(focused, row)

            # A single playable item adds straight away. This is the common case
            # and also what happens when you are *inside* an album/playlist/artist
            # looking at its individual track (or episode) rows.
            if rtype in ("track", "episode"):
                norm = self._as_add_uri(uri or item_id, rtype)
                if not norm:
                    self._notify('[b]Could not determine the URI for the selected item.[/b]', warn=True)
                    return
                self._pending_multi_add_uris = None
                self._pending_add_uri = norm
                self._show_playlists_for_adding()
                return

            # A container row (album/single/playlist/artist/podcast) selected from
            # a listing: gather every track/episode it holds and add them all.
            if not item_id:
                self._notify('[b]Could not identify the selected item.[/b]', warn=True)
                return
            self._gather_container_and_add(rtype, item_id, name)
        except Exception:
            logger.exception('action_add_to_playlist failed')

    # A container with more than this many tracks asks for confirmation before
    # the bulk add, so a stray Ctrl+Shift+P on a huge playlist or an artist's
    # whole discography cannot silently dump hundreds of tracks into a playlist.
    _BULK_ADD_CONFIRM_MIN = 25

    def _row_add_target(self, table, row):
        """(rtype, item_id, uri, name) for the row under the cursor.

        A ``tracks_table`` has no ``row_to_type`` (its rows are always plain
        tracks); the search / saved-library tables carry the type per row."""
        rtype = (getattr(table, "row_to_type", {}) or {}).get(row) or "track"
        item_id = (getattr(table, "row_to_id", {}) or {}).get(row)
        uri = (getattr(table, "row_to_uri", {}) or {}).get(row)
        obj = (getattr(table, "row_to_obj", {}) or {}).get(row) or {}
        if not item_id:
            item_id = obj.get("id")
        model = getattr(table, "_model_rows", None)
        name = ""
        if model and 0 <= row < len(model):
            name = (model[row] or {}).get("title", "") or ""
        return rtype, item_id, uri, name

    def _as_add_uri(self, raw, rtype="track"):
        """Normalise a single item's URI for ``playlist_add_items``. Tracks and
        episodes are the only kinds a playlist accepts."""
        if not raw:
            return None
        s = str(raw)
        if s.startswith("spotify:"):
            return s
        if "open.spotify.com" in s:
            kind = "episode" if "/episode/" in s else "track"
            try:
                ident = s.split("/")[-1].split("?")[0]
                return f"spotify:{kind}:{ident}"
            except Exception:
                return s
        kind = "episode" if rtype == "episode" else "track"
        ident = (self.spotify._normalize_track_id(s) or s)
        return f"spotify:{kind}:{ident}"

    def _gather_container_and_add(self, rtype, item_id, name):
        """Fetch every track/episode a container holds (off the UI thread) and
        then hand off to the playlist picker, confirming first when it is big."""
        # Snapshot the view we launched from: an artist's whole discography is
        # many calls, and a gather that finishes after the user has moved on must
        # not pop a picker over an unrelated view.
        rv = getattr(self, "_right_view", None)
        origin = tuple(rv[:3]) if rv and len(rv) >= 3 else None
        label = name or rtype
        try:
            self._notify(f"[b]Gathering tracks from[/b] {rich_escape(label)} …", seconds=20)
        except Exception:
            pass

        def worker():
            try:
                uris = self._collect_container_uris(rtype, item_id)
            except Exception:
                logger.exception("gather container %s %s failed", rtype, item_id)
                uris = None

            def done():
                cur = getattr(self, "_right_view", None)
                cur3 = tuple(cur[:3]) if cur and len(cur) >= 3 else None
                if origin is not None and cur3 != origin:
                    return  # user navigated away while we were fetching
                try: self._clear_status_line()
                except Exception: pass
                if uris is None:
                    self._notify(f"[b]Could not gather tracks from[/b] {rich_escape(label)}.", warn=True)
                    return
                if not uris:
                    self._notify(f"[b]Nothing to add from[/b] {rich_escape(label)}.", warn=True)
                    return
                self._pending_add_uri = None
                if len(uris) > self._BULK_ADD_CONFIRM_MIN:
                    self._confirm_bulk_add(uris, label)
                else:
                    self._pending_multi_add_uris = uris
                    self._show_playlists_for_adding()

            try:
                self.call_from_thread(done)
            except Exception:
                if not getattr(self, "_closing", False):
                    logger.exception("gather container done() failed")

        threading.Thread(target=worker, daemon=True).start()

    def _collect_container_uris(self, rtype, item_id):
        sp = self.spotify.ensure()
        if rtype in ("album", "single"):
            return self._album_track_uris(sp, item_id)
        if rtype == "playlist":
            return self._playlist_track_uris(item_id)
        if rtype == "podcast":
            return self._show_episode_uris(sp, item_id)
        if rtype == "artist":
            return self._artist_track_uris(sp, item_id)
        return []

    @staticmethod
    def _album_track_uris(sp, album_id):
        uris, offset = [], 0
        while True:
            pg = sp.album_tracks(album_id, limit=50, offset=offset) or {}
            for t in pg.get("items", []) or []:
                u = (t or {}).get("uri")
                if u:
                    uris.append(u)
            if pg.get("next"):
                offset += 50
            else:
                break
        return uris

    def _playlist_track_uris(self, playlist_id):
        uris, offset = [], 0
        while True:
            pg = self.spotify.playlist_items(
                playlist_id, limit=100, offset=offset,
                fields="items(item(uri),track(uri)),next") or {}
            items = pg.get("items", []) or []
            for it in items:
                u = SpotifyClient.playlist_entry_track(it).get("uri")
                if u:
                    uris.append(u)
            if pg.get("next") and items:
                offset += 100
            else:
                break
        return uris

    @staticmethod
    def _show_episode_uris(sp, show_id):
        uris, offset = [], 0
        while True:
            pg = sp.show_episodes(show_id, limit=50, offset=offset) or {}
            items = pg.get("items", []) or []
            for e in items:
                u = (e or {}).get("uri")
                if u:
                    uris.append(u)
            if pg.get("next"):
                offset += 50
            else:
                break
        return uris

    def _artist_track_uris(self, sp, artist_id):
        """Every track across the artist's albums and singles (the full
        discography the user asked for), de-duplicated."""
        album_ids, seen = [], set()
        for atype in ("album", "single"):
            offset = 0
            while True:
                try:
                    pg = sp.artist_albums(artist_id, album_type=atype, limit=50, offset=offset) or {}
                except TypeError:
                    pg = sp.artist_albums(artist_id, album_type=atype, limit=50) or {}
                    for a in pg.get("items", []) or []:
                        aid = (a or {}).get("id")
                        if aid and aid not in seen:
                            seen.add(aid); album_ids.append(aid)
                    break
                for a in pg.get("items", []) or []:
                    aid = (a or {}).get("id")
                    if aid and aid not in seen:
                        seen.add(aid); album_ids.append(aid)
                if pg.get("next"):
                    offset += 50
                else:
                    break
        uris, seen_u = [], set()
        for aid in album_ids:
            for u in self._album_track_uris(sp, aid):
                if u and u not in seen_u:
                    seen_u.add(u); uris.append(u)
        return uris

    def _confirm_bulk_add(self, uris, label):
        """Make the user acknowledge the count before taking over the picker."""
        self._pending_container_add = {"uris": list(uris), "label": label}
        self._new_view_token("confirm_bulk_add", "")
        right = self._clear_right()
        right.update(
            f"[b]Add {len(uris)} tracks from[/b] {rich_escape(label)}[b]?[/b]\n\n"
            "Press [b]Enter[/b] to choose a playlist, or [b]Esc[/b] to cancel."
        )
        self.level = self.LVL_VIEW

    def action_open_queue(self):
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_control_queue"):
            return
        # Ctrl+C held down (auto-repeat) fires this dozens of times. Ignore it
        # while a mount is in flight and, if the queue is already on screen,
        # just refresh it: never remount #queue_table (DuplicateIds).
        if getattr(self, "_queue_opening", False):
            return
        rv = getattr(self, "_right_view", None)
        if rv and rv[0] == "queue":
            try:
                table = self.right_panel.query_one("#queue_table", DataTable)
            except NoMatches:
                table = None
            if table is not None:
                # Already on screen (maybe with focus on the menu): bring the
                # user back to it without remounting.
                try: table.focus()
                except Exception: pass
                self.level = self.LVL_VIEW
                try:
                    self._refresh_queue_table()
                except Exception:
                    logger.exception('_refresh_queue_table failed')
                return
        self._leave_lyrics_mode()
        token = self._new_view_token("queue", "")
        self._queue_opening = True
        try:
            self.run_worker(self._open_queue_view(token), group="open_queue")
        except Exception:
            self._queue_opening = False
            raise

    async def _open_queue_view(self, token: int):
        try:
            await self._mount_queue_view(token)
        finally:
            self._queue_opening = False

    async def _mount_queue_view(self, token: int):
        # A newer view may already own the panel: leave its intervals and
        # widgets alone.
        if not self._is_current_view("queue", "", token):
            return
        try:
            if getattr(self, '_queue_interval', None):
                try: self._queue_interval.pause()
                except Exception: pass
                self._queue_interval = None
        except Exception:
            pass

        right = self._clear_right()
        # Widget.remove() is asynchronous: wait until the previous view (maybe
        # an older #queue_table) is really gone before mounting the new one.
        try:
            await right.remove_children()
        except Exception:
            logger.exception('remove_children before queue mount failed')
        if not self._is_current_view("queue", "", token):
            return  # another view took over while we waited
        # Same frame as an open playlist: the table borrows #right's border and
        # title (no "Queue" box inside the panel), so the rows fill the panel.
        table = self._create_table_with_full_width(
            ["#", "↓", "♥", "Title", "Artist", "Album", "Duration", "Source"],
            ["num", "dl", "heart", "title", "artist", "album", "dur", "source"],
            widget_id="queue_table", fields_attr="_queue_fields",
        )
        table.row_to_uri = {}; table.row_to_title = {}; table.row_to_id = {}
        table._model_rows = []
        self.right_panel.add_class("table-view")
        table._view_title = "Queue"
        self._refresh_table_title(table)
        right.mount(table)
        try: table.focus()
        except Exception: pass
        self._refit_after_mount(table)
        self.level = self.LVL_VIEW

        try:
            self._refresh_queue_table()
        except Exception:
            logger.exception('Initial _refresh_queue_table failed')

        try:
            self._queue_interval = self.set_interval(5.0, self._refresh_queue_table, pause=False)
        except Exception:
            logger.exception('Could not start queue refresh interval')

    def _show_playlists_for_adding(self):
        try:
            if not self.spotify.has_cached_token():
                auth_url = self.spotify.prepare_authorize_url()
                right = self._clear_right()
                right.update("[b]Spotify authorization required to list your playlists[/b]\n")
                right.mount(Static("Open the URL in your browser, authorize the app and paste the redirect URL here:\n", markup=True))
                if auth_url:
                    right.mount(Static(auth_url, markup=False))
                    try:
                        print("Spotify auth URL:", auth_url)
                    except Exception:
                        pass
                    logger.info("Authorization URL: %s", auth_url)
                    try:
                        self._open_url_in_browser(auth_url)
                    except Exception:
                        logger.exception("The browser could not be opened automatically")
                    self.auth_input = Input(placeholder="Paste the redirect URL and press Enter", id="auth_input")
                    right.mount(self.auth_input)
                    self.auth_input.focus()
                    self.level = self.LVL_VIEW
                    return
                else:
                    logger.warning("prepare_authorize_url returned empty when listing playlists; prompting for credentials")
                    right.update("[b]Not signed in[/b]\nPlease configure your Spotify credentials (Client ID/Secret/Redirect URI) or re-authorize the app.\nYou can set environment variables SPOTIPY_CLIENT_ID/SPOTIPY_CLIENT_SECRET/SPOTIPY_REDIRECT_URI or enter them in the UI.\n\n")
                    self.client_id_input = Input(placeholder="Client ID", id="client_id_input")
                    self.client_secret_input = Input(placeholder="Client Secret", id="client_secret_input")
                    self.redirect_input = Input(placeholder="Redirect URI (e.g. http://127.0.0.1:9999/callback)", id="redirect_input")
                    right.mount(self.client_id_input)
                    right.mount(self.client_secret_input)
                    right.mount(self.redirect_input)
                    self.client_id_input.focus()
                    self.level = self.LVL_VIEW
                    return

            self._new_view_token('add_to_playlist', '')
            right = self._clear_right()
            # Same frame as a playlist / Queue / Devices: the title on #right's border, no header
            # text or box inside the panel.
            right.add_class("table-view")
            right.border_title = "Add to playlist  (Enter: add · Esc: cancel)"
            pls = self.playlists_cache or []
            if not pls:
                try:
                    res = self.spotify.user_playlists(limit=50)
                    pls = (res.get('items') if isinstance(res, dict) else []) or []
                    self.playlists_cache = pls
                except Exception:
                    logger.exception('Could not fetch user playlists')
                    pls = []
            items = []
            for p in pls:
                li = ListItem(Label(p.get('name', '(no name)')))
                li.data = {'type': 'playlist', 'id': p.get('id'), 'name': p.get('name')}
                items.append(li)
            if not items:
                right.mount(Static('[dim]No playlists found.[/dim]', classes='view-note'))
                return
            self.add_pl_list = ListView(*items, id='add_pl_list')
            right.mount(self.add_pl_list)
            self.add_pl_list.focus()
            self.level = self.LVL_VIEW
        except Exception:
            logger.exception('_show_playlists_for_adding failed')

    def action_manage_devices(self):
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_list_devices"):
            return
        self._leave_lyrics_mode()
        try:
            if getattr(self, 'level', None) == self.LVL_VIEW:
                try:
                    tbl = self.query_one('#devices_table', DataTable)
                except NoMatches:
                    tbl = None
                if tbl is not None:
                    try:
                        self._go_to_menu()
                        return
                    except Exception:
                        pass

        except Exception:
            pass

        self._open_devices_view()
        try:
            if getattr(self, "_devices_interval", None) is None:
                self._devices_interval = self.set_interval(5.0, self._refresh_devices_table, pause=False)
        except Exception:
            logger.exception("Device refresh could not be activated")
        self.level = self.LVL_VIEW

    def _open_devices_view(self):
        right = self._clear_right()
        # _clear_right() is removing any #devices_table already on screen: never reuse it (the
        # panel ended up empty), mount a new one once that removal is done (_mount_in_right).
        table = self._create_table_with_full_width(
            ["", "Name", "Type"], ["mark", "title", "device_type"],
            widget_id="devices_table", fields_attr="_device_fields")
        # Same frame as an open playlist or the Queue: the table borrows #right's
        # border and title, no "Devices" box inside the panel.
        table._model_rows = []
        self.right_panel.add_class("table-view")
        table._view_title = "Devices  (Enter: transfer playback)"
        self._refresh_table_title(table)

        def ready():
            try: table.focus()
            except Exception: pass
            self._refresh_devices_table()
        self._mount_in_right(right, table, after_mount=ready)

    def _populate_devices_table(self, table: DataTable, devs):
        """Model rows for the devices (+ the local player's start row), painted
        through _repaint_rows_from_model: the cursor survives the 5 s refresh
        and the "/" filter applies. row_to_device is keyed by model index."""
        rows = list(devs or [])
        entries = []  # (mark, name, type, device_id)

        # Offer a synthetic "start" row only when the local player exists, is
        # usable, and is not already a real device in the list.
        lp = getattr(self, "local_player", None)
        if lp is not None and lp.is_available() and not lp.is_running() \
                and lp.device_name not in {d.get("name") for d in rows}:
            entries.append((GLYPHS["dot_off"], f"{lp.device_name} (start)", "local",
                            LOCAL_START_SENTINEL))

        for d in rows:
            active = GLYPHS["dot_on"] if d.get("is_active") else GLYPHS["dot_off"]
            entries.append((active, d.get("name", "(no name)"), d.get("type", ""), d.get("id")))

        table._model_rows = [{"mark": mark, "title": name, "artist": dtype, "device_type": dtype,
                              "_device": dev_id, "type": "device"}
                             for mark, name, dtype, dev_id in entries]
        table.row_to_device = {i: r["_device"] for i, r in enumerate(table._model_rows)}
        repaint = getattr(self, "_repaint_rows_from_model", None)
        if callable(repaint):
            repaint(table)
        else:                       # bare mixin (unit tests): plain rows
            table.clear()
            for i, r in enumerate(table._model_rows):
                table.add_row(r["mark"], r["title"], r["device_type"], key=i)
        n = sum(1 for r in table._model_rows if r["_device"] != LOCAL_START_SENTINEL)
        table._view_title = f"Devices · {n}  (Enter: transfer playback)"
        retitle = getattr(self, "_refresh_table_title", None)
        if callable(retitle):
            retitle(table)

    def _stop_devices_interval(self):
        """Pause and drop the devices refresh interval (idempotent). Single exit
        point so every route that leaves the devices view stops the polling."""
        it = getattr(self, "_devices_interval", None)
        if it is not None:
            it.pause()
            self._devices_interval = None

    def _refresh_devices_table(self):
        # Runs on the event-loop thread (set_interval callback). If the devices
        # view is no longer mounted, stop the interval here so no further
        # devices() network call is issued (P3: the interval used to keep firing
        # after the view was left via _back_one_level).
        try:
            self.query_one("#devices_table", DataTable)
        except NoMatches:
            self._stop_devices_interval()
            return
        # devices() is a network call; fetch off-thread then paint on the main one.
        def worker():
            try:
                devs = self.spotify.devices()
            except Exception:
                logger.exception("Error fetching devices")
                return
            def paint():
                try:
                    table = self.query_one("#devices_table", DataTable)
                except NoMatches:
                    return
                self._populate_devices_table(table, devs)
            try:
                self.call_from_thread(paint)
            except Exception:
                logger.exception("devices paint scheduling failed")
        threading.Thread(target=worker, daemon=True).start()

    def _play_queue_row(self, row_key, table) -> None:
        """Enter on a Queue row: jump playback to that track (providers that own their queue)."""
        prov = getattr(self, "spotify", None)
        fn = getattr(prov, "play_queue_position", None)
        if not callable(fn):
            self._notify("[b]Playing from the queue is not supported by this provider.[/b]", warn=True)
            return
        try:
            idx = int(row_key.value if hasattr(row_key, "value") else row_key)
        except Exception:
            return
        pos = getattr(table, "row_to_qpos", {}).get(idx)
        if pos is None:      # a "Next in queue" / "Next from" header
            return
        # Enter fires both the "open" action and DataTable.RowSelected: act once.
        now = time.time()
        last = getattr(self, "_last_queue_play", None)
        if last and last[0] == idx and now - last[1] < 0.8:
            return
        self._last_queue_play = (idx, now)

        def work():
            try:
                fn(pos)
            except Exception as exc:
                logger.exception("play_queue_position failed")
                msg = rich_escape(str(exc))
                self.call_from_thread(lambda: self._notify(f"[b]Could not play that entry:[/b] {msg}", warn=True))
                return
            self.call_from_thread(self._refresh_queue_table)
        self.run_worker(work, thread=True, group="queue_play")

    def _queue_cells(self, item: dict, index: int, fields):
        """Build one queue row's cells for the surviving field list — the fitter
        drops low-priority columns on a narrow terminal, so the cells have to
        follow the header rather than a fixed order."""
        if item.get('_header'):
            # "Next in queue" / "Next from: …": a section title, not a track.
            return [Text(str(item.get('title', '')), style="bold") if f == "title" else ""
                    for f in fields]
        heart = Text("❤", style="bold red") if bool(item.get('liked', False)) else Text("")
        cell_map = {
            "num": str(item.get('_num') or (index + 1)),
            "dl": self._dl_cell(item),
            "heart": heart,
            "title": item.get('title', ''),
            "artist": item.get('artist', ''),
            "album": item.get('album', ''),
            "dur": item.get('dur', ''),
            "source": item.get('source', ''),
        }
        return [cell_map[f] for f in fields]

    def _provider_queue_rows(self):
        """Rows for the queue view from a provider that knows its own queue (the mpv-backed ones:
        YouTube Music, Subsonic), in two parts like Spotify: "Next in queue" (what the user queued,
        Source "Queue") and "Next from: <context>" (what follows by itself). None when the active
        provider has no queue of its own (Spotify: its Web API queue is used, as before)."""
        prov = getattr(self, "spotify", None)
        if not isinstance(prov, Provider) or type(prov).queue is Provider.queue:
            return None
        try:
            data = prov.queue() or {}
        except Exception:
            logger.exception("provider queue() failed")
            return []
        items = [t for t in (data.get("queue") or []) if isinstance(t, dict)]
        ctx = getattr(self, "_play_context", None) or {}

        def row(t, source, num):
            return {
                'uri': t.get('uri') or t.get('id'),
                'id': t.get('id'),
                'type': 'track',
                'title': t.get('name') or '',
                'artist': ', '.join(a.get('name', '') for a in (t.get('artists') or []) if isinstance(a, dict)),
                'album': (t.get('album') or {}).get('name', '') if isinstance(t.get('album'), dict) else '',
                'dur': SpotifyClient.fmt_duration(int(t.get('duration_ms') or 0)),
                'source': source,
                '_num': num,
            }

        mine = [t for t in items if t.get('_queued')]
        rest = [t for t in items if not t.get('_queued')]
        rows, num = [], 0
        if mine:
            rows.append({'_header': True, 'title': 'Next in queue', 'uri': None})
            for t in mine:
                num += 1
                rows.append(row(t, 'Queue', num))
        if rest:
            name = ctx.get('name') or ''
            rows.append({'_header': True, 'uri': None,
                         'title': f"Next from: {name}" if name else "Next up"})
            for t in rest:
                num += 1
                rows.append(row(t, ctx.get('source') or 'Playlist', num))
        return rows

    def _queue_view_limit(self) -> int:
        cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
        try:
            n = int(cfg.get("queue_context_limit") or 300)
        except (TypeError, ValueError):
            n = 300
        return max(20, min(1000, n))

    def _spotify_queue_continuation(self, api_items):
        """Spotify only: ``GET /me/player/queue`` returns a short list with no
        paging. When the playback context is a playlist or album and shuffle is
        OFF, the tracks after the last one Spotify listed are taken from that
        context, in its order. Returns ``(tracks, header, note)``; with shuffle
        on (order unknowable) or no loadable context, no tracks and a note."""
        pb = self.spotify.get_playback() or {}
        ctx = pb.get('context') or {}
        uri = ctx.get('uri') or ''
        kind = (ctx.get('type') or (uri.split(':')[1] if uri.count(':') >= 2 else '')).lower()
        cur = (pb.get('item') or {}).get('uri')
        if pb.get('shuffle_state'):
            return [], None, "shuffle is on: only Spotify's own short queue is known"
        if kind not in ('playlist', 'album') or not cur:
            return [], None, "Spotify's queue only (no playlist/album context to continue)"
        cid = uri.split(':')[-1]
        limit = self._queue_view_limit()
        tracks, name = self._queue_context_tracks(kind, cid, uri, cur, pb)
        uris = [t['uri'] for t in tracks]
        if cur not in uris:
            return [], None, "the playing track is not in its context any more"
        listed = [((t.get('track') if isinstance(t, dict) and t.get('track') else t) or {}).get('uri')
                  for t in (api_items or [])]
        # The song may appear several times: of all its positions take the one
        # after which Spotify's list lines up best with the context order.
        # Spotify lists user-queued songs first, then the next context tracks,
        # so the context part is a tail of ``listed`` equal to the head of
        # ``after`` (matching "anywhere" jumped over hundreds of songs).
        best_after, best_start = None, -1
        for p in (i for i, u in enumerate(uris) if u == cur):
            after = tracks[p + 1:]
            start = 0
            for k in range(len(listed) + 1):
                n = len(listed) - k
                if n and [t['uri'] for t in after[:n]] == listed[k:]:
                    start = n
                    break
            if start > best_start:
                best_after, best_start = after, start
        rest = best_after[best_start:best_start + limit]
        if not rest:
            return [], None, "Spotify's queue reaches the end of the context"
        return rest, f"Then from {name} (deduced from its order, not from Spotify)", \
            "Spotify lists a short queue; the rest is deduced from the playlist order"

    def _queue_context_tracks(self, kind, cid, ctx_uri, cur, pb):
        """Tracks of the playing playlist/album and its name. Cached for 2 min
        per context (the 5 s queue refresh must not re-download it); paged
        until the playing track is found plus the rows the view can show."""
        import time as _time
        cache = getattr(self, '_queue_ctx_cache', None)
        if cache and cache['uri'] == ctx_uri and _time.time() - cache['t'] < 120 \
                and any(t['uri'] == cur for t in cache['tracks']):
            return cache['tracks'], cache['name']
        limit = self._queue_view_limit()
        tracks = []
        found = -1
        off = 0
        for _ in range(60):             # safety cap: 6000 tracks
            if kind == 'playlist':
                page = self.spotify.playlist_items(cid, limit=100, offset=off) or {}
            else:
                page = self.spotify.ensure().album_tracks(cid, limit=50, offset=off) or {}
            items = page.get('items') or []
            for e in items:
                t = SpotifyClient.playlist_entry_track(e) if kind == 'playlist' else e
                if t and t.get('uri'):
                    tracks.append(t)
                    if found < 0 and t['uri'] == cur:
                        found = len(tracks) - 1
            if not page.get('next') or not items:
                break
            off += len(items)
            if found >= 0 and len(tracks) - found > limit + 100:
                break
        if kind == 'playlist':
            name = next((p.get('name') for p in (getattr(self, 'playlists_cache', None) or [])
                         if p.get('id') == cid), None) or 'the playlist'
        else:
            name = ((pb.get('item') or {}).get('album') or {}).get('name') or 'the album'
        self._queue_ctx_cache = {'uri': ctx_uri, 't': _time.time(), 'tracks': tracks, 'name': name}
        return tracks, name

    def _queue_refresh_finished(self):
        """Main thread: release the in-flight guard and run one coalesced
        refresh if more were requested meanwhile."""
        self._queue_refresh_running = False
        if getattr(self, "_queue_refresh_pending", False):
            self._queue_refresh_pending = False
            self._refresh_queue_table()

    def _refresh_queue_table(self):
        # One fetch at a time: held Ctrl+C or the 5 s interval landing on a slow
        # request used to start a parallel API call per trigger. Requests made
        # while one is in flight collapse into a single follow-up refresh.
        if getattr(self, "_queue_refresh_running", False):
            self._queue_refresh_pending = True
            return
        self._queue_refresh_running = True

        def worker():
            try:
                _worker_body()
            finally:
                try:
                    self.call_from_thread(self._queue_refresh_finished)
                except Exception:
                    self._queue_refresh_running = False

        def _worker_body():
            self._queue_note = ''
            try:
                api_items = None
                try:
                    mgr = getattr(self.spotify, '_auth_manager', None)
                    token_info = None
                    if mgr and hasattr(mgr, 'get_cached_token'):
                        try:
                            token_info = mgr.get_cached_token()
                        except Exception:
                            token_info = None
                    access = token_info.get('access_token') if token_info else None
                    if access and requests is not None:
                        try:
                            h = {'Authorization': f'Bearer {access}'}
                            r = requests.get('https://api.spotify.com/v1/me/player/queue', headers=h, timeout=6)
                            if r.status_code == 200:
                                j = r.json()
                                api_items = j.get('queue') or []
                        except Exception:
                            logger.exception('Failed fetching /me/player/queue')
                except Exception:
                    logger.exception('Queue API attempt failed')

                q = []
                provider_rows = self._provider_queue_rows() if api_items is None else None
                if provider_rows is not None:
                    # The provider's own queue is the truth: no stale _local_queue entries, and
                    # no de-duplication (one song may be both queued and in the playlist).
                    q = provider_rows
                elif api_items:
                    def _sp_row(tr, source):
                        return {
                            'uri': tr.get('uri') or tr.get('id'), 'id': tr.get('id'), 'type': 'track',
                            'title': tr.get('name') or '',
                            'artist': ', '.join(a.get('name', '') for a in (tr.get('artists') or []) if a),
                            'album': (tr.get('album') or {}).get('name', '') if isinstance(tr.get('album'), dict) else '',
                            'dur': SpotifyClient.fmt_duration(int(tr.get('duration_ms') or 0)),
                            'source': source,
                        }
                    # What Spotify itself reports (its queue endpoint has no paging
                    # and returns only a short list).
                    q.append({'_header': True, 'uri': None, 'title': 'Spotify queue (from Spotify)'})
                    for t in api_items:
                        try:
                            tr = t.get('track') if isinstance(t, dict) and t.get('track') else t
                            q.append(_sp_row(tr, 'Spotify queue'))
                        except Exception:
                            continue
                    # …then, when it can be known, the rest of the playlist/album
                    # it is playing from, marked as deduced.
                    try:
                        extra, header, self._queue_note = self._spotify_queue_continuation(api_items)
                    except Exception:
                        logger.exception('queue continuation failed')
                        extra, header, self._queue_note = [], None, ''
                    if header:
                        q.append({'_header': True, 'uri': None, 'title': header})
                    for tr in extra:
                        q.append(_sp_row(tr, 'Deduced'))
                else:
                    q = list(getattr(self, '_local_queue', []) or [])

                try:
                    if provider_rows is None:     # Spotify / no provider queue: merge with _local_queue
                        max_items = self._queue_view_limit() + 4     # + section headers
                        seen = set()
                        merged = []
                        for it in q:
                            uid = (it.get('uri') or it.get('id') or '').strip()
                            if it.get('_header'):
                                merged.append(it)
                                continue
                            if uid and uid in seen and it.get('source') != 'Deduced':
                                continue
                            merged.append(it)
                            if uid: seen.add(uid)
                            if len(merged) >= max_items:
                                break

                        if len(merged) < max_items:
                            local = list(getattr(self, '_local_queue', []) or [])
                            for lit in reversed(local):
                                if len(merged) >= max_items:
                                    break
                                luid = (lit.get('uri') or lit.get('id') or '').strip()
                                if luid and luid in seen:
                                    continue
                                merged.append(lit)
                                if luid: seen.add(luid)

                        q = merged[:max_items]
                except Exception:
                    try:
                        q = (q or [])[:50]
                    except Exception:
                        pass

                try:
                    import time as _time
                    now = _time.time()
                    lc = getattr(self, '_queue_liked_cache', None)
                    if lc is None:
                        lc = self._queue_liked_cache = {}
                    ids = []
                    for it in q:
                        u = (it.get('uri') or it.get('id') or '')
                        tid = None
                        if it.get('_header') or (isinstance(u, str) and u.startswith('spotify:local:')):
                            ids.append('')      # headers and local files have no Spotify id
                            continue
                        try:
                            tid = self.spotify._normalize_track_id(u)
                        except Exception:
                            tid = None
                        if not tid and isinstance(u, str) and u:
                            tid = u.split('/')[-1].split('?')[0]
                        ids.append(tid or '')
                    # Ask Spotify only for ids not checked in the last 60 s: the
                    # view now holds hundreds of rows and refreshes every 5 s.
                    todo = list(dict.fromkeys(i for i in ids if i and (i not in lc or now - lc[i][1] > 60)))
                    if todo:
                        try:
                            res = self.spotify.check_saved_tracks(todo) or []
                            for i, r in zip(todo, res):
                                lc[i] = (bool(r), now)
                        except Exception:
                            logger.exception('queue liked check failed')
                    for idx, it in enumerate(q):
                        tid = ids[idx] if idx < len(ids) else ''
                        if tid and tid in lc:
                            it['liked'] = lc[tid][0]
                        else:
                            it['liked'] = bool(it.get('liked', False))
                except Exception:
                    logger.exception('Could not determine liked state for queue items')

                n_row = 0
                for it in q:       # real position: section headers take no number
                    if not it.get('_header'):
                        n_row += 1
                        it['_num'] = n_row

                def _update():
                    try:
                        table = None
                        try: table = self.query_one("#queue_table", DataTable)
                        except NoMatches: table = None
                        if table is None:
                            return
                        table.row_to_uri = {}
                        table.row_to_title = {}
                        table.row_to_id = {}
                        table._model_rows = list(q)
                        table._liked_map = {i: bool(it.get('liked')) for i, it in enumerate(q)}
                        # Same repaint as every content table: keeps the cursor and
                        # scroll across the 5 s refresh and honours the "/" filter.
                        self._repaint_rows_from_model(table)
                        n_real = sum(1 for it in q if not it.get('_header'))
                        note = getattr(self, '_queue_note', '') or ''
                        table._view_title = f"Queue · {n_real} songs" + (f"  [dim]({note})[/dim]" if note else "")
                        self._refresh_table_title(table)
                    except Exception:
                        logger.exception('UI update for queue failed')

                try:
                    self.call_from_thread(_update)
                except Exception:
                    if getattr(self, '_closing', False):
                        logger.debug("queue refresh paint dropped during teardown")
                    else:
                        logger.exception("queue refresh paint: call_from_thread failed")

            except Exception:
                logger.exception('Error in _refresh_queue_table worker')

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self._queue_refresh_running = False
            logger.exception('Could not start _refresh_queue_table thread')
