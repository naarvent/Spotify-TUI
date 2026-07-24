# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for SPT-TUI.

onedir (a folder, not onefile): faster startup, no per-launch unpack, and it
gives a natural place for librespot.exe to sit beside spt.exe — which is where
`LocalPlayer.binary_path()` looks first in a frozen build.

Build locally:   pyinstaller spt.spec
Output:          dist/spt/spt.exe  (+ its dependencies)

The release workflow copies librespot.exe into dist/spt/ before packaging.
"""

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Textual ships CSS/theme resources and pyfiglet ships .flf fonts as package
# data; a naive freeze drops them and the UI (or the welcome banner) breaks at
# runtime. collect_all pulls their data files, binaries and submodules.
for _pkg in ("textual", "pyfiglet"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# Our own package plus libraries imported indirectly.
hiddenimports += ["spt_tui", "spt_tui.app", "spotipy", "rich", "requests"]

a = Analysis(
    # A launcher, not spt_tui/__main__.py directly: PyInstaller runs the entry
    # as top-level __main__ with no parent package, which would break that
    # module's relative imports. run_spt.py imports the package normally.
    ["run_spt.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="spt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # the TUI needs a console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="spt",
)
