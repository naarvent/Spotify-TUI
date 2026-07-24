# Design — Distribution: Windows installer (Sub-project B)

Date: 2026-07-23
Status: Approved for planning
Scope: Packaging and distribution only. The runtime behaviour of the local player
(discovery, OAuth, lifecycle, polite start) is Sub-project A, already shipped; B
does not change it beyond one additive binary-discovery location.

## 1. Problem & goal

Sub-project A made SPT-TUI able to run its own `librespot` process, but the user
must obtain `librespot` themselves — and there is **no official Windows binary**
(librespot-org is source-only), so today that means installing Rust and running
`cargo install librespot --locked`. That is too much friction for a normal user.

Goal: a single **self-contained Windows installer**, built entirely by CI, that
installs SPT-TUI (frozen, no Python required) together with a from-source-built
`librespot.exe`, puts `spt` on the PATH, and adds a Start Menu shortcut. Publishing
a new version is a single `git tag` push; end users update by re-running the newer
installer over the old install.

### Success criteria

- A user with no Python, no Rust, and no Spotify app can download one `.exe`,
  install it, open a new terminal, type `spt`, and reach a working app.
- From the installed app, the local player works end-to-end (the same manual
  end-to-end that already passed from source).
- `librespot.exe` in the package is built from source, a pinned version, with its
  SHA256 recorded — no unvetted third-party binary.
- Cutting a release is `git tag vX.Y.Z && git push` and nothing else; the installer
  appears on the GitHub Release automatically.
- Re-running a newer installer upgrades in place, preserving the user's PATH,
  shortcuts, and data (including librespot credentials).

### Non-goals

- Code signing (installer ships unsigned; SmartScreen handling is documented).
- Auto-update inside the app.
- macOS / Linux installers (pip install stays the cross-platform path).
- MSI / winget / Chocolatey / Microsoft Store channels.
- Any change to A's OAuth, process lifecycle, or polite-start logic.

## 2. Deliverable

`SPT-TUI-Setup-<version>.exe` plus `checksums.txt` attached to the GitHub Release
for tag `vX.Y.Z`. The installer contains: the PyInstaller **onedir** freeze of
`spt_tui` (as `spt.exe` + its dependencies, including a bundled Python), and
`librespot.exe` beside it.

## 3. Architecture / components

Four units, each independently buildable and testable:

### 3.1 App changes (in `spt_tui`) — the only source edits

Small, additive, unit-testable in the repo's custom harness:

- **Binary discovery "beside the running executable".** `LocalPlayer.binary_path()`
  gains a new location, checked before PATH: the directory of the running
  executable (`os.path.dirname(sys.executable)`; for a PyInstaller onedir build
  that is the install folder, where the installer puts `librespot.exe`). This makes
  the frozen bundle self-locating regardless of PATH state. Final order:
  `SPT_LIBRESPOT_PATH` override → `CACHE_DIR/bin/librespot[.exe]` → **beside the
  executable** → `PATH`.
- **`--version` flag** in `spt_tui/__main__.py`: prints the package version and
  exits 0 without starting the TUI. Enables a headless CI smoke test and is useful
  on its own. All other invocations start the TUI exactly as today.
- **`spt` console entry point:** add `spt = "spt_tui.__main__:main"` to
  `[project.scripts]` in `pyproject.toml` (keeping the existing `spt-tui`) so pip
  users also get `spt`. PyInstaller's output executable is named `spt.exe`.

### 3.2 `spt.spec` — PyInstaller freeze

- **onedir** (a folder), not onefile: faster startup, no per-launch unpack, and it
  gives a natural folder for `librespot.exe` to sit beside `spt.exe`.
- Entry: `spt_tui/__main__.py`, output name `spt`.
- Collect Textual's package data (CSS/theme resources) and any hidden imports
  Textual/spotipy need. The freeze is correct when `spt.exe --version` and a real
  TUI launch both work on a clean machine.
- Console app (the TUI needs a console).

### 3.3 `installer/spt-tui.iss` — Inno Setup script

- Input: the PyInstaller onedir output folder + `librespot.exe`.
- **Per-user install** to `%LOCALAPPDATA%\Programs\SPT-TUI` — no admin, no UAC
  (`PrivilegesRequired=lowest`).
- **Fixed `AppId` (a stable GUID)** so a newer installer upgrades the existing
  install in place instead of creating a second copy.
- Adds the install directory to the **user** `PATH` (and removes it on uninstall).
- Start Menu shortcut "SPT-TUI" → `spt.exe`; optional desktop shortcut (a task,
  unchecked by default).
- Version string injected from the build (see 3.4), so the installer's version and
  filename match the tag.
- Uninstaller removes installed files, the PATH entry, and shortcuts. It does **not**
  touch the user data directory (`~/Documents/naarvent's projects/Spotify_TUI`,
  which holds the token cache and librespot credentials).

### 3.4 `.github/workflows/release.yml` — CI pipeline

Runner: `windows-latest`. Triggers: push of a tag matching `v*`, plus
`workflow_dispatch` for manual/test runs. Steps:

1. **Checkout.**
2. **Build librespot:** `cargo install librespot --version <PINNED> --locked`
   into a staging dir, then copy `librespot.exe` out. Cache the cargo build keyed
   by `<PINNED>` so it only recompiles when the pin changes. Assert
   `librespot.exe --version` runs.
3. **Freeze the app:** set up Python, `pip install .`, `pyinstaller spt.spec`.
4. **Smoke the freeze:** run `dist\spt\spt.exe --version` on the runner and assert
   it prints the expected version and exits 0.
5. **Stage:** copy `librespot.exe` into the onedir folder beside `spt.exe`.
6. **Package:** run Inno Setup `ISCC` with the version derived from the tag →
   `SPT-TUI-Setup-<version>.exe`.
7. **Checksums:** compute SHA256 of the installer and of `librespot.exe` →
   `checksums.txt`.
8. **Publish:** upload the installer and `checksums.txt` to the GitHub Release for
   the tag.

### 3.5 The pinned librespot version

Pin **0.8.0** — validated end-to-end this session (built with `--locked`, runs with
the `rodio`/WASAPI backend, completes OAuth, plays audio). `--locked` is mandatory:
without it a newer transitive dependency (`vergen`/`vergen-lib`) fails to compile.
The pin lives in one place the workflow reads; bumping it is a deliberate edit, and
a failed build fails the release loudly rather than shipping a broken binary.

## 4. Discovery contract (how B connects to A)

The installer places `librespot.exe` in the install directory, beside `spt.exe`.
A's `binary_path()` finds it via the **new "beside the executable"** location
(primary for the frozen case) and, redundantly, via the install dir on `PATH`. The
`SPT_LIBRESPOT_PATH` override still wins for power users. Nothing else in A changes.

## 5. Updating / release flow

**Maintainer, per release:**
1. Merge changes to `main`.
2. Bump `version` in `pyproject.toml` (e.g. `0.2.1` → `0.3.0`).
3. `git tag v0.3.0 && git push origin v0.3.0`.
4. CI builds and attaches `SPT-TUI-Setup-0.3.0.exe` + `checksums.txt` to the
   release (~5–8 min, most of it cached). No manual packaging.

**End user:** download the newer installer, run it. The fixed `AppId` makes Inno
upgrade the existing install in place — files replaced, PATH and shortcuts kept,
user data and librespot credentials untouched. No prior uninstall needed.

**librespot is not rebuilt** unless the pin changes (cargo cache keyed by the pinned
version), so ordinary app updates skip the ~3-minute Rust compile.

**Single source of version truth:** the git tag (and `pyproject.toml`) drive the
installer filename, the Inno version, and `spt --version`, so they always agree.

**Maintainer working from source is unaffected:** development still runs
`python -m spt_tui`; the installer is only the distribution artifact.

## 6. Signing / SmartScreen

The installer ships **unsigned**. On first run Windows SmartScreen shows an
"unknown publisher" warning; the user clicks **More info → Run anyway**. The README
documents this. Code signing (a paid certificate) is a possible future improvement,
out of scope here.

## 7. Install UX summary

- Per-user, `%LOCALAPPDATA%\Programs\SPT-TUI`, no admin/UAC.
- Install dir on the user PATH → `spt` works in any new terminal.
- Start Menu shortcut; optional (default-off) desktop shortcut.
- Clean uninstaller that keeps user data.

## 8. Testing & verification

- **App changes** — unit tests in the repo's custom harness:
  - beside-the-executable discovery: with a fake `sys.executable` dir containing a
    `librespot[.exe]`, `binary_path()` returns it and it ranks above PATH; absent,
    discovery falls through unchanged.
  - `--version`: invoking with `--version` prints the version and exits 0 without
    constructing the app.
- **CI build is its own integration test:** the workflow must build librespot
  (assert `--version`), freeze, smoke `spt.exe --version`, package a non-empty
  installer, and emit checksums. A failure at any step fails the release.
- **Manual acceptance (maintainer, once):** from a `workflow_dispatch` test build (or
  a pre-release tag), download the installer, install on a clean-ish Windows user,
  open a new terminal, run `spt`, and confirm the local-player Devices row plays
  audio end-to-end — the same acceptance already done from source, now from the
  installed package.
- PyInstaller/Inno output cannot be fully unit-tested; the CI smoke plus the manual
  acceptance are the coverage, and this is stated so it is not mistaken for full
  automation.

## 9. Risks

- **PyInstaller + Textual freezing gaps** (missing data files / hidden imports) —
  the classic freeze risk. Mitigation: the `spt.exe --version` smoke and the manual
  TUI-launch acceptance catch it before release; fixes go in `spt.spec`.
- **librespot pin breaks on a future bump** — mitigated by `--locked` and by the
  build failing loudly; the pin is only moved deliberately.
- **PATH not visible in already-open terminals** — inherent to Windows PATH; a new
  terminal (or the Start Menu shortcut) works. Documented.
- **SmartScreen friction** — documented; unsigned is an accepted tradeoff.
- **CI minutes / cargo cache miss** occasionally forces a full librespot recompile —
  a time cost, not a correctness risk.

## 10. Out of scope (restated)

Code signing; in-app auto-update; macOS/Linux installers; MSI/winget/Chocolatey/Store
distribution. pip install remains the cross-platform route.
