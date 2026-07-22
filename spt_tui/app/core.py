"""App core: layout, mounting, top-level event routing."""

from __future__ import annotations

import os
import time
import threading
from typing import Dict, List, Optional, Tuple

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Header, Footer, Input, Static, ListView, ListItem, Label, DataTable
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

from .. import config
from ..config import logger, LOG_PATH
from ..constants import WELCOME, WELCOME_SPOTIFY_ART, WELCOME_AUTHOR, LIBRARY_ITEMS
from ..spotify_client import SpotifyClient
from ..local_player import LocalPlayer, LOCAL_START_SENTINEL
from ..widgets import HelpScroll, ContentPanel

class CoreMixin:
    def __init__(self):
        super().__init__()
        self.spotify = SpotifyClient()
        try:
            self._auto_load_playlists = os.getenv('SPT_AUTO_LOAD_PLAYLISTS', '0') == '1'
        except Exception:
            self._auto_load_playlists = False
        self._allow_auto_revalidate_likes = False
        self._force_revalidate_likes_once = False

        self._view_stack: List[Tuple] = []
        self.playlists_cache: List[Dict] = []

        self._create_playlist_state: Optional[dict] = None
        self._pending_add_uri: Optional[str] = None

        self._last_right_focus = None
        self._local_queue: List[Dict] = []
        self.repeat_state_cycle = ["off", "context", "track"]
        self.repeat_idx = 0

        self._lyrics_on = False
        self._lyrics_lines: List[Tuple[int, str]] = []
        self._lyrics_title = ""
        self._lyrics_interval = None
        self._lyrics_timer = None
        self._lyrics_track_id: Optional[str] = None
        self.lyrics_box: Optional[Static] = None

        # Honour a persisted lyrics-cache size cap (bytes) if the user set one;
        # otherwise the class default (_LYRICS_CACHE_MAX_BYTES) stands.
        try:
            _cap = config.LOCAL_CFG.get('lyrics_cache_max_bytes') if isinstance(config.LOCAL_CFG, dict) else None
            if isinstance(_cap, int) and _cap > 0:
                self._LYRICS_CACHE_MAX_BYTES = _cap
        except Exception:
            pass

        self._now_interval = None
        self._now_sync_interval = None
        self._now_tick_interval = None
        self._now_internal_track_id: Optional[str] = None
        self._now_internal_pos_ms: int = 0
        self._now_internal_dur_ms: int = 0
        self._now_internal_is_playing: bool = False
        self._now_internal_last_wall_ms: float = 0.0
        self.np_title: Optional[Static] = None
        self.np_bar_full: Optional[Static] = None
        self.np_times: Optional[Static] = None

        self._now_worker_lock = threading.Lock()

        self._queue_interval = None

        self.level = self.LVL_SECTIONS
        self.section_order = ["search", "lib", "pl"]
        self.section_idx = 0
        self.pl_index = 0
        self._suppress_first_selection = False

        self._right_view = None
        self._view_counter = 0
        self._multi_add_mode: bool = False
        self._multi_add_table = None
        self._multi_add_selected_rows: set = set()
        self._pending_multi_add_uris: Optional[List[str]] = None
        self._pending_container_add: Optional[dict] = None
        self._help_on: bool = False
        # Armed track removal awaiting confirmation, and the status line's
        # pending clear timer (see _notify).
        self._pending_remove_track: Optional[Dict] = None
        self._status_timer = None
        # Welcome is what compose() shows first; on_resize keeps it responsive.
        self._welcome_on: bool = True
        self._welcome_variant: str = "large"

    def compose(self) -> ComposeResult:
        self.right_panel = ContentPanel(Text(WELCOME), id="right")

        self.search_input = Input(placeholder="Search ...", id="search_input")
        try:
            self.search_input.password = False
            self.search_input.cursor_blink = True
        except Exception:
            pass

        lib_items = []
        for txt in LIBRARY_ITEMS:
            li = ListItem(Label(txt))
            li.data = {"type": "library", "name": txt}
            lib_items.append(li)
        self.lib_list = ListView(*lib_items, id="lib_list")
        self.pl_list = ListView(id="pl_list")

        yield Header(show_clock=False)

        with Container(id="grid"):
            with Container(id="top_bar"):
                with Container(id="search_wrap", classes="section"):
                    yield Static("Search", id="search_title", classes="title")
                    yield self.search_input
                with Container(id="help_wrap", classes="section"):
                    yield Static("Help\n(press ?)", id="help_box")

            with Container(id="left_col"):
                yield Vertical(Static("Library", classes="title"), self.lib_list,
                               id="section_lib", classes="section")
                yield Vertical(Static("Playlists", classes="title"), self.pl_list,
                               id="section_pl", classes="section")

            yield self.right_panel

            with Container(id="now_wrap"):
                self.np_title = Static("[dim]Now Playing: —[/dim]", id="np_title")
                yield self.np_title
                with Container(id="now_row_bar"):
                    self.np_bar_full = Static("[" + ("-" * 30) + "]", id="np_bar_full", markup=False)
                    yield self.np_bar_full
                self.np_times = Static("--:--", id="np_times")
                yield self.np_times

        # Transient feedback, on the overlay layer (see the CSS): a message
        # written into #right sits behind a mounted content table and is never
        # seen, which is exactly when most messages fire.
        self.status_line = Static("", id="status_line")
        yield self.status_line

        yield Footer()

    def _show_credentials_prompt(self, right) -> None:
        """Mount the 'Not signed in' message plus the three credential inputs."""
        right.mount(Static(
            "[b]Not signed in[/b]\nPlease configure your Spotify credentials (Client ID/Secret/Redirect URI) or re-authorize the app.\n"
            "You can set environment variables SPOTIPY_CLIENT_ID/SPOTIPY_CLIENT_SECRET/SPOTIPY_REDIRECT_URI or enter them in the UI.\n\n",
            markup=True))
        self.client_id_input = Input(placeholder="Client ID", id="client_id_input")
        self.client_secret_input = Input(placeholder="Client Secret", id="client_secret_input")
        self.redirect_input = Input(placeholder="Redirect URI (e.g. http://127.0.0.1:9999/callback)", id="redirect_input")
        right.mount(self.client_id_input)
        right.mount(self.client_secret_input)
        right.mount(self.redirect_input)
        self.client_id_input.focus()
        self._defer_now = True

    def on_mount(self) -> None:
        self.section_search = self.query_one("#search_wrap")
        self.section_lib = self.query_one("#section_lib")
        self.section_pl = self.query_one("#section_pl")
        self.search_title = self.query_one("#search_title", Static)

        try:
            left_col = self.query_one("#left_col"); left_col.can_focus = True
        except Exception: pass

        try:
            if not self.spotify.has_cached_token():
                try:
                    if not config.CONFIG_LOADED:
                        self._show_credentials_prompt(self._clear_right())
                    else:
                        try:
                            right = self._clear_right()
                            auth_url = self.spotify.prepare_authorize_url()
                            right.update("[b]Spotify authorization required[/b]\n\n")
                            right.mount(Static("Open the URL in your browser, authorize the app and paste the redirect URL here.:\n", markup=True))
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
                                self._defer_now = True
                            else:
                                logger.warning("prepare_authorize_url returned empty on startup; prompting for credentials")
                                self._show_credentials_prompt(right)
                        except Exception:
                            logger.exception("Could not display authorization UI")
                            if getattr(self, '_auto_load_playlists', False):
                                threading.Thread(target=self._load_playlists, daemon=True).start()
                            self._defer_now = False
                except Exception:
                    logger.exception("on_mount auth/config flow failed")
                    if getattr(self, '_auto_load_playlists', False):
                        threading.Thread(target=self._load_playlists, daemon=True).start()
                    self._defer_now = False
            else:
                if getattr(self, '_auto_load_playlists', False):
                    threading.Thread(target=self._load_playlists, daemon=True).start()
                self._defer_now = False
        except Exception:
            logger.exception("on_mount overall failed")
            if getattr(self, '_auto_load_playlists', False):
                threading.Thread(target=self._load_playlists, daemon=True).start()
            self._defer_now = False
        self._focus_section_by_idx(0)
        try: self.search_input.blur()
        except Exception: pass
        try:
            left_col = self.query_one("#left_col"); self.set_focus(left_col)
        except Exception: self.focus()

        try:

            if not getattr(self, "_defer_now", False):
                try:
                    if self._now_sync_interval is None:
                        self._now_sync_interval = self.set_interval(1.5, self._sync_playback, pause=False)
                    if self._now_tick_interval is None:
                        self._now_tick_interval = self.set_interval(0.5, self._update_now_bar, pause=False)
                except Exception:
                    if self._now_interval is None:
                        self._now_interval = self.set_interval(1.5, self._update_now_bar, pause=False)
        except Exception:
            logger.exception("Could not start NOW BAR interval")
        try:
            # Instant paint from the on-disk cache while the network refresh runs.
            self.paint_playlists_from_disk()
        except Exception:
            logger.exception("paint_playlists_from_disk failed")
        try:
            if config.CONFIG_LOADED or self.spotify.has_cached_token():
                threading.Thread(target=self._load_playlists, args=(True,), daemon=True).start()
        except Exception:
            logger.exception("Could not start initial playlist loader")
        try:
            def _playlist_retry_worker():
                try:
                    for _ in range(8):
                        # Stop early if the app is shutting down.
                        if getattr(self, '_closing', False):
                            break
                        # Give up only if there is genuinely no token to use;
                        # an expired-but-refreshable one is fine (ensure()
                        # refreshes it). Keep retrying until playlists load.
                        try:
                            if not self.spotify.has_cached_token():
                                break
                        except Exception:
                            pass
                        try:
                            self._load_playlists(force=True)
                        except Exception:
                            logger.exception("_playlist_retry_worker: _load_playlists call failed")
                        if getattr(self, 'playlists_cache', None):
                            break
                        time.sleep(1.5)
                except Exception:
                    logger.exception("_playlist_retry_worker failed")

            threading.Thread(target=_playlist_retry_worker, daemon=True).start()
        except Exception:
            logger.exception("Could not start playlist retry worker")

    def _stop_all_intervals(self) -> None:
        """Pause and drop every periodic timer. Idempotent: safe to call more
        than once (e.g. teardown running twice)."""
        for attr in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
                     "_devices_interval", "_queue_interval", "_lyrics_interval"):
            it = getattr(self, attr, None)
            if it is not None:
                try:
                    it.pause()
                except Exception:
                    logger.debug("stopping interval %s during teardown failed", attr)
                setattr(self, attr, None)
        # One-shot, but it fires into the widget tree just the same.
        self._cancel_status_timer()

    def on_unmount(self) -> None:
        """Application teardown. Textual has already flagged the app as closing
        by this point; we mirror it on `_closing` (which background workers read)
        and stop every timer so no worker paints into a torn-down app."""
        self._closing = True
        self._stop_all_intervals()

    # How long a message stays up. Long enough to read, short enough not to sit
    # over the now-playing bar.
    STATUS_SECONDS = 4.0

    def _notify(self, message: str, *, warn: bool = False, seconds: float | None = None,
                sticky: bool = False) -> None:
        """Show a transient message on the status line.

        Feedback must never go through ``right_panel.update``: while a content
        table is mounted the panel's own content is covered by it, so the
        message is invisible — and that is precisely when most of these fire
        ("no row selected", "track removed", …). Safe to call before the widget
        exists (startup) and during teardown; it just does nothing then.
        """
        line = getattr(self, "status_line", None)
        if line is None or getattr(self, "_closing", False):
            return
        try:
            # A newer message supersedes the previous one, and its timer must be
            # cancelled or it would clear the new message early.
            self._cancel_status_timer()
            line.update(message)
            line.set_class(bool(warn), "-warn")
            line.add_class("-shown")
            if sticky:
                # A mode indicator (multi-add): it stays until the mode ends.
                return
            secs = self.STATUS_SECONDS if seconds is None else float(seconds)
            self._status_timer = self.set_timer(secs, self._clear_status_line)
        except Exception:
            logger.exception("could not show status message")

    def _cancel_status_timer(self) -> None:
        timer = getattr(self, "_status_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                logger.debug("stopping the status timer failed")
            self._status_timer = None

    def _clear_status_line(self) -> None:
        self._status_timer = None
        line = getattr(self, "status_line", None)
        if line is None:
            return
        try:
            line.remove_class("-shown")
            line.remove_class("-warn")
            line.update("")
        except Exception:
            logger.debug("clearing the status line failed")

    def _clear_right(self):
        right: Static = self.right_panel
        # Any view taking over the right panel is no longer the welcome screen,
        # so on_resize must stop re-rendering the welcome into it.
        self._welcome_on = False
        # Any right-panel takeover (results, menu, a new search) ends the
        # "searching" state, so the taking-longer watchdog must not repaint.
        self._searching_token = None
        right.update("")
        # A content table borrows #right's frame (title + no padding); reset both
        # so the next view (Welcome, lyrics, the search grid, …) is unaffected.
        try:
            right.remove_class("table-view")
            right.border_title = ""
        except Exception:
            pass
        for w in list(right.children):
            w.remove()
        return right

    # ------------------------------------------------------------------ #
    # Responsive welcome screen
    # ------------------------------------------------------------------ #
    def _welcome_dims(self, tw: int, th: int):
        """Approximate the right panel's content area from a terminal size.

        Layout chrome: the 38-col left column + ~6 cols of grid gutter/borders,
        and ~15 rows for the search bar + now-playing bar. When the left column
        is collapsed only the borders are subtracted."""
        collapsed = False
        try:
            collapsed = self.query_one("#grid").has_class("left-collapsed")
        except Exception:
            collapsed = False
        return max(0, int(tw or 0) - (6 if collapsed else 44)), max(0, int(th or 0) - 15)

    def _welcome_panel_size(self):
        # self.size is correct on a stable layout (used by _paint_welcome); on a
        # resize event self.size lags, so on_resize passes event.size instead.
        try:
            return self._welcome_dims(int(self.size.width or 0), int(self.size.height or 0))
        except Exception:
            return 80, 24

    def _welcome_variant_for(self, w: int, h: int) -> str:
        """Choose a welcome layout by available panel size. Thresholds are the
        content width × height; each level drops the piece that no longer fits:
          large   (w>=90, h>=20): Spotify figlet + portrait art + author
          medium  (w>=46, h>=12): Spotify figlet + author (portrait dropped)
          small   (w>=22, h>=5) : 3-line text header + author
          minimal (otherwise)   : title + author only
        """
        w, h = int(w or 0), int(h or 0)
        if w >= 90 and h >= 20:
            return "large"
        if w >= 46 and h >= 12:
            return "medium"
        if w >= 22 and h >= 5:
            return "small"
        return "minimal"

    def _render_welcome(self, width: int, height: int) -> str:
        v = self._welcome_variant_for(width, height)
        if v == "large":
            return WELCOME
        if v == "medium":
            return "\n\n" + WELCOME_SPOTIFY_ART + "\n\n\n" + WELCOME_AUTHOR + "\n"
        if v == "small":
            return "\n\nSPT-TUI\nSpotify in your terminal\n\n" + WELCOME_AUTHOR + "\n"
        return "SPT-TUI\n" + WELCOME_AUTHOR

    def _paint_welcome(self):
        """Render the size-appropriate welcome into the (already-clear) panel."""
        w, h = self._welcome_panel_size()
        self._welcome_variant = self._welcome_variant_for(w, h)
        try:
            self.right_panel.update(self._render_welcome(w, h))
        except Exception:
            logger.exception("could not paint welcome")
        self._welcome_on = True

    def on_resize(self, event) -> None:
        # While the welcome is showing, re-render its responsive variant (only when
        # the variant changes, so resizing never flickers it). Use event.size:
        # self.size still holds the pre-resize value at this point.
        if getattr(self, "_welcome_on", False):
            try:
                tw, th = int(event.size.width), int(event.size.height)
            except Exception:
                tw, th = int(self.size.width or 0), int(self.size.height or 0)
            w, h = self._welcome_dims(tw, th)
            variant = self._welcome_variant_for(w, h)
            if variant != getattr(self, "_welcome_variant", None):
                self._welcome_variant = variant
                try:
                    self.right_panel.update(self._render_welcome(w, h))
                except Exception:
                    logger.exception("welcome resize repaint failed")
            return
        # A visible result table re-fits its columns from ContentPanel.on_resize,
        # not here: at this point the right panel's content_size still holds its
        # pre-resize value, so the columns would be sized to the old width.
        # The combined-search 2x2 dashboard reflows (2x2 <-> stacked) and resizes
        # its panel columns on its own. Defer to after the refresh for the same
        # content_size reason.
        try:
            if len(self.query("#search_grid")) > 0:
                self.call_after_refresh(self._relayout_search_grid)
        except Exception:
            logger.exception("search grid resize failed")

    def on_input_submitted(self, event: Input.Submitted) -> None:

        try:

            if getattr(self, 'redirect_input', None) is event.input:
                try:
                    cid = (getattr(self, 'client_id_input', None).value or "").strip()
                    csec = (getattr(self, 'client_secret_input', None).value or "").strip()
                    ruri = (getattr(self, 'redirect_input', None).value or "").strip()
                    if not cid or not csec or not ruri:
                        self._notify("[b]All fields are required. Fill in all three and press Enter on the Redirect URI.[/b]")
                        return

                    cfg = {"client_id": cid, "client_secret": csec, "redirect_uri": ruri}
                    config.save_local_config(cfg)
                    config.CLIENT_ID = cid; config.CLIENT_SECRET = csec; config.REDIRECT_URI = ruri; config.CONFIG_LOADED = True

                    right = self._clear_right()
                    auth_url = self.spotify.prepare_authorize_url()
                    right.update("[b]Spotify authorization required[/b]\n\n")
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
                        self._defer_now = True
                    else:
                        logger.warning("prepare_authorize_url returned empty after entering credentials; prompting for re-entry")
                        self._show_credentials_prompt(right)
                except Exception:
                    logger.exception("client creds submit failed")
                return
            if getattr(self, 'auth_input', None) is event.input:
                raw = (event.value or "").strip()
                if not raw:
                    self._notify("[b]No URL pasted[/b]", warn=True)
                    return
                ok = self.spotify.finish_authorization(raw)
                if ok:
                    self._notify("[b]Authorization completed. Starting session...[/b]")
                    try:
                        try:
                            if self._now_sync_interval is None:
                                self._now_sync_interval = self.set_interval(1.5, self._sync_playback, pause=False)
                            if self._now_tick_interval is None:
                                self._now_tick_interval = self.set_interval(0.5, self._update_now_bar, pause=False)
                        except Exception:
                            if self._now_interval is None:
                                self._now_interval = self.set_interval(1.5, self._update_now_bar, pause=False)
                    except Exception:
                        logger.exception("Could not start NOW BAR interval after auth")
                    try:
                        self.action_escape_to_menu()
                    except Exception:
                        logger.exception("Could not navigate to main menu after auth")
                    try:
                        threading.Thread(target=lambda: self._load_playlists(force=True), daemon=True).start()
                    except Exception:
                        logger.exception("Could not start playlist loader after auth")
                    try:
                        del self.auth_input
                    except Exception:
                        pass
                    return
                else:
                    self._notify("[b]Authorization failed; check the URL and try again.[/b]")
                    return

            if getattr(self, 'create_name_input', None) is event.input:
                name = (event.value or "").strip()
                if not name:
                    self._notify("[b]Playlist name cannot be empty. Enter a name and press Enter.[/b]")
                    return
                self._create_playlist_state = {"name": name}
                right = self._clear_right()
                right.mount(Static(f'[b]Create playlist:[/b] {rich_escape(name)}\nPublic? (y/N)'))
                self.create_public_input = Input(placeholder='y = public, N = private')
                right.mount(self.create_public_input)
                self.create_public_input.focus()
                return
            if getattr(self, 'create_public_input', None) is event.input:
                ans = (event.value or "").strip().lower()
                pub = ans in ("y", "yes")
                if self._create_playlist_state is None:
                    self._create_playlist_state = {}
                self._create_playlist_state["public"] = pub
                right = self._clear_right()
                right.mount(Static(f'[b]Create playlist:[/b] {rich_escape(self._create_playlist_state.get("name",""))}\nCollaborative? (y/N)'))
                self.create_collab_input = Input(placeholder='y = collaborative, N = not collaborative')
                right.mount(self.create_collab_input)
                self.create_collab_input.focus()
                return
            if getattr(self, 'create_collab_input', None) is event.input:
                ans = (event.value or "").strip().lower()
                coll = ans in ("y", "yes")
                if self._create_playlist_state is None:
                    self._create_playlist_state = {}
                self._create_playlist_state["collaborative"] = coll
                right = self._clear_right()
                right.mount(Static(f'[b]Create playlist:[/b] {rich_escape(self._create_playlist_state.get("name",""))}\nEnter a description (or leave blank) and press Enter.'))
                self.create_desc_input = Input(placeholder='Description…')
                right.mount(self.create_desc_input)
                self.create_desc_input.focus()
                return
            if getattr(self, 'create_desc_input', None) is event.input:
                desc = (event.value or "").strip()
                st = self._create_playlist_state or {}
                name = st.get("name")
                public = bool(st.get("public", False))
                collaborative = bool(st.get("collaborative", False))
                right = self._clear_right()
                right.update(f"[b]Creating playlist:[/b] {rich_escape(name)} …")
                def _worker_create(nm, pub, collab, dsc):
                    try:
                        pl = self.spotify.create_playlist(nm, public=pub, collaborative=collab, description=dsc)

                        def _paint_result():
                            try:
                                if pl:
                                    r = self._clear_right()
                                    r.update(f"[b]Playlist created:[/b] {rich_escape(pl.get('name',''))}")
                                    try:
                                        threading.Thread(target=lambda: self._load_playlists(force=True), daemon=True).start()
                                    except Exception:
                                        logger.exception('Could not start _load_playlists thread')
                                    try:
                                        def _delayed_back():
                                            try:
                                                time.sleep(1.0)
                                                try:
                                                    self.call_from_thread(self.action_escape_to_menu)
                                                except Exception:
                                                    self.action_escape_to_menu()
                                            except Exception:
                                                pass
                                        threading.Thread(target=_delayed_back, daemon=True).start()
                                    except Exception:
                                        logger.exception('Could not start delayed back-to-menu thread')
                                else:
                                    err = getattr(self.spotify, '_last_create_error', None)
                                    r = self._clear_right()
                                    if err:
                                        safe = rich_escape(str(err))
                                        r.update(f"[b]Failed to create playlist:[/b] {safe}")
                                    else:
                                        r.update('[b]Failed to create playlist.[/b]')
                            except Exception:
                                logger.exception('paint_result for create playlist failed')

                        try:
                            self.call_from_thread(_paint_result)
                        except Exception:
                            if getattr(self, '_closing', False):
                                logger.debug("create-playlist result paint dropped during teardown")
                            else:
                                logger.exception("create-playlist result: call_from_thread failed")

                    except Exception:
                        logger.exception('Create playlist worker failed')

                        def _paint_err():
                            try:
                                r = self._clear_right()
                                r.update('[b]Error creating playlist.[/b]')
                            except Exception:
                                pass

                        try:
                            self.call_from_thread(_paint_err)
                        except Exception:
                            if getattr(self, '_closing', False):
                                logger.debug("create-playlist error paint dropped during teardown")
                            else:
                                logger.exception("create-playlist error: call_from_thread failed")

                threading.Thread(target=_worker_create, args=(name, public, collaborative, desc), daemon=True).start()
                try:
                    del self.create_name_input
                    del self.create_public_input
                    del self.create_collab_input
                    del self.create_desc_input
                except Exception:
                    pass
                self._create_playlist_state = None
                return

            # Settings wizard — four numeric seek/volume fields followed by the
            # human-readable lyrics-cache size field. The numeric fields share one
            # parse+save; the size field parses MB/GB and finalises the wizard.
            _seek_fields = [
                ('seek_vol_down_input', 'seek_volume_down', 'volume down percent', 'seek_vol_up_input'),
                ('seek_vol_up_input', 'seek_volume_up', 'volume up percent', 'seek_track_input'),
                ('seek_track_input', 'seek_seconds_track', 'track jump seconds', 'seek_episode_input'),
                ('seek_episode_input', 'seek_seconds_episode', 'episode jump seconds', 'lyrics_cache_input'),
            ]
            for attr, cfg_key, label, next_attr in _seek_fields:
                if getattr(self, attr, None) is not event.input:
                    continue
                if not self._save_seek_setting(event.value or "", cfg_key, label):
                    return
                nxt = getattr(self, next_attr, None) if next_attr else None
                if nxt is not None:
                    try: nxt.focus()
                    except Exception: pass
                return

            if getattr(self, 'lyrics_cache_input', None) is event.input:
                if not self._save_lyrics_cache_setting(event.value or ""):
                    return
                self._finish_settings()
                return

            if getattr(self, 'confirm_delete_input', None) is event.input:
                raw = (event.value or "").strip()
                pending = getattr(self, '_pending_delete_playlist', None)
                try:
                    del self.confirm_delete_input
                except Exception:
                    pass
                if not pending:
                    try: self._notify('[b]No pending delete request.[/b]', warn=True)
                    except Exception: pass
                    return
                pl_id, pl_name = pending.get('id'), pending.get('name')
                try:
                    if raw == (pl_name or '').strip():
                        pending['stage'] = 'confirm'
                        right = self._clear_right()
                        right.update(f"[b]Delete playlist:[/b] {rich_escape(pl_name)}\nAre you sure? (y/N)")
                        self.confirm_delete_confirm_input = Input(placeholder='y/N')
                        right.mount(self.confirm_delete_confirm_input)
                        self.confirm_delete_confirm_input.focus()
                        return
                    else:
                        try: self._notify('[b]Name did not match; aborting delete.[/b]', warn=True)
                        except Exception: pass
                finally:
                    if not (pending.get('stage') == 'confirm'):
                        try: del self._pending_delete_playlist
                        except Exception: pass
                return

            if getattr(self, 'confirm_delete_confirm_input', None) is event.input:
                raw = (event.value or "").strip().lower()
                pending = getattr(self, '_pending_delete_playlist', None)
                try:
                    del self.confirm_delete_confirm_input
                except Exception:
                    pass
                if not pending:
                    try: self._notify('[b]No pending delete request.[/b]', warn=True)
                    except Exception: pass
                    return
                pl_id, pl_name = pending.get('id'), pending.get('name')
                try:
                    if raw in ('y', 'yes'):
                        def _worker_del(pid, pname):
                            try:
                                sp = self.spotify.ensure()
                                done = False
                                try:
                                    if hasattr(sp, 'current_user_unfollow_playlist'):
                                        sp.current_user_unfollow_playlist(pid); done = True
                                    elif hasattr(sp, 'user_playlist_unfollow'):
                                        sp.user_playlist_unfollow(pid); done = True
                                except Exception:
                                    logger.exception('Playlist unfollow failed')
                                if done:
                                    try:
                                        threading.Thread(target=lambda: self._load_playlists(force=True), daemon=True).start()
                                    except Exception:
                                        pass
                                    try:
                                        try:
                                            self.call_from_thread(self.action_escape_to_menu)
                                        except Exception:
                                            self.call_from_thread(lambda: self._notify(f"[b]Playlist deleted:[/b] {rich_escape(pname)}"))
                                    except Exception:
                                        pass
                                else:
                                    try: self.call_from_thread(lambda: self._notify('[b]Could not delete playlist.[/b]', warn=True))
                                    except Exception: pass
                            except Exception:
                                logger.exception('Delete playlist worker failed')
                        threading.Thread(target=_worker_del, args=(pl_id, pl_name), daemon=True).start()
                    else:
                        try:
                            if threading.current_thread() is threading.main_thread():
                                try:
                                    self.action_escape_to_menu()
                                except Exception:
                                    try: self._notify('[b]Deletion cancelled.[/b]')
                                    except Exception: pass
                            else:
                                try:
                                    self.call_from_thread(self.action_escape_to_menu)
                                except Exception:
                                    try: self._notify('[b]Deletion cancelled.[/b]')
                                    except Exception: pass
                        except Exception:
                            try: self._notify('[b]Deletion cancelled.[/b]')
                            except Exception: pass
                finally:
                    try: del self._pending_delete_playlist
                    except Exception: pass
                return

            if event.input is not self.search_input and event.input is not getattr(self, 'import_input', None): return
            if getattr(self, 'import_input', None) is event.input:
                try:
                    raw = (event.value or "").strip()
                    if not raw:
                        self._notify("[b]Empty input. Enter a playlist URI or ID.[/b]")
                        return

                    def worker_import(vraw: str):
                        right = self._clear_right()
                        right.update("[b]Importing playlist…[/b]")
                        try:
                            pl = vraw.strip()
                            pl_id = pl
                            try:
                                if 'open.spotify.com/playlist/' in pl:
                                    pl_id = pl.split('open.spotify.com/playlist/')[-1].split('?')[0]
                                elif pl.startswith('spotify:playlist:'):
                                    pl_id = pl.split(':')[-1]
                                else:
                                    if '/playlist/' in pl:
                                        pl_id = pl.rsplit('/playlist/', 1)[-1].split('?')[0]
                            except Exception:
                                pass

                            try:
                                sp = self.spotify.ensure()
                                pmeta = sp.playlist(pl_id)
                            except Exception as e:
                                logger.debug("_add_import: playlist fetch failed for %s: %s", pl_id, e)
                                pmeta = None
                                try:
                                    sp_app = self.spotify.ensure_app()
                                    pmeta = sp_app.playlist(pl_id)
                                    logger.debug("_add_import: fetched playlist via app client for %s", pl_id)
                                except Exception as e_app:
                                    logger.debug("_add_import: ensure_app.playlist failed for %s: %s", pl_id, e_app)

                                if not pmeta:
                                    logger.exception("_add_import: could not resolve playlist %s", pl_id)
                                    err_msg = rich_escape(str(e))
                                    def paint_err():
                                        self._notify(f"[b]Could not get playlist:[/b] {err_msg}", warn=True)
                                    try:
                                        self.call_from_thread(paint_err)
                                    except Exception:
                                        paint_err()
                                    return

                            pl_id_real = pmeta.get('id') if pmeta else pl_id
                            pl_name = (pmeta.get('name') if pmeta else pl)

                            def _worker_follow(pid, pname):
                                try:
                                    sp = self.spotify.ensure()
                                    done = False
                                    try:
                                        if hasattr(sp, 'current_user_follow_playlist'):
                                            sp.current_user_follow_playlist(pid)
                                            done = True
                                        elif hasattr(sp, 'user_playlist_follow'):
                                            sp.user_playlist_follow(pid)
                                            done = True
                                        elif hasattr(sp, 'current_user_follow_playlists'):
                                            sp.current_user_follow_playlists([pid])
                                            done = True
                                    except Exception:
                                        logger.exception('Follow playlist failed')
                                        done = False

                                    if done:
                                        try:
                                            threading.Thread(target=lambda: self._load_playlists(force=True), daemon=True).start()
                                        except Exception:
                                            pass
                                        try:
                                            self.call_from_thread(lambda: self._notify(f"[b]Playlist added to your library:[/b] {rich_escape(pname)}"))
                                        except Exception:
                                            pass
                                    else:
                                        try:
                                            self.call_from_thread(lambda: self._notify('[b]Could not add playlist to your library.[/b]', warn=True))
                                        except Exception:
                                            pass
                                except Exception:
                                    logger.exception('worker_follow failed')

                            threading.Thread(target=_worker_follow, args=(pl_id_real, pl_name), daemon=True).start()
                        except Exception:
                            logger.exception("worker_import failed")

                    threading.Thread(target=worker_import, args=(raw,), daemon=True).start()
                except Exception:
                    logger.exception("import_input submit failed")
                return
            raw = event.value.strip()
            if not raw: return
            # Immediate on-submit feedback + synchronous token happen inside
            # _dispatch_search (UI thread); it then starts the search worker.
            self._dispatch_search(raw)
        except Exception:
            logger.exception("on_input_submitted error")

    def _save_seek_setting(self, value: str, cfg_key: str, label: str) -> bool:
        """Parse and persist one seek/volume numeric setting. False on bad input."""
        try:
            v = max(0, int(str(value).strip()))
        except (TypeError, ValueError):
            try:
                self._notify(f'[b]Invalid number for {label}.[/b]', warn=True)
            except Exception:
                pass
            return False
        try:
            if not isinstance(config.LOCAL_CFG, dict):
                config.LOCAL_CFG = {}
            config.LOCAL_CFG[cfg_key] = v
            config.save_local_config(config.LOCAL_CFG)
        except Exception:
            logger.exception('Could not save %s', cfg_key)
        return True

    def _save_lyrics_cache_setting(self, value: str) -> bool:
        """Parse a human-readable lyrics-cache size ('200 MB', '1 GB') into bytes,
        validate the range, persist it and apply it to the live cap. Shows a clear
        error and returns False on invalid or out-of-range input."""
        parsed = config.parse_size(value)
        if parsed is None:
            try:
                self._notify('[b]Invalid size for lyrics cache.[/b] Use a number with an explicit unit, e.g. 200 MB or 1 GB.', warn=True)
            except Exception:
                pass
            return False
        if parsed < config.LYRICS_CACHE_MIN_BYTES or parsed > config.LYRICS_CACHE_MAX_BYTES_LIMIT:
            lo = config.format_size(config.LYRICS_CACHE_MIN_BYTES)
            hi = config.format_size(config.LYRICS_CACHE_MAX_BYTES_LIMIT)
            try:
                self._notify(f'[b]Lyrics cache size out of range.[/b] Enter a value between {lo} and {hi}.')
            except Exception:
                pass
            return False
        try:
            if not isinstance(config.LOCAL_CFG, dict):
                config.LOCAL_CFG = {}
            config.LOCAL_CFG['lyrics_cache_max_bytes'] = parsed
            config.save_local_config(config.LOCAL_CFG)
        except Exception:
            logger.exception('Could not save lyrics_cache_max_bytes')
        # Apply immediately so the running session honours the new cap without a
        # restart (the persisted value is re-applied at startup in __init__).
        try:
            self._LYRICS_CACHE_MAX_BYTES = parsed
        except Exception:
            pass
        return True

    def _finish_settings(self) -> None:
        """Tear down the settings-wizard inputs and bounce back to the menu."""
        for a in ('seek_vol_down_input', 'seek_vol_up_input', 'seek_track_input',
                  'seek_episode_input', 'lyrics_cache_input'):
            try: delattr(self, a)
            except Exception: pass
        self._clear_right().update('[b]Settings saved![/b]')

        def _return():
            try:
                time.sleep(1)
                self.call_from_thread(lambda: (self._clear_right().update(WELCOME), setattr(self, 'level', self.LVL_SECTIONS), self._focus_section_by_idx(0)))
            except Exception:
                pass
        threading.Thread(target=_return, daemon=True).start()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is self.search_input:
            txt = event.value or ""
            self.search_title.update(f"Search: {rich_escape(txt)}" if txt else "Search")

    def action_refresh(self) -> None:
        try:
            # In the lyrics view, Ctrl+R forces a fresh LRCLIB fetch and ignores
            # any cached negative ('not found') entry for this song.
            if getattr(self, '_lyrics_on', False):
                try:
                    self._start_lyrics_load(force=True)
                except Exception:
                    logger.exception("action_refresh: lyrics reload failed")
                return

            rv = getattr(self, "_right_view", None)

            # The devices view carries no view token (it leaves _right_view stale),
            # so detect it by its mounted table before the rv-based routing.
            try:
                if len(self.query("#devices_table")) > 0:
                    self._refresh_devices_table(); return
            except Exception:
                pass

            # CONTEXTUAL priority: refresh the active right-hand view first. Leftover
            # text in the search box must never turn Ctrl+R into a search.
            if rv:
                kind = rv[0]
                if kind == 'playlist':
                    pdata = rv[3] if len(rv) > 3 and rv[3] else None
                    if pdata is None:
                        pdata = next((p for p in (self.playlists_cache or []) if p.get('id') == rv[1]), None)
                    if pdata:
                        threading.Thread(target=lambda: self._open_playlist_table(pdata), daemon=True).start()
                    return
                if kind == 'liked':
                    self._open_liked_table(); return
                if kind == 'recent':
                    # Runs on the UI thread: its setup (token + clear + loading)
                    # is light and must clear on the UI thread; the fetch is on
                    # its own inner worker.
                    self._open_recently_table(); return
                if kind == 'albums':
                    self._open_saved_albums(); return
                if kind == 'podcasts':
                    self._open_saved_podcasts(); return
                if kind == 'episodes':
                    self._open_saved_episodes(); return
                if kind == 'artists':
                    self._open_saved_artists(); return
                if kind == 'album' and len(rv) > 3 and rv[3]:
                    self._open_album_table(rv[3], push_stack=False); return
                if kind == 'artist' and len(rv) > 3 and rv[3]:
                    self._open_artist_table(rv[3], push_stack=False); return
                if kind == 'podcast' and len(rv) > 3 and rv[3]:
                    self._open_podcast_table(rv[3], push_stack=False); return
                if kind == 'queue':
                    self._refresh_queue_table(); return
                if kind == 'search':
                    q = rv[1]
                    if q:
                        self._dispatch_search(q); return

            # Search only repeats when the user is genuinely in Search context (the
            # input is focused). Text alone in the box does NOT trigger a search.
            try:
                focused = getattr(self, 'focused', None)
            except Exception:
                focused = None
            si = getattr(self, 'search_input', None)
            if si is not None and focused is si:
                raw = (si.value or "").strip()
                if raw:
                    self._dispatch_search(raw); return

            # Welcome / no applicable view: refresh only the playlists menu list.
            self._force_revalidate_likes_once = True
            threading.Thread(target=lambda: self._load_playlists(force=True), daemon=True).start()
        except Exception:
            logger.exception("action_refresh failed")

    def action_help(self):
        try:
            if getattr(self, '_help_on', False):
                try:
                    self.action_escape_to_menu()
                except Exception:
                    try: self.set_focus(getattr(self, 'search_input', None))
                    except Exception: pass
                try: self._help_on = False
                except Exception: pass
                return
        except Exception:
            pass
        self._leave_lyrics_mode()   # opening Help exits lyrics
        help_text = f"""[b]Help & Keybindings[/b]

    [b]Main / Navigation[/b]
        - Esc: Return to main menu
        - Ctrl+Q: Quit
        - ↑ / ↓ : Move between sections and list items
        - Tab / Shift+Tab : Cycle focus across Search, Help, Library, Playlists and the open content (without entering a section)
        - / : Focus search input
        - Enter: Open / Play selected item

    [b]Playback Controls[/b]
        - Space: Play / Pause
        - n: Next track
        - p: Previous / Restart
        - r: Cycle repeat (off → context → track)
        - c: Add selected track to Queue
        - Ctrl+S: Toggle shuffle
        - - / + : Volume down / up (±5%)
        - m: Mute / Unmute
        - Ctrl+Left / Ctrl+Right : Seek back / Seek forward (uses configured seconds)

    [b]Library & Playlist / Queue Management[/b]
        - f: Toggle Favorite (Like / Unlike selected track)
        - Ctrl+Shift+P: Add to a playlist — the selected track/episode, or all tracks of a selected album/artist/playlist/podcast
        - Ctrl+D: Delete (playlist or remove item)
        - Ctrl+R: Refresh / reload content
        - Ctrl+C: Open Queue view
        - Ctrl+T: Import playlists
        - Ctrl+B: Hide / show the left sidebar

    [b]Multi-Add / Selection Mode[/b]
        - Ctrl+L: Toggle Multi-Add mode
        - In Multi-Add select rows using movement keys and Enter to toggle selection
        - Use Ctrl+Shift+P to add selected rows to a playlist

    [b]Search (quick filters)[/b]
    Start your query with any of these prefixes (case-insensitive):
        - /ART <query>  — search artists
        - /ALB <query>  — search albums
        - /TRK <query>  — search tracks
        - /PLY <query>  — search playlists
    You can also craft queries normally; prefixes and smart parsing are supported.

    [b]Combined search results (2x2)[/b]
    A search without a prefix opens four panels — Songs / Artists on top,
    Albums / Playlists below. Navigation has two levels:
      Choosing a panel (start here):
        - ↑ / ↓ / ← / → : Move the selection between the four panels
        - Tab / Shift+Tab : Cycle through the panels
        - ← on a left panel: back to the main menu
        - Enter: enter the selected panel's content
      Inside a panel's content:
        - ↑ / ↓ : Move between results
        - ← / → : Back to choosing a panel
        - Enter: play a song / open an artist, album or playlist
        - f: Like / Unlike the selected song (Songs panel)
    (A prefixed search — /TRK, /ART, /ALB, /PLY, /PDC, /EPS — keeps a single
    full-size list of that one type.)

    [b]Lyrics & Now Playing[/b]
        - l: Toggle Lyrics view (shows synced lyrics when available)

    [b]Devices & Settings[/b]
        - d: Open device manager (transfer playback)
        - <: Open settings (volume steps, seek jump times, lyrics cache size)
        - - / + : Volume down / up

    [b]Help & Misc[/b]
        - ?: Toggle this Help view
        - F1: Toggle this Help view
        - Ctrl+O: Select all items (multi-select review)
        - Ctrl+A: Add the selected items (multi-select review); select/deselect all rows (Multi-Add mode)
        - Ctrl+Q: Quit application
        - Left / Right Arrows: Move focus between left column and right panel (and vice-versa)
        - Log file: {LOG_PATH}

    """

        right = self._clear_right()
        try:
            # A focusable scroll container so Up/Down/PageUp/PageDown/Home/End
            # scroll the (long) help text with the keyboard, not just the mouse.
            self.help_scroll = HelpScroll(Static(help_text, markup=True, id="help_text"), id="help_scroll")
            right.mount(self.help_scroll)
            try:
                self.help_scroll.focus()
            except Exception:
                pass
        except Exception:
            right.update(help_text)
        try:
            if self.spotify.has_cached_token():
                threading.Thread(target=self._load_playlists, args=(True,), daemon=True).start()
        except Exception:
            logger.exception("Could not start playlist loader when opening help")
        try:
            self._help_on = True
        except Exception:
            pass
        try:
            self.level = self.LVL_VIEW
        except Exception:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        sender: ListView = event.list_view
        if self.level != self.LVL_SECTION_CONTENT and not (getattr(self, '_lyrics_on', False) and sender.id in ("pl_list", "lib_list")):
            return
        if self._suppress_first_selection:
            self._suppress_first_selection = False; return
        self._leave_lyrics_mode()
        if sender.id == "pl_list" and event.index is not None:
            li: ListItem = sender.children[event.index]
            pdata = getattr(li, "data", {})
            if pdata.get("type") == "playlist":
                self._open_playlist_table(pdata)
        elif sender.id == "lib_list" and event.index is not None:
            li: ListItem = sender.children[event.index]
            name = (getattr(li, "data", {}) or {}).get("name", "")
            self._open_library_item(name)

    def _select_device(self, dev_id) -> None:
        """Route a Devices-view selection. The synthetic sentinel row starts and
        activates the local librespot player; any real id transfers to it."""
        if not dev_id:
            return
        if dev_id == LOCAL_START_SENTINEL:
            lp = getattr(self, "local_player", None)
            if lp is not None:
                threading.Thread(target=lp.start_and_activate, daemon=True).start()
            return
        threading.Thread(
            target=lambda: self.spotify.transfer(dev_id, force_play=True), daemon=True).start()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table: DataTable = event.data_table
        if table.id == "tracks_table" and hasattr(table, "row_to_uri"):
            self._play_row(event.row_key, table)
        elif table.id == "devices_table" and hasattr(table, "row_to_device"):
            dev_id = table.row_to_device.get(event.row_key)
            if dev_id:
                self._select_device(dev_id)
                self._back_one_level()

            left_col = self.query_one('#left_col')
            try:
                if self.level == self.LVL_SECTION_CONTENT:
                    which = self.section_order[self.section_idx] if hasattr(self, 'section_order') else None
                    if which == 'pl' and getattr(self, 'pl_list', None):
                        try: self.pl_list.focus()
                        except Exception: pass
                    elif which == 'lib' and getattr(self, 'lib_list', None):
                        try: self.lib_list.focus()
                        except Exception: pass
                    else:
                        try: left_col.focus()
                        except Exception: pass
                else:
                    try: left_col.focus()
                    except Exception: pass
            except Exception:
                try: left_col.focus()
                except Exception: pass
