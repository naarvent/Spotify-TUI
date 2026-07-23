# Installer Setup

How the Windows installer for SPT-TUI is built, released, and rebuilt for new
versions. Everything here is about **distribution** — turning the source into a
one-click `SPT-TUI-Setup-<version>.exe` that installs the app plus `librespot`
with no Python and no Rust on the user's machine.

If you just want to develop, ignore all of this and run `python -m spt_tui`.

> **Windows only.** Linux and macOS have no bundled installer — users install
> with `pipx install git+https://github.com/naarvent/Spotify-TUI.git` and get
> `librespot` from their package manager or `cargo install librespot --locked`.
> See the main `README.md`. This folder is entirely about the Windows `.exe`.

## The one thing to remember

**Releasing a new version is a single tag push.** Bump the version, tag it, push
the tag — GitHub Actions builds the installer and attaches it to the release.

```
git tag v0.3.0
git push origin v0.3.0
```

Full steps in **[01 - Release a new version](01%20-%20Release%20a%20new%20version.md)**.

## What lives where

| File | Purpose |
| --- | --- |
| `.github/workflows/release.yml` | CI pipeline: builds librespot, freezes the app, packages the installer, attaches it to the release. |
| `spt.spec` | PyInstaller config that freezes `spt_tui` into `dist/spt/spt.exe` (a folder with a bundled Python). |
| `installer/spt-tui.iss` | Inno Setup script: takes the frozen folder + `librespot.exe` and produces the installer. |
| `spt_tui/__init__.py` | Holds `__version__` — the single source of truth for the version. |
| `pyproject.toml` | Reads the version from `__init__.py` (hatchling dynamic) and defines the `spt` command. |

## The guides

1. **[Release a new version](01%20-%20Release%20a%20new%20version.md)** — the normal flow, start to finish.
2. **[Build the installer locally](02%20-%20Build%20the%20installer%20locally.md)** — make the `.exe` on your own Windows machine without CI (for testing).
3. **[How the pieces fit](03%20-%20How%20the%20pieces%20fit.md)** — reference: what each file does and how they connect to the app's librespot discovery.
4. **[Troubleshooting and notes](04%20-%20Troubleshooting%20and%20notes.md)** — SmartScreen, signing, common build failures.

## What was set up (2026-07-23)

Sub-project B created all of the above from scratch: the four packaging files, the
`--version` flag, the `spt` command, the "beside the executable" librespot
discovery location, and this documentation. The pinned librespot version is
**0.8.0** (built and validated end-to-end on Windows this session). Nothing has
been released yet — the first installer appears the first time a `v*` tag is
pushed (or the workflow is run manually from the Actions tab).
