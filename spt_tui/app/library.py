"""Playlists and saved-library views (albums, artists, podcasts...)."""

from __future__ import annotations

import os
import json
import threading
import webbrowser
from typing import Dict, List

from textual.widgets import Input, Static, ListItem, Label, DataTable
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
from ..config import logger
from ..spotify_client import SpotifyClient

class LibraryMixin:
    def action_prompt_import_playlists(self) -> None:
        try:
            which = self.section_order[self.section_idx]
            if which != 'lib':

                return
            if not hasattr(self, 'lib_list') or self.lib_list is None:
                return
            if self.lib_list.index is None:
                return
            li: ListItem = self.lib_list.children[self.lib_list.index]
            name = (getattr(li, 'data', {}) or {}).get('name', '')
            if name != 'Import Playlists':
                return

            self._new_view_token('import_input', '')

            right = self._clear_right()
            self.import_input = Input(placeholder='Paste the playlist URI or ID and press Enter…')
            right.mount(Static('[b]Add playlist to Import Playlists[/b]\nEnter playlist URI or ID and press Enter.'))
            right.mount(self.import_input)
            self.import_input.focus()
            self.level = self.LVL_VIEW
            return
        except Exception:
            logger.exception('action_prompt_import_playlists failed')

    def action_prompt_create_playlist(self) -> None:
        try:
            which = self.section_order[self.section_idx]
            if which != 'lib':
                return
            if not hasattr(self, 'lib_list') or self.lib_list is None:
                return
            if self.lib_list.index is None:
                return
            li: ListItem = self.lib_list.children[self.lib_list.index]
            name = (getattr(li, 'data', {}) or {}).get('name', '')
            if name != 'Create Playlist':
                return

            if not self.spotify.has_cached_token():
                auth_url = self.spotify.prepare_authorize_url() or ""
                right = self._clear_right()
                right.update("[b]Spotify authorization required to create playlists[/b]\n")
                right.mount(Static("Open the URL in your browser, authorize the app and paste the redirect URL here:\n", markup=True))
                right.mount(Static(auth_url, markup=False))
                try:
                    if auth_url:
                        webbrowser.open(auth_url)
                except Exception:
                    logger.exception("The browser could not be opened automatically")
                self.auth_input = Input(placeholder="Paste the redirect URL and press Enter", id="auth_input")
                right.mount(self.auth_input)
                self.auth_input.focus()
                self.level = self.LVL_VIEW
                return
            self._new_view_token('create_playlist', '')
            right = self._clear_right()
            right.mount(Static('[b]Create new Spotify playlist[/b]\nEnter the playlist name and press Enter.'))
            self.create_name_input = Input(placeholder='Playlist name…')
            right.mount(self.create_name_input)
            self.create_name_input.focus()
            self._create_playlist_state = {}
            self.level = self.LVL_VIEW
            return
        except Exception:
            logger.exception('action_prompt_create_playlist failed')

    def action_prompt_seek_settings(self) -> None:
        try:
            self._new_view_token('seek_settings', '')
            right = self._clear_right()
            cur_vol_down = int((config.LOCAL_CFG.get('seek_volume_down') if isinstance(config.LOCAL_CFG, dict) else None) or 5)
            cur_vol_up = int((config.LOCAL_CFG.get('seek_volume_up') if isinstance(config.LOCAL_CFG, dict) else None) or 5)
            cur_track = int((config.LOCAL_CFG.get('seek_seconds_track') if isinstance(config.LOCAL_CFG, dict) else None) or 5)
            cur_episode = int((config.LOCAL_CFG.get('seek_seconds_episode') if isinstance(config.LOCAL_CFG, dict) else None) or 15)
            right.mount(Static('[b]Configure volume steps and seek jump times[/b]\nEnter values (numbers). Press Enter or use Up/Down arrows to move between fields.'))
            right.mount(Static(f'Current: Vol Down = {cur_vol_down}%, Vol Up = {cur_vol_up}%, Tracks = {cur_track}s, Episodes = {cur_episode}s'))
            try:
                right.mount(Static('[dim]Press ESC to go back[/dim]'))
            except Exception:
                pass
            self.seek_vol_down_input = Input(placeholder=f'Volume Down step percent (current {cur_vol_down})', id='seek_vol_down_input')
            self.seek_vol_up_input = Input(placeholder=f'Volume Up step percent (current {cur_vol_up})', id='seek_vol_up_input')
            self.seek_track_input = Input(placeholder=f'Track jump seconds (current {cur_track})', id='seek_track_input')
            self.seek_episode_input = Input(placeholder=f'Episode jump seconds (current {cur_episode})', id='seek_episode_input')
            right.mount(self.seek_vol_down_input)
            right.mount(self.seek_vol_up_input)
            right.mount(self.seek_track_input)
            right.mount(self.seek_episode_input)
            self.seek_vol_down_input.focus()
            self.level = self.LVL_VIEW
            return
        except Exception:
            logger.exception('action_prompt_seek_settings failed')

    def _open_library_item(self, name: str) -> bool:
        """Single source of truth for opening a Library list item, so keyboard
        (action_open) and mouse (on_list_view_selected) behave identically.
        Returns True if the name was recognised."""
        handlers = {
            "Create Playlist": self.action_prompt_create_playlist,
            "Import Playlists": self._open_import_playlists,
            "Recently Played": self._open_recently_table,
            "Liked Songs": self._open_liked_table,
            "Saved Artists": self._open_saved_artists,
            "Saved Albums": self._open_saved_albums,
            "Saved Podcasts": self._open_saved_podcasts,
            "Saved Episodes": self._open_saved_episodes,
        }
        fn = handlers.get(name)
        if fn is None:
            return False
        try:
            fn()
        except Exception:
            logger.exception("Error opening library item %r", name)
        return True

    def _playlists_cache_path(self) -> str:
        return os.path.join(config.CACHE_DIR, "playlists_cache.json")

    def _save_playlists_disk(self, items) -> None:
        try:
            minimal = [{"name": p.get("name"), "id": p.get("id"), "uri": p.get("uri")}
                       for p in items if p]
            with open(self._playlists_cache_path(), "w", encoding="utf-8") as f:
                json.dump(minimal, f, ensure_ascii=False)
        except Exception:
            logger.exception("could not save playlists cache")

    def paint_playlists_from_disk(self) -> None:
        """Instant startup paint from the on-disk cache; the network refresh
        (``_load_playlists``) then overwrites it a moment later."""
        try:
            path = self._playlists_cache_path()
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                items = json.load(f) or []
        except Exception:
            logger.exception("could not load playlists cache")
            return
        if not items:
            return
        self.playlists_cache = items
        try:
            self.pl_list.clear()
            for p in items:
                li = ListItem(Label(p.get("name", "(no title)")))
                li.data = {"type": "playlist", "name": p.get("name"), "id": p.get("id"), "uri": p.get("uri")}
                self.pl_list.append(li)
        except Exception:
            logger.exception("could not paint cached playlists")

    def _load_playlists(self, force: bool = False):
        try:
            if not force and not getattr(self, '_auto_load_playlists', False):
                return
            # Gate on a cached token, not a currently-*valid* one: an expired
            # token is refreshed transparently by ensure() on the API call
            # below. Requiring validity here made playlists silently fail to
            # load after the ~1h token expiry (the "press Ctrl+R" workaround).
            if not self.spotify.has_cached_token():
                self.playlists_cache = []
                def _paint_empty():
                    try:
                        self.pl_list.clear()
                    except Exception:
                        pass
                try:
                    self.call_from_thread(_paint_empty)
                except Exception:
                    _paint_empty()
                return

            items: List[Dict] = []; offset = 0
            while True:
                page = self.spotify.user_playlists(limit=50, offset=offset)
                items.extend(page.get("items", []))
                if page.get("next"): offset += 50
                else: break
            self.playlists_cache = items
            self._save_playlists_disk(items)
            def paint():
                self.pl_list.clear()
                for p in items:
                    li = ListItem(Label(p.get("name", "(no title)")))
                    li.data = {"type": "playlist", "name": p.get("name"), "id": p.get("id"), "uri": p.get("uri")}
                    self.pl_list.append(li)
                if self.pl_list.children and 0 <= self.pl_index < len(self.pl_list.children):
                    self._suppress_first_selection = True; self.pl_list.index = self.pl_index
            self.call_from_thread(paint)
        except Exception as e:
            logger.exception("Error loading playlists: %s", e)
            err_msg = rich_escape(str(e))
            def show_err():
                right = self._clear_right()
                right.update(f"[b]Error loading playlists:[/b] {err_msg}")
            self.call_from_thread(show_err)

    def _open_playlist_table(self, pdata: Dict):
        pl_id = pdata.get("id",""); pl_name = pdata.get("name","")

        token = self._new_view_token("playlist", pl_id)
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            try:
                self._right_view = (rv[0], rv[1], rv[2], pdata)
            except Exception:
                logger.exception("_open_playlist_table: failed attaching pdata to right_view")
        pl_uri = pdata.get("uri", "")
        self._clear_right()
        self._safe_update_right("playlist", pl_id, token, f"[b]Loading playlist:[/b] {rich_escape(pl_name)} …")

        # Only request the fields we actually render; big playlists return huge
        # track objects otherwise, which dominates the load time.
        FIELDS = ("items(added_at,track(id,uri,name,type,duration_ms,"
                  "artists(name),album(name))),total")

        title = f"[b]{rich_escape(pl_name)}[/b]"

        def _extract(page):
            page_rows, page_track_ids = [], []
            for it in page.get("items", []):
                track = (it.get("track") or {})
                if not track:
                    continue
                page_rows.append({
                    "id": track.get("id"),
                    "uri": track.get("uri"),
                    "title": track.get("name", "(no title)"),
                    "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
                    "album": (track.get("album", {}) or {}).get("name", ""),
                    "dur": self.spotify.fmt_duration(track.get("duration_ms") or 0),
                    "added": SpotifyClient.fmt_date(it.get("added_at")),
                })
                # Only real tracks can be "liked"; episodes/podcasts would make
                # current_user_saved_tracks_contains 400 and (via the per-id
                # retry) drag the whole load to a crawl.
                if track.get("id") and (track.get("type") or "track") == "track":
                    page_track_ids.append(track.get("id"))
            return page_rows, page_track_ids

        def _paint_preview(rows):
            def do():
                if not self._is_current_view("playlist", pl_id, token):
                    return
                self._render_tracks_table(
                    title, rows, None, context_uri=pl_uri,
                    context_uris=[r["uri"] for r in rows],
                )
            self.call_from_thread(do)

        def _paint_final(rows, liked):
            def do():
                if not self._is_current_view("playlist", pl_id, token):
                    return
                try:
                    table = self.query_one("#tracks_table", DataTable)
                except Exception:
                    table = None
                if table is None:
                    # No preview was shown (single-page playlist): fresh render.
                    self._render_tracks_table(
                        title, rows, liked, context_uri=pl_uri,
                        context_uris=[r["uri"] for r in rows],
                    )
                    return
                # Update the preview table in place — mounting a second widget
                # with id "tracks_table" would collide (remove() is async).
                table._model_rows = rows
                table._liked_map = {i: bool(liked[i]) for i in range(len(rows))}
                table._context_uri = pl_uri
                table._context_uris = [r["uri"] for r in rows]
                table.row_to_uri = {}; table.row_to_title = {}; table.row_to_id = {}
                self._repaint_rows_from_model(table)
            self.call_from_thread(do)

        def _set_title(text):
            def do():
                try:
                    self.query_one("#tracks_title", Static).update(text)
                except Exception:
                    pass
            self.call_from_thread(do)

        def worker():
            try:
                # First page: paged sequentially (spotipy shares one HTTP
                # session/auth manager, so parallel calls corrupt responses).
                first = self.spotify.playlist_items(pl_id, limit=100, offset=0, fields=FIELDS) or {}
                total = int(first.get("total") or 0)
                rows, track_ids = _extract(first)

                # Multi-page playlist: show the first page right away so the user
                # isn't staring at "Loading…" while the rest streams in.
                multipage = total > len(rows)
                if multipage:
                    _paint_preview(list(rows))
                    _set_title(f"{title}  [dim](loading {len(rows)}/{total})[/dim]")

                offset = 100
                while offset < total:
                    page = self.spotify.playlist_items(pl_id, limit=100, offset=offset, fields=FIELDS) or {}
                    page_rows, page_track_ids = _extract(page)
                    if not page_rows:
                        break
                    rows += page_rows
                    track_ids += page_track_ids
                    offset += 100
                    _set_title(f"{title}  [dim](loading {min(len(rows), total)}/{total})[/dim]")

                _set_title(f"{title}  [dim](checking likes…)[/dim]" if multipage else title)
                liked_by_id = dict(zip(track_ids, self.spotify.check_saved_tracks(track_ids))) if track_ids else {}
                liked = [bool(liked_by_id.get(r.get("id"), False)) for r in rows]
            except Exception:
                logger.exception("_open_playlist_table worker failed")
                return

            # Final render: full track list with hearts resolved, clean title.
            _paint_final(rows, liked)
            _set_title(title)
        threading.Thread(target=worker, daemon=True).start()

    def _open_saved_artists(self):
        token = self._new_view_token("artists", "")
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            try:
                self._right_view = (rv[0], rv[1], rv[2], None)
            except Exception:
                pass
        self._safe_update_right("artists", "", token, "[b]Loading Saved Artists[/b]")

        def worker():
            rows = []
            try:
                sp = self.spotify.ensure()
                try:
                    page = sp.current_user_followed_artists(limit=50) or {}
                    artists = (page.get('artists') or {}).get('items', [])
                except Exception:
                    try:
                        artists = sp.current_user_followed_artists() or {}
                        artists = (artists.get('artists') or {}).get('items', [])
                    except Exception:
                        artists = []

                for it in artists:
                    if not it: continue
                    rows.append({
                        "type": "artist",
                        "id": it.get("id"),
                        "uri": it.get("uri") or it.get("external_urls", {}).get("spotify", ""),
                        "title": it.get("name", "(no name)"),
                        "artist": "",
                        "album": "",
                        "dur": "",
                        "raw": it,
                        "saved": True,
                    })
            except Exception:
                logger.exception("_open_saved_artists: error fetching saved artists")

            def paint():
                if not self._is_current_view("artists", "", token): return
                title = "[b]Saved Artists[/b]"
                self._render_search_table(title, rows)
            self.call_from_thread(paint)

        threading.Thread(target=worker, daemon=True).start()

    def _open_saved_podcasts(self):
        token = self._new_view_token("podcasts", "")
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            try:
                self._right_view = (rv[0], rv[1], rv[2], None)
            except Exception:
                pass
        self._safe_update_right("podcasts", "", token, "[b]Loading Saved Podcasts[/b]")

        def worker():
            rows = []
            offset = 0
            try:
                sp = self.spotify.ensure()
                while True:
                    page = None
                    try:
                        page = sp.current_user_saved_shows(limit=50, offset=offset) or {}
                    except Exception:
                        try:
                            page = sp.current_user_saved_shows(limit=50) or {}
                        except Exception:
                            page = {}
                    for it in page.get('items', []):
                        show = (it.get('show') or {})
                        if not show: continue
                        rows.append({
                            "type": "podcast",
                            "id": show.get("id"),
                            "uri": show.get("uri") or show.get("external_urls", {}).get("spotify", ""),
                            "title": show.get("name", "(no title)"),
                            "artist": show.get('publisher',''),
                            "album": "",
                            "dur": "",
                            "raw": show,
                            "saved": True,
                        })
                    if page.get('next'):
                        offset += 50
                    else:
                        break
            except Exception:
                logger.exception("_open_saved_podcasts: error fetching saved shows/podcasts")

            def paint():
                if not self._is_current_view("podcasts", "", token): return
                title = "[b]Saved Podcasts[/b]"
                self._render_search_table(title, rows)
            self.call_from_thread(paint)

        threading.Thread(target=worker, daemon=True).start()

    def _open_saved_episodes(self):
        token = self._new_view_token("episodes", "")
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            try:
                self._right_view = (rv[0], rv[1], rv[2], None)
            except Exception:
                pass
        self._safe_update_right("episodes", "", token, "[b]Loading Saved Episodes[/b]")

        def worker():
            rows = []
            offset = 0
            try:
                sp = self.spotify.ensure()
                while True:
                    page = None
                    try:
                        page = sp.current_user_saved_episodes(limit=50, offset=offset) or {}
                    except Exception:
                        try:
                            page = sp.current_user_saved_episodes(limit=50) or {}
                        except Exception:
                            page = {}
                    for it in page.get('items', []):
                        ep = (it.get('episode') or {})
                        if not ep: continue
                        rows.append({
                            "type": "episode",
                            "id": ep.get("id"),
                            "uri": ep.get("uri") or ep.get("external_urls", {}).get("spotify", ""),
                            "title": ep.get("name", "(no title)"),
                            "artist": ep.get('show', {}).get('name',''),
                            "album": "",
                            "dur": self.spotify.fmt_duration(ep.get('duration_ms') or 0),
                            "raw": ep,
                            "saved": True,
                        })
                    if page.get('next'):
                        offset += 50
                    else:
                        break
            except Exception:
                logger.exception("_open_saved_episodes: error fetching saved episodes")

            def paint():
                if not self._is_current_view("episodes", "", token): return
                title = "[b]Saved Episodes[/b]"
                self._render_search_table(title, rows)
            self.call_from_thread(paint)

        threading.Thread(target=worker, daemon=True).start()

    def _open_liked_table(self):
        token = self._new_view_token("liked", "")
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            self._right_view = (rv[0], rv[1], rv[2], None)
        self._safe_update_right("liked", "", token, "[b]Loading Liked Songs…[/b]")
        def worker():
            rows, offset = [], 0
            while True:
                page = self.spotify.saved_tracks(limit=50, offset=offset)
                for it in page.get("items", []):
                    track = (it.get("track") or {})
                    if not track: continue
                    rows.append({
                        "id": track.get("id"),
                        "uri": track.get("uri"),
                        "title": track.get("name", ""),
                        "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
                        "album": (track.get("album", {}) or {}).get("name", ""),
                        "dur": self.spotify.fmt_duration(track.get("duration_ms") or 0),
                        "added": SpotifyClient.fmt_date(it.get("added_at")),
                    })
                if page.get("next"): offset += 50
                else: break
            def paint():
                if not self._is_current_view("liked", "", token): return
                liked = [True] * len(rows)
                table = self._render_tracks_table("[b]Liked Songs[/b]", rows, liked, context_uris=[r["uri"] for r in rows])
                if table is not None:
                    self._revalidate_liked_column(table, max_rows=100)
            self.call_from_thread(paint)
        threading.Thread(target=worker, daemon=True).start()

    def _open_recently_table(self):
        token = self._new_view_token("recent", "")
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            self._right_view = (rv[0], rv[1], rv[2], None)
        self._safe_update_right("recent", "", token, "[b]Loading Recently Played…[/b]")
        def worker():
            rows, ids = [], []
            page = self.spotify.recently_played(limit=50)
            for it in page.get("items", []):
                track = (it.get("track") or {})
                if not track: continue
                rows.append({
                    "id": track.get("id"),
                    "uri": track.get("uri"),
                    "title": track.get("name", ""),
                    "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
                    "album": (track.get("album", {}) or {}).get("name", ""),
                    "dur": self.spotify.fmt_duration(track.get("duration_ms") or 0),
                    "added": SpotifyClient.fmt_date(it.get("played_at")),
                })
                if track.get("id"): ids.append(track.get("id"))
            liked = self.spotify.check_saved_tracks(ids) if ids else []
            def paint():
                if not self._is_current_view("recent", "", token): return
                table = self._render_tracks_table("[b]Recently Played[/b]", rows, liked, context_uris=[r["uri"] for r in rows])
                if table is not None:
                    self._revalidate_liked_column(table, max_rows=100)
            self.call_from_thread(paint)
        threading.Thread(target=worker, daemon=True).start()

    def _open_saved_albums(self):
        token = self._new_view_token("albums", "")
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            try:
                self._right_view = (rv[0], rv[1], rv[2], None)
            except Exception:
                pass
        self._safe_update_right("albums", "", token, "[b]Loading Saved Albums[/b]")

        def worker():
            rows = []
            offset = 0
            try:
                while True:
                    page = self.spotify.ensure().current_user_saved_albums(limit=50, offset=offset) or {}
                    for it in page.get("items", []):
                        album = (it.get("album") or {})
                        if not album: continue
                        rows.append({
                            "type": "album",
                            "id": album.get("id"),
                            "uri": album.get("uri") or album.get("external_urls", {}).get("spotify", ""),
                            "title": album.get("name", "(no title)"),
                            "artist": ", ".join([a.get("name", "") for a in (album.get("artists") or [])]),
                            "album": "",
                            "dur": "",
                            "raw": album,
                            "saved": True,
                        })
                    if page.get("next"):
                        offset += 50
                    else:
                        break
            except Exception:
                logger.exception("_open_saved_albums: error fetching saved albums")

            def paint():
                if not self._is_current_view("albums", "", token): return
                title = "[b]Saved Albums[/b]"
                self._render_search_table(title, rows)
            self.call_from_thread(paint)

        threading.Thread(target=worker, daemon=True).start()

    def _open_import_playlists(self):
        try:
            self._new_view_token('import_input', '')
            right = self._clear_right()
            right.mount(Static('[b]Add playlist to Import Playlists[/b]\nEnter playlist URI or ID and press Enter.'))
            self.import_input = Input(placeholder='Paste the playlist URI or ID and press Enter…')
            right.mount(self.import_input)
            try:
                self.import_input.focus()
            except Exception:
                pass
            self.level = self.LVL_VIEW
        except Exception:
            logger.exception("_open_import_playlists simplified prompt failed")
