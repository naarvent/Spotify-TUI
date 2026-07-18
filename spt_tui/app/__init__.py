"""The ``SptPy`` Textual application, assembled from topical mixins."""

from __future__ import annotations

from textual.app import App

from .core import CoreMixin
from .search import SearchMixin
from .navigation import NavigationMixin
from .tables import TablesMixin
from .library import LibraryMixin
from .lyrics_view import LyricsMixin
from .playback import PlaybackMixin
from .queue_devices import QueueDevicesMixin

__all__ = ["SptPy"]

class SptPy(
    SearchMixin,
    NavigationMixin,
    TablesMixin,
    LibraryMixin,
    LyricsMixin,
    PlaybackMixin,
    QueueDevicesMixin,
    CoreMixin,
    App,
):
    TITLE = "spt - Spotify TUI (Python)"
    CSS = (
        "Screen { layout: vertical; background: #0b1e24; color: #a9d5d9; }\n"
        "#grid { layout: grid; grid-size: 2 3; grid-gutter: 0 0; "
        "grid-columns: 38 1fr; grid-rows: auto 1fr auto; height: 1fr; margin: 0; padding: 0; }\n"
        "#top_bar { column-span: 2; layout: horizontal; margin: 0; padding: 0; align-vertical: middle; }\n"
        "#search_wrap { layout: vertical; padding: 0 1; width: 1fr; margin: 0; height: 3; }\n"
        "#search_wrap.-search-pulse { border: round #6ef7d6; }\n"
        "#search_title { margin: 0; padding: 0; }\n"
        "#search_input { height: 1; }\n"
        "#help_wrap { padding: 0 1; min-width: 24; max-width: 24; height: 3; align-horizontal: center; align-vertical: middle; margin-left: 1; }\n"
        "#help_box { margin: 0; }\n"
        "#left_col { layout: vertical; height: 1fr; margin: 0; padding: 0; }\n"
        "#section_lib { height: auto; margin: 0 0 0 0; }\n"
        "#section_pl { height: 1fr; margin: 0; padding-bottom: 0; }\n"
        ".section { border: round #1a3a40; padding: 0 0 0 0; }\n"
        ".section.-active { border: round #27e1c1; }\n"
        ".section > .title { padding: 0 1; color: #93e1e1; text-style: bold; }\n"
        "#right { border: round #27e1c1; padding: 1 2; overflow: auto; background: #0d252c; min-width: 60; margin: 0; height: 1fr; content-align: center middle; text-align: center; }\n"
        "#right.lyrics-mode { content-align: center middle; }\n"
        "#lyrics_box { width: 1fr; height: 1fr; text-align: center; padding: 1 2; }\n"
        "#help_scroll { width: 1fr; height: 1fr; }\n"
        "#help_text { width: 1fr; text-align: left; }\n"
        "ListView { height: auto; }\n"
        "#pl_list { height: 1fr; overflow: auto; }\n"
        "#lib_list { height: auto; }\n"
        "ListItem { padding: 0 1; }\n"
        "ListItem.-highlight { text-style: bold; color: #27e1c1; background: #0e2d34; }\n"
        "DataTable { height: 1fr; border: round #1a3a40; overflow: auto; "
        "border-title-color: #93e1e1; border-title-align: left; }\n"
        "DataTable:focus { border: round #27e1c1; }\n"
        # Combined-search 2x2 dashboard (Songs/Artists over Albums/Playlists).
        "#search_grid { layout: grid; grid-size: 2 2; grid-gutter: 0 1; width: 1fr; height: 1fr; }\n"
        "#search_grid.-stacked { grid-size: 1 4; grid-gutter: 0 0; }\n"
        ".search-panel { border: round #1a3a40; height: 1fr; padding: 0; "
        "border-title-color: #93e1e1; border-title-align: left; }\n"
        ".search-panel:focus-within { border: round #27e1c1; }\n"
        ".search-panel > DataTable { height: 1fr; border: none; background: transparent; overflow-x: hidden; }\n"
        "#now_wrap { column-span: 2; layout: vertical; border: round #1a3a40; margin: 0; padding: 0 1; height: 5; }\n"
        "#now_wrap.-playing { border: round #27e1c1; }\n"
        "#np_title { height: 1; padding: 0; margin: 0; text-align: left; }\n"
        "#now_row_bar { layout: grid; grid-columns: 1fr; height: 1; margin: 0; padding: 0; }\n"
        "#np_bar_full { width: 1fr; text-align: left; padding: 0; margin: 0; overflow: hidden; text-wrap: nowrap; }\n"
        "#np_times { height: 1; margin: 0; padding: 0; }\n"
        "#grid.left-collapsed { grid-columns: 0fr 1fr; }\n"
        "#left_col.left-collapsed { display: none; }\n"
        "#grid.left-collapsed #right { column-span: 2; }\n"
    )

    LVL_SECTIONS = 0
    LVL_SECTION_CONTENT = 1
    LVL_VIEW = 2
    BINDINGS = [
        ("escape", "escape_to_menu", "Menu"),
        ("ctrl+q", "quit", "Quit"),
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("/", "focus_search", "Search"),
        ("enter", "open", "Open/Play"),
        ("space", "play_pause", "Play/Pause"),
        ("n", "next", "Next"),
        ("p", "prev_logic", "Prev/Restart"),
        ("r", "toggle_repeat", "Repeat"),
        ("c", "queue_track", "Queue"),
        ("ctrl+s", "toggle_shuffle", "Shuffle"),
        ("-", "volume_down", "Vol -"),
        ("+", "volume_up", "Vol +"),
        ("m", "toggle_mute", "Mute"),
        ("ctrl+left", "seek_back", "- s"),
        ("ctrl+right", "seek_fwd", "+ s"),
        ("<", "prompt_seek_settings", "Seek Settings"),
        ("d", "manage_devices", "Devices"),
        ("?", "help", "Help"),
        ("f1", "help", "Help"),
        ("l", "toggle_lyrics", "Lyrics"),
        ("ctrl+r", "refresh", "Refresh"),
        ("ctrl+c", "open_queue", "Queue"),
        ("ctrl+t", "prompt_import_playlists", "Import Playlist"),
        ("f", "toggle_favorite", "Favorite"),
        ("ctrl+l", "toggle_multi_add", "Multi-Add"),
        ("ctrl+shift+p", "add_to_playlist", "Add to Playlist"),
        ("ctrl+d", "delete", "Delete"),

    ]
