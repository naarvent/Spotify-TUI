"""Playlists and saved-library views (albums, artists, podcasts...)."""

from __future__ import annotations

import os
import json
import threading
import webbrowser
from typing import Dict, List

from textual.widgets import DataTable, Input, Static, ListItem, Label, ListView
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
from .. import playlist_sort
from ..local_library import atomic_write_json
from ..config import logger
from ..providers.spotify import SpotifyClient

from .form_lists import FormOptionList


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
            _text, _hint = self._import_prompt_texts()
            self.import_input = Input(placeholder=_hint)
            right.mount(Static(_text))
            right.mount(self.import_input)
            self.import_input.focus()
            self.level = self.LVL_VIEW
            return
        except Exception:
            logger.exception('action_prompt_import_playlists failed')

    def _import_prompt_texts(self) -> tuple:
        """(help text, input placeholder) of Library > Import Playlists for the active provider."""
        from .. import spotify_import as _si
        prov = getattr(self, 'spotify', None)
        caps = getattr(prov, 'capabilities', None)
        return _si.import_playlists_text(getattr(prov, 'name', '') or '',
                                         getattr(caps, 'can_import_from_spotify', False) is True)

    def action_prompt_create_playlist(self) -> None:
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_edit_playlists"):
            return
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
            where = ("in the local library" if self._cap("local_library")
                     else f"on {getattr(self.spotify, 'display_name', 'Spotify')}")
            right.mount(Static(f'[b]Create new playlist[/b] ({rich_escape(str(where))})\n'
                               'Enter the playlist name and press Enter.'))
            self.create_name_input = Input(placeholder='Playlist name…')
            right.mount(self.create_name_input)
            self.create_name_input.focus()
            self._create_playlist_state = {}
            self.level = self.LVL_VIEW
            return
        except Exception:
            logger.exception('action_prompt_create_playlist failed')

    def action_rename_playlist(self) -> None:
        """Ctrl+E on a sidebar playlist: rename it (local playlists only)."""
        try:
            focused = getattr(self, 'focused', None)
            if not isinstance(focused, ListView) or getattr(focused, 'id', '') != 'pl_list' \
                    or focused.index is None:
                self._notify('[b]Select a playlist in the sidebar to rename it.[/b]', warn=True)
                return
            pdata = getattr(focused.children[focused.index], 'data', {}) or {}
            pid, name = pdata.get('id'), pdata.get('name') or ''
            can = getattr(self.spotify, 'can_rename_playlist', None)
            if not pid or not callable(can) or not can(pid):
                self._notify('[b]Only local-library playlists can be renamed here.[/b]', warn=True)
                return
            self._new_view_token('rename_playlist', pid)
            self._pending_rename = {'id': pid, 'name': name}
            right = self._clear_right()
            right.mount(Static(f'[b]Rename playlist:[/b] {rich_escape(name)}\nType the new name and press Enter (Esc cancels).'))
            self.rename_playlist_input = Input(value=name, placeholder='New name…')
            right.mount(self.rename_playlist_input)
            self.rename_playlist_input.focus()
            self.level = self.LVL_VIEW
        except Exception:
            logger.exception('action_rename_playlist failed')

    def _submit_rename_playlist(self, value: str) -> None:
        pending = getattr(self, '_pending_rename', None) or {}
        self._pending_rename = None
        try:
            del self.rename_playlist_input
        except Exception:
            pass
        new = (value or '').strip()
        if not pending or not new:
            self._notify('[b]Rename cancelled.[/b]', warn=True)
            return

        def worker():
            try:
                ok = bool(self.spotify.rename_playlist(pending['id'], new))
            except Exception as exc:
                logger.exception('rename playlist failed')
                ok, err = False, str(exc)
            else:
                err = ''
            def done():
                if ok:
                    self._notify(f"[b]Playlist renamed:[/b] {rich_escape(new)}")
                    self._go_to_menu()
                else:
                    self._notify(f"[b]Could not rename the playlist.[/b] {rich_escape(err)}", warn=True)
            try:
                self.call_from_thread(done)
            except Exception:
                pass
            self._load_playlists(force=True)
        threading.Thread(target=worker, daemon=True).start()

    def action_prompt_settings(self) -> None:
        """Settings: one scrollable form grouped in sections (Playback,
        Lyrics, Music provider, Subsonic). The fields, their order and the
        Enter / ↑↓ / Esc behaviour are those of the original wizard."""
        try:
            from textual.containers import VerticalScroll
            self._new_view_token('settings', '')
            right = self._clear_right()
            cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
            cur_vol_down = int(cfg.get('seek_volume_down') or 5)
            cur_vol_up = int(cfg.get('seek_volume_up') or 5)
            cur_track = int(cfg.get('seek_seconds_track') or 5)
            cur_episode = int(cfg.get('seek_seconds_episode') or 15)
            cur_cache_bytes = int(cfg.get('lyrics_cache_max_bytes') or config.LYRICS_CACHE_DEFAULT_BYTES)
            cur_cache_h = config.format_size(cur_cache_bytes)
            # No widget ids: remove() is async, so a second Settings open in the same
            # turn would hit DuplicateIds. The wizard finds the inputs by attribute.
            self.seek_vol_down_input = Input(placeholder=f'{cur_vol_down}')
            self.seek_vol_up_input = Input(placeholder=f'{cur_vol_up}')
            self.seek_track_input = Input(placeholder=f'{cur_track}')
            self.seek_episode_input = Input(placeholder=f'{cur_episode}')
            self.lyrics_cache_input = Input(placeholder=f'{cur_cache_h}')

            parts = [
                Static('⚙  Settings', classes='settings-title'),
                Static('Playback steps, lyrics cache and music provider. Type a value and press '
                       'Enter to save it; an empty field keeps the current value.',
                       classes='settings-subtitle'),
                Static('Playback', classes='settings-section'),
                *self._settings_field('Volume step down', self.seek_vol_down_input,
                                      f'Percent per "-" press. Current: {cur_vol_down}%'),
                *self._settings_field('Volume step up', self.seek_vol_up_input,
                                      f'Percent per "+" press. Current: {cur_vol_up}%'),
                *self._settings_field('Track seek', self.seek_track_input,
                                      f'Seconds per Ctrl+←/→ on a song. Current: {cur_track}s'),
                *self._settings_field('Episode seek', self.seek_episode_input,
                                      f'Seconds per Ctrl+←/→ on a podcast episode. Current: {cur_episode}s'),
                Static('Lyrics', classes='settings-section'),
                *self._settings_field('Lyrics cache size', self.lyrics_cache_input,
                                      f'With a unit: "200 MB", "1 GB" (1 MB – 8 GB). Current: {cur_cache_h}. '
                                      'Enter here saves and closes Settings.'),
            ]
            parts += self._spicy_settings_widgets()
            parts += self._visualizer_settings_widgets()
            parts += self._offline_settings_widgets()
            parts += self._appearance_settings_widgets()
            parts += self._provider_settings_widgets()
            parts.append(Static('Enter save · ↑↓/Tab move · Esc back', classes='settings-keys'))
            form = VerticalScroll(*parts, classes='settings-form')
            try:
                width = int(getattr(right.content_size, 'width', 0) or 0) or (int(self.size.width) - 44)
                form.set_class(width < 72, '-narrow')
            except Exception:
                pass
            right.mount(form)
            self.seek_vol_down_input.focus()
            # The fields live in a nested container that finishes mounting a
            # moment later; focus again once it is on screen.
            first = self.seek_vol_down_input
            self.call_after_refresh(lambda: first.focus() if first.is_attached else None)
            self.level = self.LVL_VIEW
            return
        except Exception:
            logger.exception('action_prompt_settings failed')

    def _spicy_settings_widgets(self) -> list:
        """Lyrics source with word-by-word times (Spicy Lyrics API). On by default; it only does
        something when the user has their own key (keyring or SPT_SPICY_LYRICS_KEY, never the JSON).
        The key is looked up off the UI thread; only whether it exists is shown, never its value."""
        from textual.widgets.option_list import Option
        from .. import spicy_lyrics as sl
        on = sl.enabled_in(config.LOCAL_CFG)
        self.spicy_select = FormOptionList(
            Option('Use Spicy Lyrics (word-by-word) when a key is found', id='on'),
            Option('Off · LRCLIB only', id='off'),
            classes='settings-provider-select')
        self.spicy_select.highlighted = 0 if on else 1
        self.spicy_key_status = Static('Key: checking…', classes='settings-help settings-help-wide')
        self.spicy_key_input = Input(placeholder='paste your key and press Enter (empty = keep)', password=True)

        def probe():
            key, where = sl.resolve_key()
            if key:
                text = f"Key: found ({'environment ' + sl.KEY_ENV if where == 'env' else 'keyring'})."
            else:
                text = "Key: not found. " + sl.key_hint()
            try:
                self.call_from_thread(self._set_spicy_key_status, text)
            except Exception:
                pass
        threading.Thread(target=probe, daemon=True).start()
        return [Static('Word-by-word lyrics source (Spicy Lyrics API; needs your own free key). '
                       'Order: your files > Spicy Lyrics > LRCLIB. Pick one and press Enter.',
                       classes='settings-help settings-help-wide'),
                self.spicy_select, self.spicy_key_status,
                *self._settings_field('API key', self.spicy_key_input,
                                      'Stored in the system keyring, never in spt_config.json '
                                      '(no keyring: export SPT_SPICY_LYRICS_KEY). Free key: developers.spicylyrics.org')]

    def _set_spicy_key_status(self, text: str) -> None:
        w = getattr(self, 'spicy_key_status', None)
        if w is not None and w.is_attached:
            w.update(rich_escape(text))

    def _visualizer_settings_widgets(self) -> list:
        """Visualizer: frame rate of the bars (`visualizer_fps`, 10-60, default 30)."""
        from .. import visualizer as viz
        cur = self._vis_fps()
        self.visualizer_fps_input = Input(placeholder=f'{cur}')
        from textual.widgets.option_list import Option
        self.visualizer_mode_select = FormOptionList(
            Option('Mirrored · bass in the middle (default)', id='mirrored'),
            Option('Bars · plain bars', id='bars'),
            classes='settings-provider-select')
        self.visualizer_mode_select.highlighted = 0 if self._vis_default_mirror() else 1
        return [Static('Visualizer', classes='settings-section'),
                *self._settings_field('Visualizer FPS', self.visualizer_fps_input,
                                      f'Frames per second of the bars, {viz.MIN_FPS}-{viz.MAX_FPS} '
                                      f'(default {viz.DEFAULT_FPS}). Lower uses less CPU. Current: {cur}. '
                                      'Enter saves and applies it.'),
                Static('Default visualizer mode when you press v. Shift+M still switches it while it is open. '
                       'Pick one and press Enter.', classes='settings-help settings-help-wide'),
                self.visualizer_mode_select]

    def _offline_settings_widgets(self) -> list:
        """Offline downloads: the size cap (only for providers that download)."""
        if not self._cap("can_download") or not hasattr(getattr(self, "spotify", None), "capabilities"):
            self.offline_cap_input = None
            return []
        from .. import offline
        cur = config.format_size(offline.configured_limit())
        self.offline_cap_input = Input(placeholder=cur)
        where = ""
        try:
            st = getattr(self.spotify, "offline", None)
            if st is not None:
                where = f" Stored in {st.root}."
        except Exception:
            pass
        lo, hi = config.format_size(offline.OFFLINE_MIN_BYTES), config.format_size(offline.OFFLINE_MAX_BYTES_LIMIT)
        return [Static('Offline downloads', classes='settings-section'),
                *self._settings_field('Offline space', self.offline_cap_input,
                                      f'With a unit: "500 MB", "1 GB" ({lo} – {hi}). Current: {cur}. '
                                      f'Older copies are deleted first when it is full.{where}')]

    @staticmethod
    def _settings_field(label: str, widget, help_text: str) -> list:
        """A labelled form row with its short help line underneath."""
        from textual.containers import Horizontal
        return [Horizontal(Static(rich_escape(label), classes='settings-label'), widget,
                           classes='settings-row'),
                Static(rich_escape(help_text), classes='settings-help')]

    def _mount_provider_settings(self, right) -> None:
        """Kept for callers that mount the provider block on its own."""
        for w in self._provider_settings_widgets():
            right.mount(w)

    def _appearance_settings_widgets(self) -> list:
        """Appearance: opaque (the spt-teal theme) or transparent (the terminal's own background shows
        through; only frames, text and the selection stay painted). Applies at once and is remembered."""
        from textual.widgets.option_list import Option
        cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
        on = bool(cfg.get('transparent_background'))
        self.appearance_select = FormOptionList(
            Option('Opaque · spt-teal theme', id='opaque'),
            Option('Transparent · terminal background, only the frames', id='transparent'),
            classes='settings-provider-select')
        self.appearance_select.highlighted = 1 if on else 0
        from ..theming import PRESETS, normalize_color
        cur = normalize_color(cfg.get('accent_color') or '') or PRESETS['teal']
        self.accent_select = FormOptionList(
            *[Option(f'[{hexv}]●[/] {name}', id=name) for name, hexv in PRESETS.items()],
            classes='settings-provider-select')
        names = list(PRESETS)
        self.accent_select.highlighted = next((i for i, n in enumerate(names) if PRESETS[n] == cur), None)
        self.accent_input = Input(placeholder=cur)
        hints_on = cfg.get('show_key_hints', True) is not False
        self.hints_select = FormOptionList(
            Option('Show the key hints bar', id='show'),
            Option('Hide it', id='hide'),
            classes='settings-provider-select')
        self.hints_select.highlighted = 0 if hints_on else 1
        header_on = cfg.get('show_header', True) is not False
        self.header_select = FormOptionList(
            Option('Show the title bar', id='show'),
            Option('Hide it (one more row for the content)', id='hide'),
            classes='settings-provider-select')
        self.header_select.highlighted = 0 if header_on else 1
        return [Static('Appearance', classes='settings-section'),
                Static(f'Background: {"transparent" if on else "opaque"}. Choose one and press Enter; '
                       'it applies now. Transparent works best with a terminal that has opacity/blur.',
                       classes='settings-help settings-help-wide'),
                self.appearance_select,
                Static('Colour of the frames, titles and selection. Pick one and press Enter, or type '
                       'your own below.', classes='settings-help settings-help-wide'),
                self.accent_select,
                *self._settings_field('Custom colour', self.accent_input,
                                      f'Hex like #ff7eb6 (or a name above). Current: {cur}. Enter applies it.'),
                Static('The bar at the bottom with the keys (q, esc, /, space…). Help (?) lists them all.',
                       classes='settings-help settings-help-wide'),
                self.hints_select,
                Static('The title bar at the top ("spt - Spotify TUI (Python) — provider"). '
                       'Pick one and press Enter; it applies now.',
                       classes='settings-help settings-help-wide'),
                self.header_select]

    def _provider_settings_widgets(self) -> list:
        """Provider choice + Subsonic server fields, below the numeric wizard.

        The provider is a selectable list with each provider's readiness;
        picking one switches live (see provider_switch). The Subsonic fields
        are a separate mini-chain (URL -> user -> password): Enter on the
        lyrics-cache field still finishes the settings as before. An empty
        field keeps the current value. The password is handed to the keyring,
        never to the JSON config (see spt_tui.secret_store)."""
        from ..providers import resolve_provider_name
        from textual.widgets import OptionList
        from .provider_switch import build_provider_options, provider_statuses, env_warning_text
        cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
        try:
            cur_provider = resolve_provider_name()
        except Exception:
            cur_provider = "spotify"
        cur_url = cfg.get('subsonic_url') or ''
        cur_user = cfg.get('subsonic_user') or ''
        active = getattr(getattr(self, 'spotify', None), 'name', None)
        active = active if isinstance(active, str) else cur_provider
        warn = env_warning_text()
        try:
            statuses = provider_statuses()
        except Exception:
            logger.exception('provider statuses failed')
            statuses = []
        self.provider_select = FormOptionList(*build_provider_options(active, statuses),
                                          classes="settings-provider-select")
        out = [Static('Music provider', classes='settings-section'),
               Static(f'Active: {rich_escape(active)}. Choose one and press Enter to switch now '
                      '(no restart). Also P from any view.', classes='settings-help settings-help-wide')]
        if warn:
            out.append(Static(f'▲ {rich_escape(warn)}', classes='settings-warn'))
        out.append(self.provider_select)
        self.subsonic_url_input = Input(placeholder=cur_url or 'https://music.example.org')
        self.subsonic_user_input = Input(placeholder=cur_user or 'not set')
        self.subsonic_password_input = Input(placeholder='leave empty to keep', password=True)
        out += [Static('Subsonic / Navidrome', classes='settings-section'),
                *self._settings_field('Server URL', self.subsonic_url_input,
                                      f'Current: {cur_url or "not set"}'),
                *self._settings_field('User', self.subsonic_user_input,
                                      f'Current: {cur_user or "not set"}'),
                *self._settings_field('Password', self.subsonic_password_input,
                                      'Stored in the system keyring, never in spt_config.json '
                                      '(no keyring: export SPT_SUBSONIC_PASSWORD). Then pick '
                                      'subsonic above to apply.')]
        return out

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
            "Import from Spotify": self.action_import_from_spotify,
            "Downloaded": self._open_downloaded_table,
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
        # One cache per provider family: after switching to Subsonic the
        # startup paint must not show (and let the user open) Spotify playlists.
        # Both Spotify modes share the historical file name.
        name = getattr(getattr(self, "spotify", None), "name", None)
        if isinstance(name, str) and name not in ("spotify", "spotify_mpris") and name.isidentifier():
            return os.path.join(config.CACHE_DIR, f"playlists_cache_{name}.json")
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
            # Unique temp file per write: two loads finishing together used to
            # share `<path>.tmp` and one os.replace() then failed with
            # FileNotFoundError.
            atomic_write_json(self._playlists_cache_path(), minimal)
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
        # Spotify (Feb 2026) renamed the per-entry `track` to `item`; ask for both
        # so the filter doesn't strip the payload on either API shape.
        _TR = "id,uri,name,type,duration_ms,artists(name),album(name)"
        FIELDS = f"items(added_at,item({_TR}),track({_TR})),total"

        base_title = f"[b]{rich_escape(pl_name)}[/b]"

        def _title(suffix=""):
            # Re-read on every call: the sort can change while pages stream in.
            return self._pl_title(base_title) + suffix

        def _extract(page, page_offset=0):
            page_rows, page_track_ids = [], []
            for n, it in enumerate(page.get("items", [])):
                track = SpotifyClient.playlist_entry_track(it)
                if not track:
                    continue
                artists = track.get("artists") or []
                page_rows.append({
                    "id": track.get("id"),
                    "uri": track.get("uri"),
                    "title": track.get("name", "(no title)"),
                    "artist": ", ".join(a.get("name", "") for a in track.get("artists", [])),
                    "album": (track.get("album", {}) or {}).get("name", ""),
                    "dur": self.spotify.fmt_duration(track.get("duration_ms") or 0),
                    "added": SpotifyClient.fmt_date(it.get("added_at")),
                    # View-only sort keys. `_pos` is the entry's position in the
                    # playlist: playback offsets use it, so a sorted row still
                    # starts the right track.
                    "_pos": page_offset + n,
                    "_artist0": ((artists[0] or {}).get("name") or "") if artists else "",
                    "_dur_ms": track.get("duration_ms") or 0,
                    "_added_at": it.get("added_at") or "",
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
                srows, sliked = self._pl_sorted(rows, liked)
                state["table"] = self._render_tracks_table(
                    _title(), srows, sliked, context_uri=pl_uri,
                    context_uris=[r["uri"] for r in srows], profile="playlist",
                )
                state["table"]._pl_base_title = base_title
                if state["pending"]:
                    _add_rows(state["table"], state["pending"])
                    state["pending"] = []
            attempt()

        def _add_rows(table, page_rows):
            key, desc = self._pl_sort_pref()
            if key == "default" and not desc:
                self._append_track_rows(table, page_rows)
            else:
                # A new page can land anywhere in a sorted view, and re-sorting +
                # repainting the whole table per page was O(n²) on a long
                # playlist. Only extend the model here (as an unpainted tail, so
                # the indices of the rows on screen stay valid); _paint_final
                # does the single full sort + repaint.
                model = getattr(table, "_model_rows", None)
                if model is None:
                    model = table._model_rows = []
                model.extend(page_rows)

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
                _add_rows(table, page_rows)
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
                table._context_uri = pl_uri
                self._resort_playlist_table(table, replace=(rows, liked))
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
            # Painting cached rows synchronously here happens in the same UI-thread
            # callstack that just wrote "Loading playlist…", so Textual never paints
            # that frame and the open looks like nothing happened. Defer the cached
            # paint by a short beat so the loading line is actually visible first.
            # Guarded: skip if a fast background refresh already mounted the table,
            # or if the user left the view before the timer fired.
            def _paint_cached():
                if state["table"] is not None or not current():
                    return
                _mount(list(cached_rows), list(cached_liked))
                self._set_table_title("tracks_table", _title("  [dim](refreshing…)[/dim]"))
            self.set_timer(self._CACHE_LOADING_MIN_S, _paint_cached)

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
                    _set_title(_title(f"  [dim](loading {len(rows)}/{total})[/dim]"))

                offset = 100
                while offset < total:
                    if not current():
                        return
                    page = self.spotify.playlist_items(pl_id, limit=100, offset=offset, fields=FIELDS) or {}
                    page_rows, page_track_ids = _extract(page, offset)
                    if not page_rows:
                        break
                    rows += page_rows
                    track_ids += page_track_ids
                    offset += 100
                    if progressive:
                        _paint_page(list(page_rows))
                        _set_title(_title(f"  [dim](loading {min(len(rows), total)}/{total})[/dim]"))

                if not current():
                    return
                _set_title(_title("  [dim](checking likes…)[/dim]") if multipage else _title())
                liked_by_id = {}

                def _on_liked_batch(start, vals):
                    batch = dict(zip(track_ids[start:start + len(vals)], vals))
                    liked_by_id.update(batch)
                    if multipage or not progressive:
                        _paint_hearts(batch)

                if track_ids:
                    self.spotify.check_saved_tracks(track_ids, on_batch=_on_liked_batch)
                liked = [bool(liked_by_id.get(r.get("id"), False)) for r in rows]
            except Exception as e:
                logger.exception("_open_playlist_table worker failed")
                # Don't leave "Loading playlist…" on screen forever. Typical cause:
                # Spotify only returns items for playlists the user owns or
                # collaborates on (403 otherwise, since Feb 2026).
                status = getattr(e, "http_status", None)
                if status == 403:
                    msg = ("[b]Can't load this playlist's tracks[/b]\n"
                           "[dim]Spotify only allows reading items of playlists you own "
                           "or collaborate on.[/dim]")
                else:
                    msg = f"[b]Couldn't load playlist[/b]\n[dim]{rich_escape(str(e)[:200])}[/dim]"
                self.call_from_thread(lambda: self._safe_update_right("playlist", pl_id, token, msg))
                return

            # Cache the finished list even if the user has since left: the work
            # is done, and the next open should not repeat it.
            self._cache_playlist(pl_id, rows, liked)

            # Final render: full track list with hearts resolved, clean title.
            _paint_final(rows, liked)
            _set_title(_title())
        threading.Thread(target=worker, daemon=True).start()

    # --- View-only sort of the open playlist (`s` / `S`) ------------------ #

    def _pl_sort_pref(self, profile: str = "playlist"):
        """(key, desc) for playlist tables (or another sort profile, e.g.
        Library > Downloaded), read once from spt_config.json."""
        if profile != "playlist":
            prefs = getattr(self, "_sort_prefs", None)
            if prefs is None:
                prefs = self._sort_prefs = {}
            if profile not in prefs:
                keys, ck, cdk, _ = playlist_sort.profile(profile)
                prefs[profile] = playlist_sort.load_pref(config.LOCAL_CFG, keys=keys, cfg_key=ck, cfg_desc_key=cdk)
            return prefs[profile]
        pref = getattr(self, "_pl_sort", None)
        if pref is None:
            pref = self._pl_sort = playlist_sort.load_pref(config.LOCAL_CFG)
        return pref

    def _pl_title(self, base_title, profile: str = "playlist"):
        lab = playlist_sort.label(*self._pl_sort_pref(profile))
        return f"{base_title}  [dim]({lab})[/dim]" if lab else base_title

    def _pl_sorted(self, rows, liked, profile: str = "playlist"):
        """Rows and their liked flags, both in the current view order."""
        rows = list(rows or [])
        liked = list(liked) if liked else []
        flag = {id(r): bool(liked[i]) if i < len(liked) else False for i, r in enumerate(rows)}
        keys, _, _, missing_last = playlist_sort.profile(profile)
        key, desc = self._pl_sort_pref(profile)
        out = playlist_sort.sort_rows(rows, key, desc, keys=keys, missing_last=missing_last)
        return out, [flag[id(r)] for r in out]

    @staticmethod
    def _pl_row_ident(r):
        # Rows are rebuilt on a cache refresh, so match by position + URI.
        return (r.get("_pos"), r.get("uri")) if isinstance(r, dict) else None

    def _resort_playlist_table(self, table, *, extra_rows=None, replace=None, follow=False):
        """Re-order a playlist table's model and repaint it.

        Row keys/indices are view positions, so everything indexed by them
        (liked map, row_to_* maps, context URIs, Multi-Add selection) is
        rebuilt from the sorted model. ``follow`` (user-initiated sort) jumps to
        the top row; otherwise the highlighted track stays highlighted and the
        scroll offset is kept (pages streaming in).
        """
        model = list(getattr(table, "_model_rows", None) or [])
        liked_map = getattr(table, "_liked_map", None) or {}
        cur = None
        try:
            cur = self._get_cursor_row(table)
        except Exception:
            cur = None
        cur_ident = self._pl_row_ident(model[cur]) if cur is not None and 0 <= cur < len(model) else None
        # Untouched cursor on row 0 while loading: stay at the top.
        if not follow and cur == 0:
            cur_ident = None

        multi = getattr(self, "_multi_add_selected_rows", None)
        multi_on = (getattr(self, "_multi_add_table", None) is table and isinstance(multi, set))
        sel_idents = {self._pl_row_ident(model[i]) for i in multi
                      if isinstance(i, int) and 0 <= i < len(model)} if multi_on else set()

        if replace is not None:
            rows, liked = replace
            pairs_rows, pairs_liked = list(rows or []), list(liked or [])
        else:
            pairs_rows = model + list(extra_rows or [])
            pairs_liked = [bool(liked_map.get(i, False)) for i in range(len(model))] + \
                [False] * len(extra_rows or [])
        new_rows, new_liked = self._pl_sorted(pairs_rows, pairs_liked,
                                              getattr(table, "_sort_profile", "playlist"))

        table._model_rows = new_rows
        table._liked_map = {i: v for i, v in enumerate(new_liked)}
        table._context_uris = [r.get("uri") for r in new_rows]
        table.row_to_uri = {}; table.row_to_title = {}; table.row_to_id = {}
        self._repaint_rows_from_model(table)

        if multi_on:
            multi.clear()
            multi.update(i for i, r in enumerate(new_rows) if self._pl_row_ident(r) in sel_idents)
        if follow and new_rows:
            # User-initiated sort: jump to the top of the new order.
            try:
                col = int(getattr(getattr(table, "cursor_coordinate", None), "column", 0) or 0)
                table.move_cursor(row=0, column=col, animate=False, scroll=True)
                table.scroll_home(animate=False)
            except Exception:
                logger.debug("_resort_playlist_table: jump to top failed")
        elif cur_ident is not None:
            for i, r in enumerate(new_rows):
                if self._pl_row_ident(r) == cur_ident:
                    # With a "/" filter the model index is not the screen row.
                    shown = self._model_to_display_row(table, i)
                    if shown is None:
                        break
                    try:
                        col = int(getattr(getattr(table, "cursor_coordinate", None), "column", 0) or 0)
                        table.move_cursor(row=shown, column=col, animate=False, scroll=follow)
                    except Exception:
                        logger.debug("_resort_playlist_table: cursor restore failed")
                    break

    def _open_playlist_tracks_table(self):
        rv = getattr(self, "_right_view", None)
        if not rv or rv[0] not in ("playlist", "downloaded"):
            return None
        try:
            table = self.query_one("#tracks_table", DataTable)
        except Exception:
            return None
        return table if getattr(table, "_pl_base_title", None) is not None else None

    def action_sort_playlist(self) -> None:
        """`s`: next sort criterion for the open playlist (view only)."""
        self._change_playlist_sort(cycle=True)

    def action_reverse_playlist_sort(self) -> None:
        """`S`: flip ascending/descending for the open playlist (view only)."""
        self._change_playlist_sort(reverse=True)

    def _change_playlist_sort(self, *, cycle=False, reverse=False) -> None:
        table = self._open_playlist_tracks_table()
        if table is None:
            self._notify("[b]Open a playlist (or Library > Downloaded) to sort its tracks.[/b]")
            return
        prof = getattr(table, "_sort_profile", "playlist")
        keys, ck, cdk, _ = playlist_sort.profile(prof)
        key, desc = self._pl_sort_pref(prof)
        if cycle:
            key, desc = playlist_sort.next_key(key, keys), False
        if reverse:
            desc = not desc
        if prof == "playlist":
            self._pl_sort = (key, desc)
        else:
            self._pl_sort_pref(prof)
            self._sort_prefs[prof] = (key, desc)
        try:
            if not isinstance(config.LOCAL_CFG, dict):
                config.LOCAL_CFG = {}
            config.LOCAL_CFG[ck] = key
            config.LOCAL_CFG[cdk] = desc
            config.save_local_config(config.LOCAL_CFG)
        except Exception:
            logger.exception("saving playlist sort failed")
        self._resort_playlist_table(table, follow=True)
        self._set_table_title("tracks_table", self._pl_title(table._pl_base_title, prof))
        arrow = "↓" if desc else "↑"
        what = "the copies are unchanged" if prof == "downloaded" else "the playlist is unchanged"
        self._notify(f"[b]Sorted by {playlist_sort.SORT_LABELS[key]} {arrow}[/b]  "
                     f"[dim](view only; {what})[/dim]")

    # How many playlists keep their tracks in memory. Enough to make going back
    # and forth instant without holding every big playlist of a long session.
    _PLAYLIST_CACHE_MAX = 8

    # A cache hit paints instantly, which reads as "nothing happened". Hold the
    # "Loading…" line up for this long before swapping in the cached rows, so
    # opening a playlist / library view always has a short visual acknowledgement.
    # Cold loads show loading naturally (network latency) and are untouched.
    _CACHE_LOADING_MIN_S = 0.25

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
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_follow_artists"):
            return
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
                # Same as the playlist path: rendering the cached rows in this
                # callstack overwrites loading_msg before Textual paints it, so a
                # cache hit reads as no response. Defer the cached paint a short
                # beat; guard against a fast refresh finishing first or the user
                # leaving the view.
                def _paint_cached():
                    if state["table"] is not None or not current():
                        return
                    state["table"] = render_first(list(cached))
                    set_status(f"{title}  [dim](refreshing…)[/dim]")
                self.set_timer(self._CACHE_LOADING_MIN_S, _paint_cached)

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
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_podcasts"):
            return
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
        req = getattr(self, "_require_cap", None)
        if callable(req) and not req("can_podcasts"):
            return
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
            _text, _hint = self._import_prompt_texts()
            right.mount(Static(_text))
            self.import_input = Input(placeholder=_hint)
            right.mount(self.import_input)
            try:
                self.import_input.focus()
            except Exception:
                pass
            self.level = self.LVL_VIEW
        except Exception:
            logger.exception("_open_import_playlists simplified prompt failed")
