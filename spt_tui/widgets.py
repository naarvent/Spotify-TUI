"""Custom Textual widgets."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static


class ContentPanel(Static):
    """The right-hand content panel.

    It reports its own resize so the visible table can re-fit its columns to the
    real panel width. App.on_resize is too early for that: while it runs, the
    panel's content_size still holds its pre-resize value, so a fit done there
    sizes the columns to the old width.
    """

    def on_resize(self, event) -> None:
        try:
            self.app._recompute_all_table_widths()
        except Exception:
            pass


class SearchPanel(DataTable):
    """One quadrant of the combined-search 2x2 results grid (Songs / Artists /
    Albums / Playlists).

    Navigation is two-level, and lives here so navigation.py's global on_key
    stays untouched:

    * "select" mode (the initial state): you pick WHICH panel. Arrow keys move
      the selection across the 2x2 (Left/Right horizontally, Up/Down vertically,
      Tab/Shift+Tab to cycle); no row is highlighted. Enter dives into the
      selected panel's content. Left from a left-edge panel falls through to the
      app's existing 'back to the main menu' handler.
    * "content" mode: you're inside a panel's rows. Up/Down move the row cursor
      (DataTable's own bindings); Left OR Right step back out to panel selection;
      Enter / f / Escape bubble to the app (play/open, favourite, menu)."""

    def on_resize(self, event) -> None:
        # Same reason as ContentPanel: the panel's real width is only readable
        # once it has been laid out, so it re-fits its own columns here rather
        # than from the app's resize handler.
        try:
            self.app._refit_grid_panel(self)
        except Exception:
            pass

    _SELECT_NAV = {
        "left": "left", "right": "right", "up": "up", "down": "down",
        "tab": "next", "shift+tab": "prev", "backtab": "prev",
    }

    def on_key(self, event) -> None:
        key = getattr(event, "key", "")
        app = self.app
        panel_key = (getattr(self, "id", "") or "").replace("_table", "")
        mode = getattr(app, "_grid_mode", "select")

        if mode == "content":
            # Left OR Right steps back out to panel selection; everything else
            # (Up/Down rows, Enter open, f, Escape) bubbles as usual.
            if key in ("left", "right"):
                fn = getattr(app, "_grid_exit_to_select", None)
                if fn is not None:
                    try: fn(panel_key)
                    except Exception: pass
                try: event.stop(); event.prevent_default()
                except Exception: pass
            return

        # select mode
        if key == "enter":
            fn = getattr(app, "_grid_enter_content", None)
            if fn is not None:
                try: fn(panel_key)
                except Exception: pass
            try: event.stop(); event.prevent_default()
            except Exception: pass
            return

        direction = self._SELECT_NAV.get(key)
        if direction is None:
            return
        moved = False
        nav = getattr(app, "_search_grid_key", None)
        if nav is not None:
            try:
                moved = bool(nav(panel_key, direction))
            except Exception:
                moved = False
        # Consume the key, EXCEPT an unhandled Left (from a left-edge panel):
        # let that fall through to the app's back-to-the-menu handler.
        if moved or key != "left":
            try:
                event.stop(); event.prevent_default()
            except Exception:
                pass


class HelpScroll(VerticalScroll):
    """Focusable scroll container for the Help view. Arrow / page / home-end keys
    scroll it; because these bindings live on the focused widget they take
    precedence over the app's global cursor bindings while Help is open. Escape
    is deliberately left unbound so it bubbles to the app and closes Help."""

    BINDINGS = [
        ("up", "scroll_up", "Up"),
        ("down", "scroll_down", "Down"),
        ("pageup", "page_up", "Page Up"),
        ("pagedown", "page_down", "Page Down"),
        ("home", "scroll_home", "Home"),
        ("end", "scroll_end", "End"),
    ]
