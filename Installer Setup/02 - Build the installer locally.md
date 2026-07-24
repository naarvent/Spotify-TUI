# Build the installer locally

For testing the installer on your own Windows machine without going through CI.
The result is identical to what CI produces. CI is still the way to publish a
real release (reproducible, hashed, attached to the tag).

## One-time prerequisites

1. **Rust with the MSVC toolchain** (to build librespot):
   - Install [Visual Studio Build Tools 2022](https://visualstudio.microsoft.com/downloads/)
     with the **"Desktop development with C++"** workload (includes the Windows SDK).
   - Install [rustup](https://rustup.rs/), then:
     ```
     rustup default stable-x86_64-pc-windows-msvc
     ```

2. **Python 3.9+** (you already have it) and **PyInstaller**:
   ```
   pip install pyinstaller
   ```

3. **Inno Setup 6** — install it one of two ways, which land the compiler
   (`ISCC.exe`) in different places:
   - `winget install JRSoftware.InnoSetup` → `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`
   - the installer from https://jrsoftware.org/isdl.php → `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`

   Use whichever path exists in the ISCC command in step 5. (CI uses `choco`,
   which lands it in `Program Files (x86)`.)

## Build steps

Run these from the repo root (`SPT-TUI - Refactorizar`) in PowerShell.

1. **Get `librespot.exe`.** If you already built it this session it is at
   `%USERPROFILE%\.cargo\bin\librespot.exe`. Otherwise:
   ```powershell
   cargo install librespot --version 0.8.0 --locked
   ```
   `--locked` matters — without it a newer transitive dependency fails to compile.

2. **Freeze the app:**
   ```powershell
   pyinstaller spt.spec --noconfirm
   ```
   This creates `dist\spt\` containing `spt.exe` and its dependencies.

3. **Copy librespot beside the app:**
   ```powershell
   Copy-Item "$env:USERPROFILE\.cargo\bin\librespot.exe" dist\spt\librespot.exe
   ```

4. **Sanity-check the freeze:**
   ```powershell
   dist\spt\spt.exe --version
   ```
   It should print `spt-tui <version>`. You can also just run `dist\spt\spt.exe`
   to launch the full TUI and confirm the local player works.

5. **Build the installer** (use your real version for `AppVersion`, and the ISCC
   path that matches how you installed Inno — see prerequisites):
   ```powershell
   # winget install:
   & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" /DAppVersion=0.2.1 installer\spt-tui.iss
   # or classic installer:
   & "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=0.2.1 installer\spt-tui.iss
   ```
   The installer lands at `dist\installer\SPT-TUI-Setup-0.2.1.exe`.

6. **Install it and test:** double-click the `.exe`, let it install, open a
   **new** terminal (so it picks up the updated `PATH`), and run `spt`.

## Notes

- Steps 2–5 are exactly what the CI workflow does; the only difference is CI also
  computes checksums and attaches the result to a release.
- If PyInstaller misses a module or data file and the app fails at runtime, add it
  to `spt.spec` — see [Troubleshooting](04%20-%20Troubleshooting%20and%20notes.md).
- The frozen `dist\spt\` folder is throwaway; delete `dist\` and `build\` to start
  clean.
