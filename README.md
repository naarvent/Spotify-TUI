# Spotify-TUI

A fast keyboard-driven Spotify client for the terminal, built with [Textual](https://github.com/Textualize/textual).
Search, browse your library and playlists, control playback, follow along with
synced lyrics, manage your queue and devices — all from the keyboard.

**Status:** v0.1.0 (early release). It works and is covered by a regression test
suite, but APIs and behaviour may still change before `1.0`.

![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Textual](https://img.shields.io/badge/Textual-8.x-5a4fcf.svg)

---

<img width="1919" height="1199" alt="imagen" src="https://github.com/user-attachments/assets/35ab7ab0-43a7-43f3-b8b6-056d45521255" />

## Features

- **Search** across tracks, artists, albums, playlists, podcasts and episodes.
- **Playlists** — your own and followed ones, with a disk cache so the list
  paints instantly on startup and refreshes in the background.
- **Library** — liked songs, saved albums, followed artists, saved podcasts and
  episodes.
- **Favorites** — like/unlike tracks and follow/save items directly from the UI.
- **Playback control** — play/pause, next/previous, seek, volume, mute, repeat
  and shuffle.
- **Synced lyrics** with a highlighted current line, plus ASCII-art headers via
  `pyfiglet` (with a plain-text fallback on small terminals).
- **Queue** — view and add to the playback queue.
- **Devices** — list and transfer playback between your active Spotify devices.
- **Keyboard-driven** navigation throughout, with a built-in help view.

## Requirements

- **Python 3.9+** (developed and tested on Python 3.14).
- A **Spotify account.** A **Spotify Premium** account is required for playback
  control (play/pause, skip, seek, volume, shuffle, device transfer) — this is a
  restriction of the Spotify Web API, not of this app. Browsing and search work
  on free accounts.
- A **Spotify Developer application** (for the Client ID / Secret — see below).
- An **active Spotify device** (the desktop app, a phone, a speaker, etc.) to
  send playback to.

## Installation

```bash
# 1. Clone
git clone https://github.com/naarvent/Spotify-TUI.git
cd Spotify-TUI

# 2. Create and activate a virtual environment
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

# 3. Install
pip install -e .
```

`pip install -e .` installs the package and its dependencies from
`pyproject.toml`. Alternatively, `pip install -r requirements.txt` installs just
the runtime dependencies without the package/entry point.

## Spotify setup

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   and **create an app**.
2. Copy the app's **Client ID** and **Client Secret**.
3. Add a **Redirect URI** to the app settings and use the *same* value in your
   environment. A loopback address such as `http://127.0.0.1:8888/callback`
   works well.
4. Provide the credentials to the app in either of two ways:

   **Environment variables** (they take precedence):

   ```bash
   # Linux/macOS
   export SPOTIPY_CLIENT_ID="your_client_id"
   export SPOTIPY_CLIENT_SECRET="your_client_secret"
   export SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"
   ```

   ```powershell
   # Windows (PowerShell)
   $env:SPOTIPY_CLIENT_ID = "your_client_id"
   $env:SPOTIPY_CLIENT_SECRET = "your_client_secret"
   $env:SPOTIPY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
   ```

   **Or** enter them inside the app on first run; they are saved to a local
   config file (see [Security](#security) for the location).

On first launch you complete the standard Spotify OAuth flow in your browser;
the resulting token is cached locally and refreshed automatically.

## Running

```bash
python -m spt_tui
```

or, after `pip install -e .`, the console script:

```bash
spt-tui
```

## Keyboard shortcuts

These are the application's real key bindings.

### Navigation

| Key | Action |
| --- | --- |
| `↑` / `↓` | Move cursor up / down |
| `←` / `→` | Move focus between the left panel and the open view |
| `Enter` | Open / play the selected item |
| `/` | Focus the search box |
| `Escape` | Back to the menu |
| `?` / `F1` | Help |
| `Ctrl+Q` | Quit |

### Playback

| Key | Action |
| --- | --- |
| `Space` | Play / pause |
| `n` | Next track |
| `p` | Previous / restart |
| `r` | Toggle repeat |
| `Ctrl+S` | Toggle shuffle |
| `-` / `+` | Volume down / up |
| `m` | Mute |
| `Ctrl+←` / `Ctrl+→` | Seek backward / forward |
| `<` | Seek settings |

### Library, queue & tools

| Key | Action |
| --- | --- |
| `f` | Toggle favorite (like / follow / save) |
| `l` | Toggle lyrics view |
| `c` | Add selected track to the queue |
| `Ctrl+C` | Open the queue |
| `d` | Manage devices |
| `Ctrl+L` | Toggle multi-add selection |
| `Ctrl+Shift+P` | Add to a playlist |
| `Ctrl+T` | Import a playlist |
| `Ctrl+D` | Delete |
| `Ctrl+R` | Refresh |

### Help view

While the help view is open: `↑`/`↓` scroll, `PgUp`/`PgDn` page, `Home`/`End`
jump to top/bottom, `Escape` closes it.

## Tests

The suites are standalone scripts — no test framework is required. Each one
exits `0` only if every check passes. From the project root:

```bash
# Run a single suite
python tests/test_spotify_client.py

# Run all suites (Linux/macOS)
for f in tests/test_*.py; do python "$f" || echo "FAILED: $f"; done
```

```powershell
# Run all suites (Windows PowerShell)
Get-ChildItem tests/test_*.py | ForEach-Object { python $_.FullName }
```

The tests are deterministic and offline: the Spotify client and network calls
are faked, and the Textual UI is exercised through its test pilot. A few of the
concurrency tests are timing-sensitive and may occasionally need a re-run under
heavy CPU load.

## Architecture

This project started life as a single ~5,000-line file (one large class) and was
later refactored — with behaviour preserved — into a small package organised by
topic:

```
spt_tui/
├── __main__.py         Entry point (python -m spt_tui / spt-tui)
├── config.py           Paths, logging and credentials (single source of truth)
├── constants.py        Welcome screen, glyphs, library items
├── widgets.py          Resizable data table and help scroll
├── spotify_client.py   Rate limiter and Spotify API wrapper
└── app/                The SptPy application, split into mixins:
    ├── core.py         Layout, mounting and event routing
    ├── search.py       Search and result rendering
    ├── navigation.py   Keyboard, focus and section navigation
    ├── tables.py       Data-table construction and liked/saved columns
    ├── library.py      Playlists and saved library
    ├── lyrics_view.py  Synced lyrics (fetch / parse / render)
    ├── playback.py     Playback and the "now playing" bar
    └── queue_devices.py Queue and device selection
```

`SptPy` is assembled from these mixins by multiple inheritance; at runtime it is
still a single class, so the split across files does not change any logic. A set
of regression suites (see [Tests](#tests)) guards the refactored behaviour.

## Limitations

- Requires the Spotify Web API and a working Spotify Developer app; playback
  control needs Spotify Premium and an active device.
- Lyrics depend on a third-party lyrics service and are not always available.
- Behaviour is subject to upstream changes in Spotify's API and in the
  `spotipy` / `textual` libraries.
- This is a `0.x` release — interfaces and key bindings may change before `1.0`.

## Security

- **Never commit your Client Secret, tokens or the OAuth cache.** The repository
  `.gitignore` already excludes common secret/token/cache patterns.
- Credentials, the token cache and logs are stored **outside** the repository,
  under `~/Documents/naarvent's projects/Spotify_TUI/`:
  - `spt_config.json` — Client ID/Secret/Redirect URI, if entered in-app.
  - `.cache_spotify_token` — the cached OAuth token.
  - `spt_py_textual_spotify.log` — the rotating log file.
- Prefer environment variables over the on-disk config file when you can, and
  keep the config directory private.

## Contributing

Issues and pull requests are welcome. Please keep changes focused, preserve the
existing behaviour covered by the test suites, and run all suites before opening
a PR.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 naarvent_.
