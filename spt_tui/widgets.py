"""Custom Textual widgets."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import DataTable


class SearchPanel(DataTable):
    """One quadrant of the combined-search 2x2 results grid (Songs / Artists /
    Albums / Playlists).

    Local key handling moves focus BETWEEN panels so navigation.py's global
    on_key stays untouched:
      * Left / Right  -> horizontal panel;
      * Ctrl+Up / Ctrl+Down -> vertical panel;
      * Tab / Shift+Tab -> cycle through the non-empty panels.
    Up / Down keep moving the row cursor inside the panel (DataTable's own
    bindings); Enter, ``f`` and Escape bubble to the app as usual. A key is only
    consumed when it actually moved focus, so a move that has no target (Left
    from a left-edge panel, Right from a right-edge panel) falls through to the
    app's existing handler — e.g. Left from Songs/Albums reaches the standard
    'back to the main menu' behaviour, exactly like the single-table view."""

    _NAV = {
        "left": "left", "right": "right",
        "ctrl+up": "up", "ctrl+down": "down",
        "tab": "next", "shift+tab": "prev", "backtab": "prev",
    }

    def on_key(self, event) -> None:
        direction = self._NAV.get(getattr(event, "key", ""))
        if direction is None:
            return
        panel_key = (getattr(self, "id", "") or "").replace("_table", "")
        nav = getattr(self.app, "_search_grid_key", None)
        handled = False
        if nav is not None:
            try:
                handled = bool(nav(panel_key, direction))
            except Exception:
                handled = False
        if handled:
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
