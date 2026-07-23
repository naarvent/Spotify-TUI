# How the pieces fit

Reference for what each packaging file does and how the installed app finds
librespot.

## The build chain

```
spt_tui/ (source)                 librespot (crates.io, pinned 0.8.0)
      |                                   |
      | PyInstaller (spt.spec)            | cargo install --locked
      v                                   v
  dist/spt/  (spt.exe + deps)  <--copy--  librespot.exe
      |
      | Inno Setup (installer/spt-tui.iss, /DAppVersion=X.Y.Z)
      v
  dist/installer/SPT-TUI-Setup-X.Y.Z.exe   (+ checksums.txt)
```

The GitHub Actions workflow (`.github/workflows/release.yml`) runs this whole
chain on a Windows runner and attaches the result to the release.

## Each file

### `spt.spec` (PyInstaller)

Freezes the app into `dist/spt/` — a folder ("onedir") holding `spt.exe`, a
bundled Python, and every dependency. onedir (not a single `.exe`) is chosen so
startup is fast and so `librespot.exe` has a natural home right next to `spt.exe`.
It explicitly collects Textual's CSS/theme data and pyfiglet's fonts, which a
naive freeze would miss.

### `installer/spt-tui.iss` (Inno Setup)

Turns `dist/spt/` (with `librespot.exe` already inside) into the installer:

- Installs to `%LOCALAPPDATA%\Programs\SPT-TUI` — **per-user, no admin/UAC**.
- Optionally adds that folder to the **user** `PATH` — a default-on wizard
  checkbox (the "addtopath" task), so `spt` works in any new terminal unless the
  user opts out.
- Creates a Start Menu shortcut (and an optional, default-off desktop shortcut),
  offers a "Launch SPT-TUI now" checkbox on the final page, and shows the MIT
  `LICENSE` on an agreement page.
- Has a **fixed `AppId`** — this is what makes re-running a newer installer an
  in-place upgrade instead of a second copy. Never change it.
- Its uninstaller removes files, the `PATH` entry, and shortcuts, but leaves the
  user's data (token cache, librespot credentials) alone.

The version comes in as `/DAppVersion=X.Y.Z` on the command line.

### `.github/workflows/release.yml` (CI)

Orchestrates everything on `windows-latest`, triggered by a `v*` tag (or a manual
run). It pins librespot via the `LIBRESPOT_VERSION` env var and caches the built
`librespot.exe` by that version, so ordinary releases don't recompile Rust.

### `spt_tui/__init__.py` + `pyproject.toml` (versioning)

`__version__` in `__init__.py` is the single source of truth. `pyproject.toml`
reads it via hatchling's dynamic version (`[tool.hatch.version]`), so a wheel and
the frozen app always report the same version. The workflow reads it too and
checks the tag matches.

## How the installed app finds librespot

The installer drops `librespot.exe` beside `spt.exe`. At runtime,
`LocalPlayer.binary_path()` (in `spt_tui/local_player.py`) looks in this order:

1. `SPT_LIBRESPOT_PATH` — an explicit override (power users / a custom build).
2. `CACHE_DIR/bin/librespot.exe` — an optional per-user location.
3. **Beside the running executable** — `dist/spt/` for a frozen build. This is
   the one the installer relies on, and it works regardless of `PATH` (so the
   Start Menu shortcut finds it even before any terminal is opened).
4. `PATH` — because the install dir is also added to `PATH`.

So the installer satisfies discovery two independent ways (beside-the-exe **and**
on `PATH`), and a developer running from source is unaffected — they use `PATH`
or the override as before.

## The `spt` command

`pyproject.toml` defines both `spt` and `spt-tui` as console entry points (for
pip installs). PyInstaller names the frozen executable `spt.exe`, and the
installer puts its folder on `PATH` — so `spt` is the command everywhere.
