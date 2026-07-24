# librespot Local Player Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `LocalPlayer` that manages an external librespot process so SPT-TUI acts as its own Spotify Connect device, independent of the official app.

**Architecture:** One isolated module `spt_tui/local_player.py` owns binary discovery, librespot's one-time OAuth, process lifecycle, and a polite (non-stealing) auto-start. Once librespot runs it registers as a Connect device and appears in `SpotifyClient.devices()`, so all existing control code drives it unchanged. Thin, additive hooks in `core.py` (instantiate / autostart / teardown) and `queue_devices.py` (Devices view).

**Tech Stack:** Python 3.14, stdlib `subprocess`/`shutil`/`threading`, existing `SpotifyClient`, Textual (unchanged). External `librespot` binary (provided out-of-band in this sub-project; Sub-project B ships it).

## Global Constraints

- **Real-code-only docs:** do not update the Obsidian vault until code ships.
- **No new runtime dependency:** use the stdlib only; do not add packages to `pyproject.toml`.
- **Premium unchanged:** librespot still requires Spotify Premium; surface a clear message on stream failure, do not try to work around it.
- **UI-thread rule:** all blocking work (spawn, OAuth wait, device poll, `get_playback`) runs off the UI thread; never call into the Textual widget tree from `LocalPlayer`. Feedback goes through the app's existing `_notify` / `call_from_thread`.
- **Cache isolation:** all librespot state lives under `config.CACHE_DIR` (which honours `SPT_TUI_CACHE_DIR`), so the test suites stay hermetic.
- **Test harness:** this repo uses a custom harness, NOT pytest. Each suite is a standalone script with `check(name, cond, detail)`, an `ALL` list, a `main()` returning bool, and `sys.exit(0 if main() else 1)`. New tests follow that exact shape and run with `python tests/test_local_player.py`.
- **librespot invocation contract:** headless run = `librespot --name <name> --cache <dir> --backend rodio --bitrate 160`; first-time login adds `--enable-oauth`. librespot writes `<cache>/credentials.json` after a successful login and reuses it headless. Exact flags are pinned against the shipped version in Sub-project B; this plan tests argv *structure*, never real librespot behaviour.

---

## File Structure

- **Create:** `spt_tui/local_player.py` — the `LocalPlayer` class + config helpers + `LOCAL_START_SENTINEL`. Sole owner of librespot.
- **Create:** `tests/test_local_player.py` — standalone suite (repo harness) for everything in the module and the two mixin helpers.
- **Modify:** `spt_tui/app/core.py` — instantiate `LocalPlayer` in `CoreMixin.__init__`; start `maybe_autostart()` on a daemon thread in `on_mount`; call `stop()` in `on_unmount`; route the sentinel row in `on_data_table_row_selected` via a new `_select_device` helper.
- **Modify:** `spt_tui/app/queue_devices.py` — inject the synthetic "start" row in `_populate_devices_table`.

---

## Task 1: Config helpers, construction & binary discovery

**Files:**
- Create: `spt_tui/local_player.py`
- Test: `tests/test_local_player.py`

**Interfaces:**
- Consumes: `config.LOCAL_CFG` (dict), `config.CACHE_DIR` (str), `config.logger`.
- Produces:
  - `LOCAL_START_SENTINEL: str`
  - `_cfg_bool(key: str, env: str, default: bool) -> bool`
  - `_cfg_str(key: str, env: str, default)` (returns str or the given default)
  - `LocalPlayer(spotify, *, cache_dir=None, popen=subprocess.Popen, open_url=None, now=time.time, sleep=time.sleep)`
  - `LocalPlayer.device_name -> str` (property)
  - `LocalPlayer.binary_path() -> Optional[str]`
  - `LocalPlayer.is_available() -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_local_player.py`:

```python
"""Deterministic unit tests for local_player.py. No Textual, no network, no real
librespot. The process factory, clock, sleep, browser opener and cache dir are
all injected, and a FakeSpotify records device/playback/transfer calls.

Run standalone:  python tests/test_local_player.py
Exit code is 0 only if every check passes.
"""
import os, sys, time, tempfile, shutil, traceback, threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spt_tui.config as config
import spt_tui.local_player as lp_mod
from spt_tui.local_player import LocalPlayer, LOCAL_START_SENTINEL

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeSpotify:
    """Minimal SpotifyClient stand-in. `devices` is a list; playback is a dict."""
    def __init__(self, devices=None, playback=None):
        self._devices = list(devices or [])
        self._playback = dict(playback or {})
        self.transfers = []          # (device_id, force_play)
    def devices(self):
        return list(self._devices)
    def get_playback(self):
        return dict(self._playback)
    def transfer(self, device_id, force_play=False):
        self.transfers.append((device_id, force_play))


def _clean_env():
    for k in ("SPT_LOCAL_PLAYER", "SPT_LIBRESPOT_PATH", "SPT_LOCAL_AUTOSTART", "SPT_LOCAL_NAME"):
        os.environ.pop(k, None)


def test_cfg_defaults():
    _clean_env()
    old = config.LOCAL_CFG
    config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("default name", lp.device_name == "SPT-TUI Local", lp.device_name)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        config.LOCAL_CFG = old


def test_cfg_env_precedence():
    _clean_env()
    old = config.LOCAL_CFG
    config.LOCAL_CFG = {"local_player_name": "FromFile"}
    os.environ["SPT_LOCAL_NAME"] = "FromEnv"
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("env overrides config file", lp.device_name == "FromEnv", lp.device_name)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_discovery_override_first():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "my_librespot.bin")
        open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("override path wins", lp.binary_path() == fake_bin, repr(lp.binary_path()))
        check("available with binary", lp.is_available() is True)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_discovery_bundled_then_path(monkey_which=None):
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    orig_which = lp_mod.shutil.which
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        exe = "librespot.exe" if os.name == "nt" else "librespot"
        bin_dir = os.path.join(tmp, "bin"); os.makedirs(bin_dir)
        bundled = os.path.join(bin_dir, exe); open(bundled, "w").close()
        lp_mod.shutil.which = lambda name: "/usr/bin/librespot"
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("bundled path beats PATH", lp.binary_path() == bundled, repr(lp.binary_path()))
        os.remove(bundled)
        check("falls back to PATH", lp.binary_path() == "/usr/bin/librespot", repr(lp.binary_path()))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        lp_mod.shutil.which = orig_which
        _clean_env(); config.LOCAL_CFG = old


def test_discovery_none_disables():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    orig_which = lp_mod.shutil.which
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp_mod.shutil.which = lambda name: None
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("no binary -> not available", lp.is_available() is False)
        check("no binary -> path None", lp.binary_path() is None)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        lp_mod.shutil.which = orig_which
        _clean_env(); config.LOCAL_CFG = old


def test_disabled_flag():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        os.environ["SPT_LOCAL_PLAYER"] = "false"
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("disabled flag -> not available", lp.is_available() is False)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


ALL = [test_cfg_defaults, test_cfg_env_precedence, test_discovery_override_first,
       test_discovery_bundled_then_path, test_discovery_none_disables, test_disabled_flag]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (local_player) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_local_player.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'spt_tui.local_player'` (crash reported by the harness).

- [ ] **Step 3: Write minimal implementation**

Create `spt_tui/local_player.py`:

```python
"""Local librespot player: run librespot as our own Spotify Connect device.

librespot is an external process. Once running and authenticated it registers as
a Connect device and appears in SpotifyClient.devices(); all existing control
code then drives it unchanged. This module owns only: finding the binary, its
one-time OAuth, its process lifecycle, and the polite auto-start decision (never
steal an active session on another device).

No audio and no real librespot are needed to test this: the process factory
(`popen`), the clock (`now`), the sleep, the browser opener (`open_url`) and the
cache directory are all injectable.
"""
from __future__ import annotations

import os
import time
import shutil
import subprocess
import threading
from typing import Callable, List, Optional

from . import config
from .config import logger

# Sentinel device id for the synthetic "start local player" row in the Devices
# view. Not a real Spotify id; selecting it routes to start_and_activate().
LOCAL_START_SENTINEL = "__spt_local_start__"


def _cfg_bool(key: str, env: str, default: bool) -> bool:
    v = os.getenv(env)
    if v is not None:
        return v.strip().lower() in ("1", "true", "yes", "on")
    cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
    val = cfg.get(key)
    return val if isinstance(val, bool) else default


def _cfg_str(key: str, env: str, default):
    v = os.getenv(env)
    if v is not None and v.strip():
        return v.strip()
    cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
    val = cfg.get(key)
    return val if isinstance(val, str) and val else default


class LocalPlayer:
    def __init__(self, spotify, *, cache_dir: Optional[str] = None,
                 popen: Callable = subprocess.Popen,
                 open_url: Optional[Callable[[str], None]] = None,
                 now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self._spotify = spotify
        self._cache_dir = cache_dir or config.CACHE_DIR
        self._librespot_cache = os.path.join(self._cache_dir, "librespot")
        self._popen = popen
        self._open_url = open_url or (lambda u: None)
        self._now = now
        self._sleep = sleep
        self._proc = None
        self._lock = threading.RLock()

        self._enabled = _cfg_bool("local_player_enabled", "SPT_LOCAL_PLAYER", True)
        self._path_override = _cfg_str("librespot_path", "SPT_LIBRESPOT_PATH", None)
        self._autostart = _cfg_bool("local_player_autostart", "SPT_LOCAL_AUTOSTART", True)
        self._name = _cfg_str("local_player_name", "SPT_LOCAL_NAME", "SPT-TUI Local")

    @property
    def device_name(self) -> str:
        return self._name

    def _exe_name(self) -> str:
        return "librespot.exe" if os.name == "nt" else "librespot"

    def binary_path(self) -> Optional[str]:
        cand = self._path_override
        if cand and os.path.isfile(cand):
            return cand
        bundled = os.path.join(self._cache_dir, "bin", self._exe_name())
        if os.path.isfile(bundled):
            return bundled
        return shutil.which("librespot")

    def is_available(self) -> bool:
        return bool(self._enabled) and self.binary_path() is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_local_player.py`
Expected: PASS — `6/6 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add spt_tui/local_player.py tests/test_local_player.py
git commit -m "feat(local-player): config, construction and binary discovery"
```

---

## Task 2: Auth state & argv building

**Files:**
- Modify: `spt_tui/local_player.py`
- Test: `tests/test_local_player.py`

**Interfaces:**
- Consumes: `LocalPlayer` from Task 1.
- Produces:
  - `LocalPlayer.credentials_path() -> str`
  - `LocalPlayer.is_authenticated() -> bool`
  - `LocalPlayer._build_argv(binary: str, *, login: bool) -> List[str]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_local_player.py` (add the two functions, then add them to `ALL`):

```python
def test_is_authenticated():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("no creds -> not authenticated", lp.is_authenticated() is False)
        os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
        open(lp.credentials_path(), "w").close()
        check("creds file -> authenticated", lp.is_authenticated() is True)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_build_argv():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        run = lp._build_argv("/x/librespot", login=False)
        login = lp._build_argv("/x/librespot", login=True)
        check("argv starts with binary", run[0] == "/x/librespot", repr(run[:1]))
        check("argv carries name", "--name" in run and "SPT-TUI Local" in run)
        check("argv carries cache dir", os.path.join(tmp, "librespot") in run)
        check("run mode has no oauth", "--enable-oauth" not in run)
        check("login mode enables oauth", "--enable-oauth" in login)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


ALL += [test_is_authenticated, test_build_argv]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_local_player.py`
Expected: FAIL — `AttributeError: 'LocalPlayer' object has no attribute 'credentials_path'` (reported for the two new tests).

- [ ] **Step 3: Write minimal implementation**

Add to `LocalPlayer` in `spt_tui/local_player.py`:

```python
    # librespot writes credentials.json into its --cache dir after a successful
    # login; its presence means we can start headless (no browser).
    CREDENTIALS_FILE = "credentials.json"

    def credentials_path(self) -> str:
        return os.path.join(self._librespot_cache, self.CREDENTIALS_FILE)

    def is_authenticated(self) -> bool:
        return os.path.isfile(self.credentials_path())

    def _build_argv(self, binary: str, *, login: bool) -> List[str]:
        argv = [binary,
                "--name", self._name,
                "--cache", self._librespot_cache,
                "--backend", "rodio",
                "--bitrate", "160"]
        if login:
            # Interactive OAuth to obtain and cache credentials. Headless runs
            # (login=False) reuse the cached credentials.json in --cache.
            argv.append("--enable-oauth")
        return argv
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_local_player.py`
Expected: PASS — `8/8 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add spt_tui/local_player.py tests/test_local_player.py
git commit -m "feat(local-player): auth-state detection and argv building"
```

---

## Task 3: Process lifecycle — spawn, is_running, stop

**Files:**
- Modify: `spt_tui/local_player.py`
- Test: `tests/test_local_player.py`

**Interfaces:**
- Consumes: `LocalPlayer._build_argv`, `binary_path`.
- Produces:
  - `LocalPlayer.is_running() -> bool`
  - `LocalPlayer._spawn(*, login: bool) -> bool`
  - `LocalPlayer.stop() -> None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_local_player.py` (add a fake process/popen near the top-level classes, then the tests, then extend `ALL`):

```python
class FakeProc:
    def __init__(self, argv, lines=None, write_creds_path=None):
        self.argv = argv
        self._lines = list(lines or [])
        self._write_creds_path = write_creds_path
        self._alive = True
        self.terminated = False
        self.killed = False
        self.stdout = self
        if write_creds_path:  # simulate librespot caching credentials on login
            os.makedirs(os.path.dirname(write_creds_path), exist_ok=True)
            open(write_creds_path, "w").close()
    def poll(self):
        return None if self._alive else 0
    def readline(self):
        return self._lines.pop(0) if self._lines else ""
    def terminate(self):
        self.terminated = True; self._alive = False
    def kill(self):
        self.killed = True; self._alive = False
    def wait(self, timeout=None):
        self._alive = False
        return 0


class FakePopen:
    """Callable replacement for subprocess.Popen. Records the last spawn."""
    def __init__(self, lines=None, creds_for_login=None):
        self.lines = lines
        self.creds_for_login = creds_for_login   # cache path written when --enable-oauth present
        self.spawns = []
        self.last = None
    def __call__(self, argv, **kw):
        login = "--enable-oauth" in argv
        creds = self.creds_for_login if (login and self.creds_for_login) else None
        proc = FakeProc(argv, lines=(self.lines if login else None), write_creds_path=creds)
        self.spawns.append(argv); self.last = proc
        return proc


def test_spawn_and_is_running():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        check("not running before spawn", lp.is_running() is False)
        ok = lp._spawn(login=False)
        check("spawn returns True", ok is True)
        check("running after spawn", lp.is_running() is True)
        check("headless argv, no oauth", "--enable-oauth" not in fp.last.argv)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_stop_terminates():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        lp._spawn(login=False)
        proc = fp.last
        lp.stop()
        check("stop terminates process", proc.terminated is True)
        check("not running after stop", lp.is_running() is False)
        lp.stop()  # idempotent, must not raise
        check("stop is idempotent", True)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_spawn_no_binary():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    orig_which = lp_mod.shutil.which
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp_mod.shutil.which = lambda name: None
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=FakePopen())
        check("spawn with no binary -> False", lp._spawn(login=False) is False)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        lp_mod.shutil.which = orig_which
        _clean_env(); config.LOCAL_CFG = old


ALL += [test_spawn_and_is_running, test_stop_terminates, test_spawn_no_binary]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_local_player.py`
Expected: FAIL — `AttributeError: 'LocalPlayer' object has no attribute 'is_running'`.

- [ ] **Step 3: Write minimal implementation**

Add to `LocalPlayer`:

```python
    def is_running(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def _spawn(self, *, login: bool) -> bool:
        binary = self.binary_path()
        if binary is None:
            return False
        try:
            os.makedirs(self._librespot_cache, exist_ok=True)
        except Exception:
            logger.exception("LocalPlayer: could not create cache dir")
        argv = self._build_argv(binary, login=login)
        try:
            with self._lock:
                self._proc = self._popen(
                    argv, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True)
            return True
        except Exception:
            logger.exception("LocalPlayer: could not spawn librespot")
            self._proc = None
            return False

    def stop(self) -> None:
        with self._lock:
            p = self._proc
            self._proc = None
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
        except Exception:
            logger.exception("LocalPlayer: error stopping librespot")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_local_player.py`
Expected: PASS — `11/11 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add spt_tui/local_player.py tests/test_local_player.py
git commit -m "feat(local-player): process spawn, is_running and stop"
```

---

## Task 4: One-time OAuth (`authenticate`) and `start`

**Files:**
- Modify: `spt_tui/local_player.py`
- Test: `tests/test_local_player.py`

**Interfaces:**
- Consumes: `_spawn`, `is_authenticated`, `_open_url`, `_now`, `_sleep`.
- Produces:
  - `LocalPlayer._extract_url(line: str) -> Optional[str]`
  - `LocalPlayer.authenticate() -> bool`
  - `LocalPlayer.start() -> bool`
  - Class attr `OAUTH_WAIT_SECONDS = 120.0`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_local_player.py`:

```python
def test_extract_url():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        u = lp._extract_url("Please browse to https://accounts.spotify.com/authorize?x=1 now")
        check("extracts url", u == "https://accounts.spotify.com/authorize?x=1", repr(u))
        check("no url -> None", lp._extract_url("nothing here") is None)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_authenticate_opens_url_and_caches():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        creds = os.path.join(tmp, "librespot", "credentials.json")
        fp = FakePopen(lines=["go to https://accounts.spotify.com/authorize?y=2\n"],
                       creds_for_login=creds)
        opened = []
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp,
                         open_url=lambda u: opened.append(u), sleep=lambda s: None)
        ok = lp.authenticate()
        check("authenticate returns True", ok is True)
        check("browser opened with auth url",
              opened == ["https://accounts.spotify.com/authorize?y=2"], repr(opened))
        check("login spawn used oauth", "--enable-oauth" in fp.last.argv)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_authenticate_short_circuits_when_cached():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
        open(os.path.join(tmp, "librespot", "credentials.json"), "w").close()
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        check("already-auth authenticate True", lp.authenticate() is True)
        check("no spawn when already authenticated", fp.spawns == [], repr(fp.spawns))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_start_headless_when_authenticated():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
        open(os.path.join(tmp, "librespot", "credentials.json"), "w").close()
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        check("start returns True", lp.start() is True)
        check("headless spawn (no oauth)", "--enable-oauth" not in fp.last.argv)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


ALL += [test_extract_url, test_authenticate_opens_url_and_caches,
        test_authenticate_short_circuits_when_cached, test_start_headless_when_authenticated]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_local_player.py`
Expected: FAIL — `AttributeError: 'LocalPlayer' object has no attribute '_extract_url'`.

- [ ] **Step 3: Write minimal implementation**

Add to `LocalPlayer` (class attr near the top with the others, methods with the rest):

```python
    # Bound wait for the interactive OAuth to complete (browser round-trip).
    OAUTH_WAIT_SECONDS = 120.0

    def _extract_url(self, line: str) -> Optional[str]:
        s = (line or "").strip()
        i = s.find("https://")
        if i == -1:
            return None
        return s[i:].split()[0]

    def authenticate(self) -> bool:
        """Run librespot's OAuth once and return True when credentials are
        cached. Reads librespot's stdout for the auth URL, opens it in the
        browser, and waits (bounded) for credentials.json to appear."""
        if self.is_authenticated():
            return True
        if not self._spawn(login=True):
            return False
        proc = self._proc
        deadline = self._now() + self.OAUTH_WAIT_SECONDS
        opened = False
        try:
            while self._now() < deadline and proc is not None and proc.poll() is None:
                line = proc.stdout.readline() if proc.stdout else ""
                if line and not opened:
                    url = self._extract_url(line)
                    if url:
                        try:
                            self._open_url(url)
                        except Exception:
                            logger.exception("LocalPlayer: opening auth url failed")
                        opened = True
                if self.is_authenticated():
                    return True
                if not line:
                    self._sleep(0.1)
        except Exception:
            logger.exception("LocalPlayer.authenticate failed")
        return self.is_authenticated()

    def start(self) -> bool:
        """Ensure librespot is running. If authenticated, spawn headless; else
        run the one-time OAuth (which leaves librespot running as the device)."""
        if self.is_running():
            return True
        if self.is_authenticated():
            return self._spawn(login=False)
        return self.authenticate()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_local_player.py`
Expected: PASS — `18/18 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add spt_tui/local_player.py tests/test_local_player.py
git commit -m "feat(local-player): one-time librespot OAuth and start()"
```

---

## Task 5: Device wait & polite `maybe_autostart`

**Files:**
- Modify: `spt_tui/local_player.py`
- Test: `tests/test_local_player.py`

**Interfaces:**
- Consumes: `start`, `is_available`, `is_authenticated`, `_autostart`, `self._spotify.devices()`, `self._spotify.get_playback()`, `self._spotify.transfer(id, force_play=...)`.
- Produces:
  - `LocalPlayer._wait_for_local_device() -> Optional[str]`
  - `LocalPlayer.maybe_autostart() -> None`
  - Class attr `DEVICE_WAIT_SECONDS = 12.0`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_local_player.py`. These use an authenticated cache + a `FakePopen` and drive the polite decision table:

```python
def _authed_player(tmp, spotify, **kw):
    """Helper: an available, already-authenticated LocalPlayer over a temp dir."""
    fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
    os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
    os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
    open(os.path.join(tmp, "librespot", "credentials.json"), "w").close()
    return LocalPlayer(spotify, cache_dir=tmp, popen=FakePopen(),
                       now=kw.get("now", time.time), sleep=lambda s: None)


def test_autostart_transfers_when_idle():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        sp = FakeSpotify(devices=[{"id": "LOCAL1", "name": "SPT-TUI Local", "is_active": False}],
                         playback={"is_playing": False})
        lp = _authed_player(tmp, sp)
        lp.maybe_autostart()
        check("idle: transferred to local", sp.transfers == [("LOCAL1", False)], repr(sp.transfers))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_does_not_steal():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        sp = FakeSpotify(devices=[{"id": "LOCAL1", "name": "SPT-TUI Local", "is_active": False},
                                  {"id": "PHONE", "name": "Phone", "is_active": True}],
                         playback={"is_playing": True})
        lp = _authed_player(tmp, sp)
        lp.maybe_autostart()
        check("playing elsewhere: NO transfer", sp.transfers == [], repr(sp.transfers))
        check("local player still running", lp.is_running() is True)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_device_never_appears():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        # No device with our name ever shows up; the bounded wait must give up.
        sp = FakeSpotify(devices=[{"id": "PHONE", "name": "Phone", "is_active": True}],
                         playback={"is_playing": False})
        clock = [1000.0]
        lp = _authed_player(tmp, sp, now=lambda: clock[0])
        # advance the clock past DEVICE_WAIT_SECONDS on each sleep
        lp._sleep = lambda s: clock.__setitem__(0, clock[0] + 5.0)
        lp.maybe_autostart()
        check("no device: no transfer, no crash", sp.transfers == [], repr(sp.transfers))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_skipped_when_not_authenticated():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin  # available but NOT authenticated
        fp = FakePopen()
        sp = FakeSpotify()
        lp = LocalPlayer(sp, cache_dir=tmp, popen=fp, sleep=lambda s: None)
        lp.maybe_autostart()
        check("no creds: no spawn on autostart", fp.spawns == [], repr(fp.spawns))
        check("no creds: no transfer", sp.transfers == [])
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_disabled_flag():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        os.environ["SPT_LOCAL_AUTOSTART"] = "false"
        sp = FakeSpotify(devices=[{"id": "LOCAL1", "name": "SPT-TUI Local"}],
                         playback={"is_playing": False})
        lp = _authed_player(tmp, sp)
        lp.maybe_autostart()
        check("autostart disabled: no transfer", sp.transfers == [], repr(sp.transfers))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


ALL += [test_autostart_transfers_when_idle, test_autostart_does_not_steal,
        test_autostart_device_never_appears, test_autostart_skipped_when_not_authenticated,
        test_autostart_disabled_flag]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_local_player.py`
Expected: FAIL — `AttributeError: 'LocalPlayer' object has no attribute 'maybe_autostart'`.

- [ ] **Step 3: Write minimal implementation**

Add to `LocalPlayer` (class attr with the others; methods with the rest):

```python
    # Bound wait for librespot to register as a Connect device after spawn.
    DEVICE_WAIT_SECONDS = 12.0

    def _wait_for_local_device(self) -> Optional[str]:
        deadline = self._now() + self.DEVICE_WAIT_SECONDS
        while self._now() < deadline:
            try:
                for d in self._spotify.devices():
                    if d.get("name") == self._name:
                        return d.get("id")
            except Exception:
                logger.exception("LocalPlayer: devices() during device wait failed")
            self._sleep(0.5)
        return None

    def maybe_autostart(self) -> None:
        """Startup entry point (call on a daemon thread). Politely: spawn only
        with cached credentials, and never steal an active foreign session."""
        if not self.is_available() or not self._autostart:
            return
        if not self.is_authenticated():
            # Do not pop a browser on every launch; the Devices view offers a
            # manual start row that runs OAuth on explicit selection.
            return
        if not self.start():
            return
        dev_id = self._wait_for_local_device()
        if dev_id is None:
            logger.warning("LocalPlayer: local device did not register in time")
            return
        try:
            pb = self._spotify.get_playback() or {}
        except Exception:
            logger.exception("LocalPlayer: get_playback during autostart failed")
            pb = {}
        if pb.get("is_playing"):
            return  # something is playing elsewhere — stay available, do not transfer
        try:
            self._spotify.transfer(dev_id, force_play=False)
        except Exception:
            logger.exception("LocalPlayer: polite transfer to local device failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_local_player.py`
Expected: PASS — `23/23 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add spt_tui/local_player.py tests/test_local_player.py
git commit -m "feat(local-player): polite non-stealing autostart"
```

---

## Task 6: `start_and_activate` (explicit user selection)

**Files:**
- Modify: `spt_tui/local_player.py`
- Test: `tests/test_local_player.py`

**Interfaces:**
- Consumes: `is_available`, `is_running`, `start`, `_wait_for_local_device`, `self._spotify.transfer`.
- Produces: `LocalPlayer.start_and_activate() -> Optional[str]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_local_player.py`:

```python
def test_start_and_activate_transfers_force_play():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        sp = FakeSpotify(devices=[{"id": "LOCAL1", "name": "SPT-TUI Local"}],
                         playback={"is_playing": True})  # playing elsewhere, but user chose local
        lp = _authed_player(tmp, sp)
        dev = lp.start_and_activate()
        check("returns local device id", dev == "LOCAL1", repr(dev))
        check("explicit activate uses force_play=True",
              sp.transfers == [("LOCAL1", True)], repr(sp.transfers))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_start_and_activate_unavailable():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    orig_which = lp_mod.shutil.which
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp_mod.shutil.which = lambda name: None
        sp = FakeSpotify()
        lp = LocalPlayer(sp, cache_dir=tmp, popen=FakePopen(), sleep=lambda s: None)
        check("unavailable -> None", lp.start_and_activate() is None)
        check("unavailable -> no transfer", sp.transfers == [])
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        lp_mod.shutil.which = orig_which
        _clean_env(); config.LOCAL_CFG = old


ALL += [test_start_and_activate_transfers_force_play, test_start_and_activate_unavailable]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_local_player.py`
Expected: FAIL — `AttributeError: 'LocalPlayer' object has no attribute 'start_and_activate'`.

- [ ] **Step 3: Write minimal implementation**

Add to `LocalPlayer`:

```python
    def start_and_activate(self) -> Optional[str]:
        """User explicitly chose the local device (Devices view). Ensure it is
        running (running OAuth if needed), then transfer to it and play."""
        if not self.is_available():
            return None
        if not self.is_running():
            if not self.start():
                return None
        dev_id = self._wait_for_local_device()
        if dev_id is None:
            return None
        try:
            self._spotify.transfer(dev_id, force_play=True)
        except Exception:
            logger.exception("LocalPlayer: transfer to local device failed")
        return dev_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_local_player.py`
Expected: PASS — `27/27 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add spt_tui/local_player.py tests/test_local_player.py
git commit -m "feat(local-player): explicit start_and_activate with force_play"
```

---

## Task 7: Devices view — synthetic start row + selection routing

**Files:**
- Modify: `spt_tui/app/queue_devices.py:495-501` (`_populate_devices_table`)
- Modify: `spt_tui/app/core.py:1186-1214` (`on_data_table_row_selected`, add `_select_device` helper)
- Test: `tests/test_local_player.py`

**Interfaces:**
- Consumes: `LOCAL_START_SENTINEL`, `LocalPlayer.is_available/is_running/device_name/start_and_activate`.
- Produces:
  - `QueueDevicesMixin._populate_devices_table` now prepends a sentinel row when the local player is available but not yet a real device.
  - `CoreMixin._select_device(dev_id) -> None` (routes sentinel → `local_player.start_and_activate` on a daemon thread; real id → `spotify.transfer(..., force_play=True)` on a daemon thread).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_local_player.py`. These test the two mixin methods in isolation with tiny fakes (no Textual app):

```python
from spt_tui.app.queue_devices import QueueDevicesMixin
from spt_tui.app.core import CoreMixin


class FakeTable:
    def __init__(self):
        self.rows = []            # list of (cells, key)
        self.row_to_device = {}
        self.cleared = False
    def clear(self):
        self.cleared = True; self.rows = []
    def add_row(self, *cells, key=None):
        self.rows.append((cells, key))


class FakeLP:
    def __init__(self, available=True, running=False, name="SPT-TUI Local"):
        self._available = available; self._running = running; self._name = name
        self.activated = 0
    @property
    def device_name(self): return self._name
    def is_available(self): return self._available
    def is_running(self): return self._running
    def start_and_activate(self): self.activated += 1; return "LOCAL1"


def test_devices_table_injects_start_row():
    obj = QueueDevicesMixin()
    obj.local_player = FakeLP(available=True, running=False)
    table = FakeTable()
    QueueDevicesMixin._populate_devices_table(obj, table, [{"id": "PHONE", "name": "Phone", "is_active": True}])
    # first row is the synthetic start entry, mapped to the sentinel id
    check("start row present", table.row_to_device.get(0) == LOCAL_START_SENTINEL, repr(table.row_to_device))
    check("phone still listed", table.row_to_device.get(1) == "PHONE", repr(table.row_to_device))


def test_devices_table_no_start_row_when_running():
    obj = QueueDevicesMixin()
    obj.local_player = FakeLP(available=True, running=True)
    table = FakeTable()
    # librespot already appears as a real device named "SPT-TUI Local"
    QueueDevicesMixin._populate_devices_table(
        obj, table, [{"id": "LOCAL1", "name": "SPT-TUI Local", "is_active": True}])
    check("no synthetic row when running", LOCAL_START_SENTINEL not in table.row_to_device.values(),
          repr(table.row_to_device))
    check("real local device listed", table.row_to_device.get(0) == "LOCAL1", repr(table.row_to_device))


def test_select_device_routes_sentinel(monkey=None):
    obj = CoreMixin.__new__(CoreMixin)   # bare instance, skip __init__
    obj.local_player = FakeLP()
    # run threads synchronously so the test is deterministic
    orig_thread = lp_core_thread_patch(True)
    try:
        CoreMixin._select_device(obj, LOCAL_START_SENTINEL)
        check("sentinel -> start_and_activate called", obj.local_player.activated == 1,
              str(obj.local_player.activated))
    finally:
        lp_core_thread_patch(False, orig_thread)


def test_select_device_routes_real_id():
    obj = CoreMixin.__new__(CoreMixin)
    obj.local_player = FakeLP()
    obj.spotify = FakeSpotify()
    orig_thread = lp_core_thread_patch(True)
    try:
        CoreMixin._select_device(obj, "PHONE")
        check("real id -> transfer force_play", obj.spotify.transfers == [("PHONE", True)],
              repr(obj.spotify.transfers))
    finally:
        lp_core_thread_patch(False, orig_thread)


# --- helper: make threading.Thread in core.py run its target synchronously ---
import spt_tui.app.core as core_mod
class _SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t = target; self._a = args; self._k = kwargs or {}
    def start(self):
        if self._t: self._t(*self._a, **self._k)
def lp_core_thread_patch(on, saved=None):
    if on:
        prev = core_mod.threading.Thread
        core_mod.threading.Thread = _SyncThread
        return prev
    else:
        core_mod.threading.Thread = saved
        return None


ALL += [test_devices_table_injects_start_row, test_devices_table_no_start_row_when_running,
        test_select_device_routes_sentinel, test_select_device_routes_real_id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_local_player.py`
Expected: FAIL — the synthetic row is absent (`table.row_to_device.get(0)` is `"PHONE"`), and `CoreMixin` has no `_select_device`.

- [ ] **Step 3: Write minimal implementation**

In `spt_tui/app/queue_devices.py`, add the import near the top (after the existing `from ..spotify_client import SpotifyClient`):

```python
from ..local_player import LOCAL_START_SENTINEL
```

Replace `_populate_devices_table` (currently lines ~495-501) with:

```python
    def _populate_devices_table(self, table: DataTable, devs):
        table.clear()
        table.row_to_device = {}
        rows = list(devs or [])
        entries = []  # (mark, name, type, device_id)

        # Offer a synthetic "start" row only when the local player exists, is
        # usable, and is not already a real device in the list.
        lp = getattr(self, "local_player", None)
        if lp is not None and lp.is_available() and not lp.is_running() \
                and lp.device_name not in {d.get("name") for d in rows}:
            entries.append((GLYPHS["dot_off"], f"{lp.device_name} (start)", "local",
                            LOCAL_START_SENTINEL))

        for d in rows:
            active = GLYPHS["dot_on"] if d.get("is_active") else GLYPHS["dot_off"]
            entries.append((active, d.get("name", "(no name)"), d.get("type", ""), d.get("id")))

        for i, (mark, name, dtype, dev_id) in enumerate(entries):
            table.add_row(mark, name, dtype, key=i)
            table.row_to_device[i] = dev_id
```

In `spt_tui/app/core.py`, add the import near the other app-package imports (after `from ..spotify_client import SpotifyClient`):

```python
from ..local_player import LocalPlayer, LOCAL_START_SENTINEL
```

Add the `_select_device` helper to `CoreMixin` (place it just above `on_data_table_row_selected`):

```python
    def _select_device(self, dev_id) -> None:
        """Route a Devices-view selection. The synthetic sentinel row starts and
        activates the local librespot player; any real id transfers to it."""
        if not dev_id:
            return
        if dev_id == LOCAL_START_SENTINEL:
            lp = getattr(self, "local_player", None)
            if lp is not None:
                threading.Thread(target=lp.start_and_activate, daemon=True).start()
            return
        threading.Thread(
            target=lambda: self.spotify.transfer(dev_id, force_play=True), daemon=True).start()
```

Replace the `devices_table` branch inside `on_data_table_row_selected` (currently lines ~1190-1194) with:

```python
        elif table.id == "devices_table" and hasattr(table, "row_to_device"):
            dev_id = table.row_to_device.get(event.row_key)
            if dev_id:
                self._select_device(dev_id)
                self._back_one_level()
```

(The focus-restoration block that follows this branch stays exactly as it is.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_local_player.py`
Expected: PASS — `31/31 checks passed`.

- [ ] **Step 5: Commit**

```bash
git add spt_tui/app/queue_devices.py spt_tui/app/core.py tests/test_local_player.py
git commit -m "feat(local-player): Devices start row and selection routing"
```

---

## Task 8: Wire into app lifecycle (instantiate / autostart / teardown)

**Files:**
- Modify: `spt_tui/app/core.py:34` (`CoreMixin.__init__`)
- Modify: `spt_tui/app/core.py:176-291` (`on_mount`, add autostart thread)
- Modify: `spt_tui/app/core.py:308-313` (`on_unmount`, stop the player)
- Test: `tests/test_local_player.py`

**Interfaces:**
- Consumes: `LocalPlayer`, `self.spotify`, `self._open_url_in_browser` (existing app method used for the Spotify OAuth URL).
- Produces: `self.local_player` on the app; started on mount, stopped on unmount.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_local_player.py`. Test the lifecycle glue with a bare `CoreMixin` instance and a recording fake player (no Textual app needed):

```python
class LifecycleLP:
    def __init__(self):
        self.autostarted = 0; self.stopped = 0
    def maybe_autostart(self): self.autostarted += 1
    def stop(self): self.stopped += 1


def test_on_unmount_stops_player():
    obj = CoreMixin.__new__(CoreMixin)
    obj.local_player = LifecycleLP()
    obj._closing = False
    # _stop_all_intervals touches timers we don't have; stub it for this unit test
    obj._stop_all_intervals = lambda: None
    CoreMixin.on_unmount(obj)
    check("on_unmount stops local player", obj.local_player.stopped == 1,
          str(obj.local_player.stopped))
    check("on_unmount sets _closing", obj._closing is True)


def test_start_local_autostart_thread():
    obj = CoreMixin.__new__(CoreMixin)
    obj.local_player = LifecycleLP()
    orig = lp_core_thread_patch(True)
    try:
        CoreMixin._start_local_player(obj)
        check("autostart invoked", obj.local_player.autostarted == 1,
              str(obj.local_player.autostarted))
    finally:
        lp_core_thread_patch(False, orig)


ALL += [test_on_unmount_stops_player, test_start_local_autostart_thread]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_local_player.py`
Expected: FAIL — `on_unmount` does not call `local_player.stop()` (stopped == 0), and `CoreMixin` has no `_start_local_player`.

- [ ] **Step 3: Write minimal implementation**

In `CoreMixin.__init__` (`spt_tui/app/core.py`), right after `self.spotify = SpotifyClient()` (line 34), add:

```python
        try:
            self.local_player = LocalPlayer(
                self.spotify, open_url=getattr(self, "_open_url_in_browser", None))
        except Exception:
            logger.exception("LocalPlayer init failed")
            self.local_player = None
```

Add a small helper method to `CoreMixin` (place it just after `on_mount`):

```python
    def _start_local_player(self) -> None:
        """Kick off the polite local-player autostart on a daemon thread so the
        UI thread is never blocked by spawn / device-poll / get_playback."""
        lp = getattr(self, "local_player", None)
        if lp is None:
            return
        try:
            threading.Thread(target=lp.maybe_autostart, daemon=True).start()
        except Exception:
            logger.exception("Could not start local player autostart thread")
```

Call it near the end of `on_mount`, right after the playlist retry worker is started (after line ~291, inside the method):

```python
        self._start_local_player()
```

In `on_unmount` (lines ~308-313), add the stop call after `_stop_all_intervals()`:

```python
    def on_unmount(self) -> None:
        """Application teardown. Textual has already flagged the app as closing
        by this point; we mirror it on `_closing` (which background workers read)
        and stop every timer so no worker paints into a torn-down app."""
        self._closing = True
        self._stop_all_intervals()
        lp = getattr(self, "local_player", None)
        if lp is not None:
            try:
                lp.stop()
            except Exception:
                logger.exception("Stopping local player during teardown failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_local_player.py`
Expected: PASS — `33/33 checks passed`.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `python tests/run_all.py -k local_player` then `python tests/run_all.py -k devices`
Expected: both suites PASS (the devices suite still passes because a bare app has `local_player` set, and with no binary `is_available()` is False so no synthetic row appears).

- [ ] **Step 6: Commit**

```bash
git add spt_tui/app/core.py tests/test_local_player.py
git commit -m "feat(local-player): instantiate, autostart on mount, stop on teardown"
```

---

## Task 9: Manual smoke test & docs note

**Files:**
- Modify: `README.md` (a short "Local player (experimental)" subsection)

**Interfaces:** none (documentation + manual verification).

- [ ] **Step 1: Manual smoke test (requires a real librespot binary + Premium)**

Place a real `librespot` (or `librespot.exe`) on `PATH`, or set `SPT_LIBRESPOT_PATH=/abs/path/to/librespot`. Launch the app with nothing else playing:

```bash
python -m spt_tui
```

Verify, in order:
1. First launch with no cached credentials: open the Devices view (`d`). A `SPT-TUI Local (start)` row is present. Select it → a browser opens for librespot's OAuth → after authorizing, playback transfers to the local device and audio plays with no other Spotify client running.
2. Quit and relaunch with nothing playing elsewhere: the local device auto-appears and becomes active (polite transfer, `force_play=False`).
3. Start playback on another device (e.g. phone), then relaunch SPT-TUI: the local device is listed but does **not** steal the phone's session.
4. Quit the app: no orphaned `librespot` process remains (check Task Manager / `ps`).

Record the actual librespot version used; hand it to Sub-project B to pin.

- [ ] **Step 2: Add the README subsection**

Add under the existing feature/usage section of `README.md`:

```markdown
### Local player (experimental)

SPT-TUI can run its own audio player via [librespot](https://github.com/librespot-org/librespot),
so it plays music without any other Spotify client open. Spotify **Premium** is
still required.

- Provide the binary: put `librespot` on your `PATH`, or set
  `SPT_LIBRESPOT_PATH` to its absolute path.
- First run: open Devices (`d`) and select **SPT-TUI Local (start)** to authorize
  once in your browser. After that it starts headless.
- On launch it activates automatically **only if nothing is already playing** on
  another device — it never interrupts an active session.

Config keys (in `spt_config.json`) / env vars:

| Key | Env | Default |
| --- | --- | --- |
| `local_player_enabled` | `SPT_LOCAL_PLAYER` | `true` |
| `librespot_path` | `SPT_LIBRESPOT_PATH` | (auto-discovered) |
| `local_player_autostart` | `SPT_LOCAL_AUTOSTART` | `true` |
| `local_player_name` | `SPT_LOCAL_NAME` | `SPT-TUI Local` |

Shipping the binary and a one-click installer is tracked separately (Sub-project B).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the experimental local player"
```

---

## Self-Review

**1. Spec coverage:**
- §2 auth decision (librespot's own OAuth) → Task 4. ✓
- §3 module boundary + integration points → Tasks 1-8 (module) + 7-8 (core/devices hooks). ✓
- §4 public API (`is_available`, `is_running`, `maybe_autostart`, `start_and_activate`, `stop`, `device_name`) → Tasks 1,3,4,5,6. ✓
- §5 binary discovery order → Task 1. ✓
- §6 OAuth flow (URL capture + browser open + credential cache) → Task 4. ✓
- §7 polite start (idle transfer, foreign-playing no-transfer, device-never-appears) → Task 5. ✓
- §8 Devices UX (synthetic row + sentinel routing) → Task 7. ✓
- §9 config (four keys, env precedence) → Task 1. ✓
- §10 errors/edge (missing binary degrade, teardown, thread safety) → Tasks 1,3,8. ✓
- §11 testing (mock subprocess, decision table, no live API) → every task. ✓
- §13 handoff (binary path contract) → documented in README (Task 9) and honoured by discovery (Task 1). ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases" — every code and test step is concrete. ✓

**3. Type consistency:** `binary_path`, `is_available`, `is_running`, `is_authenticated`, `credentials_path`, `_build_argv(binary, *, login)`, `_spawn(*, login)`, `authenticate`, `start`, `_wait_for_local_device`, `maybe_autostart`, `start_and_activate`, `stop`, `device_name`, `_select_device`, `_start_local_player`, and `LOCAL_START_SENTINEL` are used with identical names/signatures across tasks. ✓

**Deferred to Sub-project B (out of scope here):** CI cross-build of `librespot.exe`, the Windows installer, adding the install dir to PATH, the `spt` launch command. The manual smoke test (Task 9) stands in for automated audio verification.

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints for review.
