# spt — Spotify TUI (Python) 🎧🐍

[![License: MIT](https://img.shields.io/badge/license-MIT-blueviolet.svg)](https://opensource.org/licenses/MIT) [![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![Issues](https://img.shields.io/github/issues/naarvent/Spotify-From-Terminal)](https://github.com/naarvent/Spotify-From-Terminal/issues) [![GitHub stars](https://img.shields.io/github/stars/naarvent/Spotify-From-Terminal?style=social)](https://github.com/naarvent/Spotify-From-Terminal/stargazers)

---

A terminal-based Spotify client built with Python, Spotipy and Textual. It provides a keyboard-driven TUI for searching, browsing and controlling Spotify playback, viewing synced lyrics, managing playlists and devices, and more.

---

## Table of Contents 📚

* [Summary 📝](#summary-)
* [Features ✨](#features-)
* [Requirements & Installation 🛠️](#requirements--installation-️)
* [Environment & Credentials 🔐](#environment--credentials-)
* [How it works (high level) ⚙️](#how-it-works-high-level-️)
* [APIs & External Services 🌐](#apis--external-services-)
* [Keybindings / Controls ⌨️](#keybindings--controls-️)
* [Configuration & Cache 📂](#configuration--cache-)
* [Troubleshooting & Notes 📝](#troubleshooting--notes-)

---

## Summary 📝

`spt` is a full-featured TUI for Spotify built using:

* **Textual** for the terminal UI
* **Spotipy** for the Spotify Web API
* **Requests** for lyrics and metadata providers

It supports search, playback, playlists, devices, lyrics, multi-selection workflows, and rich UI panels.

---

## Features ✨

* Full Spotify search (tracks, albums, artists, playlists)
* Playback control: play / pause / next / previous / seek / shuffle / repeat
* Synced LRC lyrics when available; plain lyric fallback
* Multi-review / multi-add batch playlist operations
* Playlist creation, import, and modification
* Device selection and playback transfer
* Real-time playback sync with progress bar
* Persistent cache + config + OAuth token storage
* Expandable / collapsible Library & Playlists panels via keyboard

---

## Requirements & Installation 🛠️

**Recommended:** Python 3.10+

Install dependencies:

```
pip install spotipy textual requests rich pyfiglet
```

Run the app:

```
python spt.py
```

---

## Environment & Credentials 🔐

`spt` requires Spotify credentials:

* `SPOTIPY_CLIENT_ID`
* `SPOTIPY_CLIENT_SECRET`
* `SPOTIPY_REDIRECT_URI`

If not provided, the app will ask for credentials on first launch and can save them locally.

Scopes include playback, playlists, library, and user data.

---

## How it works (high level) ⚙️

* Textual builds a two-column TUI: Search/Library/Playlists (left) and Details/Results/Lyrics (right).
* Spotipy handles authentication, tokens, and playback/playlist operations.
* Lyrics are fetched using LRCLib and lyrics.ovh, parsed into synced or pseudo-synced lines.
* A periodic task syncs playback state and updates the UI.
* Keyboard shortcuts drive all navigation and actions.

---

## APIs & External Services 🌐

* **Spotify Web API** — playback, search, library, playlists, devices
* **LRCLib** — synced LRC lyrics
* **lyrics.ovh** — plain lyric fallback

---

## Keybindings / Controls ⌨️

### Playback Controls

* **Enter** — Open / Play selected item (in Multi-Review toggles selection)
* **Space** — Play / Pause
* **n** — Next track
* **p** — Previous / Restart (>3s rule)
* **r** — Toggle repeat (off → context → track)
* **c** — Add selected track to queue
* **Ctrl+S** — Toggle shuffle
* **- / +** — Volume down / up (±5%)
* **m** — Mute / Unmute
* **Ctrl+← / Ctrl+→** — Seek (configurable seconds)

### Library & Playlist Management

* **f** — Like / Unlike track
* **Ctrl+A** — Add to playlist / Select all (multi-review)
* **Ctrl+C** — Confirm multi-review add
* **Ctrl+T** — Import playlist (paste URI)
* **Ctrl+D** — Delete playlist or remove item
* **Ctrl+R** — Refresh
* **Ctrl+Q** — Quit

### Multi-Add / Multi-Review

* **Ctrl+L** — Toggle multi-add/review
* **Enter** — Select item and move down
* **Ctrl+Enter** — Choose playlist for add

### Navigation

* **↑ / ↓** — Navigate
* **←** — Return focus to left column
* **→** — Open item / focus first right widget
* **/** — Focus search bar
* **?** — Help

### Expand / Collapse Panels

* **Ctrl+Shift+→** — Expand Library / Playlists
* **Ctrl+Shift+←** — Collapse Library / Playlists

### Search Prefixes

* `/ART query` — artists
* `/ALB query` — albums
* `/TRK query` — tracks
* `/PLY query` — playlists

### Lyrics & Devices

* **l** — Toggle lyrics
* **d** — Device manager
* **<** — Seek settings

---

## Configuration & Cache 📂

Default location:

```
Documents/naarvent's projects/Spotify_TUI/
```

Files:

* `spt_config.json` — saved credentials
* `.cache_spotify_token` — OAuth token
* `spt_py_textual_spotify.log` — log file

---

## Troubleshooting & Notes 📝

* Use **Ctrl+R** to reload playlists or fix stale content.
* Lyrics fall back gracefully if synced versions aren't found.
* Device switching requires an active Spotify client.
* Logs stored in `spt_py_textual_spotify.log` for debugging.

---

## License 📄

This project is licensed under the **MIT License**. You are free to use, modify, distribute, and contribute.
