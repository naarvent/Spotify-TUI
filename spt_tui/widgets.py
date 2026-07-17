"""Custom Textual widgets."""

from __future__ import annotations

from textual.containers import VerticalScroll


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
