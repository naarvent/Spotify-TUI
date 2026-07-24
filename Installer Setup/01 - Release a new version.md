# Release a new version

The normal way to publish an installer. CI does all the building; you just bump
the version and push a tag.

## Steps

1. **Finish and merge your changes** to `main` as usual.

2. **Bump the version** in `spt_tui/__init__.py`:

   ```python
   __version__ = "0.3.0"
   ```

   This is the single source of truth. `pyproject.toml`, the installer filename,
   and `spt --version` all read from here. Do not edit the version anywhere else.

3. **Commit the bump:**

   ```
   git add spt_tui/__init__.py
   git commit -m "chore: bump version to 0.3.0"
   git push
   ```

4. **Tag and push the tag** — the tag must match the version, with a `v` prefix:

   ```
   git tag v0.3.0
   git push origin v0.3.0
   ```

   If the tag and `__version__` disagree, the build fails on purpose (a guard in
   the workflow) so a mislabelled release can't go out.

5. **Wait for CI.** Open the repo's **Actions** tab and watch "Build Windows
   installer". It takes roughly 5–8 minutes — most of it is cached, except when
   the librespot pin changed (then add ~3 minutes for the Rust compile).

6. **Done.** When the run is green, `SPT-TUI-Setup-0.3.0.exe` and `checksums.txt`
   are attached to the GitHub Release for `v0.3.0`. Share that download link.

## What CI actually does

Defined in `.github/workflows/release.yml`, on a `windows-latest` runner:

1. Reads the version from `spt_tui/__init__.py` and checks it matches the tag.
2. Builds `librespot.exe` from source (`cargo install librespot --version 0.8.0
   --locked`). Cached by version, so it only recompiles when the pin changes.
3. Freezes the app with PyInstaller (`pyinstaller spt.spec`) into `dist/spt/`.
4. Copies `librespot.exe` into that folder, beside `spt.exe`.
5. Smoke-tests the freeze (`spt.exe --version`).
6. Builds the installer with Inno Setup.
7. Writes SHA256 checksums for the installer and `librespot.exe`.
8. Attaches the installer and checksums to the release.

## How users update

They download the newer installer and run it. Because the installer keeps a fixed
identity (`AppId` in `spt-tui.iss`), it upgrades the existing install in place:
files are replaced, the `PATH` entry and shortcuts stay, and their data —
including the librespot login — is untouched. No uninstall-first needed.

## Testing a build without releasing

Use the **Run workflow** button on the Actions tab (`workflow_dispatch`). It runs
the exact same build but, instead of touching a release, uploads the installer as
a **workflow artifact** you can download from the run's summary page. Good for a
dry run before committing to a real tag.

## Changing the librespot version

Rare. Edit `LIBRESPOT_VERSION` in `.github/workflows/release.yml`, confirm that
version builds (see [Build the installer locally](02%20-%20Build%20the%20installer%20locally.md)),
then release as usual. Changing it invalidates the CI cache, so that release
recompiles librespot once.
