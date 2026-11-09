"""Provider picker and live provider switching.

* ``P`` (from any view) opens :class:`ProviderPickerScreen`, a modal list of
  the providers with their readiness ("available", "missing mpv"...). Settings
  embeds the same list (``self.provider_select``).
* Picking one calls :meth:`ProviderSwitchMixin.switch_provider`, which swaps
  the provider **without a restart**: the new provider is created first (if
  that fails nothing changes), then the current one's resources are released
  (mpv, sockets, librespot), session state is reset (caches, views, queue,
  playback state) and the active view is reloaded. A failure after the old
  provider was closed rolls back to a fresh instance of the previous one.
* The choice is saved to ``spt_config.json`` — unless ``SPT_PROVIDER`` is set:
  the environment wins at startup, so the switch then applies to this session
  only, and the picker says so.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from .. import providers
from ..config import logger
from ..local_player import LocalPlayer


def provider_statuses(**kw) -> List[dict]:
    out = []
    for name in providers.PROVIDER_NAMES:
        try:
            out.append(providers.provider_status(name, **kw))
        except Exception:
            logger.exception("provider_status(%s) failed", name)
            out.append({"name": name, "usable": True, "issues": ["status unknown"]})
    return out


def status_label(st: dict) -> str:
    if not st.get("issues"):
        return "available"
    return ("" if st.get("usable", True) else "unavailable: ") + ", ".join(st["issues"])


def status_markup(st: dict) -> str:
    """Status in theme colours: green available, amber something missing
    (still usable), red cannot start. Textual markup ($variables)."""
    label = rich_escape(status_label(st))
    if not st.get("issues"):
        return f"[$success]✓ {label}[/]"
    if st.get("usable", True):
        return f"[$warning]▲ {label}[/]"
    return f"[$error]✗ {label}[/]"


def build_provider_options(current: Optional[str], statuses: List[dict]) -> list:
    """Options for the provider list (picker and Settings): a mark for the
    active provider (● / ○), its name and description, and the coloured
    readiness on the second line. Entries are separated by a rule."""
    opts: list = []
    for st in statuses:
        name = st["name"]
        is_active = name == current
        mark = "[$primary]●[/]" if is_active else "[$text-muted]○[/]"
        desc = rich_escape(providers.PROVIDER_DESCRIPTIONS.get(name, name))
        active = "  [b $background on $primary] ACTIVE [/]" if is_active else ""
        prompt = (f"{mark} [b]{rich_escape(name)}[/b]  [$text-muted]{desc}[/]{active}\n"
                  f"   {status_markup(st)}")
        if opts:
            opts.append(None)   # separator line
        opts.append(Option(prompt, id=name))
    return opts


def env_warning_text() -> Optional[str]:
    env = providers.env_override()
    if not env:
        return None
    return (f"SPT_PROVIDER={env} is set: it decides the provider at startup, so a "
            f"change here applies to this session only and is not saved.")


class ProviderPickerScreen(ModalScreen):
    """Modal list of providers. Dismisses with the chosen name, or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    ProviderPickerScreen { align: center middle; background: $background 70%; }
    #provider_picker { width: 92; max-width: 96%; height: auto; max-height: 94%;
        border: round $primary; border-title-color: $primary; border-title-style: bold;
        background: $surface; padding: 1 2; }
    #provider_picker_sub { color: $text-muted; padding-bottom: 1; }
    #provider_picker_env { color: $warning; padding-bottom: 1; }
    #provider_options { height: auto; max-height: 20; border: round $panel-lighten-2;
        background: $surface; padding: 0 1; }
    #provider_options:focus { border: round $primary; }
    #provider_options > .option-list--option-highlighted { background: $primary 22%; }
    #provider_options:focus > .option-list--option-highlighted { background: $primary 30%; }
    #provider_picker_hint { margin-top: 1; color: $text-muted; background: $panel; padding: 0 1; }
    """

    def __init__(self, current: Optional[str], statuses: List[dict], *, offer_import: bool = False):
        super().__init__()
        self._current = current
        self._statuses = statuses
        self._offer_import = offer_import

    def compose(self) -> ComposeResult:
        box = Vertical(id="provider_picker")
        box.border_title = "♫ Music provider"
        with box:
            yield Static("Where SPT-TUI searches, keeps your library and plays. Switching is "
                         "instant and keeps each provider's library.", id="provider_picker_sub")
            warn = env_warning_text()
            if warn:
                yield Static("▲ " + rich_escape(warn), id="provider_picker_env")
            opts = build_provider_options(self._current, self._statuses)
            if self._offer_import:
                from .spotify_import_view import PICKER_IMPORT_ID
                opts += [None, Option("[$accent]⇪[/] [b]Import from Spotify…[/b]  [$text-muted]copy your "
                                      "Spotify playlists, likes, albums and artists into this "
                                      "provider's local library (read-only on Spotify)[/]",
                                      id=PICKER_IMPORT_ID)]
            yield OptionList(*opts, id="provider_options")
            yield Static("↑↓ choose  ·  Enter apply  ·  Esc close", id="provider_picker_hint")

    def on_mount(self) -> None:
        ol = self.query_one("#provider_options", OptionList)
        ids = [ol.get_option_at_index(i).id for i in range(ol.option_count)]
        if self._current in ids:
            ol.highlighted = ids.index(self._current)
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProviderSwitchMixin:
    # ------------------------------------------------------------------ #
    # Picker
    # ------------------------------------------------------------------ #
    def _current_provider_name(self) -> Optional[str]:
        name = getattr(getattr(self, "spotify", None), "name", None)
        return name if isinstance(name, str) else None

    def action_choose_provider(self) -> None:
        """Open the provider picker (statuses are probed off the UI thread:
        the Subsonic check may ask the keyring)."""
        if getattr(self, "_picker_opening", False) or isinstance(self.screen, ProviderPickerScreen):
            return
        self._picker_opening = True

        def worker():
            try:
                statuses = provider_statuses()
            except Exception:
                logger.exception("provider statuses failed")
                statuses = [{"name": n, "usable": True, "issues": []} for n in providers.PROVIDER_NAMES]

            def show():
                self._picker_opening = False
                try:
                    self.push_screen(ProviderPickerScreen(self._current_provider_name(), statuses,
                                                          offer_import=self._spotify_import_visible()),
                                     self._on_provider_picked)
                except Exception:
                    logger.exception("could not open the provider picker")
            try:
                self.call_from_thread(show)
            except Exception:
                self._picker_opening = False
        threading.Thread(target=worker, daemon=True).start()

    def _on_provider_picked(self, name: Optional[str]) -> None:
        from .spotify_import_view import PICKER_IMPORT_ID
        if name == PICKER_IMPORT_ID:
            self.action_import_from_spotify()
        elif name:
            self.switch_provider(name)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is getattr(self, "appearance_select", None):
            event.stop()
            on = event.option.id == "transparent"
            self._save_plain_flag("transparent_background", on)
            self._apply_transparency(on)
            self._notify(f"[b]Background: {'transparent' if on else 'opaque'}[/b]")
            return
        if event.option_list is getattr(self, "ytdlp_select", None):
            event.stop()
            if event.option.id == "update":
                self._notify("[b]yt-dlp:[/b] updating…")
                threading.Thread(target=self._ytdlp_update_worker, daemon=True).start()
            else:
                threading.Thread(target=self._ytdlp_refresh_status, args=(True,), daemon=True).start()
            return
        if event.option_list is getattr(self, "spicy_select", None):
            event.stop()
            on = event.option.id == "on"
            self._save_plain_flag("spicy_lyrics", on)
            self._notify(f"[b]Spicy Lyrics:[/b] {'on (used when a key is found)' if on else 'off, LRCLIB only'}")
            return
        if event.option_list is getattr(self, "visualizer_mode_select", None):
            event.stop()
            mirrored = event.option.id == "mirrored"
            self._save_plain_flag("visualizer_mode", "mirrored" if mirrored else "bars")
            self._apply_visualizer_mode(mirrored)
            self._notify(f"[b]Visualizer default:[/b] {'mirrored' if mirrored else 'bars'}")
            return
        if event.option_list is getattr(self, "header_select", None):
            event.stop()
            on = event.option.id == "show"
            self._save_plain_flag("show_header", on)
            self._apply_title_bar(on)
            return
        if event.option_list is getattr(self, "hints_select", None):
            event.stop()
            on = event.option.id == "show"
            self._save_plain_flag("show_key_hints", on)
            self._apply_key_hints(on)
            return
        if event.option_list is getattr(self, "accent_select", None):
            event.stop()
            self._set_accent(event.option.id)
            return
        # The provider list embedded in Settings.
        if event.option_list is getattr(self, "provider_select", None):
            event.stop()
            self.switch_provider(event.option.id)

    # ------------------------------------------------------------------ #
    # Live switch
    # ------------------------------------------------------------------ #
    def switch_provider(self, name: str) -> None:
        """Swap the active provider at runtime (worker thread + UI-thread
        install). Picking the active provider reloads it (e.g. after editing
        the Subsonic server settings)."""
        canon = providers.normalize_provider_name(name)
        if not canon:
            self._notify(f"[b]Unknown provider:[/b] {rich_escape(str(name))}", warn=True)
            return
        if getattr(self, "_switching_provider", False):
            self._notify("[b]A provider switch is already in progress.[/b]", warn=True)
            return
        if getattr(self, "_import_running", False):
            self._notify("[b]A Spotify import is running:[/b] let it finish or press Esc to cancel it first.",
                         warn=True)
            return
        self._switching_provider = True
        self._notify(f"[b]Switching to {rich_escape(canon)}…[/b]", sticky=True)
        threading.Thread(target=self._switch_provider_worker, args=(canon,), daemon=True).start()

    def _ui(self, fn, *args, **kw):
        """Run on the UI thread and return its result (re-raising its error)."""
        return self.call_from_thread(fn, *args, **kw)

    def _switch_provider_worker(self, canon: str) -> None:
        old = getattr(self, "spotify", None)
        old_name = self._current_provider_name()
        rv = getattr(self, "_right_view", None)
        try:
            # 1) Create the new provider first: if it cannot start, nothing has
            #    been touched and the current one simply stays.
            try:
                new = providers.create_named(canon)
            except Exception as exc:
                logger.warning("provider switch to %s refused: %s", canon, exc)
                self._ui(self._notify,
                         f"[b]Could not switch to {rich_escape(canon)}:[/b] {rich_escape(str(exc))} "
                         f"Staying on {rich_escape(str(old_name))}.", warn=True, seconds=10.0)
                return
            # 2) Stop what the old provider is playing (a Spotify device keeps
            #    playing otherwise), then release it (mpv, sockets, librespot).
            pause_warning = self._pause_outgoing(old)
            self._close_provider_resources(old)
            # 3) Install it, reset state and reload the view.
            try:
                self._ui(self._install_provider, new, rv)
            except Exception as exc:
                logger.exception("installing provider %s failed; rolling back", canon)
                try:
                    new.close()
                except Exception:
                    logger.exception("closing the failed provider")
                restored = None
                if old_name:
                    try:
                        restored = providers.create_named(old_name)
                    except Exception:
                        logger.exception("re-creating %s for the rollback failed", old_name)
                if restored is None:
                    restored = old
                try:
                    self._ui(self._install_provider, restored, rv)
                except Exception:
                    logger.exception("rollback install failed")
                    self.spotify = restored
                self._ui(self._notify,
                         f"[b]Switch to {rich_escape(canon)} failed:[/b] {rich_escape(str(exc))} "
                         f"Restored {rich_escape(str(old_name))}.", warn=True, seconds=10.0)
                return
            # 4) Persist (unless SPT_PROVIDER forces the startup choice).
            label = getattr(new, "display_name", canon)
            if providers.env_override():
                msg = (f"[b]Provider: {rich_escape(label)}[/b] (this session only: "
                       f"SPT_PROVIDER is set, so the choice is not saved)")
            else:
                providers.save_provider_choice(canon)
                msg = f"[b]Provider: {rich_escape(label)}[/b] (saved)"
            if pause_warning:
                msg += f" — {pause_warning}"
            self._ui(self._notify, msg, warn=bool(pause_warning),
                     seconds=10.0 if pause_warning else 6.0)
        except Exception:
            logger.exception("provider switch failed")
        finally:
            self._switching_provider = False

    def _pause_outgoing(self, prov) -> Optional[str]:
        """Pause the outgoing provider if it is playing. mpv is stopped by
        close() anyway; a remote Spotify device needs an explicit pause, which
        a Free account (or a vanished device) cannot do: then say so."""
        try:
            pb = prov.get_playback() or {}
        except Exception:
            logger.exception("get_playback of the outgoing provider failed")
            pb = {}
        if not pb.get("is_playing"):
            return None
        caps = getattr(prov, "capabilities", None)
        can = bool(getattr(caps, "can_control_playback", True)) if caps is not None else True
        ok = False
        if can:
            try:
                ok = prov.pause() is not False
            except Exception:
                logger.exception("pausing the outgoing provider failed")
                ok = False
        if ok:
            return None
        # Returned, not shown: it is appended to the final "Provider: …"
        # message, which would otherwise overwrite it a moment later.
        label = getattr(prov, "display_name", "the previous provider")
        return (f"[b]could not pause {rich_escape(str(label))}[/b]: it may still be "
                f"playing on its device")

    def _close_provider_resources(self, prov) -> None:
        lp = getattr(self, "local_player", None)
        if lp is not None:
            try:
                lp.stop()
            except Exception:
                logger.exception("stopping librespot during provider switch failed")
            self.local_player = None
        close = getattr(prov, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.exception("closing the previous provider failed")

    def _reset_provider_state(self) -> None:
        """Forget everything that belongs to the previous provider."""
        self.playlists_cache = []
        self._playlist_cache = {}
        self._lib_cache = {}
        self._local_queue = []
        self._play_context = None
        # Invalidate in-flight playlist loads of the old provider.
        self._playlists_gen = int(getattr(self, "_playlists_gen", 0)) + 1
        self._sync_seq = int(getattr(self, "_sync_seq", 0)) + 1
        self._bar_last_track = None
        self._now_internal_track_id = None
        self._now_internal_pos_ms = 0
        self._now_internal_dur_ms = 0
        self._now_internal_is_playing = False
        self._now_shuffle = False
        self._now_repeat_state = "off"
        self.repeat_idx = 0
        self._last_played = None
        self._lyrics_track_id = None
        for attr in ("_pending_add_uri", "_pending_multi_add_uris", "_pending_container_add",
                     "_pending_remove_track"):
            setattr(self, attr, None)
        try:
            self._leave_lyrics_mode()
        except Exception:
            pass
        try:
            self.pl_list.clear()
        except Exception:
            pass

    def _install_provider(self, prov, rv=None) -> None:
        """UI thread: make ``prov`` active and reload. Raises on failure (the
        worker then rolls back)."""
        self.spotify = prov
        self._provider_notice = None
        self._provider_status_message = None
        self._reset_provider_state()
        if self._cap("supports_local_player"):
            try:
                self.local_player = LocalPlayer(
                    prov, open_url=getattr(self, "_open_url_in_browser", None),
                    on_event=getattr(self, "_local_player_event", None))
                self._start_local_player()
            except Exception:
                logger.exception("LocalPlayer init after switch failed")
                self.local_player = None
        name = getattr(prov, "display_name", None)
        self.sub_title = name if isinstance(name, str) and name != "Spotify" else ""
        self._refresh_library_items()
        try:
            self._update_now_bar()   # repaint "Now Playing: —" right away
        except Exception:
            logger.exception("now-bar repaint after switch failed")
        self._reload_after_switch(rv)
        self._start_provider_checks()

    # Library views that make sense to reopen on any provider that has them.
    _RELOADABLE = {
        "liked": ("_open_liked_table", "can_browse_library"),
        "recent": ("_open_recently_table", "can_browse_library"),
        "albums": ("_open_saved_albums", "can_browse_library"),
        "artists": ("_open_saved_artists", "can_follow_artists"),
        "podcasts": ("_open_saved_podcasts", "can_podcasts"),
        "episodes": ("_open_saved_episodes", "can_podcasts"),
    }

    def _reload_after_switch(self, rv) -> None:
        kind = rv[0] if rv else None
        # Back to the menu: clears the right panel, stops view timers and starts
        # the playlist load from the new provider.
        try:
            self.paint_playlists_from_disk()
        except Exception:
            logger.exception("paint_playlists_from_disk after switch failed")
        self._go_to_menu()
        if self._now_sync_interval is None and self.spotify.has_cached_token():
            try:
                self._now_sync_interval = self.set_interval(1.5, self._sync_playback, pause=False)
                self._now_tick_interval = self.set_interval(0.5, self._update_now_bar, pause=False)
                self._defer_now = False
            except Exception:
                logger.exception("could not start the now-playing timers after switch")
        if not self.spotify.has_cached_token():
            self._notify("[b]Spotify is not signed in yet.[/b] Restart SPT-TUI to run the sign-in.",
                         warn=True, seconds=8.0)
            return
        # Reload the view the user was looking at, where it exists for the new
        # provider. Item views (a playlist, album...) belong to the old one.
        if kind == "search" and len(rv) > 1 and rv[1]:
            self._dispatch_search(rv[1])
        elif kind in self._RELOADABLE:
            opener, cap = self._RELOADABLE[kind]
            if self._cap(cap):
                getattr(self, opener)()
