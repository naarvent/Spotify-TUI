# Troubleshooting and notes

## SmartScreen "unknown publisher"

The installer is **unsigned**, so on first run Windows SmartScreen shows a blue
"Windows protected your PC" screen. This is expected. Click **More info → Run
anyway**. Tell users this in the release notes.

Removing the warning means **code signing** — buying a certificate (OV ~€200/yr,
or an EV cert for instant reputation). Out of scope for now; if you ever get a
cert, sign `dist\spt\spt.exe` and the final installer with `signtool`, and add a
signing step to the workflow.

## The build fails: `cargo` / librespot

- **`the trait ... vergen_lib ... is not satisfied` / multiple versions of a
  crate** — you left off `--locked`. Always build librespot with
  `cargo install librespot --version 0.8.0 --locked`.
- **linker / MSVC errors** — the Visual Studio "Desktop development with C++"
  workload (with the Windows SDK) is missing. Install it, reopen the terminal.
- In CI this is cached by version; it only recompiles when `LIBRESPOT_VERSION`
  changes.

## The frozen app fails at runtime (missing module / data file)

PyInstaller froze the app but something it imports lazily wasn't bundled. Symptom:
`spt.exe` crashes with `ModuleNotFoundError` or a missing-resource error that
`python -m spt_tui` never shows.

Fix in `spt.spec`:
- Missing import → add it to `hiddenimports`.
- Missing data file (e.g. a package's CSS/fonts) → add the package to the
  `collect_all` loop, or add the specific files to `datas`.

Rebuild and re-run `dist\spt\spt.exe`. The CI `spt.exe --version` smoke catches
the grossest failures, but a runtime UI resource gap may only show when you
actually launch the TUI — hence the manual acceptance step.

## `ISCC.exe` not found

Inno Setup isn't installed, or not at the default path. Install it from
https://jrsoftware.org/isdl.php; the compiler is
`C:\Program Files (x86)\Inno Setup 6\ISCC.exe`. In CI it's installed with
`choco install innosetup`.

## `spt` isn't recognised after installing

The installer adds its folder to `PATH`, but **already-open terminals keep the
old `PATH`**. Open a **new** terminal, or use the Start Menu shortcut. This is
normal Windows behaviour, not a bug.

## The local player does nothing / "did not start"

That's an app-runtime issue, not a packaging one — see the main README's
**Local player** section. The usual cause is librespot's OAuth callback port
`127.0.0.1:5588` already held by an earlier instance
(`Failed to bind server ... os error 10048`). Quit the app to free it and retry.
Since the app now reports librespot's own error on the status line, the reason
will be on screen.

## Tag / version mismatch stops the release

The workflow refuses to build if the pushed tag (say `v0.3.0`) doesn't match
`__version__` in `spt_tui/__init__.py`. Fix: bump `__init__.py` to match the tag,
commit, and re-push the tag (delete and re-create it if needed:
`git tag -d v0.3.0 && git push origin :refs/tags/v0.3.0`, then re-tag).

## Where the user's data lives (and survives uninstall)

`~/Documents/naarvent's projects/Spotify_TUI/` — token cache, config, logs, and
the librespot credentials under `librespot/`. The uninstaller deliberately does
**not** touch it, so reinstalling keeps the user logged in.
