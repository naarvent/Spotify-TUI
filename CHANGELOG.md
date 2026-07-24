# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `Add to playlist` (`Ctrl+Shift+P`) now works on every row, not just plain
  tracks. A track or episode row adds that one item; a container row selected
  from a listing expands to every track/episode it holds and adds them all — an
  album/single adds its tracks, a playlist adds its tracks, a podcast adds its
  episodes, and an artist adds its whole discography (all albums and singles,
  de-duplicated). Adding more than 25 tracks asks for confirmation first. Covered
  by `tests/test_add_to_playlist.py`.
- `Tab` / `Shift+Tab` cycle focus across the top-level stops — Search, Help,
  Library, Playlists and the open content view — in visual order, without diving
  into a section's content (the content stop is skipped when only the welcome
  screen is up). This overrides Textual's default Tab-moves-focus-anywhere
  behaviour, while form inputs keep their field-to-field Tab and the search grid
  keeps cycling its four panels. Covered by `tests/test_tab_focus.py`.

### Changed

- Removed the `Ctrl+P` alias for `Add to playlist`. Textual binds `Ctrl+P` to its
  command palette as a priority binding (the `^p palette` footer entry), which
  shadowed the alias so it never fired. The binding is `Ctrl+Shift+P` only.

### Fixed

- A track-removal confirmation is now tied to the playlist view where it was
  requested. Opening a search or another view clears the pending confirmation,
  and a final view-token check prevents a stale second `Ctrl+D` from removing a
  track after navigation. Covered by `tests/test_ui_affordances.py`.
- Opening a cached playlist or library view now shows a brief "Loading…" beat
  before the cached rows appear. A cache hit painted the rows in the same
  UI-thread frame that wrote the loading line, so Textual never rendered that
  line and the open looked like nothing had happened. Both cache-hit paths now
  defer the cached paint by 0.25 s (`_CACHE_LOADING_MIN_S`), guarded so a fast
  background refresh or leaving the view never double-mounts or paints a stale
  table. Cold loads are unchanged — network latency already showed the line.

## [v0.2.2] - 2026-07-24

### Added

- An experimental built-in local player backed by
  [librespot](https://github.com/librespot-org/librespot): Spotify-TUI can now
  play audio itself, without any other Spotify client open (Premium still
  required). One-time browser authorization from Devices, then it runs
  headless. Autostart only activates when nothing is already playing
  elsewhere, so it never interrupts an active session. Covered by
  `tests/test_local_player.py`.
- A one-click Windows installer (`SPT-TUI-Setup-<version>.exe`, published to
  GitHub Releases): no Python required, bundles `librespot`, adds `spt` to
  `PATH` (opt-out) and shows the MIT license during setup.
- Documented the Linux/macOS install path via `pipx` (no bundled installer
  off Windows).

### Fixed

- Devices: pressing `Enter` on a row now reliably selects it — `RowKey`
  resolution was wrong, so selection silently failed.
- Local player start is now serialized so a race cannot orphan a second
  `librespot` process, and startup failures surface on the status line
  instead of failing silently.

## [v0.2.1] - 2026-07-20

### Added

- A status line for transient feedback, and `Ctrl+B` to hide the left sidebar
  (its 38 columns go to the content panel; the collapsed-state CSS already
  existed but nothing ever set it).
- `Ctrl+P` as an alias for `Ctrl+Shift+P` (add to playlist): many terminals
  cannot tell the two chords apart, so the original binding may never arrive.
- A table whose columns were dropped for lack of width now names them in its
  title, e.g. `My List   (hidden: Added)`, instead of them silently vanishing.
- `tests/run_all.py` runs the suites in parallel: the full run went from over
  ten minutes to about 50 seconds. Supports `-j` (workers), `-k` (name filter),
  `-t` (per-suite timeout) and `-v`, and exits non-zero on failure.
- `SPT_TUI_CACHE_DIR` overrides the cache/config/log directory. The test runner
  gives each suite its own temp copy, which both isolates the suites from each
  other and stops them reading and writing the user's real token, playlist cache
  and log — which they did before, and which was most of the slowness (every
  suite was appending to and rotating the same multi-megabyte log file).
- `tests/check_live_api.py`, a read-only script that verifies against the real
  API the assumptions the offline fakes can only assert about themselves: that
  the restricted `fields=` mask still returns `total` and every rendered field,
  that paging advances, and that `check_saved_tracks` stays aligned with the ids
  it was given. It needs credentials and a network, so it is not part of
  `run_all.py`.
- Playlist contents are cached for the session (the last 8 playlists). Reopening
  a playlist paints its tracks immediately and a background refresh replaces
  them, instead of downloading the whole thing again.

- Configurable maximum size for the lyrics cache, editable from the Settings
  screen as a new field.
  - Accepts human-readable sizes with an explicit, unambiguous unit — `MB` or
    `GB` (for example `200 MB`, `500 MB`, `1024 MB`, `1 GB`, `2 GB`, `4 GB`).
    Units are binary (1 MB = 1024 × 1024 bytes) to match the existing byte-based
    cap; parsing is case-insensitive and tolerant of surrounding whitespace.
  - Robust input validation: a bare number (no unit), an unsupported unit, a
    non-positive value or any malformed input is rejected with a clear error
    message, and a value outside the accepted range (1 MB – 8 GB) reports the
    allowed bounds.
  - The value is stored internally as a byte count and integrated with the
    existing local JSON configuration, so it persists across restarts and is
    re-applied to the live cache cap on startup. A configured cap immediately
    bounds the on-disk cache the next time it is trimmed.
  - Defaults to 2 MB when unset, preserving the previous built-in behaviour.
- New test suite `tests/test_lyrics_cache_size.py` covering MB/GB parsing,
  formatting round-trips, minimum/maximum valid values, invalid and
  out-of-range formats, persistence and reload, fallback to the default when no
  value is configured, the configured cap actually bounding the trimmed cache,
  and an end-to-end walk of the Settings wizard.

### Changed

- Table columns now use the whole width of the panel. Title, Artist and Album
  used to stop at a fixed cap (42 / 28 / 26 characters) and the rest of the
  panel was left empty, which showed as a gap on the right of every table on a
  wide terminal. The flexible columns now share all the free space in proportion
  to their weight, so a table always reaches the right edge.
- On a narrow terminal the low-priority columns are dropped instead of every
  column being squeezed to an unreadable 6 characters. The drop order is Source,
  then Added, then Album, then Artist; the heart, Title and Duration columns
  always stay. A dropped column comes back when the terminal is widened again,
  preserving the cursor row and the scroll position.
- Column geometry moved to a single catalogue keyed by field name, shared by the
  tracks, search, queue and devices tables, replacing the per-table maps that
  addressed columns by position. The heart is 3 wide, Duration 9 and Added 12 in
  every table that shows them.
- The queue table builds its cells from its surviving field list rather than a
  fixed cell order, so a dropped column stays consistent with the header.

- Renamed the "Seek Settings" screen to "Settings" everywhere it is visible to
  the user (the `<` key binding label, the settings screen header and the
  keyboard-shortcut documentation in the README). The internal action and view
  route were renamed to match; the persisted `seek_*` configuration keys were
  kept unchanged for backward compatibility.

### Fixed

- User feedback was invisible. Messages went out as `right_panel.update(...)`,
  but whenever a table is on screen it stays mounted as the panel's child and
  covers the panel's own content — which is exactly when most of these fire. So
  "No row selected", "Track removed from playlist", "Could not update ..." and
  61 others were written and never shown: pressing a key with nothing selected
  looked like the key was broken. They now go to a status line docked on an
  overlay layer, above the table, which clears itself after a few seconds.
  Genuine content and loading states still use the panel.
- Removing a track from a playlist now asks for confirmation (`Ctrl+D` again to
  confirm, `Esc` to cancel). Deleting a whole playlist already made you type its
  name, so the smaller destructive action had the weaker safety net.
- Adding or removing a track now drops that playlist's cached tracks. The remove
  path reopens the playlist right after, so without this the deleted track was
  painted straight back from the cache until the refresh landed.
- The playlist cache is guarded by a lock: two playlists finishing at once could
  race in the eviction loop, whose read-modify-write is not atomic under the GIL.
- Leaving a playlist (or opening another one) no longer keeps downloading it. The
  page loop had no staleness check, so a big playlist kept fetching every
  remaining page and then ran the whole liked-songs lookup for a view nobody was
  looking at; only the painting was discarded.
- Opening a playlist while another was still loading could kill the new load:
  the previous table's removal is asynchronous, so mounting the new one hit a
  duplicate-id error and the second playlist stayed stuck on its first page.
- The liked-songs lookup batched 50 ids per call, but the endpoint spotipy uses
  (`me/library/contains`) rejects anything over 40 with `400 Too many uris
  requested` — verified against the live API. So *every* batch failed and fell
  through to the per-id retry: 51 requests instead of 1, about 612 for a
  596-track playlist, and the hearts took ~73 seconds to fill (now ~3). Each
  failure also logged a full traceback with the request URL, which is what grew
  the log file to megabytes. The batch size is now 40, shared by the save and
  remove paths (same endpoint, same limit, which would have failed on saving 41+
  tracks at once). The per-id fallback stays for a genuinely bad id, and a
  failed batch now logs one short line instead of a traceback.
- In-place heart updates never reached the screen. `_set_heart_icon` passed the
  column *index* to `DataTable.update_cell`, which takes a column *key*; the
  call and its fallback both raised and both were swallowed by a bare `except`,
  so the function silently did nothing. It now addresses the cell by coordinate.
  Two paths were affected: a loading playlist's per-batch hearts (they only
  appeared at the end, in the final repaint) and the podcast view, which renders
  its rows unliked and relies entirely on revalidation to fill them — so saved
  episodes showed no heart at all. The `f` toggle was *not* affected: it is
  followed by a full repaint that painted the right thing anyway.
- Hearts now appear batch by batch instead of all at once at the end. The API
  takes 50 ids per call, so a long playlist meant many sequential calls with no
  visible progress until the last one returned.
- The four panels of the combined-search dashboard did not fit their quadrant:
  they sized their columns from the panel width minus a flat 4, which reserved a
  border they do not have, did not cover the per-column cell padding of the
  two-column Songs panel (which overflowed by 2) and left no room for the
  vertical scrollbar. They now go through the same fitter as the other tables
  and re-fit themselves on resize.
- Large playlists appeared to stop loading partway through: only the first page
  of 100 tracks was painted, and the remaining pages stayed invisible until the
  liked-songs lookup had finished for the entire playlist. Pages are now
  appended to the table as soon as each one arrives, so the full track list is
  on screen while the (slower) hearts are still resolving. Appending replaces
  the previous full repaint per page, which would have re-added every earlier
  row on every page.
- New test suite `tests/test_playlist_progressive.py` covering the per-page
  paint, the guarantee that the likes lookup never gates the rows, single-page
  playlists, and cursor preservation while later pages paint.
