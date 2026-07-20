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

from ..config import logger
from ..constants import GLYPHS
from ..spotify_client import SpotifyClient

class QueueDevicesMixin:
    def action_queue_track(self):
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
                        "source": getattr(focused, 'id', '') or row_obj.get('source', '') or '',
                        "added_at": int(time.time() * 1000),
                    }
                    self._local_queue.append(qitem)
                except Exception:
                    logger.exception("Could not record local queue item")
                try:
                    self.call_from_thread(lambda: self._notify(f"[b]Added to queue:[/b] {rich_escape(title)}"))
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
            uri = getattr(focused, 'row_to_uri', {}).get(row) or getattr(focused, 'row_to_id', {}).get(row)
            if not uri:
                self._notify('[b]Could not determine URI for selected item.[/b]', warn=True)
                return
            try:
                s = str(uri)
                norm = None
                if s.startswith('spotify:'):
                    norm = s
                elif 'open.spotify.com' in s:
                    try:
                        norm_id = s.split('/')[-1].split('?')[0]
                        norm = f'spotify:track:{norm_id}'
                    except Exception:
                        norm = s
                else:
                    tid = self.spotify._normalize_track_id(s) or s
                    norm = f'spotify:track:{tid}'
                self._pending_add_uri = norm
            except Exception:
                self._pending_add_uri = uri
            self._show_playlists_for_adding()
        except Exception:
            logger.exception('action_add_to_playlist failed')

    def action_open_queue(self):
        self._leave_lyrics_mode()
        self._new_view_token("queue", "")

        try:
            if getattr(self, '_queue_interval', None):
                try: self._queue_interval.pause()
                except Exception: pass
                self._queue_interval = None
        except Exception:
            pass

        right = self._clear_right()
        right.mount(Static("[b]Queue[/b]", markup=True))

        table = self._create_table_with_full_width(
            ["#", "♥", "Title", "Artist", "Album", "Duration", "Source"],
            ["num", "heart", "title", "artist", "album", "dur", "source"],
            widget_id="queue_table", fields_attr="_queue_fields",
        )
        table.row_to_uri = {}; table.row_to_title = {}; table.row_to_id = {}
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
            right.mount(Static('[b]Add to playlist[/b]\nSelect a playlist and press Enter.', markup=True))
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
                right.mount(Static('[dim]No playlists found.[/dim]'))
                return
            self.add_pl_list = ListView(*items, id='add_pl_list')
            right.mount(self.add_pl_list)
            self.add_pl_list.focus()
            self.level = self.LVL_VIEW
        except Exception:
            logger.exception('_show_playlists_for_adding failed')

    def action_manage_devices(self):
        self._leave_lyrics_mode()
        try:
            if getattr(self, 'level', None) == self.LVL_VIEW:
                try:
                    tbl = self.query_one('#devices_table', DataTable)
                except NoMatches:
                    tbl = None
                if tbl is not None:
                    try:
                        self.action_escape_to_menu()
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
        try:
            table = self.query_one("#devices_table", DataTable)
        except NoMatches:
            table = None
        if table is None:
            table = self._create_table_with_full_width(
                ["", "Name", "Type"], ["mark", "title", "device_type"],
                widget_id="devices_table", fields_attr="_device_fields")
            right.mount(Static("[b]Devices[/b] (Press Enter to transfer)", markup=True))
            right.mount(table)
        table.focus()
        self._refresh_devices_table()

    def _populate_devices_table(self, table: DataTable, devs):
        table.clear()
        table.row_to_device = {}
        for i, d in enumerate(devs or []):
            active = GLYPHS["dot_on"] if d.get("is_active") else GLYPHS["dot_off"]
            table.add_row(active, d.get("name", "(no name)"), d.get("type", ""), key=i)
            table.row_to_device[i] = d.get("id")

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

    def _queue_cells(self, item: dict, index: int, fields):
        """Build one queue row's cells for the surviving field list — the fitter
        drops low-priority columns on a narrow terminal, so the cells have to
        follow the header rather than a fixed order."""
        heart = Text("❤", style="bold red") if bool(item.get('liked', False)) else Text("")
        cell_map = {
            "num": str(index + 1),
            "heart": heart,
            "title": item.get('title', ''),
            "artist": item.get('artist', ''),
            "album": item.get('album', ''),
            "dur": item.get('dur', ''),
            "source": item.get('source', ''),
        }
        return [cell_map[f] for f in fields]

    def _refresh_queue_table(self):
        def worker():
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
                if api_items:
                    for t in api_items:
                        try:
                            tr = t.get('track') if isinstance(t, dict) and t.get('track') else t
                            title = tr.get('name') or ''
                            artists = ', '.join(a.get('name','') for a in (tr.get('artists') or []) if a)
                            album = (tr.get('album') or {}).get('name','')
                            dur = SpotifyClient.fmt_duration(int(tr.get('duration_ms') or 0))
                            uri = tr.get('uri') or tr.get('id')
                            q.append({
                                'uri': uri,
                                'title': title,
                                'artist': artists,
                                'album': album,
                                'dur': dur,
                                'source': 'device-queue',
                            })
                        except Exception:
                            continue
                else:
                    q = list(getattr(self, '_local_queue', []) or [])

                try:
                    max_items = 50
                    seen = set()
                    merged = []
                    for it in q:
                        uid = (it.get('uri') or it.get('id') or '').strip()
                        if uid and uid in seen:
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
                    ids = []
                    for it in q:
                        u = (it.get('uri') or it.get('id') or '')
                        tid = None
                        try:
                            tid = self.spotify._normalize_track_id(u)
                        except Exception:
                            tid = None
                        if not tid and isinstance(u, str) and u:
                            tid = u.split('/')[-1].split('?')[0]
                        ids.append(tid or '')
                    liked_results = []
                    try:
                        liked_results = self.spotify.check_saved_tracks([i for i in ids if i]) if any(ids) else []
                    except Exception:
                        liked_results = []
                    try:
                        li = 0
                        for idx, it in enumerate(q):
                            tid = ids[idx] if idx < len(ids) else ''
                            if tid and li < len(liked_results):
                                it['liked'] = bool(liked_results[li])
                                li += 1
                            else:
                                it['liked'] = bool(it.get('liked', False))
                    except Exception:
                        pass
                except Exception:
                    logger.exception('Could not determine liked state for queue items')

                def _update():
                    try:
                        table = None
                        try: table = self.query_one("#queue_table", DataTable)
                        except NoMatches: table = None
                        if table is None:
                            return
                        table.clear()
                        table.row_to_uri = {}
                        table.row_to_title = {}
                        table.row_to_id = {}
                        table._model_rows = list(q)
                        fields = getattr(table, "_queue_fields", None) or [
                            "num", "heart", "title", "artist", "album", "dur", "source"]
                        for i, item in enumerate(q):
                            try:
                                table.add_row(*self._queue_cells(item, i, fields), key=i)
                            except Exception:
                                try:
                                    table.add_row(str(i+1), item.get('title',''), key=i)
                                except Exception:
                                    pass
                            table.row_to_uri[i] = item.get('uri')
                            table.row_to_title[i] = f"{item.get('title','')} {GLYPHS['sep']} {item.get('artist','')}"
                        try: table.refresh()
                        except Exception: pass
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
            logger.exception('Could not start _refresh_queue_table thread')
