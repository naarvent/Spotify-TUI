"""The ``SptPy`` Textual application, assembled from topical mixins."""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding
from ..theming import build_theme

from .core import CoreMixin
from .search import SearchMixin
from .navigation import NavigationMixin
from .tables import TablesMixin
from .library import LibraryMixin
from .lyrics_view import LyricsMixin
from .playback import PlaybackMixin
from .queue_devices import QueueDevicesMixin
from .provider_switch import ProviderSwitchMixin
from .spotify_import_view import SpotifyImportMixin
from .radio import RadioMixin
from .offline import OfflineMixin
from .visualizer_view import VisualizerMixin

__all__ = ["SptPy", "SPT_THEME"]

# The app's own teal palette as a Textual theme, so widgets and the CSS below
# can use theme variables ($primary, $text-muted, $success, $warning...) and
# still match the hand-picked colours used everywhere else.
SPT_THEME = build_theme("spt-teal")

# "Transparent background" (Settings > Appearance): with `ansi_color` on, `ansi_default` is the
# terminal's own background (opacity / blur / wallpaper show through) and only the frames, the text
# and the selection stay painted. All the rules hang off the `-transparent` class of the App.
_T = ".-transparent "
TRANSPARENT_CSS = (
    _T + "Screen, " + _T + "#grid, " + _T + "#top_bar, " + _T + "#left_col, " + _T + ".section, "
    + _T + "#right, " + _T + "#search_wrap, " + _T + "#help_wrap, " + _T + "#now_wrap, "
    + _T + "#search_grid, " + _T + ".search-panel, " + _T + "Static, " + _T + "Input, "
    + _T + "ListView, " + _T + "ListItem, " + _T + "VerticalScroll, " + _T + "Horizontal, "
    + _T + "DataTable, " + _T + "OptionList, " + _T + ".settings-section, " + _T + ".settings-keys, "
    + _T + "Header, " + _T + "Footer, " + _T + "HeaderIcon, " + _T + "HeaderTitle, " + _T + "HeaderClock, "
    + _T + "#provider_picker, " + _T + "#provider_options, " + _T + "#provider_picker_hint, "
    + _T + "ProviderPickerScreen { background: ansi_default; }\n"
    + _T + "#provider_options > .option-list--option-highlighted { background: $spt-sel2; }\n"
    + _T + "Input:focus { background: ansi_default; background-tint: transparent; }\n"
    + _T + "Input > .input--placeholder { color: $spt-muted; }\n"
    # Selection stays visible, but as a thin tint instead of a block of colour.
    + _T + "ListItem.-highlight { background: $spt-sel; }\n"
    + _T + "DataTable > .datatable--header, " + _T + "DataTable > .datatable--header-hover, "
    + _T + "DataTable > .datatable--odd-row, " + _T + "DataTable > .datatable--even-row, "
    + _T + "DataTable > .datatable--fixed { background: ansi_default; }\n"
    + _T + "DataTable > .datatable--header { color: $secondary; text-style: bold; }\n"
    + _T + "DataTable > .datatable--cursor { background: $spt-sel2; color: $spt-text-hi; }\n"
    + _T + "DataTable:focus > .datatable--cursor { background: $spt-sel3; color: #ffffff; }\n"
    + _T + "OptionList > .option-list--option-highlighted { background: $spt-sel2; }\n"
    # The transient status line: coloured text instead of a coloured bar.
    + _T + "#status_line { background: ansi_default; color: $accent; text-style: bold; }\n"
    + _T + "#status_line.-warn { background: ansi_default; color: #ffb454; }\n"
)


class SptPy(
    SearchMixin,
    NavigationMixin,
    TablesMixin,
    LibraryMixin,
    LyricsMixin,
    PlaybackMixin,
    QueueDevicesMixin,
    ProviderSwitchMixin,
    SpotifyImportMixin,
    RadioMixin,
    OfflineMixin,
    VisualizerMixin,
    CoreMixin,
    App,
):
    TITLE = "spt - Spotify TUI (Python)"
    CSS = (
        "Screen { layout: vertical; background: $background; color: $foreground; "
        "layers: base overlay; }\n"
        "#grid { layout: grid; grid-size: 2 3; grid-gutter: 0 0; "
        "grid-columns: 38 1fr; grid-rows: auto 1fr auto; height: 1fr; margin: 0; padding: 0; }\n"
        "#top_bar { column-span: 2; layout: horizontal; margin: 0; padding: 0; align-vertical: middle; }\n"
        "#search_wrap { layout: vertical; padding: 0 1; width: 1fr; margin: 0; height: 3; }\n"
        "#search_wrap.-search-pulse { border: round $accent; }\n"
        # Typing in Search: a heavy accent frame, a bright title and a hint underneath, so it is
        # obvious where the keys go. While a search runs the hint spins (see _start_search_spinner).
        "#search_wrap:focus-within { border: heavy $primary; border-subtitle-color: $secondary; }\n"
        "#search_wrap:focus-within #search_title { color: $accent; text-style: bold; }\n"
        "#search_wrap:focus-within.-search-pulse { border: heavy $accent; }\n"
        "#search_wrap.-searching { border: heavy $accent; }\n"
        "#search_wrap.-done { border: heavy $success; }\n"
        "#search_input > .input--placeholder { color: $spt-muted; }\n"
        # Title bar: no icon (it only toggled a taller header), accent title.
        "HeaderIcon { display: none; }\n"
        "HeaderTitle { color: $primary; text-style: bold; }\n"
        "#search_title { margin: 0; padding: 0; }\n"
        "#search_input { height: 1; }\n"
        "#help_wrap { padding: 0 1; min-width: 12; max-width: 12; height: 3; align-horizontal: center; align-vertical: middle; margin-left: 1; }\n"
        "#help_box { margin: 0; }\n"
        "#left_col { layout: vertical; height: 1fr; margin: 0; padding: 0; }\n"
        "#section_lib { height: auto; margin: 0 0 0 0; }\n"
        "#section_pl { height: 1fr; margin: 0; padding-bottom: 0; }\n"
        ".section { border: round $spt-dim; padding: 0 0 0 0; }\n"
        ".section.-active { border: round $primary; }\n"
        ".section > .title { padding: 0 1; color: $secondary; text-style: bold; }\n"
        "#right { border: round $primary; padding: 1 2; overflow: auto; background: $surface; min-width: 60; margin: 0; height: 1fr; content-align: center middle; text-align: center; }\n"
        # A single full-width content table brings its own bordered+titled frame,
        # so #right drops its border and padding: the table fills edge to edge and
        # there is no #0d252c gap between the two frames. (The 2x2 grid keeps
        # #right's border — it does not set this class.)
        # A single content table borrows #right's frame: #right keeps its border
        # and shows the title, but drops its padding, and the table inside is
        # borderless + transparent. So there is one clean frame (no nested double
        # border, no #0d252c gap) and #right's border clips the DataTable's grey
        # header/zebra layer — exactly how the grid panels do it.
        "#right.table-view { padding: 0; }\n"
        "#right.table-view > DataTable, #right.table-view > DataTable:focus { border: none; }\n"
        "#right.table-view > ListView { height: 1fr; border: none; padding: 0 1; }\n"
        "#right.table-view > .view-note { padding: 1 2; text-align: left; }\n"
        "#right.table-view > #help_scroll { padding: 0 1; }\n"
        # The "/" filter box sits on top of the table it filters (tables.py).
        "#right.table-view > .view-filter { height: 1; border: none; padding: 0 1; "
        "background: $surface; }\n"
        "#right.table-view > .view-filter:focus { background: $spt-sel; }\n"
        "#right.lyrics-mode { content-align: center middle; }\n"
        ".visualizer_box { width: 1fr; height: 1fr; padding: 1 1 0 1; overflow: hidden; }\n"
        ".lyrics_box { width: 1fr; height: 1fr; text-align: center; padding: 1 2; overflow: hidden; }\n"
        "#help_scroll { width: 1fr; height: 1fr; }\n"
        "#help_text { width: 1fr; text-align: left; }\n"
        "ListView { height: auto; }\n"
        "#pl_list { height: 1fr; overflow: auto; }\n"
        "#lib_list { height: auto; }\n"
        "ListItem { padding: 0 1; }\n"
        "ListItem.-highlight { text-style: bold; color: $primary; background: $spt-sel; }\n"
        "DataTable { height: 1fr; border: round $spt-dim; overflow: auto; "
        "border-title-color: $secondary; border-title-align: left; }\n"
        "DataTable:focus { border: round $primary; }\n"
        # Combined-search 2x2 dashboard (Songs/Artists over Albums/Playlists).
        "#search_grid { layout: grid; grid-size: 2 2; grid-gutter: 0 1; width: 1fr; height: 1fr; }\n"
        "#search_grid.-stacked { grid-size: 1 4; grid-gutter: 0 0; }\n"
        ".search-panel { border: round $spt-dim; height: 1fr; padding: 0; "
        "border-title-color: $secondary; border-title-align: left; }\n"
        ".search-panel:focus-within { border: round $primary; }\n"
        ".search-panel > DataTable, .search-panel > DataTable:focus { height: 1fr; border: none; background: transparent; overflow-x: hidden; }\n"
        # Transient feedback. It lives on an overlay layer docked to the bottom
        # so it shows *over* a content table: written into #right it would sit
        # behind the table (a mounted child covers the panel's own content) and
        # never be seen. Hidden until there is something to say.
        "#status_line { layer: overlay; dock: bottom; width: 100%; height: 1; "
        "padding: 0 1; background: #123; color: $spt-text-hi; display: none; }\n"
        "#status_line.-shown { display: block; }\n"
        "#status_line.-warn { background: #4a2a12; color: #ffd9a8; }\n"
        "#now_wrap { column-span: 2; layout: vertical; border: round $spt-dim; margin: 0; padding: 0 1; height: 5; }\n"
        "#now_wrap.-playing { border: round $primary; }\n"
        "#np_title { height: 1; padding: 0; margin: 0; text-align: left; }\n"
        "#now_row_bar { layout: grid; grid-columns: 1fr; height: 1; margin: 0; padding: 0; }\n"
        "#np_bar_full { width: 1fr; text-align: left; padding: 0; margin: 0; overflow: hidden; text-wrap: nowrap; }\n"
        "#np_times { height: 1; margin: 0; padding: 0; }\n"
        "#grid.left-collapsed { grid-columns: 0fr 1fr; }\n"
        "#left_col.left-collapsed { display: none; }\n"
        "#grid.left-collapsed #right { column-span: 2; }\n"
        # Settings form (theme variables; see SPT_THEME).
        "#right .settings-form { width: 1fr; max-width: 118; height: 1fr; text-align: left; "
        "content-align: left top; padding: 0 1; scrollbar-gutter: stable; }\n"
        ".settings-title { text-style: bold; color: $primary; width: 1fr; }\n"
        ".settings-subtitle { color: $text-muted; width: 1fr; margin-bottom: 1; }\n"
        ".settings-section { width: 1fr; margin: 1 0 1 0; padding: 0 0 0 1; text-style: bold; "
        "color: $secondary; background: $panel; border-left: thick $primary; }\n"
        ".settings-row { height: auto; width: 1fr; margin-top: 0; }\n"
        ".settings-label { width: 22; padding: 1 1 0 1; text-style: bold; color: $foreground; }\n"
        ".settings-row > Input { width: 1fr; max-width: 60; }\n"
        ".settings-help { width: 1fr; color: $text-muted; padding-left: 23; margin-bottom: 1; }\n"
        ".settings-form.-narrow .settings-row { layout: vertical; }\n"
        ".settings-form.-narrow .settings-label { width: 1fr; padding: 0 1; }\n"
        ".settings-form.-narrow .settings-help { padding-left: 1; }\n"
        ".settings-help.settings-help-wide { padding-left: 1; }\n"
        ".settings-warn { width: 1fr; color: $warning; padding-left: 1; }\n"
        ".settings-keys { dock: bottom; width: 1fr; color: $text-muted; background: $panel; padding: 0 1; }\n"
        ".settings-provider-select { height: auto; max-height: 14; margin: 1 0 0 1; border: round $panel-lighten-2; }\n"
        ".settings-provider-select:focus { border: round $primary; }\n"
        ".import-selection { height: auto; max-height: 24; margin: 0 0 1 1; border: round $panel-lighten-2; }\n"
        ".import-selection:focus { border: round $primary; }\n"
        ".import-start { margin: 0 0 1 1; }\n"
        # Highlighted provider: a soft panel instead of the solid primary bar,
        # so the ● mark, the ACTIVE badge and the amber status stay readable.
        ".settings-provider-select > .option-list--option-highlighted { background: $primary 22%; }\n"
        + TRANSPARENT_CSS
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
        ("s", "sort_playlist", "Sort"),
        ("S", "reverse_playlist_sort", "Sort ↑↓"),
        ("P", "choose_provider", "Provider"),
        ("R", "start_radio", "Radio"),
        ("v", "toggle_visualizer", "Visualizer"),
        # Visualizer only (no-op elsewhere): `m` stays Mute, so mirror is Shift+M.
        Binding("M", "visualizer_mirror", "Mirror", show=False),
        ("o", "toggle_offline", "Offline"),
        ("O", "download_all", "Download all"),
        ("-", "volume_down", "Vol -"),
        ("+", "volume_up", "Vol +"),
        ("m", "toggle_mute", "Mute"),
        ("ctrl+left", "seek_back", "- s"),
        ("ctrl+right", "seek_fwd", "+ s"),
        ("<", "prompt_settings", "Settings"),
        ("d", "manage_devices", "Devices"),
        ("?", "help", "Help"),
        ("f1", "help", "Help"),
        ("l", "toggle_lyrics", "Lyrics"),
        # Lyrics view only (no-ops elsewhere): karaoke mode and live offset.
        Binding("k", "lyrics_mode", "Karaoke", show=False),
        Binding("right_square_bracket", "lyrics_offset_up", "Lyrics +0.25s", show=False),
        Binding("left_square_bracket", "lyrics_offset_down", "Lyrics -0.25s", show=False),
        Binding("right_curly_bracket", "lyrics_offset_up_big", "Lyrics +1s", show=False),
        Binding("left_curly_bracket", "lyrics_offset_down_big", "Lyrics -1s", show=False),
        ("ctrl+r", "refresh", "Refresh"),
        ("ctrl+c", "open_queue", "Queue"),
        ("ctrl+t", "prompt_import_playlists", "Import Playlist"),
        ("f", "toggle_favorite", "Favorite"),
        ("ctrl+l", "toggle_multi_add", "Multi-Add"),
        ("ctrl+shift+p", "add_to_playlist", "Add to Playlist"),
        ("ctrl+d", "delete", "Delete"),
        ("ctrl+e", "rename_playlist", "Rename"),
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
    ]
