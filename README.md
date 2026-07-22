# Spotify-TUI

[![Version](https://img.shields.io/badge/version-0.2.1-blue.svg)](https://github.com/naarvent/Spotify-TUI/releases) [![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) [![Textual](https://img.shields.io/badge/Textual-8.x-5a4fcf.svg)](https://github.com/Textualize/textual)

A fast keyboard-driven Spotify client for the terminal, built with [Textual](https://github.com/Textualize/textual).

Search Spotify, browse your library and playlists, control playback, follow synced lyrics, manage your queue and devices, all from the keyboard.

**Status:** `v0.2.1` (early release). The application includes responsive search and tables, reliable cached library and playlist loading, synchronized lyrics, visible action feedback and an extensive offline regression suite. APIs and behaviour may still change before `1.0`.

<p align="center">
  <img alt="Spotify-TUI interface" src="https://github.com/user-attachments/assets/35ab7ab0-43a7-43f3-b8b6-056d45521255" />
</p>

---

## Contents

- [Features](#features)
- [What's New in v0.2.1](#whats-new-in-v021)
- [Previous Release: v0.2.0](#previous-release-v020)
- [Requirements](#requirements)
- [Installation](#installation)
- [Spotify Setup](#spotify-setup)
- [Running](#running)
- [Search](#search)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Tests](#tests)
- [Architecture](#architecture)
- [Caching](#caching)
- [Local player (experimental)](#local-player-experimental)
- [Limitations](#limitations)
- [Security](#security)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Multi-panel search dashboard** with dedicated Songs, Artists, Albums and Playlists sections.
- **Type-specific search** for tracks, artists, albums, playlists, podcasts and episodes.
- **Responsive search layout** that adapts to large, medium and narrow terminals.
- **Playlists** with a persistent disk cache, instant startup rendering and safe background refresh.
- **Library** with liked songs, recently played tracks, saved albums, followed artists, saved podcasts and saved episodes.
- **Favorites** for liking tracks and following or saving albums, artists, podcasts and episodes directly from the interface.
- **Unified saved-state indicators** using consistent heart symbols throughout the application.
- **Playback control** with play, pause, next, previous, seek, volume, mute, repeat and shuffle.
- **Synced lyrics** with current-line highlighting, background loading and persistent caching.
- **Bounded lyrics cache** with automatic pruning by entry count and a configurable total-size cap (set in Settings using human-readable sizes such as `200 MB` or `1 GB`).
- **Queue** for viewing the current queue and adding selected tracks.
- **Add to playlist** for tracks, episodes and complete albums, artists, playlists or podcasts, with confirmation for large additions.
- **Devices** for listing Spotify devices and transferring playback.
- **Visible action feedback** through a status line above the active content.
- **Collapsible sidebar** to give more room to the content area when needed.
- **Responsive tables** with automatic column sizing and no manual mouse-driven column resizing.
- **Context-aware refresh** that updates the active view instead of repeating stale searches.
- **Keyboard-driven navigation** throughout the interface.
- **Built-in help view** with keyboard scrolling.
- **Responsive welcome screen** with layouts adapted to the available terminal size.
- **Offline regression suites** covering navigation, search, library loading, playback, lyrics, caching and responsive layouts.

## What's New in v0.2.1

v0.2.1 focuses on making everyday playlist work faster, safer and clearer.

### Playlist Workflow

- Add a selected track or episode to a playlist with `Ctrl+Shift+P`.
- Add every track or episode from a selected album, artist, playlist or podcast; large additions ask for confirmation.
- Playlist pages stream into the table as they load, while liked-state hearts fill in progressively.
- Reopened playlists use an in-session cache for immediate rendering and refresh safely in the background.
- Removing a playlist track requires a second `Ctrl+D`; the confirmation is cancelled when leaving that playlist.

### Interface and Navigation

- A dedicated status line makes feedback visible above tables instead of hiding it behind them.
- `Ctrl+B` hides or restores the sidebar, assigning its width to the active content panel.
- `Tab` and `Shift+Tab` cycle predictably between the principal navigation stops.
- Tables use the available panel width; on narrow terminals, low-priority columns are hidden in a predictable order and identified in the table title.

### Reliability and Testing

- Spotify liked-track checks are batched within the API limit, making long playlists much quicker to load.
- Test runs use isolated cache directories, avoiding access to local tokens, logs and playlist caches.
- `tests/run_all.py` can execute suites in parallel and supports filtering, worker-count and timeout options.
- `tests/check_live_api.py` provides an optional read-only verification against the real Spotify API.

For the full release record, see [CHANGELOG.md](CHANGELOG.md).

## Previous Release: v0.2.0

The v0.2.0 notes are retained below as reference for the previous major application update.

### Multi-panel Search

Searches without a type prefix now use a categorized dashboard:

- Songs
- Artists
- Albums
- Playlists

Each category has its own panel, selection state and navigation behaviour.

The search dashboard uses a two-level navigation model:

1. Select a result category with the arrow keys.
2. Press `Enter` to enter the selected panel.
3. Use `Up` and `Down` to navigate its results.
4. Use `Left` or `Right` to return to panel selection.

Searches with explicit prefixes still use full single-type result tables.

### Search Reliability

- Immediate visual feedback when a search begins.
- Existing results remain visible while a new search is running.
- A visible frame title indicates the active search query.
- Long-running searches display an additional waiting state.
- Older results cannot overwrite a newer search.
- Empty results are distinguished from request failures.
- Partial search failures preserve successful result categories.
- Search workers cannot repaint the interface after leaving Search.

### Library Reliability

- Saved Albums now tolerate unavailable Spotify entries.
- Saved Episodes handle unavailable tombstone objects without displaying fake durations.
- Saved Artists use the same reliable background-loading infrastructure as the other saved-library views.
- Recently Played provides clear loading feedback.
- Errors no longer silently appear as empty libraries.
- Valid cached data remains visible during temporary failures.
- Library responses are protected against stale worker updates.
- Cursor and scroll position are preserved during refreshes where possible.

### Playlists

- Improved disk-cache safety.
- Atomic cache writes.
- Invalid or incomplete responses no longer erase valid playlists.
- Older workers cannot replace newer playlist data.
- Playlist refreshes preserve existing content during temporary API errors.
- Reduced risk of the playlist list disappearing during refresh.

### Saved and Liked State

- Fixed liked-state rendering for tracks opened from albums.
- Track hearts are resolved in the background and matched by Spotify track ID.
- Favorite mutations update the relevant row without resetting the table.
- Saved-state visuals now use hearts consistently.
- Removed the old `S` saved-state column header.
- Removed floppy-disk saved indicators.
- Track, album, artist, podcast and episode semantics remain independent internally.

### Tables and Layout

- Redesigned responsive column-width calculation.
- Removed unnecessary horizontal scrolling in normal terminal sizes.
- Removed manual column resizing with the mouse.
- Added automatic width profiles for different table types.
- Improved width distribution for titles, artists, albums, owners, durations and dates.
- Long text is truncated predictably without hiding compact fields.
- Table resize operations preserve cursor, selection and vertical scroll.
- Removed the Source column from standard content views.
- Queue retains Source where it still provides useful context.
- Improved table borders and content framing.

### Now Playing

- Removed unnecessary spacer rows.
- Search, Help, the main content area and Now Playing now use a tighter layout.
- Now Playing remains visible across supported terminal sizes.

### Lyrics

- Added a hard maximum number of cached lyrics entries.
- Added a maximum cache-file size.
- Older entries are automatically removed when limits are exceeded.
- Lyrics cache files can no longer grow indefinitely.
- Improved metadata updates when the current track changes.
- Previous lyrics no longer remain associated with a newly selected track.

### Refresh Behaviour

`Ctrl+R` is now context-aware:

- Lyrics refreshes the current lyrics.
- Search repeats the active search.
- Playlists refreshes the current playlist.
- Library views refresh the active library section.
- Queue and Devices refresh their current data.
- Welcome refreshes the visible playlist list.
- Text left in the Search input no longer hijacks refresh actions elsewhere.

## Requirements

- **Python 3.9+**. Developed and tested primarily on Python 3.14.
- A **Spotify account**.
- A **Spotify Premium** account for playback control, including play, pause, skip, seek, volume, shuffle and device transfer. This is a Spotify Web API restriction.
- A **Spotify Developer application** for the Client ID, Client Secret and Redirect URI.
- An **active Spotify device**, such as the desktop application, a phone or a compatible speaker, for playback operations.

Browsing, searching and some library operations may work with a free Spotify account, but playback-control endpoints require Premium.

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/naarvent/Spotify-TUI.git
cd Spotify-TUI

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate it on Linux or macOS
source .venv/bin/activate

# Or activate it on Windows PowerShell
.venv\Scripts\Activate.ps1

# 4. Install the project and its dependencies
pip install -e .
```

`pip install -e .` installs Spotify-TUI and its dependencies using `pyproject.toml`.

Alternatively, install only the runtime dependencies:

```bash
pip install -r requirements.txt
```

Installing only `requirements.txt` does not install the `spt-tui` console entry point.

## Spotify Setup

1. Open the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create a Spotify application.
3. Copy its Client ID and Client Secret.
4. Add a Redirect URI in the application settings.
5. Use the same Redirect URI when configuring Spotify-TUI.

A loopback URI such as the following generally works well:

```text
http://127.0.0.1:8888/callback
```

### Environment Variables

Environment variables take precedence over the local configuration file.

Linux and macOS:

```bash
export SPOTIPY_CLIENT_ID="your_client_id"
export SPOTIPY_CLIENT_SECRET="your_client_secret"
export SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"
```

Windows PowerShell:

```powershell
$env:SPOTIPY_CLIENT_ID = "your_client_id"
$env:SPOTIPY_CLIENT_SECRET = "your_client_secret"
$env:SPOTIPY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
```

You may also enter the credentials inside the application on first launch. They are then stored in the local configuration directory described in the [Security](#security) section.

On first launch, Spotify-TUI opens the standard Spotify OAuth authorization flow in the browser. After authorization, the resulting token is cached locally and refreshed automatically when possible.

## Running

Run the package directly:

```bash
python -m spt_tui
```

Or use the console entry point after installing with `pip install -e .`:

```bash
spt-tui
```

## Search

### Combined Search

Enter a normal query without a prefix:

```text
radiohead
```

Results are displayed in the multi-panel search dashboard:

- Songs
- Artists
- Albums
- Playlists

The dashboard can display up to 20 results in each panel.

### Type-specific Search

Use a prefix to request a single result type:

```text
/TRK radiohead
/ART radiohead
/ALB radiohead
/PLY radiohead
/PDC radiohead
/EPS radiohead
```

Type-specific searches use a full-width result table and can display more items than the combined dashboard.

## Keyboard Shortcuts

These shortcuts reflect the application's current bindings.

### Global Navigation

| Key | Action |
| --- | --- |
| `Up` / `Down` | Move through sections, lists or rows |
| `Tab` / `Shift+Tab` | Cycle focus across Search, Help, Library, Playlists and the open content (without entering a section) |
| `Left` / `Right` | Move between the menu and the active view |
| `Enter` | Open, select or play the focused item |
| `/` | Focus the search input |
| `Escape` | Return to the main menu |
| `?` / `F1` | Open or close Help |
| `Ctrl+Q` | Quit |

### Multi-panel Search

The search dashboard has two navigation levels.

#### Panel Selection

| Key | Action |
| --- | --- |
| Arrow keys | Select Songs, Artists, Albums or Playlists |
| `Tab` / `Shift+Tab` | Cycle through available result panels |
| `Enter` | Enter the selected panel |
| `Left` from a left-side panel | Return to the main menu |
| `Escape` | Return to the main menu |

#### Panel Content

| Key | Action |
| --- | --- |
| `Up` / `Down` | Move through results |
| `Enter` | Play or open the selected result |
| `Left` / `Right` | Return to panel selection |
| `f` | Toggle favorite for a supported result |
| `Escape` | Return to the main menu |

### Playback

| Key | Action |
| --- | --- |
| `Space` | Play or pause |
| `n` | Next track |
| `p` | Previous track or restart |
| `r` | Toggle repeat |
| `Ctrl+S` | Toggle shuffle |
| `-` / `+` | Volume down or up |
| `m` | Mute or unmute |
| `Ctrl+Left` / `Ctrl+Right` | Seek backward or forward |
| `<` | Open settings (volume steps, seek jump times, lyrics cache size) |

### Library, Queue and Tools

| Key | Action |
| --- | --- |
| `f` | Like, unlike, follow or save the selected item |
| `l` | Toggle the lyrics view |
| `c` | Add the selected track to the queue |
| `Ctrl+C` | Open Queue |
| `d` | Open Devices |
| `Ctrl+L` | Toggle multi-add mode |
| `Ctrl+O` | Multi-select review: select all items |
| `Ctrl+A` | Multi-select review: add the selected items · Multi-add mode: select or deselect all rows |
| `Ctrl+Shift+P` | Add to a playlist: the selected track/episode, or every track of a selected album, artist, playlist or podcast (large adds ask to confirm) |
| `Ctrl+T` | Import a playlist |
| `Ctrl+D` | Delete or remove the selected item (press again to confirm a track removal, `Esc` to cancel) |
| `Ctrl+R` | Refresh the active view |
| `Ctrl+B` | Hide/show the left sidebar (gives its width to the content) |

### Help View

While Help is open:

| Key | Action |
| --- | --- |
| `Up` / `Down` | Scroll |
| `PageUp` / `PageDown` | Scroll by page |
| `Home` / `End` | Jump to the beginning or end |
| `Escape` | Close Help |

## Tests

The test suites are standalone Python scripts. No external test framework is required.

Each test script exits with status `0` only when all its checks pass.

Run a single suite:

```bash
python tests/test_spotify_client.py
```

Run the complete suite (parallel by default):

```bash
python tests/run_all.py
```

Use `python tests/run_all.py --help` for worker-count, name-filter, timeout and verbose options. The optional `tests/check_live_api.py` performs read-only checks against Spotify's live API and requires configured credentials; it is not included in the offline suite.

Run all suites on Linux or macOS:

```bash
for f in tests/test_*.py; do
    python "$f" || echo "FAILED: $f"
done
```

Run all suites on Windows PowerShell:

```powershell
Get-ChildItem tests/test_*.py | ForEach-Object {
    python $_.FullName
}
```

The regression suites use fake Spotify clients and deterministic Textual test pilots. Network operations are mocked, allowing the primary behaviour to be tested offline.

Some concurrency tests depend on event-loop and thread scheduling and may occasionally require a retry under unusually heavy CPU load.

Current coverage includes:

- Spotify client behaviour
- Search loading and result ordering
- Multi-panel search navigation
- Search limits and partial failures
- Library pagination and cache behaviour
- Playlist refresh protection
- Favorites and saved-state synchronization
- Album-track liked state
- Lyrics lookup and bounded caching
- Queue and device views
- Responsive tables
- Table titles and content frames
- Recently Played loading feedback
- Welcome responsiveness
- Now Playing spacing
- Application teardown
- Worker error handling

## Architecture

Spotify-TUI started as a single approximately 6,700-line Python file containing one large application class.

The project was later refactored, with behaviour preserved, into a package organized by responsibility:

```text
Spotify-TUI/
├── .gitignore
├── LICENSE
├── README.md
├── pyproject.toml
├── requirements.txt
├── spt_tui/
│   ├── __init__.py          Package metadata
│   ├── __main__.py          python -m spt_tui and spt-tui entry point
│   ├── config.py            Paths, logging and credentials
│   ├── constants.py         Welcome content, glyphs and library entries
│   ├── spotify_client.py    Spotify API wrapper and rate limiter
│   ├── widgets.py           Help scrolling and multi-panel search widgets
│   └── app/
│       ├── __init__.py      SptPy composition and Textual CSS
│       ├── core.py          Layout, mounting and top-level event routing
│       ├── library.py       Playlists, saved library and recently played
│       ├── lyrics_view.py   Synced lyrics, rendering and persistent cache
│       ├── navigation.py    Keyboard, focus and section navigation
│       ├── playback.py      Playback controls, favorites and Now Playing
│       ├── queue_devices.py Queue and device management
│       ├── search.py        Search workers, feedback and result dashboard
│       └── tables.py        Table construction, widths and saved-state columns
└── tests/
    ├── test_album_likes.py
    ├── test_devices.py
    ├── test_favorites_p5.py
    ├── test_favorites_p6.py
    ├── test_favorites_p7.py
    ├── test_help_scroll.py
    ├── test_library_load.py
    ├── test_lyrics.py
    ├── test_lyrics_cache.py
    ├── test_lyrics_cleanup.py
    ├── test_narrowed_exceptions.py
    ├── test_now_playing_spacing.py
    ├── test_playback_sync.py
    ├── test_playlist_refresh.py
    ├── test_recent_and_titles.py
    ├── test_refresh_routing.py
    ├── test_responsive_tables.py
    ├── test_saved_hearts.py
    ├── test_search_feedback.py
    ├── test_search_grid.py
    ├── test_search_limits.py
    ├── test_search_loading.py
    ├── test_spotify_client.py
    ├── test_table_scroll.py
    ├── test_teardown.py
    ├── test_tui_flows.py
    ├── test_welcome_responsive.py
    └── test_worker_error_handling.py
```

`SptPy` is composed through multiple inheritance from focused mixins. At runtime, the application is still a single Textual `App` subclass, while its implementation is separated into modules by responsibility.

The 28 standalone regression suites protect application behaviour introduced during and after the refactor.

## Caching

Spotify-TUI uses multiple cache strategies depending on the type of data.

### Playlists

- Stored on disk.
- The eight most recently opened playlist contents are also cached for the current session.
- Painted immediately during startup.
- Refreshed in the background.
- Written atomically.
- Protected against invalid, stale or unexpectedly empty responses.

### Saved Library Views

- Cached in memory for the current session.
- Refreshed in the background.
- Protected by view tokens and worker generations.

### Recently Played

- Treated as volatile data.
- Refreshed directly instead of being persisted as a long-lived cache.
- Shows immediate loading feedback while new data is being retrieved.

### Lyrics

- Stored in a persistent local cache.
- Bounded by entry count.
- Bounded by total serialized size.
- Older entries are removed automatically when limits are exceeded.
- Temporary network failures are not stored as permanent missing results.

## Local player (experimental)

Spotify-TUI can run its own audio player via [librespot](https://github.com/librespot-org/librespot),
so it plays music without any other Spotify client open. Spotify **Premium** is
still required (librespot cannot bypass it).

- **Provide the binary:** put `librespot` on your `PATH`, or set
  `SPT_LIBRESPOT_PATH` to its absolute path. With no binary present, the app
  behaves exactly as before (it controls other Spotify Connect devices).
- **First run:** open Devices (`d`) and select **SPT-TUI Local (start)** to
  authorize once in your browser. After that it starts headless — no browser,
  and never the official app.
- **On launch** it activates automatically **only if nothing is already
  playing** on another device — it never interrupts an active session.

Config keys (in `spt_config.json`) / environment variables:

| Key | Env | Default |
| --- | --- | --- |
| `local_player_enabled` | `SPT_LOCAL_PLAYER` | `true` |
| `librespot_path` | `SPT_LIBRESPOT_PATH` | (auto-discovered) |
| `local_player_autostart` | `SPT_LOCAL_AUTOSTART` | `true` |
| `local_player_name` | `SPT_LOCAL_NAME` | `SPT-TUI Local` |

There is no official librespot binary for Windows, so build it yourself with
`cargo install librespot --locked` (the `--locked` matters: without it a newer
transitive dependency breaks the build). Do not run an unvetted prebuilt
binary — it handles your Spotify credentials.

**If the local player does not start**, the failure now appears on the status
line and every line librespot prints is in the log. The usual cause is:

```
Failed to bind server to 127.0.0.1:5588 (os error 10048)
```

librespot binds `127.0.0.1:5588` for its OAuth redirect, and only one instance
can hold it. Another librespot — typically an earlier one still waiting for you
to finish authorizing — is already there. Quit the app (which stops its
librespot), confirm nothing holds the port, and try once more.

Shipping the binary and a one-click installer is tracked separately (Sub-project B).

## Limitations

- Spotify Web API access and a valid Spotify Developer application are required.
- Playback control requires Spotify Premium and an active device.
- Lyrics depend on a third-party lyrics provider and may not be available for every track.
- Spotify may return unavailable or incomplete library objects for removed albums, episodes or regional content.
- Behaviour may be affected by upstream changes in Spotify, Spotipy or Textual.
- Spotify-TUI is still a `0.x` project. Interfaces and keyboard bindings may change before `1.0`.

## Security

- Never commit the Spotify Client Secret, OAuth tokens or token-cache files.
- The repository `.gitignore` excludes common credentials, token caches, logs and local workspace files.
- Runtime credentials, token caches and logs are stored outside the repository under:

```text
~/Documents/naarvent's projects/Spotify_TUI/
```

Set `SPT_TUI_CACHE_DIR` to use a different directory, which is useful for isolated test runs or separate local environments.

The directory may contain:

```text
spt_config.json
.cache_spotify_token
spt_py_textual_spotify.log
lyrics_cache.json
playlists_cache.json
```

- `spt_config.json` stores the Spotify Client ID, Client Secret and Redirect URI when credentials are entered through the interface.
- `.cache_spotify_token` stores the Spotify OAuth token.
- Log files may contain technical diagnostics.
- Cache files may contain Spotify metadata.
- Keep the configuration directory private.
- Prefer environment variables when possible.

## Contributing

Issues and pull requests are welcome.

Before opening a pull request:

1. Keep changes focused.
2. Preserve existing keyboard and navigation behaviour unless the change explicitly modifies it.
3. Add regression coverage for bug fixes and new behaviour.
4. Run every standalone test suite.
5. Run the static checks.

Recommended static checks:

```bash
python -m compileall -q spt_tui
python -m pyflakes spt_tui
python -m vulture spt_tui --min-confidence 80
git diff --check
```

## License

Released under the [MIT License](LICENSE).

Copyright (c) 2026 naarvent_
