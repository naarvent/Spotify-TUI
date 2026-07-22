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
    # librespot writes credentials.json into its --cache dir after a successful
    # login; its presence means we can start headless (no browser).
    CREDENTIALS_FILE = "credentials.json"

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

    def is_running(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def _spawn(self, *, login: bool) -> bool:
        """Spawn librespot. Precondition: call only when not already running
        (is_running() is False); the caller owns stopping any prior process
        first. Returns True on a successful spawn, False if there is no binary
        or Popen fails."""
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
            with self._lock:
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
                except subprocess.TimeoutExpired:
                    p.kill()
                    try:
                        p.wait(timeout=5)  # reap after force-kill; avoid a zombie
                    except Exception:
                        logger.exception("LocalPlayer: librespot did not exit after kill")
        except Exception:
            logger.exception("LocalPlayer: error stopping librespot")
