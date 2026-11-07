# Spotify-TUI

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/naarvent/Spotify-TUI/releases) [![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) [![Textual](https://img.shields.io/badge/Textual-8.x-5a4fcf.svg)](https://github.com/Textualize/textual)

A fast keyboard-driven Spotify client for the terminal, built with [Textual](https://github.com/Textualize/textual).

Search Spotify, browse your library and playlists, control playback, follow synced lyrics, manage your queue and devices, all from the keyboard.

**Status:** `v1.0.0` (first stable release). The application includes responsive search and tables, reliable cached library and playlist loading, synchronized lyrics, visible action feedback, an experimental built-in local player and a one-click Windows installer, backed by an extensive offline regression suite.

<p align="center">
  <img alt="Spotify-TUI interface" src="https://github.com/user-attachments/assets/35ab7ab0-43a7-43f3-b8b6-056d45521255" />
</p>

---

## Contents

- [Features](#features)
- [What's New in v1.0.0](#whats-new-in-v100)
- [What's New in v0.2.3](#whats-new-in-v023)
- [Requirements](#requirements)
- [Installation](#installation)
- [Spotify Setup](#spotify-setup)
- [Running](#running)
- [Search](#search)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Providers](#providers)
- [Local player (experimental)](#local-player-experimental)
- [Caching](#caching)
- [Architecture](#architecture)
- [Tests](#tests)
- [Limitations](#limitations)
- [Security](#security)
- [Contributing](#contributing)
- [Release notes](#release-notes)
- [License](#license)

## Features

- **Multi-panel search dashboard** with dedicated Songs, Artists, Albums and Playlists sections.
- **Type-specific search** for tracks, artists, albums, playlists, podcasts and episodes.
- **Responsive search layout** that adapts to large, medium and narrow terminals.
- **Playlists** with a persistent disk cache, instant startup rendering and safe background refresh.
- **View-only playlist sorting** (`s` / `S`) by title, artist, album, duration or date added, remembered between sessions.
- **Library** with liked songs, recently played tracks, saved albums, followed artists, saved podcasts and saved episodes.
- **Favorites** for liking tracks and following or saving albums, artists, podcasts and episodes directly from the interface.
- **Unified saved-state indicators** using consistent heart symbols throughout the application.
- **Playback control** with play, pause, next, previous, seek, volume, mute, repeat and shuffle.
- **Synced lyrics** with current-line highlighting, background loading and persistent caching.
- **Bounded lyrics cache** with automatic pruning by entry count and a configurable total-size cap (set in Settings using human-readable sizes such as `200 MB` or `1 GB`).
- **Queue** for viewing the current queue and adding selected tracks.
- **Add to playlist** for tracks, episodes and complete albums, artists, playlists or podcasts, with confirmation for large additions.
- **Devices** for listing Spotify devices and transferring playback.
- **Multiple providers** (see [Providers](#providers)): Spotify (default), Spotify Free through the browser player (MPRIS), Subsonic / OpenSubsonic servers such as Navidrome, and YouTube Music. The UI hides or disables what the active provider cannot do and says why on the status line.
- **Spotify Free degraded mode**: a non-Premium account is detected, playback keys are disabled with a clear notice, and search, library, likes and playlists keep working.
- **Visible action feedback** through a status line above the active content.
- **Collapsible sidebar** to give more room to the content area when needed.
- **Responsive tables** with automatic column sizing and no manual mouse-driven column resizing.
- **Context-aware refresh** that updates the active view instead of repeating stale searches.
- **Keyboard-driven navigation** throughout the interface.
- **Built-in help view** with keyboard scrolling.
- **Responsive welcome screen** with layouts adapted to the available terminal size.
- **Offline regression suites** covering navigation, search, library loading, playback, lyrics, caching and responsive layouts.

## What's New in v1.0.0

v1.0.0 is the first stable release. It turns Spotify-TUI into a multi-provider player:

- **Providers**: Spotify, YouTube Music (no account), Subsonic/Navidrome and Spotify via MPRIS, switchable at runtime with `P`.
- **Local library**: likes, saved albums/artists and playlists kept on disk for providers without an account; Import from Spotify.
- **Offline downloads** (`o` / `O`), **radio** (`R`), **artist pages**, a `/` list filter and wrap-around Up/Down in track lists.
- **Lyrics**: karaoke mode (`k`) and optional word-by-word timings from the Spicy Lyrics API (your own free key, kept in the keyring or `SPT_SPICY_LYRICS_KEY`).
- **Visualizer** (`v`, needs `cava`) with a configurable default mode and frame rate.
- Full-screen Queue, Devices, Help and Add to playlist views; Create Playlist asks only what each provider supports.

See [CHANGELOG.md](CHANGELOG.md) for the details.

## What's New in v0.2.3

v0.2.3 adds playlist sorting and makes the queue, Settings and lyrics noticeably more reliable.

### Playlist Sorting

- Press `s` on an open playlist to cycle the order: Default (playlist order) → Title → Artist → Album → Duration → Date added. `S` reverses it.
- Sorting is view-only: the playlist on Spotify is never reordered. The criterion and direction appear in the table title and are remembered between sessions.
- Pages that stream in are merged into the sorted view, and the highlighted track is followed instead of jumping to the top.
- Playing a track from a sorted playlist starts it at its real playlist position, not at its table row.
- `Ctrl+D` removes only the selected occurrence of a track that appears more than once in a playlist. If the playlist changed in the meantime, it re-reads the playlist and removes the closest matching occurrence.

### Queue

- The Source column shows a readable label (Playlist, Liked Songs, Recently Played, Album...) instead of an internal widget id.
- Opening the queue twice, or holding `Ctrl+C`, no longer crashes the app. Repeated presses refresh the open queue once at a time and bring focus back to it.

### Settings and Lyrics

- Settings and the lyrics view no longer crash when opened while another view is on screen, and `Up` / `Down` move correctly between the Settings fields.
- Lyrics stay in sync more reliably: pausing no longer makes them jump back, a playback sync that was already in flight can no longer undo a seek or pause, and the position is anchored to the middle of the playback request so network latency no longer delays the lyrics.
- Plain (unsynced) lyrics are only used as a last resort, after looking for a synced version, and the LRC `[offset:]` tag is now applied.

### Favorites

- The heart of a liked track is applied to the right row even if the table is re-sorted while the request is running.

## Requirements

- **Python 3.9+**. Developed and tested primarily on Python 3.14.
- A **Spotify account**.
- A **Spotify Premium** account for playback control, including play, pause, skip, seek, volume, shuffle and device transfer. This is a Spotify Web API restriction.
- A **Spotify Developer application** for the Client ID, Client Secret and Redirect URI.
- An **active Spotify device**, such as the desktop application, a phone or a compatible speaker, for playback operations.

Browsing, searching and some library operations may work with a free Spotify account, but playback-control endpoints require Premium. With a Free account SPT-TUI switches to a degraded mode automatically (playback keys disabled with a notice), or you can use the `spotify_mpris` provider to control the Spotify web player in your browser.

Optional, per provider (see [Providers](#providers)):

- **`spotify_mpris`**: [`playerctl`](https://github.com/altdesktop/playerctl) (Linux, MPRIS) and an opener command for URLs — `abrir` by default, configurable with `open_command` / `SPT_OPEN_COMMAND`.
- **`subsonic`**: the [`mpv`](https://mpv.io) binary for playback; optionally `pip install "Spotify-TUI[subsonic]"` (adds `keyring` to store the password in the system keyring; libsecret's `secret-tool` also works).
- **`ytmusic`**: `pip install "Spotify-TUI[ytmusic]"` (`ytmusicapi` + `yt-dlp`) and the `mpv` binary.

A missing optional dependency never crashes the app: the provider is disabled (falling back to Spotify) or its playback is disabled, with a message explaining what to install.

## Installation

### Windows installer (easiest)

Download the latest `SPT-TUI-Setup-<version>.exe` from the
[Releases page](https://github.com/naarvent/Spotify-TUI/releases) and run the
wizard. It installs everything — **no Python needed** — and bundles the local
player (`librespot`), so you can play music without any other Spotify client open
(Spotify **Premium** required). The wizard adds `spt` to your `PATH` (optional),
so you can open a new terminal and just run `spt`.

The installer is unsigned, so on first run Windows SmartScreen shows an "unknown
publisher" warning — click **More info → Run anyway**.

### Linux / macOS (pipx)

There is no bundled installer for Linux/macOS — use `pipx`, which installs the
app in an isolated environment and puts the `spt` command on your `PATH`.

**1. Install pipx** (it is not preinstalled):

```bash
sudo apt install pipx          # Debian / Ubuntu
sudo dnf install pipx          # Fedora
sudo pacman -S python-pipx     # Arch
brew install pipx              # macOS (Homebrew)
python3 -m pip install --user pipx   # any system, fallback
```

**2. Put pipx apps on your PATH** (once), then open a new terminal:

```bash
pipx ensurepath
```

**3. Install SPT-TUI:**

```bash
pipx install git+https://github.com/naarvent/Spotify-TUI.git
```

**4. (Optional) Install librespot** for local playback — the built-in player:

```bash
sudo pacman -S librespot       # Arch (repos/AUR)
cargo install librespot --locked   # any system, builds from source
```

SPT-TUI finds `librespot` on your `PATH` automatically (or set
`SPT_LIBRESPOT_PATH` to point at it). Without it, the app still works as a
controller for another Spotify Connect device.

**5. Run it:**

```bash
spt
```

Update later with `pipx upgrade Spotify-TUI`. Spotify **Premium** is required for
playback, with or without the local player. On macOS the same steps apply
(`brew install pipx`).

### From source (any OS)

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
| `/` | Focus the search input; on an open list (playlist, album, Liked Songs, search results, artist) filter its loaded rows instead — see below |
| `Escape` | Return to the main menu |
| `?` / `F1` | Open or close Help |
| `Ctrl+Q` | Quit |

### Filtering the open list (`/`)

With a track list or result table focused, `/` opens a one-line filter box above it.
Typing narrows the rows **already loaded** (no new search) by title, artist or album:
every word must appear, case and accents ignored (`beyonce` finds `Beyoncé`). The frame
title shows `filter “…”: N of M`. `Enter` or `↓` goes back to the list and keeps the
filter; `Esc` clears it (a second `Esc` goes back to the menu as usual).

- It works with the playlist sort (`s` / `S`): sorting keeps the filter.
- Playing a filtered row starts it at its **real** position in the playlist/album,
  not at its row on screen; `f`, `Ctrl+D`, `c` and Multi-Add (`Ctrl+A` selects what is
  visible) act on the highlighted track too.
- Pages that are still streaming in are filtered as they arrive.

### Artist page

Enter on an artist (search results, Saved Artists, a related artist) opens one list with
four sections, marked in the **Type** column: `TOP` (top tracks), `ALB` (albums), `SNG`
(singles / EPs) and `REL` (related or similar artists); with Spotify, playlists published
by the artist's own account appear as `PLY`. The frame title counts each section.
Enter on a top track plays the top tracks from that one; on an album or single it opens
it; on a related artist it opens their page. `/` filters this list too.

What each provider can show:

| Provider | Top tracks | Albums / singles | Related |
| --- | --- | --- | --- |
| YouTube Music | yes (artist page songs) | yes (full list when YouTube offers it) | yes |
| Subsonic / Navidrome | `getTopSongs` (Navidrome takes it from Last.fm; empty without it) | `getArtist`; singles/EPs split only with OpenSubsonic `releaseTypes` | `getArtistInfo2` (only artists in your library) |
| Spotify / `spotify_mpris` | tried; Spotify **removed** `artists/{id}/top-tracks` for development-mode apps (Feb 2026) | yes | tried; **removed** for development-mode apps since Nov 2024 |

A section the provider cannot give is left out and the status line says why; the rest
of the page still loads.

### Radio (`R`)

`R` (Shift+R) opens a **radio**: tracks like the highlighted track, artist or sidebar
playlist (with nothing usable highlighted, like the track that is playing). It opens as
a normal list, `Radio: <seed>`; it does not start playing by itself — Enter plays from
that row and the rest of the radio follows. `Ctrl+R` asks for a fresh radio.

| Provider | How |
| --- | --- |
| YouTube Music | `get_watch_playlist` (YouTube Music's own "Start radio"); artist = their radio, playlist = playlist radio, a local playlist = radio of its first song |
| Subsonic / Navidrome | `getSimilarSongs` (track), `getSimilarSongs2` (artist), playlist = songs similar to its first 3; Navidrome needs Last.fm for this |
| Spotify / `spotify_mpris` | `/recommendations` is tried, but Spotify **removed** it for development-mode apps (Nov 2024): the status line says so |

Albums and podcasts are not radio seeds (the status line says what is).

### Offline downloads (`o` / `O`) — YouTube Music and Subsonic

- `o` on a song: download it for offline play (`o` again deletes the copy). `O`: download every
  song of the open list (only the rows on screen when the `/` filter is on). One download at a
  time; the status line shows progress and, at the end, the space used.
- Every song list (playlists, albums, Liked Songs, search results, artist page, radio, Queue) has a
  1-cell column **before the heart**: `↓` downloaded, `⇣` downloading, `·` waiting, empty otherwise.
  It is there with every provider; with Spotify / `spotify_mpris` its header is dimmed and `o`/`O`
  explain why (the Web API has no download endpoint and Premium downloads only exist inside Spotify's
  own apps, under DRM — pending). On a very narrow terminal it is the first column to go.
- **Library > Downloaded** lists them, with an Added (download date) column. `s` / `S` sort it like a
  playlist (Default = newest download first, Title, Artist, Album, Duration, Date added, Size), remembered
  separately (`downloaded_sort`); songs without a known duration go last. Copies saved by older versions
  without a duration get it filled in in the background the first time you open it (no re-download).
- Playing a downloaded song hands mpv the local file: no stream URL, no yt-dlp, no request to the
  server. (Metadata calls still happen when you open an online playlist or album, and Subsonic
  still scrobbles if the server is reachable.)
- Where: `<data folder>/offline/ytmusic/` and `<data folder>/offline/subsonic/<server>_<user>-<hash>/`
  (the data folder is `~/Documents/naarvent's projects/Spotify_TUI/` or `$SPT_TUI_CACHE_DIR`), one
  audio file per song plus `index.json`. Downloads go to `.partial/` first.
- Space: **Settings > Offline downloads > Offline space** (`offline_max_bytes`, default `1 GB`,
  50 MB – 64 GB). When it is full, the least recently played copies are deleted first.
- YouTube Music keeps the best audio YouTube gives without an account (usually Opus ~137 kbps,
  `.webm`, no re-encoding). Subsonic downloads the original file (`download`); if your user has no
  download permission it falls back to the server's `stream` (transcoded).

### Karaoke lyrics (`k` in the lyrics view)

The lyrics view (`l`) keeps its line-by-line mode by default. Press `k` inside it to switch to
**karaoke** (remembered as `lyrics_mode` in `spt_config.json`; `k` again goes back):

- the current line lights up word by word (a character wipe inside the current word), the
  other lines fade with their distance to it, and instrumental gaps show as `● ● ●` filling up;
- the status line under the title says how good the timing is: **`≈ approximate word timing`**
  when the lyrics only have line times (LRCLIB's usual LRC: each line's time is split between its
  words by length), or **`word-synced`** when the lyrics carry real word times;
- `]` / `[` move the lyrics ±0.25 s for the current song, `}` / `{` ±1 s (on top of the file's own
  `[offset:]`); the offset shows in the status. It works in the line view too.

Real word times come from **enhanced LRC** (`[00:12.00]<00:12.00>Hel<00:12.40>lo`, if LRCLIB has it)
or from **your own files**: put `<track id>.ttml` / `.lrc`, or `<Artist> - <Title>.ttml` / `.lrc`, in
`<data folder>/lyrics/` (`~/Documents/naarvent's projects/Spotify_TUI/lyrics/`). They win over LRCLIB.
TTML: `<p begin end ttm:agent>` lines with `<span begin end>` words; background vocals
(`ttm:role="x-bg"`), duets (`v1` left, `v2` right) and romanisation (`ttm:role="x-roman"` or the head's
`<transliteration>`) are shown. Files with a DOCTYPE/ENTITY are refused.

#### Word-by-word lyrics from Spicy Lyrics (optional, needs your own free key)

With a key, the lyrics view asks the Spicy Lyrics developer API first (source order: **your files > Spicy Lyrics >
LRCLIB**). It returns real **syllable** times (the karaoke wipe follows them), line times, duets and background vocals.
Without a key nothing changes: no request, no error, LRCLIB as before.

- Get a free key at developers.spicylyrics.org (one account per person; every copy of this app needs its own key).
- Keyring (Linux): `secret-tool store --label='SPT-TUI (spicy-lyrics)' service spt-tui username spicy-lyrics` (it asks
  for the key), or `python -m keyring set spt-tui spicy-lyrics`; or the environment variable `SPT_SPICY_LYRICS_KEY`.
  The key is never written to `spt_config.json`, the log or the cache.
- Settings (`<`) > Lyrics: **Use Spicy Lyrics when a key is found** / **Off** (`spicy_lyrics` in the config; also
  `SPT_SPICY_LYRICS=off`). Settings says whether a key was found, never its value.
- Spotify tracks use their id; YouTube Music, Subsonic and MPRIS tracks are first matched with the API's search by
  title/artist/album/duration and only a result it rates `exact` (with a matching length) is accepted.
- **Credit**: the lyrics always show where they came from on the last rows of the lyrics box: `Lyrics: Spicy Lyrics`
  (plus the uploader and the maker with their profile links when it is a community sync), `Apple Music`, `Spotify`,
  `LRCLIB` or `Local file`.
- Cache: `<data folder>/lyrics/spicy_cache/`, **30 days at most** (an older copy is never used and is deleted). The app
  keeps under the API's limit (50 requests / 50 s here) and never exports or saves the data in any other form.
- Network, 401 or 429 problems fall back to LRCLIB with a one-line notice. "Static" (untimed) answers are only used when
  LRCLIB has nothing.

Fixed layout: the karaoke frame is built as exactly as many one-row lines as the box has (nothing
wraps), so the title and the status line never move, and the current line always sits on the middle
row while the lyrics scroll past it (like Spicy Lyrics). A long line takes at most 2 rows and ends
with `…`; background vocals go under it in grey italics, romanisation under that. The wipe has a
soft truecolor edge; neighbours fade with their distance. What a terminal cannot do: real blur,
different font sizes for the current line, and pixel-smooth scrolling (it moves row by row).

Performance (measured headless with Textual's test driver, one core): karaoke while playing
repaints ~8 times/s and used about 5–8 % CPU; paused it drops back to the 0.4 s tick (~0.2–0.6 %);
the line view used 2–5 %. Frames that would look the same are skipped, and frames do not trigger
a layout pass. Inspired by the idea of Spicy Lyrics; no code from it.

### Queue view (`Ctrl+C`)

The queue opens like a playlist: the list takes the whole panel (title `Queue · N songs` on the frame),
with `↓` and `♥`, `/` to filter and Enter to jump to a song. Sections stay as header rows ("Next in
queue", "Next from: …"); the Source column says where each song comes from (Playlist, Downloaded,
`Radio`, Artist…). YouTube Music / Subsonic list up to `queue_context_limit` songs (default 300).
With Spotify, `/me/player/queue` returns only a short list and has no paging: it is shown as
"Spotify queue", and when the context is a playlist or album and shuffle is off, the rest of that
playlist is added below it as "Deduced" (the frame title says so). With shuffle on nothing is deduced.

### Visualizer (`v`)

`v` opens a **Visualizer** over the whole content panel: bars from `cava` listening to the
real system audio (so it works with every provider), coloured with a gradient of the accent colour,
with the song header of the lyrics view (big title / artist) at a fixed position on top. `v` again or Esc closes it (and kills cava). It opens in **mirrored** mode by
default (bass in the middle); choose the default mode (`visualizer_mode`: `mirrored` or `bars`) in Settings > Visualizer, and press Shift+M to switch it for the
current session (`m` stays Mute). While nothing plays, cava is paused (no CPU). The number
of bars follows the panel width; the frame rate is `visualizer_fps` (default 30, 10–60; change it in Settings > Visualizer FPS
or in `spt_config.json`; it applies at once). The audio source is taken from `[input] method/source` of `~/.config/cava/config` if you set
them (that file is only read). Without `cava` installed, `v` says so. This replaces the idea of
Rigellute/spotify-tui's Analysis view, whose Spotify Audio Analysis API is closed to new apps.
Measured: cava itself ~1 % CPU at 30 fps; spt ~11 % of one core while repainting 30 times/s
(headless test driver), ~0.1 % when paused.

### Devices (`d`)

Devices open in the same full-panel frame (`Devices · N`): `●` marks the active one, Enter transfers
playback, `/` filters, and the list refreshes every 5 s without moving the cursor.

### Local player: first time

The first time you choose **SPT-TUI Local (start)** in Devices, librespot needs you to log in once: spt opens
the link with `open_command` / `SPT_OPEN_COMMAND` (default `abrir`; the system browser if that is missing) and
the status line says *Authorize SPT-TUI Local in your browser… Waiting* until you do (up to 2 minutes). If the
browser cannot be opened it says why and shows the link (also in the log).

### Esc

Esc first cancels whatever is pending in the current view (removing a track, Multi-Add, the `/` filter,
the "add N tracks?" confirmation, deleting a playlist, a running Spotify import) and stays there; with
nothing left to cancel it goes back to the menu.

### Title bar

Settings > Appearance can hide the title bar at the top (`show_header`); it applies at once and the
content takes the freed row.

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
| `<` | Open settings (volume steps, seek jump times, lyrics cache size, transparent/opaque background) |

### Library, Queue and Tools

| Key | Action |
| --- | --- |
| `f` | Like, unlike, follow or save the selected item |
| `l` | Toggle the lyrics view |
| `c` | Add the selected track to the queue |
| `o` / `O` | Download the highlighted song / every song of the list for offline play (YouTube Music, Subsonic) |
| `k` (lyrics view) | Line-by-line ⇄ karaoke lyrics |
| `[` / `]`, `{` / `}` (lyrics view) | Lyrics offset −/+0.25 s, −/+1 s |
| `v` | Visualizer (cava); `v`/Esc close it, Shift+M mirrored |
| `R` (Shift+R) | Radio from the highlighted track, artist or playlist (see [Radio](#radio-r)) |
| `Ctrl+C` | Open Queue |
| `d` | Open Devices |
| `Ctrl+L` | Toggle multi-add mode |
| `Ctrl+O` | Multi-select review: select all items |
| `Ctrl+A` | Multi-select review: add the selected items · Multi-add mode: select or deselect all rows |
| `Ctrl+Shift+P` | Add to a playlist: the selected track/episode, or every track of a selected album, artist, playlist or podcast (large adds ask to confirm) |
| `Ctrl+T` | Import a playlist |
| `Ctrl+D` | Delete or remove the selected item (press again to confirm a track removal, `Esc` to cancel) |
| `Ctrl+E` | Rename the selected sidebar playlist (local-library playlists) |
| `s` | Sort the open playlist: Default (playlist order) → Title → Artist → Album → Duration → Date added. View only; the choice is remembered in `spt_config.json` |
| `S` (`Shift+S`) | Reverse the playlist sort (ascending / descending) |
| `Ctrl+R` | Refresh the active view |
| `Ctrl+B` | Hide/show the left sidebar (gives its width to the content) |
| `P` (`Shift+P`) | Choose the music provider (switches live, see [Providers](#providers)) |

### Help View

While Help is open:

| Key | Action |
| --- | --- |
| `Up` / `Down` | Scroll |
| `PageUp` / `PageDown` | Scroll by page |
| `Home` / `End` | Jump to the beginning or end |
| `Escape` | Close Help |

## Providers

SPT-TUI talks to a *provider*, not to Spotify directly. At startup the active one is chosen, in priority order, by:

1. the `SPT_PROVIDER` environment variable;
2. `"provider"` in `spt_config.json` (written by the provider picker);
3. the default, `spotify`.

### Settings and the provider picker

**Settings** (`<`) is one scrollable form in sections — *Playback* (volume steps, seek times), *Lyrics* (cache size), *Visualizer* (FPS), *Music provider* (the provider list) and *Subsonic / Navidrome* (URL, user, password) — each field with a short help line. Enter saves a field, `↑`/`↓` and `Tab`/`Shift+Tab` move between fields (Tab leaves the form after the last one), `Esc` goes back. If you click or Tab away to the sidebar, Search or Help, `→` (or Tab) brings you back to the field you were editing; the same holds for Import Playlists, Rename and Devices. On narrow terminals labels stack above their fields.

The provider picker (**`P`**) is a modal window: `↑`/`↓` choose, Enter applies, Esc closes. The active provider is marked `●` / **ACTIVE**; each entry shows its status in colour (green available, amber something missing, red cannot start, with the reason).

Both use the app's teal palette as a Textual theme (`spt-teal`), so they follow the same colours as the rest of the interface.

### Switching provider (live)

Press **`P`** (Shift+P) from any view — or open **Settings** (`<`) — to get the provider list. Each entry shows its readiness: `available`, or what is missing (`missing mpv (no playback)`, `missing ytmusicapi`, `missing playerctl`, `missing config (server URL / user)`...). Pick one with Enter and it is switched **without a restart**:

- the new provider is created first; if it cannot start, the current one stays and the status line says why;
- otherwise the current provider's resources are released (mpv, sockets, librespot), the session state is reset (playlist and library caches, queue, now-playing state, open views) and the active view is reloaded on the new provider (a search is re-run, a Liked Songs / Saved Albums view reopened; a specific playlist or album of the old provider goes back to the menu);
- if anything fails after the old provider was closed, a fresh instance of the previous provider is restored, with a notice;
- the choice is saved to `spt_config.json`. If `SPT_PROVIDER` is set it still wins at the next start, so the picker warns about it and the switch applies to this session only (nothing is saved).

Picking the active provider again reloads it (useful after editing the Subsonic server in Settings).

| Provider | Search / browse | Playback | Likes | Edit playlists | Needs |
| --- | --- | --- | --- | --- | --- |
| `spotify` | Web API | Spotify Connect (Premium) | yes | yes | Spotify Developer app |
| `spotify_mpris` | Web API | your own browser / desktop player via MPRIS | yes | yes | Developer app, `playerctl`, `abrir` |
| `subsonic` | `search3`, playlists | local `mpv` (`stream`) | `star` / `unstar` | yes | server URL, user, password, `mpv` |
| `ytmusic` | `ytmusicapi` (anonymous) | local `mpv` + `yt-dlp` | local library | local playlists + followed public ones | `[ytmusic]` extra, `mpv` |

Each provider declares capabilities (`can_play`, `can_control_playback`, `can_seek`, `can_set_volume`, `can_control_queue`, `can_like`, `can_edit_playlists`, `can_list_devices`, ...). Keys for an unsupported action show a status-line notice instead of failing, and Spotify-only views (podcasts, episodes, followed artists) are disabled elsewhere.

If the chosen provider cannot start (missing package or configuration), SPT-TUI falls back to Spotify and shows the reason.

### Spotify Free

The account tier is read once at startup (`product` of the current user, and again if Spotify answers `PREMIUM_REQUIRED`). With a Free account, play / pause / next / previous / seek / volume / queue / shuffle / repeat / devices and the local librespot player are disabled with a notice; search, library, likes and playlists work as usual.

### Spotify Free via MPRIS (`spotify_mpris`)

For Free accounts that want playback from the terminal, this mode **only controls the player you opened yourself**:

- `Enter` on a track opens `https://open.spotify.com/track/<id>` with `abrir <url>` (configurable: `open_command` / `SPT_OPEN_COMMAND`); press play there.
- Space / `n` / `p` / seek / volume are sent through `playerctl` to the Spotify web player (or desktop client). The player is auto-detected (a player named `spotify`, or one whose URL is on `open.spotify.com`); force one with `mpris_player` / `SPT_MPRIS_PLAYER`.
- Now playing, position and synced lyrics come from the MPRIS metadata.

It deliberately does **not** automate or run a headless browser, use the `sp_dc` cookie, call internal Spotify endpoints, decrypt streams or skip ads — that would break Spotify's terms of use. Without `playerctl` it still opens links and shows a notice.

### Subsonic / Navidrome (`subsonic`)

```json
{ "provider": "subsonic", "subsonic_url": "https://music.example.org", "subsonic_user": "alice" }
```

(or `SPT_SUBSONIC_URL` / `SPT_SUBSONIC_USER`; the URL and user can also be entered in Settings). The password is **never stored in `spt_config.json`**: enter it in Settings and it goes to the system keyring (`keyring` package or `secret-tool`); without a keyring, export `SPT_SUBSONIC_PASSWORD` (which also takes precedence). Requests use token authentication (`u`, `t = md5(password + salt)`, `s`, `v`, `c`, `f=json`), so the password is not sent either.

Supported: `search3` search, playlists (open, play, create, add, remove one entry, delete), favourites as Liked Songs / Saved Albums (`star` / `unstar`), `scrobble` (now playing on start, a submission after half the track or 4 minutes) and audio through `stream`, played by `mpv`.

### YouTube Music (`ytmusic`)

Anonymous: search and **public** playlists through `ytmusicapi`, audio through `mpv` + `yt-dlp`. There is no account, so likes, saved albums, followed artists, your own playlists and the play history live in the **local library** (below). Follow a public playlist with `f` on it (search results, or `f` again on the sidebar entry to drop it) or with **Import Playlists** (paste `https://music.youtube.com/playlist?list=...` or the id); followed playlists are stored as `ytmusic_playlists` in `spt_config.json`.

Uploads by ordinary channels are shown with the artist taken from the title (`JOJI - Song` by channel *2Restarted* becomes *Song — JOJI*), and `<Artist> - Topic` channels are shown as `<Artist>`. mpv only plays what the TUI queued: a watch URL is never expanded into a playlist and nothing autoplays after the queue.

### Local library

For providers without an account (YouTube Music) the hearts and the Library sections work from a **local library**: a JSON file per provider next to `spt_config.json` (`local_library_ytmusic.json`; Subsonic only keeps its play history there), never mixed with Spotify.

- `f` on a song / album or single / artist saves, follows or removes it: **Liked Songs**, **Saved Albums**, **Saved Artists** list them, with the same hearts as Spotify (following artists is its own capability, `can_follow_artists`, separate from podcasts).
- **Create Playlist** creates a local playlist (`local:<uuid>`) shown in the sidebar *Playlists* with the followed ones; **Add to Playlist** (`Ctrl+Shift+P`) adds tracks, `Ctrl+D` removes one entry (or deletes the selected sidebar playlist), **`Ctrl+E`** renames it, and `Enter` plays it.
- **Recently Played** is a local history (last 50 tracks actually played).
- Each entry stores a minimal copy of its metadata (id, name, artists, album, duration), so everything still lists without the network.
- When something disappears upstream — a video removed or made private, an album gone, a followed playlist made private or deleted, no network, a provider switch — **nothing is deleted**: the entry is marked *(unavailable)*, an unavailable track is skipped with a status-line notice while the rest keeps playing, and the mark clears when it works again. `f` / `Ctrl+D` still remove it (from the local store only).
- Writes are atomic (unique temp file + rename); a corrupt file is kept aside as `*.corrupt-<time>`.

### Import from Spotify

With YouTube Music active and Spotify credentials configured, **Import from Spotify** appears at the end of the Library menu (and in the `P` picker). It copies your Spotify library into the local library:

- Mark what to import (Space): Liked Songs, Saved Albums, Followed Artists and each playlist, then the *Import selected* button.
- Each track is searched on YouTube Music (`"title artist"`, songs filter) and scored on the normalised title (no `(feat. …)`, `- Remastered 2011`, `- From … Soundtrack`), the artist, the duration (±3 s) and the album; below the doubt threshold a second, laxer search (songs and videos) is tried. Matches are imported; **doubtful** matches are imported marked *(? check match)*; not-found ones are skipped.
- Playlists become local playlists with their original name (re-importing refreshes the same one), Liked Songs go to the local Liked Songs, albums and artists are matched by search into Saved Albums / Saved Artists.
- It runs in the background with progress on the status line, pauses between searches, and `Esc` cancels; results are cached (`import_match_cache.json`), so a re-run resumes without searching again and retries what failed for network reasons.
- At the end: a summary (matched / doubtful / not found) and `import_report.json` next to `spt_config.json` with every doubtful and not-found track.

Spotify keeps using its real account; nothing changes there. The Library menu only shows what the provider supports: *Saved Podcasts* / *Saved Episodes* only with Spotify, *Import Playlists* not with Subsonic (its playlists are the server's own).

### Shared mpv player

`subsonic` and `ytmusic` play audio with one `mpv --idle --no-video` subprocess driven over its JSON IPC socket (`spt_tui/players/mpv.py`): queue, pause, seek, volume, repeat, position/duration for the progress bar. It is started on first playback and stopped when SPT-TUI exits.

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
│   ├── spotify_client.py    Backward-compatible import of SpotifyClient
│   ├── secret_store.py      Passwords: env var, keyring or secret-tool
│   ├── providers/
│   │   ├── __init__.py      Provider selection (SPT_PROVIDER / config) and fallback
│   │   ├── base.py          Provider, Capabilities, Spotify-shaped adapter
│   │   ├── spotify.py       SpotifyProvider (Web API, OAuth, Free detection)
│   │   ├── mpris.py         Spotify Free: control the user's player via playerctl
│   │   ├── mpv_backed.py    Shared mpv playback for local-audio providers
│   │   ├── subsonic.py      Subsonic / OpenSubsonic (Navidrome)
│   │   └── ytmusic.py       YouTube Music (ytmusicapi + yt-dlp)
│   ├── players/
│   │   └── mpv.py           mpv subprocess + JSON IPC controller
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

The standalone regression suites protect application behaviour introduced during and after the refactor.

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
- Provider selection, capabilities and the Spotify-shaped adapter
- Spotify Free detection and the playback gates in the UI
- Subsonic (auth token, search, playlists, star/unstar, scrobble, stream) with a fake HTTP session
- YouTube Music (missing dependency, mapping, imported playlists) with a fake client
- MPRIS mode with a fake `playerctl` and opener
- The mpv controller with a fake process and IPC socket
- Password storage (env / keyring / secret-tool)
- The provider picker, live switching with rollback, and `SPT_PROVIDER` precedence

## Limitations

- Spotify Web API access and a valid Spotify Developer application are required.
- Playback control through the Spotify Web API requires Spotify Premium and an active device. Free accounts get a degraded mode, or can use `spotify_mpris`.
- `spotify_mpris` needs MPRIS (Linux) and `playerctl`; it opens tracks in the browser and controls that player, it does not stream audio itself. Opening a track inside a playlist plays that track, not necessarily the rest of the playlist.
- The mpv backend (Subsonic, YouTube Music) needs a Unix IPC socket, so it is not available on Windows. Shuffle reorders mpv's own queue.
- Subsonic has no podcasts; its likes / saved albums / followed artists are server stars and Recently Played is a local history. YouTube Music works without an account (the local library replaces likes and playlists, which therefore do not sync anywhere) and depends on `ytmusicapi` / `yt-dlp` keeping up with YouTube changes.
- Only local-library playlists can be renamed (`Ctrl+E`). Devices stay reachable with `d` for every provider (it lists the mpv device for Subsonic / YouTube Music); only the Library menu entries are hidden per provider.
- A provider switch resets the session (queue, caches, open view); a Spotify provider that has never been signed in still needs a restart to run the sign-in.
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

## Release notes

The latest release is described in [What's New in v1.0.0](#whats-new-in-v100). Earlier releases are summarised below; the full record is in [CHANGELOG.md](CHANGELOG.md).

### v0.2.2

v0.2.2 adds a built-in local player and a one-click Windows installer.

#### Local Player

- Spotify-TUI can now run its own playback backend via [librespot](https://github.com/librespot-org/librespot), so it plays music without any other Spotify client open (Premium still required).
- One-time browser authorization from Devices (`d` → **SPT-TUI Local (start)**); after that it starts headless.
- Polite autostart: activates on launch only if nothing is already playing elsewhere, never stealing an active session.
- Serialized start prevents a race from orphaning a second `librespot` process, and startup failures now surface on the status line instead of failing silently.
- Fixed: pressing `Enter` on a Devices row now reliably selects it (row key resolution bug).

See [Local player (experimental)](#local-player-experimental) for configuration.

#### Windows Installer

- Download `SPT-TUI-Setup-<version>.exe` from [Releases](https://github.com/naarvent/Spotify-TUI/releases) — no Python needed, bundles `librespot`, adds `spt` to `PATH` (optional), shows the MIT license during setup.
- Linux/macOS keep the `pipx` install path; there is no bundled installer for those platforms.

<details>
<summary><b>Older releases (v0.2.1, v0.2.0)</b></summary>

### v0.2.1

v0.2.1 focuses on making everyday playlist work faster, safer and clearer.

#### Playlist Workflow

- Add a selected track or episode to a playlist with `Ctrl+Shift+P`.
- Add every track or episode from a selected album, artist, playlist or podcast; large additions ask for confirmation.
- Playlist pages stream into the table as they load, while liked-state hearts fill in progressively.
- Reopened playlists use an in-session cache for immediate rendering and refresh safely in the background.
- Removing a playlist track requires a second `Ctrl+D`; the confirmation is cancelled when leaving that playlist.

#### Interface and Navigation

- A dedicated status line makes feedback visible above tables instead of hiding it behind them.
- `Ctrl+B` hides or restores the sidebar, assigning its width to the active content panel.
- `Tab` and `Shift+Tab` cycle predictably between the principal navigation stops.
- Tables use the available panel width; on narrow terminals, low-priority columns are hidden in a predictable order and identified in the table title.

#### Reliability and Testing

- Spotify liked-track checks are batched within the API limit, making long playlists much quicker to load.
- Test runs use isolated cache directories, avoiding access to local tokens, logs and playlist caches.
- `tests/run_all.py` can execute suites in parallel and supports filtering, worker-count and timeout options.
- `tests/check_live_api.py` provides an optional read-only verification against the real Spotify API.

For the full release record, see [CHANGELOG.md](CHANGELOG.md).

### v0.2.0

The v0.2.0 notes are retained below as reference for the previous major application update.

#### Multi-panel Search

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

#### Search Reliability

- Immediate visual feedback when a search begins.
- Existing results remain visible while a new search is running.
- A visible frame title indicates the active search query.
- Long-running searches display an additional waiting state.
- Older results cannot overwrite a newer search.
- Empty results are distinguished from request failures.
- Partial search failures preserve successful result categories.
- Search workers cannot repaint the interface after leaving Search.

#### Library Reliability

- Saved Albums now tolerate unavailable Spotify entries.
- Saved Episodes handle unavailable tombstone objects without displaying fake durations.
- Saved Artists use the same reliable background-loading infrastructure as the other saved-library views.
- Recently Played provides clear loading feedback.
- Errors no longer silently appear as empty libraries.
- Valid cached data remains visible during temporary failures.
- Library responses are protected against stale worker updates.
- Cursor and scroll position are preserved during refreshes where possible.

#### Playlists

- Improved disk-cache safety.
- Atomic cache writes.
- Invalid or incomplete responses no longer erase valid playlists.
- Older workers cannot replace newer playlist data.
- Playlist refreshes preserve existing content during temporary API errors.
- Reduced risk of the playlist list disappearing during refresh.

#### Saved and Liked State

- Fixed liked-state rendering for tracks opened from albums.
- Track hearts are resolved in the background and matched by Spotify track ID.
- Favorite mutations update the relevant row without resetting the table.
- Saved-state visuals now use hearts consistently.
- Removed the old `S` saved-state column header.
- Removed floppy-disk saved indicators.
- Track, album, artist, podcast and episode semantics remain independent internally.

#### Tables and Layout

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

#### Now Playing

- Removed unnecessary spacer rows.
- Search, Help, the main content area and Now Playing now use a tighter layout.
- Now Playing remains visible across supported terminal sizes.

#### Lyrics

- Added a hard maximum number of cached lyrics entries.
- Added a maximum cache-file size.
- Older entries are automatically removed when limits are exceeded.
- Lyrics cache files can no longer grow indefinitely.
- Improved metadata updates when the current track changes.
- Previous lyrics no longer remain associated with a newly selected track.

#### Refresh Behaviour

`Ctrl+R` is now context-aware:

- Lyrics refreshes the current lyrics.
- Search repeats the active search.
- Playlists refreshes the current playlist.
- Library views refresh the active library section.
- Queue and Devices refresh their current data.
- Welcome refreshes the visible playlist list.
- Text left in the Search input no longer hijacks refresh actions elsewhere.

</details>

## License

Released under the [MIT License](LICENSE).

Copyright (c) 2026 naarvent_
