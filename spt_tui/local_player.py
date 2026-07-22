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
import queue
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

    # Bound wait for the interactive OAuth to complete (browser round-trip).
    OAUTH_WAIT_SECONDS = 120.0

    # Bound wait for librespot to register as a Connect device after spawn.
    DEVICE_WAIT_SECONDS = 12.0

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
        self._starting = False
        # Last error line librespot printed, so a caller can surface it.
        self.last_error: Optional[str] = None

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

    def _extract_url(self, line: str) -> Optional[str]:
        s = (line or "").strip()
        i = s.find("https://")
        if i == -1:
            return None
        return s[i:].split()[0]

    def authenticate(self) -> bool:
        """Run librespot's OAuth once and return True when credentials are
        cached. librespot prints the auth URL on stdout; we read stdout on a
        background daemon thread (so a quiet pipe never blocks us) and poll for
        credentials.json, bounded by OAUTH_WAIT_SECONDS."""
        if self.is_authenticated():
            return True
        if not self._spawn(login=True):
            return False
        with self._lock:
            proc = self._proc
        if proc is None:
            return self.is_authenticated()

        # Drain librespot stdout on a daemon thread; the wait loop never calls
        # the blocking readline() itself, so it always honours the deadline.
        line_q = queue.Queue()

        def _reader(p):
            try:
                out = p.stdout
                if out is not None:
                    for ln in iter(out.readline, ""):
                        line_q.put(ln)
                        # librespot's own diagnostics are the only explanation we
                        # get when it dies on startup (a busy OAuth callback port,
                        # a missing audio backend); dropping them made those
                        # failures look like "nothing happened".
                        text = ln.rstrip()
                        if text:
                            logger.info("librespot: %s", text)
                            if "ERROR" in text:
                                self.last_error = text
            except Exception:
                logger.exception("LocalPlayer: librespot stdout reader failed")

        threading.Thread(target=_reader, args=(proc,), daemon=True).start()

        deadline = self._now() + self.OAUTH_WAIT_SECONDS
        opened = False
        while proc.poll() is None and self._now() < deadline:
            if not opened:
                try:
                    line = line_q.get_nowait()
                except queue.Empty:
                    line = ""
                if line:
                    url = self._extract_url(line)
                    if url:
                        try:
                            self._open_url(url)
                        except Exception:
                            logger.exception("LocalPlayer: opening auth url failed")
                        opened = True
            if self.is_authenticated():
                return True
            self._sleep(0.1)
        return self.is_authenticated()

    def start(self) -> bool:
        """Ensure librespot is running. Serialized: only one start may be in
        flight, so the autostart thread and an explicit device selection can
        never spawn two librespots sharing the same name and cache dir."""
        with self._lock:
            if self.is_running():
                return True
            in_flight = self._starting
            if not in_flight:
                self._starting = True
        if in_flight:
            # Another start is already running; wait (bounded) for its outcome
            # instead of spawning a second process.
            deadline = self._now() + self.OAUTH_WAIT_SECONDS
            while self._now() < deadline:
                if self.is_running():
                    return True
                with self._lock:
                    if not self._starting:
                        break
                self._sleep(0.1)
            return self.is_running()
        try:
            if self.is_authenticated():
                return self._spawn(login=False)
            return self.authenticate()
        finally:
            with self._lock:
                self._starting = False

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
            return  # unknown playback state — fail safe, never risk stealing a foreign session
        if pb.get("is_playing"):
            return  # something is playing elsewhere — stay available, do not transfer
        try:
            self._spotify.transfer(dev_id, force_play=False)
        except Exception:
            logger.exception("LocalPlayer: polite transfer to local device failed")

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
