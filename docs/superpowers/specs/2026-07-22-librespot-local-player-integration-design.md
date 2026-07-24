# Design — librespot local player integration (Sub-project A)

Date: 2026-07-22
Status: Approved for planning
Scope: Integration layer only. Distribution (CI build of the librespot binary, Windows installer, `spt` on PATH) is **Sub-project B**, a separate spec.

## 1. Problem & goal

Today `spt_tui` is a *controller*: it drives playback through the Spotify Web API,
which never delivers audio. It therefore needs a separate Spotify Connect device
already running (official app, phone, web player, speaker). See the Obsidian notes
`Limitations` and `Device Control`.

Goal: let SPT-TUI act as its **own** player by managing a local
[librespot](https://github.com/librespot-org/librespot) process. librespot runs as
a Spotify Connect device; once it is running and authenticated it appears in
`SpotifyClient.devices()` like any other device, and all existing control code
(play/pause/seek/volume/queue/transfer) drives it unchanged. The user never needs
the official Spotify app open.

Spotify **Premium is still required** — librespot cannot bypass it. This constraint
is unchanged from today.

### Success criteria

- With a librespot binary present, SPT-TUI can start it, authenticate it once, and
  play audio locally without any other Spotify client running.
- The local device shows up in the Devices view and is selectable like any other.
- On launch the local player starts **politely**: it never steals an active playback
  session on another device.
- With no binary present, the app behaves exactly as it does today (control-only),
  never crashing.

### Non-goals (this sub-project)

- Shipping/downloading the binary. That is Sub-project B.
- The `spt` command / PATH entry. Sub-project B.
- Audio-quality UI (bitrate selector), normalisation, cache tuning. Deferred (YAGNI).
- Replacing the Web API control path with librespot's own event API. We keep driving
  via Spotify Connect; librespot is just the audio sink.

## 2. Authentication decision (why librespot's own OAuth)

Reusing SPT-TUI's existing Web API token does **not** work:

1. **Scope.** `config.SCOPE` does not include `streaming`; librespot requires an
   access token with the `streaming` scope to play audio.
2. **Client identity.** SPT-TUI's token is minted with the user's own Developer app
   `client_id`. librespot streaming is only reliable with Spotify's own "keymaster"
   client. Third-party `client_id` streaming support is recent and fragile
   (librespot PR #1385, issue #1331).
3. Web API access tokens expire in ~1h, which would require feeding refreshed tokens
   into librespot continuously.

Decision: librespot performs its **own** OAuth (PKCE, keymaster client, `streaming`
scope) **once**, caches credentials, then runs headless forever. This is a separate
credential from the Web API token and is documented as such. It reaches the same
practical UX as "one extra login on first run" but on the known-good streaming path,
and it fully satisfies the "never need the official app" goal.

## 3. Architecture

One new, isolated module: `spt_tui/local_player.py`, class `LocalPlayer`. Single
responsibility: own the librespot binary discovery, its OAuth, its process
lifecycle, and the polite auto-start decision. It does **not** touch
`SpotifyClient` or the devices/control code — librespot appears in `devices()`
automatically once running.

Integration points (small, additive):

| File | Hook | Change |
| --- | --- | --- |
| `spt_tui/app/core.py` | `CoreMixin.__init__` (~line 34) | `self.local_player = LocalPlayer(self.spotify)` |
| `spt_tui/app/core.py` | `on_mount` (~line 176) | start `local_player.maybe_autostart()` on a daemon thread (never the UI thread) |
| `spt_tui/app/core.py` | `on_unmount` (~line 308) | call `local_player.stop()` alongside `_stop_all_intervals()` |
| `spt_tui/app/core.py` | `on_data_table_row_selected`, `devices_table` branch (~line 1190) | if the selected row is the synthetic "start local" entry, spawn/auth first, then transfer |
| `spt_tui/app/queue_devices.py` | `_populate_devices_table` (~line 495) | inject the synthetic "SPT-TUI Local (start)" row when the local player is available but not yet running |
| `spt_tui/config.py` | credentials/config block | add the four config fields (below) |

`LocalPlayer` runs all blocking work (process spawn, OAuth wait, `get_playback`
poll) on its own threads and never calls into the Textual widget tree directly; the
existing worker/`call_from_thread` conventions (Obsidian `UI Thread Rules`,
`Workers`) are preserved. Status feedback goes through the existing `_notify`.

## 4. `LocalPlayer` public API

```
class LocalPlayer:
    def __init__(self, spotify: SpotifyClient): ...

    # True when a usable binary was found (config/bundled/PATH) and the feature
    # is enabled. False => the app runs exactly as today.
    def is_available(self) -> bool: ...

    # True when the librespot process is running and registered as a device.
    def is_running(self) -> bool: ...

    # Called on startup (daemon thread). Discovers the binary; if credentials are
    # cached, spawns librespot and applies the polite-start rule. If no
    # credentials exist, does NOT spawn (avoids a browser popup every launch) —
    # the device is offered instead and started on first explicit selection.
    def maybe_autostart(self) -> None: ...

    # Called when the user selects the synthetic "start local" device row.
    # Ensures the binary is running, runs OAuth if there are no cached
    # credentials, then transfers playback to it. Returns the device id or None.
    def start_and_activate(self) -> Optional[str]: ...

    # Terminate the process (idempotent; safe during teardown).
    def stop(self) -> None: ...

    # The librespot device name (default "SPT-TUI Local"), so the devices layer
    # can recognise / label it.
    @property
    def device_name(self) -> str: ...
```

## 5. Binary discovery

Resolution order, first hit wins:

1. `config` override `librespot_path` (absolute path to the binary).
2. Bundled path: `librespot[.exe]` next to the app, or in `CACHE_DIR/bin/`
   (Sub-project B's installer places it here).
3. `PATH` lookup (`shutil.which`).

If none found → `is_available()` is False, `maybe_autostart()` is a no-op, the app
is control-only. A single non-blocking status line explains it once
("Local player unavailable — no librespot binary found"); the app never crashes.

## 6. Authentication flow (first run)

Driven the same way SPT-TUI already drives its own OAuth (`prepare_authorize_url` /
`finish_authorization` / `_open_url_in_browser` in `core.py`):

1. Spawn librespot in OAuth mode (PKCE, keymaster client, `streaming` scope), with a
   loopback redirect on `127.0.0.1`.
2. `_open_url_in_browser(auth_url)` opens the consent page.
3. Capture the redirect on the loopback port; hand the code back to librespot, which
   writes its credentials blob into `CACHE_DIR` (e.g. `librespot_credentials.json`).
4. Subsequent runs read that blob and start **headless** — no browser, no official
   app, ever.

Credential storage lives under `CACHE_DIR` (respects `SPT_TUI_CACHE_DIR`, so the
test suites' temp dirs isolate it just like the Spotify token).

## 7. Polite start-up (never steal playback)

`maybe_autostart()` on a daemon thread:

1. Discover binary. None → disabled, done.
2. No cached librespot credentials → **do not spawn** (would pop a browser every
   launch). The device is still offered as the synthetic "start" row; OAuth happens
   only on explicit selection.
3. Credentials present → spawn librespot headless with `device_name`
   (default "SPT-TUI Local"). It registers as a Connect device.
4. Wait until the device appears in `spotify.devices()` (bounded poll, e.g. up to
   ~10s), then read `spotify.get_playback()`:
   - `is_playing` is True on **another** device → **do not transfer**. librespot
     stays listed and available; the foreign session is untouched.
   - Nothing is playing → `spotify.transfer(local_id, force_play=False)` so the local
     device becomes the default target. **`force_play` is never used here.**

The decision table (both branches, plus "device never appeared") is the core unit to
test.

Config `local_player_autostart=false` skips steps 3–4 entirely (device is still
offered for manual start).

## 8. Devices UX

- Once running, the local device is a normal Connect device: it already appears in
  `_populate_devices_table` from `spotify.devices()`, no special-casing needed.
- When available but **not** running (disabled autostart, or no credentials yet),
  `_populate_devices_table` prepends a synthetic row, e.g.
  `▷  SPT-TUI Local (start)  local`, mapped to a sentinel device id.
- In `on_data_table_row_selected`'s `devices_table` branch, a sentinel selection
  routes to `local_player.start_and_activate()` (spawn + OAuth-if-needed + transfer)
  on a daemon thread, instead of the normal `spotify.transfer(dev_id, force_play=True)`.
- Switching to another device leaves librespot running and available
  (`stop_local_on_switch=false` default). It is a valid Connect target to switch back
  to.

## 9. Configuration

New fields, same mechanism as existing settings (`config.LOCAL_CFG` +
`SPOTIPY_*`-style env precedence in `config.py`). Kept minimal:

| Key (config.json) | Env | Default | Meaning |
| --- | --- | --- | --- |
| `local_player_enabled` | `SPT_LOCAL_PLAYER` | `true` | Master switch for the whole feature |
| `librespot_path` | `SPT_LIBRESPOT_PATH` | `null` | Explicit binary path override |
| `local_player_autostart` | `SPT_LOCAL_AUTOSTART` | `true` | Polite auto-start on launch |
| `local_player_name` | `SPT_LOCAL_NAME` | `"SPT-TUI Local"` | Connect device name |

Bitrate/quality and `stop_local_on_switch` are intentionally deferred; the latter is
fixed to "keep running" for now.

## 10. Error handling & edge cases

- **Binary missing / spawn fails / OAuth fails** → log, one-line `_notify`, degrade to
  control-only. The TUI never crashes.
- **librespot dies mid-session** → detect via process exit and/or its disappearance
  from `devices()`; show a hint; optionally respawn once (single retry, no loop).
- **Premium required** → librespot needs Premium too. Surface a clear message if a
  stream fails to start on the local device.
- **Teardown** → `on_unmount` sets `_closing` and calls `stop()`, which terminates the
  child process (graceful terminate, then kill on timeout). No orphaned process. Fits
  the existing teardown pattern (Obsidian `Teardown`).
- **Thread safety** → all `LocalPlayer` blocking work is off the UI thread; results
  reach the UI only through `_notify` / existing `call_from_thread` paths.

## 11. Testing

Mirrors the existing strategy (Obsidian `Mocking`, `Testing Strategy`): no live API,
no real audio. librespot is replaced by a fake subprocess/command.

- **Binary discovery** — resolution order (config > bundled > PATH); "none found" ⇒
  `is_available()` False and `maybe_autostart()` no-op.
- **Polite-start decision table** — mock `get_playback()`:
  (a) foreign device playing ⇒ **no** transfer; (b) nothing playing ⇒ transfer with
  `force_play=False`; (c) device never appears ⇒ bounded give-up, no crash.
- **Autostart credential gate** — no cached credentials ⇒ no spawn; credentials
  present ⇒ spawn.
- **OAuth drive** — the loopback-capture → credentials-written path, with librespot
  faked.
- **Lifecycle/teardown** — spawn args are correct; `stop()` terminates the fake
  process and is idempotent.
- **Config** — defaults, env precedence, `local_player_enabled=false` disables
  everything.

No test requires a real Spotify stream or network.

## 12. Risks

- **Protocol drift**: librespot is reverse-engineered and can break on Spotify
  changes — same class of risk as the existing Web API drift note (Obsidian
  `Limitations`). Mitigation: Sub-project B pins a known-good librespot version.
- **Streaming client identity**: handled by using librespot's keymaster OAuth, not
  the user's Developer app. Low.
- **Windows audio**: confirmed working — librespot's default `rodio` backend uses
  WASAPI via cpal (librespot COMPILING.md / Audio Backends wiki). Low.
- **Premium**: unchanged constraint, clearly surfaced.

## 13. Handoff to Sub-project B

B inherits the binary path contract from §5: the installer must place
`librespot[.exe]` at `CACHE_DIR/bin/` (or beside the app), which discovery already
searches. B also owns: CI cross-build of `librespot.exe` (windows-amd64, pinned
version, hash-verified — upstream ships no Windows binary), the Inno Setup installer
bundling the frozen app + deps + binary, adding the install dir to PATH, and the
`spt` launch command (`spt = spt_tui.__main__:main` in `[project.scripts]` for pip,
plus a frozen `spt.exe` shim).
