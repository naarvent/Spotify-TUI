"""Playlists and saved-library views (albums, artists, podcasts...)."""

from __future__ import annotations

import os
import json
import threading
import webbrowser
from typing import Dict, List

from textual.widgets import Input, Static, ListItem, Label
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

    def action_prompt_settings(self) -> None:
        try:
            self._new_view_token('settings', '')
            right = self._clear_right()
            cur_vol_down = int((config.LOCAL_CFG.get('seek_volume_down') if isinstance(config.LOCAL_CFG, dict) else None) or 5)
            cur_vol_up = int((config.LOCAL_CFG.get('seek_volume_up') if isinstance(config.LOCAL_CFG, dict) else None) or 5)
            cur_track = int((config.LOCAL_CFG.get('seek_seconds_track') if isinstance(config.LOCAL_CFG, dict) else None) or 5)
            cur_episode = int((config.LOCAL_CFG.get('seek_seconds_episode') if isinstance(config.LOCAL_CFG, dict) else None) or 15)
            cur_cache_bytes = int((config.LOCAL_CFG.get('lyrics_cache_max_bytes') if isinstance(config.LOCAL_CFG, dict) else None) or config.LYRICS_CACHE_DEFAULT_BYTES)
            cur_cache_h = config.format_size(cur_cache_bytes)
            right.mount(Static('[b]Settings[/b]\nConfigure volume steps, seek jump times and the lyrics cache size.\nEnter values and press Enter (or use Up/Down arrows) to move between fields.'))
            right.mount(Static(f'Current: Vol Down = {cur_vol_down}%, Vol Up = {cur_vol_up}%, Tracks = {cur_track}s, Episodes = {cur_episode}s, Lyrics cache = {cur_cache_h}'))
            try:
                right.mount(Static('[dim]Lyrics cache accepts sizes like "200 MB", "500 MB", "1 GB" or "2 GB". Press ESC to go back.[/dim]'))
            except Exception:
                pass
            self.seek_vol_down_input = Input(placeholder=f'Volume Down step percent (current {cur_vol_down})', id='seek_vol_down_input')
            self.seek_vol_up_input = Input(placeholder=f'Volume Up step percent (current {cur_vol_up})', id='seek_vol_up_input')
            self.seek_track_input = Input(placeholder=f'Track jump seconds (current {cur_track})', id='seek_track_input')
            self.seek_episode_input = Input(placeholder=f'Episode jump seconds (current {cur_episode})', id='seek_episode_input')
            self.lyrics_cache_input = Input(placeholder=f'Lyrics cache max size, e.g. 200 MB or 1 GB (current {cur_cache_h})', id='lyrics_cache_input')
            right.mount(self.seek_vol_down_input)
            right.mount(self.seek_vol_up_input)
            right.mount(self.seek_track_input)
            right.mount(self.seek_episode_input)
            right.mount(self.lyrics_cache_input)
            self.seek_vol_down_input.focus()
            self.level = self.LVL_VIEW
            return
        except Exception:
            logger.exception('action_prompt_settings failed')

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
        self._leave_lyrics_mode()   # opening any library view exits lyrics
        try:
            fn()
        except Exception:
            logger.exception("Error opening library item %r", name)
        return True

    def _playlists_cache_path(self) -> str:
        return os.path.join(config.CACHE_DIR, "playlists_cache.json")

    def _save_playlists_disk(self, items, *, allow_empty: bool = False) -> None:
        # Never overwrite a valid on-disk cache with an empty list unless the API
        # unambiguously confirmed an empty library. Write atomically (tmp + replace)
        # so a crash mid-write can't leave a truncated/corrupt cache.
        if not items and not allow_empty:
            return
        try:
            minimal = [{"name": p.get("name"), "id": p.get("id"), "uri": p.get("uri")}
                       for p in (items or []) if p]
            path = self._playlists_cache_path()
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(minimal, f, ensure_ascii=False)
            os.replace(tmp, path)
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
        if not force and not getattr(self, '_auto_load_playlists', False):
            return
        # Generation guard: many events trigger a reload (startup, escape, help,
        # retry worker, Ctrl+R). Only the newest load may replace the list, so a
        # slow older worker can never overwrite a newer result.
        self._playlists_gen = int(getattr(self, "_playlists_gen", 0)) + 1
        gen = self._playlists_gen
        try:
            # Gate on a cached token, not a currently-*valid* one: an expired
            # token is refreshed transparently by ensure() on the API call below.
            if not self.spotify.has_cached_token():
                # A transient token miss must not wipe an already-loaded list.
                if not self.playlists_cache:
                    def _paint_empty():
                        try:
                            self.pl_list.clear()
                        except Exception:
                            pass
                    try:
                        self.call_from_thread(_paint_empty)
                    except Exception:
                        if getattr(self, '_closing', False):
                            logger.debug("playlists empty paint dropped during teardown")
                        else:
                            logger.exception("playlists empty paint: call_from_thread failed")
                return

            items: List[Dict] = []
            offset = 0
            total = None
            ok = True
            while True:
                page = self.spotify.user_playlists(limit=50, offset=offset)
                if not isinstance(page, dict) or "items" not in page:
                    ok = False   # malformed / unexpected shape -> treat as a failure
                    break
                if total is None:
                    total = page.get("total")
                items.extend(page.get("items") or [])
                if page.get("next"):
                    offset += 50
                else:
                    break

            if gen != getattr(self, "_playlists_gen", gen):
                return   # superseded by a newer load; do not touch anything

            # Only trust an empty result if the API unambiguously reports 0 total.
            confirmed_empty = ok and not items and (total == 0)
            if not ok:
                logger.warning("playlists load: malformed response, keeping current list")
                return
            if not items and not confirmed_empty:
                logger.warning("playlists load: unexpected empty response, keeping current list")
                return

            # Valid, complete result (non-empty, or a confirmed empty library).
            self.playlists_cache = items
            self._save_playlists_disk(items, allow_empty=confirmed_empty)

            def paint():
                if gen != getattr(self, "_playlists_gen", gen):
                    return
                self.pl_list.clear()
                for p in items:
                    li = ListItem(Label(p.get("name", "(no title)")))
                    li.data = {"type": "playlist", "name": p.get("name"), "id": p.get("id"), "uri": p.get("uri")}
                    self.pl_list.append(li)
                if self.pl_list.children and 0 <= self.pl_index < len(self.pl_list.children):
                    self._suppress_first_selection = True; self.pl_list.index = self.pl_index
            try:
                self.call_from_thread(paint)
            except Exception:
                if getattr(self, '_closing', False):
                    logger.debug("playlists paint dropped during teardown")
                else:
                    logger.exception("playlists paint: call_from_thread failed")
        except Exception:
            # Never wipe a valid list on error: keep data (memory + disk), log,
            # and do not repaint (so a Welcome refresh can't replace the welcome).
            logger.exception("Error loading playlists")

    def _open_playlist_table(self, pdata: Dict):
        self._leave_lyrics_mode()
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

        if getattr(self, "_playlist_cache", None) is None:
            self._playlist_cache = {}
        cached = self._playlist_cache.get(pl_id)
        # With a cache hit the rows go up at once and the worker only repaints at
        # the end; streaming pages into an already-full table would double them.
        progressive = cached is None

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

        # The table this open created, plus any pages that arrived before it was
        # mounted. Looking the table up by id instead would, right after
        # reopening, still find the *previous* playlist's table (Textual's
        # remove() is async) and append the pages to a dying widget.
        state = {"table": None, "pending": []}

        def _mount(rows, liked):
            """Render the table once the previous view's table is really gone.

            `_clear_right` only *schedules* the old table's removal, so opening a
            playlist while another is still loading could mount a second widget
            with id "tracks_table" — DuplicateIds, which killed the new load.
            Retry on the next frames instead, and flush whatever pages landed in
            the meantime."""
            def attempt(retries=4):
                if not self._is_current_view("playlist", pl_id, token):
                    return
                try:
                    still_there = len(self.query("#tracks_table")) > 0
                except Exception:
                    still_there = False
                if still_there and retries > 0:
                    self.call_after_refresh(lambda: attempt(retries - 1))
                    return
                state["table"] = self._render_tracks_table(
                    title, rows, liked, context_uri=pl_uri,
                    context_uris=[r["uri"] for r in rows], profile="playlist",
                )
                if state["pending"]:
                    self._append_track_rows(state["table"], state["pending"])
                    state["pending"] = []
            attempt()

        def _paint_preview(rows):
            self.call_from_thread(lambda: _mount(rows, None))

        def _paint_page(page_rows):
            # Append each page as it lands. Holding the rows back until the
            # likes lookup finished made a big playlist look stuck on its first
            # page until the hearts resolved.
            def do():
                if not self._is_current_view("playlist", pl_id, token):
                    return
                table = state["table"]
                if table is None:
                    # The preview is still waiting for the old table to go.
                    state["pending"].extend(page_rows)
                    return
                self._append_track_rows(table, page_rows)
            self.call_from_thread(do)

        def _paint_final(rows, liked):
            def do():
                if not self._is_current_view("playlist", pl_id, token):
                    return
                table = state["table"]
                if table is None:
                    # No preview was shown (single-page playlist), or it is still
                    # waiting for the old table: render the full list instead.
                    state["pending"] = []
                    _mount(rows, liked)
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

        def _paint_hearts(id_to_liked):
            # Hearts land one API batch (50 ids) at a time; touch just those
            # cells so a long playlist lights up progressively instead of
            # staying blank until the last batch returns.
            def do():
                table = state["table"]
                if table is None or not self._is_current_view("playlist", pl_id, token):
                    return
                liked_map = getattr(table, "_liked_map", None)
                if liked_map is None:
                    liked_map = table._liked_map = {}
                for i, r in enumerate(getattr(table, "_model_rows", []) or []):
                    val = id_to_liked.get(r.get("id"))
                    if val is None or bool(liked_map.get(i, False)) == bool(val):
                        continue
                    liked_map[i] = bool(val)
                    self._set_heart_icon(table, i, bool(val))
            self.call_from_thread(do)

        def _set_title(text):
            self.call_from_thread(lambda: self._set_table_title("tracks_table", text))

        def current():
            # The user left, opened something else, or the app is closing: stop
            # downloading. Without this a big playlist kept paging (and then ran
            # the whole liked lookup) for a view nobody is looking at.
            return (not getattr(self, "_closing", False)) and \
                self._is_current_view("playlist", pl_id, token)

        if cached:
            cached_rows, cached_liked = cached
            _mount(list(cached_rows), list(cached_liked))
            self._set_table_title("tracks_table", f"{title}  [dim](refreshing…)[/dim]")

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
                if multipage and progressive:
                    _paint_preview(list(rows))
                    _set_title(f"{title}  [dim](loading {len(rows)}/{total})[/dim]")

                offset = 100
                while offset < total:
                    if not current():
                        return
                    page = self.spotify.playlist_items(pl_id, limit=100, offset=offset, fields=FIELDS) or {}
                    page_rows, page_track_ids = _extract(page)
                    if not page_rows:
                        break
                    rows += page_rows
                    track_ids += page_track_ids
                    offset += 100
                    if progressive:
                        _paint_page(list(page_rows))
                        _set_title(f"{title}  [dim](loading {min(len(rows), total)}/{total})[/dim]")

                if not current():
                    return
                _set_title(f"{title}  [dim](checking likes…)[/dim]" if multipage else title)
                liked_by_id = {}

                def _on_liked_batch(start, vals):
                    batch = dict(zip(track_ids[start:start + len(vals)], vals))
                    liked_by_id.update(batch)
                    if multipage or not progressive:
                        _paint_hearts(batch)

                if track_ids:
                    self.spotify.check_saved_tracks(track_ids, on_batch=_on_liked_batch)
                liked = [bool(liked_by_id.get(r.get("id"), False)) for r in rows]
            except Exception:
                logger.exception("_open_playlist_table worker failed")
                return

            # Cache the finished list even if the user has since left: the work
            # is done, and the next open should not repeat it.
            self._cache_playlist(pl_id, rows, liked)

            # Final render: full track list with hearts resolved, clean title.
            _paint_final(rows, liked)
            _set_title(title)
        threading.Thread(target=worker, daemon=True).start()

    # How many playlists keep their tracks in memory. Enough to make going back
    # and forth instant without holding every big playlist of a long session.
    _PLAYLIST_CACHE_MAX = 8

    def _playlist_cache_lock(self) -> threading.Lock:
        # Two playlists can finish loading at once, and read-modify-write on the
        # dict (the eviction loop especially) is not atomic even under the GIL.
        lock = getattr(self, "_pl_cache_lock", None)
        if lock is None:
            lock = self._pl_cache_lock = threading.Lock()
        return lock

    def _cache_playlist(self, pl_id: str, rows: List[Dict], liked: List[bool]) -> None:
        if not pl_id:
            return
        with self._playlist_cache_lock():
            if getattr(self, "_playlist_cache", None) is None:
                self._playlist_cache = {}
            cache = self._playlist_cache
            # Re-insert so the dict order is least- to most-recently loaded.
            cache.pop(pl_id, None)
            cache[pl_id] = (list(rows), list(liked))
            while len(cache) > self._PLAYLIST_CACHE_MAX:
                try:
                    cache.pop(next(iter(cache)))
                except StopIteration:
                    break

    def _invalidate_playlist_cache(self, pl_id: str) -> None:
        """Drop a playlist's cached tracks after it has been modified, so the
        next open refetches instead of painting rows that no longer match."""
        if not pl_id:
            return
        with self._playlist_cache_lock():
            cache = getattr(self, "_playlist_cache", None)
            if cache:
                cache.pop(pl_id, None)

    def _open_saved_artists(self):
        # Unified with the other saved-list views via the shared streaming loader:
        # gains the session cache, generation/token guard and empty-vs-error
        # distinction. Followed artists uses cursor pagination; the app shows the
        # first page, returned as a single page here (offset>0 => no more).
        def fetch_page(offset):
            if offset > 0:
                return [], False
            sp = self.spotify.ensure()
            try:
                page = sp.current_user_followed_artists(limit=50) or {}
            except TypeError:
                # Signature drift only — a real API error must propagate so the
                # loader shows an error state instead of a false "empty".
                page = sp.current_user_followed_artists() or {}
            artists = (page.get('artists') or {}).get('items', [])
            rows = []
            for it in artists:
                if not it:
                    continue
                rows.append({
                    "type": "artist", "id": it.get("id"),
                    "uri": it.get("uri") or (it.get("external_urls") or {}).get("spotify", ""),
                    "title": it.get("name") or "(no name)",
                    "artist": "", "album": "", "dur": "", "raw": it, "saved": True,
                })
            return rows, False
        self._stream_library_view(
            key="artists", title="[b]Saved Artists[/b]",
            loading_msg="[b]Loading Saved Artists…[/b]",
            empty_msg="[b]No saved artists yet.[/b]",
            fetch_page=fetch_page, tracks_mode=False, layout="artists")

    def _stream_library_view(self, *, key, title, loading_msg, empty_msg, fetch_page, tracks_mode, layout="full"):
        """Responsive, deduplicated loader shared by the saved-library views.

        - Immediate uniform feedback (Loading…) and, when a same-session cache
          exists, an instant paint of the cached rows before the network runs.
        - Progressive per-page paint on a cold load; the table widget is reused
          so the cursor/scroll are preserved (see _repaint_rows_from_model).
        - A single source of truth per view via the view token: a newer open (or
          leaving the view, or teardown) stops this worker fetching and painting,
          so a double Enter / Ctrl+R never accumulates workers and a stale load
          never overwrites a newer view.
        - Errors are kept distinct from an empty collection; partial data is
          preserved and never blanked.

        ``fetch_page(offset) -> (rows, has_next)`` runs in the worker and may
        raise; ``tracks_mode`` picks the tracks vs search render path.
        """
        token = self._new_view_token(key, "")
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            try:
                self._right_view = (rv[0], rv[1], rv[2], None)
            except Exception:
                pass

        if getattr(self, "_lib_cache", None) is None:
            self._lib_cache = {}
        cache = self._lib_cache
        # Newest-load-per-key marker: lets a worker fill the cache even after the
        # user leaves the view, while a superseding load still wins the cache.
        if getattr(self, "_lib_gen", None) is None:
            self._lib_gen = {}
        self._lib_gen[key] = token
        cached = cache.get(key)
        progressive = cached is None
        state = {"table": None}

        def current():
            return (not getattr(self, "_closing", False)) and self._is_current_view(key, "", token)

        def render_first(rows):
            if tracks_mode:
                return self._render_tracks_table(
                    title, rows, [True] * len(rows),
                    context_uris=[r["uri"] for r in rows],
                )
            # Saved-library items are saved by definition — skip the redundant
            # saved-state revalidation the search view would otherwise fire.
            return self._render_search_table(title, rows, check_saved=False, layout=layout)

        def refresh_model(tbl, rows):
            if tbl is None:
                return render_first(rows)
            tbl._model_rows = list(rows)
            if tracks_mode:
                tbl._liked_map = {i: True for i in range(len(rows))}
                tbl._context_uris = [r["uri"] for r in rows]
            self._repaint_rows_from_model(tbl)
            return tbl

        def set_status(msg):
            # Progress now rides in the table's integrated border title (a no-op
            # until the table is mounted).
            self._set_table_title("tracks_table", msg)

        def setup():
            if not current():
                return
            right = self._clear_right()
            right.update(loading_msg)
            if cached:
                state["table"] = render_first(list(cached))
                set_status(f"{title}  [dim](refreshing…)[/dim]")

        def worker():
            try:
                self.call_from_thread(setup)
            except Exception:
                if getattr(self, "_closing", False):
                    logger.debug("library %s setup dropped during teardown", key)
                    return
                logger.exception("library %s setup failed", key)

            rows = []
            errored = False
            offset = 0
            try:
                while True:
                    if not current():
                        return  # superseded / left / closing: stop fetching
                    page_rows, has_next = fetch_page(offset)
                    rows += page_rows
                    if progressive:
                        snap, hn = list(rows), has_next
                        def emit(snap=snap, hn=hn):
                            if not current():
                                return
                            state["table"] = refresh_model(state["table"], snap)
                            set_status(f"{title}  [dim](loading {len(snap)}…)[/dim]" if hn else title)
                        try:
                            self.call_from_thread(emit)
                        except Exception:
                            if getattr(self, "_closing", False):
                                return
                            logger.exception("library %s progressive paint failed", key)
                    if not has_next:
                        break
                    offset += 50
            except Exception:
                logger.exception("library %s page fetch failed", key)
                errored = True

            # Cache a clean full load independently of painting: a worker may
            # finish after the user left the view (it fills the cache) but only
            # the newest load for this key is allowed to write it.
            if not errored and rows and self._lib_gen.get(key) == token:
                cache[key] = list(rows)

            def finalize():
                if not current():
                    return
                if errored:
                    if state["table"] is not None or cached:
                        # Keep whatever we already have; never blank on a
                        # transient failure.
                        set_status(f"{title}  [dim](partial — Ctrl+R to retry)[/dim]")
                    else:
                        self._clear_right().update("[b]Could not load. Press Ctrl+R to retry.[/b]")
                    return
                if not rows:
                    self._clear_right().update(empty_msg)
                    return
                state["table"] = refresh_model(state["table"], rows)
                set_status(title)
            try:
                self.call_from_thread(finalize)
            except Exception:
                if getattr(self, "_closing", False):
                    logger.debug("library %s finalize dropped during teardown", key)
                else:
                    logger.exception("library %s finalize failed", key)

        threading.Thread(target=worker, daemon=True).start()

    def _open_saved_podcasts(self):
        def fetch_page(offset):
            sp = self.spotify.ensure()
            try:
                page = sp.current_user_saved_shows(limit=50, offset=offset) or {}
            except TypeError:
                page = sp.current_user_saved_shows(limit=50) or {}
            rows = []
            for it in page.get('items', []):
                show = (it.get('show') or {})
                if not show:
                    continue
                rows.append({
                    "type": "podcast", "id": show.get("id"),
                    "uri": show.get("uri") or show.get("external_urls", {}).get("spotify", ""),
                    "title": show.get("name", "(no title)"), "artist": show.get('publisher', ''),
                    "album": "", "dur": "", "raw": show, "saved": True,
                })
            return rows, bool(page.get('next'))
        self._stream_library_view(
            key="podcasts", title="[b]Saved Podcasts[/b]",
            loading_msg="[b]Loading Saved Podcasts…[/b]",
            empty_msg="[b]No saved podcasts yet.[/b]",
            fetch_page=fetch_page, tracks_mode=False, layout="podcasts")

    def _open_saved_episodes(self):
        def fetch_page(offset):
            sp = self.spotify.ensure()
            try:
                page = sp.current_user_saved_episodes(limit=50, offset=offset) or {}
            except TypeError:
                page = sp.current_user_saved_episodes(limit=50) or {}
            rows = []
            for it in page.get('items', []):
                if not it:
                    continue
                ep = (it.get('episode') or {})
                if not ep:
                    continue
                # Spotify returns "tombstone" episode objects (every field None)
                # for unavailable episodes: the keys exist but the values are
                # null, so .get(key, default) yields None, not the default.
                # Coalesce explicitly and degrade instead of inventing data.
                show = (ep.get('show') or {})
                name = ep.get('name')
                dur_ms = ep.get('duration_ms')
                rows.append({
                    "type": "episode", "id": ep.get("id"),
                    "uri": ep.get("uri") or (ep.get("external_urls") or {}).get("spotify", ""),
                    "title": name or "(unavailable episode)",
                    "artist": show.get('name') or "",
                    "album": "",
                    "dur": self.spotify.fmt_duration(dur_ms) if dur_ms else "",
                    "raw": ep, "saved": True,
                })
            return rows, bool(page.get('next'))
        self._stream_library_view(
            key="episodes", title="[b]Saved Episodes[/b]",
            loading_msg="[b]Loading Saved Episodes…[/b]",
            empty_msg="[b]No saved episodes yet.[/b]",
            fetch_page=fetch_page, tracks_mode=False)

    def _open_liked_table(self):
        def fetch_page(offset):
            page = self.spotify.saved_tracks(limit=50, offset=offset) or {}
            rows = []
            for it in page.get("items", []):
                track = (it.get("track") or {})
                if not track:
                    continue
                rows.append({
                    "id": track.get("id"), "uri": track.get("uri"),
                    "title": track.get("name", ""),
                    "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
                    "album": (track.get("album", {}) or {}).get("name", ""),
                    "dur": self.spotify.fmt_duration(track.get("duration_ms") or 0),
                    "added": SpotifyClient.fmt_date(it.get("added_at")),
                })
            return rows, bool(page.get("next"))
        # A Liked Songs view implies every track is saved — no check_saved_tracks.
        self._stream_library_view(
            key="liked", title="[b]Liked Songs[/b]",
            loading_msg="[b]Loading Liked Songs…[/b]",
            empty_msg="[b]No Liked Songs yet.[/b]",
            fetch_page=fetch_page, tracks_mode=True)

    def _open_recently_table(self):
        token = self._new_view_token("recent", "")
        rv = getattr(self, "_right_view", None)
        if rv and len(rv) >= 3:
            self._right_view = (rv[0], rv[1], rv[2], None)
        # Clear any stale view first so the loading message is actually visible —
        # a bare right_panel.update() leaves a previously-mounted table on top of
        # it, which is why opening Recently Played looked like nothing happened.
        # Runs on the UI thread (keyboard Enter and Ctrl+R both call this here).
        right = self._clear_right()
        if self._is_current_view("recent", "", token):
            right.update("[b]Loading Recently Played…[/b]")
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
                table = self._render_tracks_table("[b]Recently Played[/b]", rows, liked, context_uris=[r["uri"] for r in rows], profile="recent")
                if table is not None:
                    self._revalidate_liked_column(table, max_rows=100)
            self.call_from_thread(paint)
        threading.Thread(target=worker, daemon=True).start()

    def _open_saved_albums(self):
        def fetch_page(offset):
            page = self.spotify.ensure().current_user_saved_albums(limit=50, offset=offset) or {}
            rows = []
            for it in page.get("items", []):
                # Spotify returns a null item for an unavailable saved album;
                # accessing it.get() on None was crashing the whole page fetch.
                if not it:
                    continue
                album = (it.get("album") or {})
                if not album:
                    continue
                rows.append({
                    "type": "album", "id": album.get("id"),
                    "uri": album.get("uri") or album.get("external_urls", {}).get("spotify", ""),
                    "title": album.get("name", "(no title)"),
                    "artist": ", ".join([a.get("name", "") for a in (album.get("artists") or [])]),
                    "album": "", "dur": "", "raw": album, "saved": True,
                })
            return rows, bool(page.get("next"))
        self._stream_library_view(
            key="albums", title="[b]Saved Albums[/b]",
            loading_msg="[b]Loading Saved Albums…[/b]",
            empty_msg="[b]No saved albums yet.[/b]",
            fetch_page=fetch_page, tracks_mode=False)

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
