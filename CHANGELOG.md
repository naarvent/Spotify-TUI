# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.0.2] - 2026-11-09

### Added

- Windows: the mpv player backend (YouTube Music and Subsonic playback) now talks to mpv through a named pipe, so those providers can play audio on Windows instead of showing "Playback is disabled".
- Windows installer bundles `mpv.exe` and `yt-dlp.exe` (pinned versions) and the Python parts of the YouTube Music / Subsonic providers (`ytmusicapi`, `yt-dlp`, `keyring`); nothing else needs installing.

### Fixed

- A track no longer starts, stops and restarts from the beginning when you press Enter: one Enter reached the play action twice and the second call reloaded the queue.
- `mpv.exe` is used on Windows instead of the `mpv.com` console wrapper, so closing the app no longer leaves mpv playing.

### Changed

- Upgrading with the installer replaces the previous install completely (stale files from older versions are removed) and closes a running `spt` instead of failing.

## [v1.0.1] - 2026-11-08

### Fixed

- Missing Spotify credentials now show a clear message instead of 401 errors, and the first-run form appears if the config file has none.
- `AttributeError: '_bar_last_track'` in the Now Playing bar when nothing had been played yet.

### Added

- Settings > Lyrics has an **API key** field for Spicy Lyrics (hidden input, stored in the system keyring, never in `spt_config.json`).

### Tests

- New tests for the Spicy Lyrics key field, the missing-credentials message and the Now Playing default.

## [v1.0.0] - 2026-11-07

First stable release: multi-provider (Spotify, YouTube Music, Subsonic/Navidrome), local library, offline downloads, radio, artist pages, karaoke lyrics with Spicy Lyrics word timings, audio visualizer.

### Changed

- Now Playing: the progress bar no longer has `[ ]` around it; Repeat is `↻` (`↻¹` for one track) and Shuffle `⇄`, bold when active and dim when off (before, Repeat off and Shuffle off shared the same `↪`).

### Fixed

- Saving a setting right after entering the Spotify credentials no longer wipes `client_id`, `client_secret` and `redirect_uri` from `spt_config.json` (the form now updates the in-memory config, and saving never drops credentials already on disk). Reported in #9.

### Added

- Create Playlist only asks what the provider supports (`playlist_visibility`, `playlist_description` capabilities): Spotify = public + collaborative + description, YouTube Music = description, Subsonic = name only.
- Settings > **Visualizer default mode** (`visualizer_mode`: `mirrored` (default) or `bars`); Shift+M still toggles it per session.
- **Spicy Lyrics as a lyrics source** (`spt_tui/spicy_lyrics.py`, optional, own key from the keyring or `SPT_SPICY_LYRICS_KEY`):
  real syllable times for karaoke, line times, duets and background vocals. Order: your files > Spicy Lyrics > LRCLIB; without a key
  it does nothing. Spotify ids go straight to the API; other providers are matched with its search (only an `exact` result with
  a matching duration is trusted). Cache in `<data>/lyrics/spicy_cache/` with a hard 30-day limit, own request pacing, errors fall
  back to LRCLIB. Settings > Lyrics has an on/off list and shows whether a key was found (never its value).
- The lyrics view now shows where the lyrics came from (provider; for a community sync also uploader and maker with links) under
  the lines, in line mode and karaoke (also for LRCLIB and your own files). The key must be regenerated since it went through a chat; it
  is not stored in the repo.
- Track lists wrap around: Up on the first row jumps to the last, Down on the last row jumps to the first (`WrapTable`).
- Settings > **Visualizer FPS** (`visualizer_fps`, 10-60, default 30), with a one-line help; Enter saves it and a running
  visualizer picks it up at once (cava is restarted with the new framerate). The lower bound moved from 5 to 10.

### Changed

- Help and the "Add to playlist" picker now use the playlist frame like Queue and Devices: the title sits on the panel's
  border and the content fills it (no heading or box nested inside).
- Removed the dead view stack (`_view_stack` and the call to the non-existent `_restore_view` in `_back_one_level`).

### Fixed

- Esc while a search is running left the Search bar on "searching…" for good. Esc now cancels the search first (the late
  results are discarded, the bar goes back to rest and the panel says "Search cancelled"); the next Esc goes to the menu.
  The spinner also stops by itself if anything else clears the panel.
- With the Visualizer open and the focus in a sidebar list, Enter on a playlist/library entry did nothing (the
  Visualizer has no focusable widget and sets the "in a view" level, which `action_open` ignored). It now opens the item.
- Karaoke and Visualizer: the big ASCII title/artist header was off-centre (about 11 columns to the right) and its rows
  did not line up (Rich's `justify="center"` strips each row's trailing spaces and re-centres it). Each figlet block is now a
  rectangle centred as a unit on the usable width, title and artist on the same axis; one shared helper (`_header_rows`).
- Local player (librespot) first-time authorisation: the link now opens with the shared opener (`open_command` /
  `SPT_OPEN_COMMAND`, default `abrir`, then the system browser), the status line says "Authorize SPT-TUI Local in
  your browser… Waiting" until it is done (or why the browser could not be opened, with the link), and a clear
  message on timeout. Choosing "SPT-TUI Local (start)" in Devices no longer sends that placeholder id to Spotify
  (the "404 Device not found"), and Enter there acts once.
- Tests never start a real librespot or open a browser (`SPT_LOCAL_PLAYER=0`, `SPT_OPEN_COMMAND=true` in the runner).
- Esc ran its action twice (from the key handler AND the `escape` binding), so an Esc that cancels something
  (Ctrl+D on a track, a running Spotify import) also went back to the menu. Now one Esc = one action, and
  Multi-Add, the "/" filter, the big-album add confirmation and the delete-playlist prompt are each cancelled
  by the first Esc (the view stays); the next Esc goes to the menu.
- Library > Downloaded (and Radio) now paint the hearts of liked songs (one batched lookup; from memory with
  YouTube Music's local library); they follow `s`/`S`, the `/` filter and `f`. Without likes the ♥ header is
  dimmed and nothing is looked up.
- Karaoke: the title and status rows could move as the lyrics advanced. The frame is now exactly the box
  height (one row per line, no wrapping), the current line stays on the middle row, long lines take at most
  2 rows with `…`; the wipe has a soft truecolor edge.
- Offline copies were stored with `duration_ms: 0` when downloaded from a list row (Library > Downloaded
  showed `0:00`): the duration now comes from the row, the provider's track, yt-dlp or ffprobe, and old
  copies are filled in in the background.
- The queue said `Source: Playlist` for songs played from Downloaded, Radio or the artist page; it now
  says Downloaded / Radio / Artist with the right "Next from".
- Multi-Add: any key (e.g. `s`) toggled the highlighted row; only Enter does now.
- A 1.2 s search-bar timer could fire while the app was closing and raise `ScreenStackError` (seen as random UI-suite crashes under parallel load).
- Artist page: the playlist search by the artist's name used `limit=50`, which development-mode Spotify apps now reject (search is capped at 10 since Feb 2026); it uses 10.
- The first entry of Playlists (and Library) is highlighted as soon as you enter the list; it used to look unselected until you moved down and back up.
- Liking / following / saving an album, artist or playlist from the combined-search grid no longer empties its panel (`_repaint_rows_from_model` only knew the Songs panel).

### Added

- Visualizer (`v`, `spt_tui/visualizer.py`, `app/visualizer_view.py`): `cava` bars from the real system audio,
  accent gradient, the lyrics view's song header (fixed position), Shift+M mirror; cava is SIGSTOPped while nothing plays, killed when the view
  closes, another view opens or spt exits (also if spt is killed: `PR_SET_PDEATHSIG`). `visualizer_fps` (30).
- Devices view (`d`) in the same full-panel frame as a playlist (`Devices · N`), with `/` filter and the
  cursor kept across refreshes.
- Queue view like a playlist: full panel, frame title, `↓`/`♥`, `/` filter, sections as header rows; mpv
  providers list up to `queue_context_limit` (300) songs; Spotify's short `/me/player/queue` is followed
  by the rest of the playlist/album, marked "Deduced" (never with shuffle on).
- Library > Downloaded can be sorted with `s` / `S` (also by Date added and Size; own remembered order;
  unknown durations last) and has an Added column.
- Settings > Appearance: hide the title bar (`show_header`), live.
- A 1-cell download column before the heart in every song list and for every provider (`↓` / `⇣` / `·`);
  dimmed with Spotify, where `o`/`O` explain that downloading is not possible (no API, DRM; pending).
- Karaoke lyrics (`k` in the lyrics view, `spt_tui/karaoke.py`): word-by-word highlight (approximate from line
  times, marked "≈ approximate"; real with enhanced LRC or your own TTML/LRC files in `<data>/lyrics/`), fading
  by distance, `● ● ●` interludes, TTML background vocals / duets / romanisation, live offset `[` `]` `{` `}`.
  The line view stays the default and unchanged (it now shows enhanced-LRC lines without their `<mm:ss>` tags).
  Ticks at 10 Hz only while playing in karaoke, unchanged frames skipped, no layout pass per frame; the figlet
  title is memoised (~2 ms per tick saved in both modes).
- Offline downloads for YouTube Music and Subsonic (`spt_tui/offline.py`, `spt_tui/app/offline.py`): `o` downloads
  or deletes the highlighted song, `O` the whole open list (filtered rows only with `/`), `↓` mark next to the heart,
  Library > Downloaded. Files in `<data>/offline/<provider>/` with `index.json`; cap `offline_max_bytes` (Settings,
  default 1 GB) with least-recently-used pruning. A downloaded song plays from the local file (no stream URL, no
  yt-dlp, no server request); its metadata is kept so it plays after a restart without network. YouTube Music via
  yt-dlp (best audio, no re-encode); Subsonic `download` with a fallback to `stream`.
- Radio (`R`): tracks like the highlighted track, artist or playlist (or the one playing), as a "Radio: …"
  list that plays from the chosen row. `Provider.radio` + `can_radio`: YouTube Music `get_watch_playlist`,
  Subsonic `getSimilarSongs`/`getSimilarSongs2`, Spotify `/recommendations` (retired for development-mode
  apps: explained on the status line, not retried). `Ctrl+R` reloads it.
- Artist page: Enter on an artist shows its top tracks, albums, singles/EPs and related artists in one list
  (Type column `TOP`/`ALB`/`SNG`/`REL`), with per-section counts in the title. New provider API
  `artist_overview` / `artist_top_tracks` / `related_artists` and capabilities `can_artist_top_tracks` /
  `can_related_artists`. YouTube Music: one `get_artist` call (+ `get_artist_albums` for the full discography).
  Subsonic: `getTopSongs`, `getArtistInfo2`, OpenSubsonic `releaseTypes` (singles/EPs); `artist_albums(single)`
  now returns them. Spotify: top tracks / related are tried once and, on 403/404 (Spotify removed them for
  development-mode apps), the page says so and does not retry in that session. Missing sections never blank
  the page; the status line explains.
- `/` on an open list (playlist, album, Liked Songs, search results, artist view) filters the rows already
  loaded by title/artist/album (all words, case- and accent-insensitive) without a new search. `Enter`/`↓`
  keep it, `Esc` clears it. Works with the `s`/`S` sort; a filtered row plays from its real position, and
  `f`, `Ctrl+D`, Multi-Add address the highlighted track. Elsewhere `/` still focuses Search.
- Welcome screen: "Spotify" and "TUI" in the same figlet font (graffiti) with `by naarvent_ :)` centred under them, and
  the whole text block centred against the headphones. It is now picked from the panel's REAL size (large /
  medium / compact / small / minimal) at start and on every resize, so a window that is not fullscreen no
  longer breaks the art; every line is padded to one width so the centred art keeps its shape.
- Search bar: while you type it gets a heavy accent frame and a hint (`⏎ search · esc leave`); on Enter the
  hint spins (`⠋ searching…`) until the results are up, then a green `✓`. Help box is narrower, the
  title bar lost its icon, and the key hints bar uses the accent colour and can be hidden in Settings >
  Appearance (`show_key_hints`).
- Settings > Appearance: choose the **colour** of frames, titles and selection from 9 presets
  (teal stays the default) or type any hex (`#ff7eb6`). The whole palette is derived from that one
  colour (`spt_tui/theming.py`), applies at once and is remembered (`accent_color`).
- Settings > Appearance: **Transparent** background. The terminal's own background (opacity, blur,
  wallpaper) shows through and only the frames, the text and the selection stay painted; it
  applies at once and is remembered (`transparent_background`). **Opaque** keeps the `spt-teal`
  theme. Enter on a song in the Queue view now plays it (YouTube Music / Subsonic).
- `spt_tui/scraper/`: anonymous reader of public Spotify metadata (track, playlist; no
  credentials, no Premium), a small maintained port of SpotifyScraper (MIT, see
  `spt_tui/scraper/NOTICE.md`): anonymous token from the embed page and persisted GraphQL
  queries. Library > Import Playlists with YouTube Music now also accepts a Spotify playlist link
  (or ID): it is copied into a local playlist, each song matched on YouTube Music. The screen
  explains what each provider accepts (YouTube link kept on YouTube; Spotify link copied).
- Stream prefetch for YouTube Music (`spt_tui/stream_cache.py`): the direct audio URL of the
  next 3 queued tracks is resolved ahead (2 workers, 20-entry cache, expires before the URL
  does), so skipping starts in ~0.4 s instead of ~2 s. A failing prefetched URL falls back to the
  normal one without marking the track unavailable.
- Import report: Liked Songs entries say how many songs were really new (`added_new`,
  `merged_or_already_there`).

- Provider layer (`spt_tui/providers/`): an abstract `Provider` with search,
  library/playlists, playback and queue operations plus `Capabilities`
  (`can_play`, `can_control_playback`, `can_seek`, `can_set_volume`,
  `can_control_queue`, `can_like`, `can_edit_playlists`, `can_list_devices`...).
  The UI talks to the active provider and gates unsupported actions with a
  status-line notice. Chosen with `SPT_PROVIDER`, `"provider"` in
  `spt_config.json`, or the provider picker; an unusable provider falls back
  to Spotify with a notice at startup.
- Spotify Free degraded mode: `product != premium` (or a `PREMIUM_REQUIRED`
  answer) disables play/pause/seek/volume/queue/devices/librespot with a clear
  notice; search, library, likes and playlists keep working.
- `subsonic` provider (Subsonic / OpenSubsonic, Navidrome): token+salt auth,
  `search3`, playlists, `star`/`unstar`, `scrobble`, `stream` playback. The
  password is stored in the system keyring (`keyring` or `secret-tool`) or read
  from `SPT_SUBSONIC_PASSWORD`, never written to `spt_config.json`.
- `ytmusic` provider: anonymous `ytmusicapi` search and public playlists
  (imported through Import Playlists), audio through `mpv` + `yt-dlp`.
- Provider picker: `P` (Shift+P) from any view, and a selectable list in
  Settings, show every provider with its readiness (available / missing mpv,
  ytmusicapi, configuration...).
- Live provider switch (no restart): the new provider is created first (on
  failure the current one stays, with a notice), then the old one's mpv /
  sockets / librespot are released, caches, queue, views and playback state
  are reset and the active view is reloaded; a failure after that rolls back
  to a fresh instance of the previous provider. The choice is saved to
  `spt_config.json`, except while `SPT_PROVIDER` is set (session-only switch,
  the picker warns). Covered by `tests/test_provider_switch.py`.
- Local library (`spt_tui/local_library.py`, `local_library_<provider>.json`)
  for account-less providers (YouTube Music): `f` likes songs and saves
  albums/singles and artists (Liked Songs / Saved Albums / Saved Artists with
  hearts), `f` on a playlist result follows it into the sidebar (and `f` there
  drops it), Create Playlist makes local playlists (`local:<uuid>`) that can be
  renamed (`Ctrl+E`), deleted, played and edited (Add to Playlist, `Ctrl+D`),
  and Recently Played is a local history (50). Entries keep minimal metadata
  so they list offline; anything that vanishes upstream (removed/private video,
  404 playlist, no network) is marked unavailable and skipped with a notice,
  never deleted. Subsonic stars albums and artists on the server and keeps its
  play history locally. Spotify is unchanged.
- Import from Spotify (YouTube Music): Library entry and `P` picker entry
  when Spotify credentials are configured. Reads the user's Spotify playlists,
  Liked Songs, saved albums and followed artists read-only (official API, own
  token, a wrapper refusing writes), matches every track by ytmusicapi search
  (normalised title, artist, duration ±3 s, album; laxer second search),
  imports playlists as local playlists, likes/albums/artists into the local
  library, marks doubtful matches, caches matches (resume / retry), runs in
  the background with progress, rate limiting and Esc to cancel, and writes
  `import_report.json`. `tests/test_spotify_import.py` (offline) and the
  optional online live check script.
- Shared `mpv` player (`spt_tui/players/mpv.py`) over JSON IPC: queue, pause,
  seek, volume, repeat, position/duration, subprocess cleanup on exit.
- Optional extras in `pyproject.toml`: `[subsonic]` (keyring) and `[ytmusic]`
  (ytmusicapi, yt-dlp).
- Offline suites: `test_providers_base.py`, `test_spotify_free_mode.py`,
  `test_mpv_player.py`, `test_subsonic_provider.py`, `test_ytmusic_provider.py`,
  `test_secret_store.py`, `test_provider_ui.py`,
  `test_provider_switch.py`, `test_local_library.py`,
  `test_ytmusic_library.py` (recorded ytmusicapi responses in
  `tests/fixtures/ytmusic_recorded.json`), `test_local_library_ui.py`,
  `test_view_focus.py`, `test_spotify_import.py`.

### Fixed

- Add to queue (mpv-backed providers) put the song at the END of the playlist, so `next` skipped it.
  It now plays right after the current track, after earlier queued songs, and stays there across
  shuffle on/off. A queued song that already played leaves the queue (and mpv's playlist).
- Queue view with YouTube Music showed a stale local list (one row, already played). It now has two
  parts like Spotify, "Next in queue" (manual, Source Queue) and "Next from <context>" (the next ~20),
  follows mpv's real order (shuffle, repeat), handles the same song twice in the queue, and refreshes
  on every track change. Reopening Devices after a view change no longer leaves the panel empty.
- Repainting a table with the same id right after clearing the right panel could leave an unstarted
  duplicate widget (Textual `DuplicateIds`) that stalled the UI; views now mount after the old one is gone.
- Settings (`<`): Down on the last provider wrapped to the first and never reached the Subsonic
  fields (Up on the first wrapped to the last). Arrows now leave lists at their edges
  (`app/form_lists.py`); Tab and Up/Down also reach the "Import selected" button.
- A burst of failures (network, yt-dlp) marked dozens of songs "unavailable" and skipped them
  afterwards. Marks now expire after 24 h, a streak of 3 failures marks nothing, network errors
  never mark, and the repeated "Skipped N unavailable track(s)" notice shows once.
- Import from Spotify aborted on one slow reply (spotipy's 5 s default timeout): 25 s timeout and
  per-page retries for timeouts and dropped connections.

- Switching provider pauses what the outgoing one is playing (a Spotify device
  kept playing), warns when it cannot (Free / no device), resets the Now
  Playing bar and stops polling the old provider.
- No `AttributeError` from saved-state checks on non-Spotify providers
  (`current_user_saved_albums_contains` and the rest of the spotipy names the
  UI calls now exist on every provider with safe defaults).
- Concurrent writes of `playlists_cache_*.json`, the lyrics cache and
  `spt_config.json` no longer share one `.tmp` file (`FileNotFoundError`):
  unique temp file per write, config writes serialised.
- Now Playing: the Repeat / Shuffle indicators sit flush with the right edge
  of the progress bar at any width; long titles are cut with "…" (measured in
  terminal cells). With mpv the bar no longer freezes: a skip unpauses, and the
  loading phase (no duration yet) is reported as not playing.
- YouTube Music: albums/singles of an artist page carry the artist; channel
  uploads show the artist from the title instead of the channel, "- Topic"
  suffixes are dropped; mpv never expands a URL into a playlist or autoplays.
- YouTube Music: `f` on an artist said "Podcasts and followed artists is not
  available": Saved Artists was gated on `can_podcasts`. Following artists is
  now its own capability (`can_follow_artists`) and works through the local
  library; the podcast notice no longer mentions artists. `f` on an album or
  artist in the search grid now fills its heart at once (any provider).
- Saved Podcasts / Saved Episodes (and Import Playlists for Subsonic) are
  hidden from the Library menu when the provider has no such thing, also after
  a live switch.
- Focus could get stuck: with Settings (or Import Playlists / Rename / any form
  view) open, moving focus to Playlists, Library, Search or Help left no way
  back. `→` now returns to the field last used in the open view (also from
  Help), Tab offers the form as a stop and walks every field before leaving,
  and `↑`/`↓` reach the provider list and the Subsonic fields. The provider
  picker stays modal (Esc closes). Covered by `tests/test_view_focus.py`.

### Changed (UI)

- Settings redesigned: title and subtitle, sections (Playback, Lyrics, Music
  provider, Subsonic / Navidrome), aligned labels with a grey help line each,
  a key-hint bar, scrolling, and a stacked layout on narrow terminals. Same
  fields, keys and behaviour.
- Provider picker redesigned: titled rounded window, ● / ○ and an ACTIVE badge
  for the active provider, status in colour with its reason, separators and a
  key-hint line (↑↓ choose · Enter apply · Esc close).
- The app's teal palette is registered as a Textual theme (`spt-teal`) so the
  new screens (and Textual's own widgets) use theme variables in the same
  colours.

### Changed

- `spt_tui/spotify_client.py` moved to `spt_tui/providers/spotify.py` as
  `SpotifyProvider`; `spt_tui.spotify_client.SpotifyClient` still works (same
  class).

## [v0.2.3] - 2026-11-06

### Added

- Sort the open playlist's track table with `s` (Default → Title → Artist →
  Album → Duration → Date added) and reverse it with `S`. It is view-only: the
  playlist on Spotify is never reordered. The criterion and direction show in
  the table title and are saved to `spt_config.json` (`playlist_sort`,
  `playlist_sort_desc`; missing or invalid values fall back to the playlist
  order). Pages that stream in are merged into the sorted view, keeping the
  highlighted track. Artist sorts by the first artist, case-insensitively, with
  album and title as tie-breaks. Covered by `tests/test_playlist_sort.py`.

### Fixed

- Playing a track from a playlist starts the playlist context at the track's
  real playlist position instead of its table row index (they differ once the
  view is sorted, and when the playlist has unavailable entries).
- `Ctrl+D` on a playlist track removes only the selected occurrence of a track
  that appears more than once. If the position is stale (the playlist changed
  elsewhere), the playlist is re-read and the closest matching occurrence is
  removed; a failed removal never falls back to removing every occurrence.
- Sorting a playlist follows the highlighted track instead of jumping to the
  top, and streamed pages of a sorted playlist are added to the model and
  repainted once at the end instead of re-sorting the whole table per page.
- The queue `Source` column shows a readable label chosen from the view the
  track was queued from (Playlist, Liked Songs, Recently Played, Album, ...)
  instead of the internal widget id.
- Opening the queue twice, or holding `Ctrl+C`, no longer crashes with
  `DuplicateIds`. With the queue already open, `Ctrl+C` focuses it and refreshes
  it with at most one request in flight (extra presses are coalesced), and a
  queue mount that finishes after another view took over no longer blanks it.
- The lyrics view and Settings no longer crash with `DuplicateIds` when opened
  with another view, and `Up` / `Down` move between the Settings fields again
  (the focused field is matched by identity, not by an id that is now `None`).
- Lyrics stay in sync: pausing freezes the position instead of losing what
  played since the last sync, a playback sync that started before a
  seek/pause/skip is discarded instead of pulling the lyrics back, and the
  position is anchored to the middle of the playback request so network latency
  no longer delays the lyrics. Plain (unsynced) lyrics are kept as a last
  resort while a synced version is still searched for, and the LRC `[offset:]`
  tag is applied (metadata tags such as `[ar:]` are no longer shown as lyric
  lines).
- Toggling a favourite resolves the row by track id when it paints, so the
  heart no longer lands on another track if the table was re-sorted while the
  request ran.

## [RELEASED]

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
