"""Keep yt-dlp current without touching app code.

yt-dlp breaks whenever YouTube changes and is fixed upstream within days, so the
app only has to be able to *update the tool*. It is used in two ways: the CLI
(mpv spawns ``yt-dlp`` for every track) and the ``yt_dlp`` module (imported lazily by
``stream_cache`` and the YouTube Music downloader). Both are refreshed here and the
new version is used by the next track, with no restart: nothing keeps ``yt_dlp``
imported between calls, and mpv starts a new yt-dlp process per file.

How it is updated depends on how the user installed the app:

* ``frozen``  – the Windows installer. ``Program Files`` is not writable, so the newest
  ``yt-dlp.exe`` (and the zipapp with the module) go to ``<data>/tools`` and win over the
  bundled copies (PATH / sys.path, see ``apply_user_tools``).
* ``pipx``    – ``pip install -U yt-dlp`` inside the app's own pipx venv, plus
  ``pipx upgrade yt-dlp`` for a separate ``pipx install yt-dlp`` CLI.
* ``venv`` / ``pip`` – ``pip install -U yt-dlp`` with the interpreter running the app.

Network and process calls are injectable so the tests never touch the network.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from . import config
from .config import logger

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
GITHUB_LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
GITHUB_ASSET = "https://github.com/yt-dlp/yt-dlp/releases/download/{tag}/{name}"
CHECK_TTL = 24 * 3600
_UA = {"User-Agent": "Spotify-TUI-yt-dlp-updater"}


def tools_dir() -> str:
    return os.path.join(config.CACHE_DIR, "tools")


def _check_file() -> str:
    return os.path.join(config.CACHE_DIR, "ytdlp_check.json")


# ---------------------------------------------------------------- versions
def parse_version(v: str) -> tuple:
    """'2026.10.26' / '2026.10.26.123456' -> comparable tuple; junk -> ()."""
    try:
        return tuple(int(p) for p in str(v).strip().split("."))
    except ValueError:
        return ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and bool(b) and a > b


def module_version() -> Optional[str]:
    """Version of the yt_dlp module this process would use (loaded one first)."""
    mod = sys.modules.get("yt_dlp.version")
    if mod is not None and getattr(mod, "__version__", None):
        return str(mod.__version__)
    try:
        return importlib.metadata.version("yt-dlp")
    except Exception:
        pass
    try:
        from yt_dlp import version as v   # noqa: WPS433 (lazy on purpose)
        return str(v.__version__)
    except Exception:
        return None


def cli_version(run: Callable = subprocess.run) -> Optional[str]:
    exe = shutil.which("yt-dlp")
    if not exe:
        return None
    try:
        out = run([exe, "--version"], capture_output=True, text=True, timeout=15)
        return (out.stdout or "").strip() or None
    except Exception:
        return None


def installed_version(run: Callable = subprocess.run) -> Optional[str]:
    """What the user actually plays with: the CLI mpv spawns, else the module."""
    return cli_version(run) or module_version()


def _http_get(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:       # nosec - fixed https URLs
        return r.read()


def latest_version(fetch: Callable[[str], bytes] = _http_get) -> Optional[str]:
    try:
        return str(json.loads(fetch(PYPI_URL))["info"]["version"])
    except Exception:
        logger.info("yt-dlp: could not read the latest version", exc_info=True)
        return None


def cached_latest(force: bool = False, fetch: Callable[[str], bytes] = _http_get,
                  now: Callable[[], float] = time.time) -> Optional[str]:
    """Latest version, asked at most once a day (the last answer survives offline)."""
    path = _check_file()
    data = {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
    fresh = now() - float(data.get("at") or 0) < CHECK_TTL
    if fresh and not force and data.get("latest"):
        return str(data["latest"])
    latest = latest_version(fetch)
    if latest:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"at": now(), "latest": latest}, f)
        except OSError:
            pass
        return latest
    return str(data["latest"]) if data.get("latest") else None


# ---------------------------------------------------------------- install kind
@dataclass
class Install:
    kind: str          # frozen | pipx | venv | pip | cli | none
    label: str

    @property
    def can_update(self) -> bool:
        return self.kind != "none"


def detect_install() -> Install:
    if getattr(sys, "frozen", False):
        return Install("frozen", "Windows installer (updates go to the app data folder)")
    has_module = importlib.util.find_spec("yt_dlp") is not None
    prefix = sys.prefix.replace("\\", "/").lower()
    if has_module:
        if "/pipx/venvs/" in prefix:
            return Install("pipx", "pipx")
        if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
            return Install("venv", "pip (virtual environment)")
        return Install("pip", "pip")
    if shutil.which("yt-dlp"):
        return Install("cli", "yt-dlp program on PATH")
    return Install("none", "not installed")


# ---------------------------------------------------------------- hot reload
def apply_user_tools() -> None:
    """Frozen build: put ``<data>/tools`` first so the newest yt-dlp wins over the bundled one.
    Safe to call repeatedly; a no-op elsewhere (pip / pipx manage their own copy)."""
    if not getattr(sys, "frozen", False):
        return
    d = tools_dir()
    if not os.path.isdir(d):
        return
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if d not in parts:
        os.environ["PATH"] = os.pathsep.join([d] + parts)
    zipapp = os.path.join(d, "yt-dlp")
    if os.path.isfile(zipapp) and zipapp not in sys.path:
        sys.path.insert(0, zipapp)


def reload_ytdlp() -> None:
    """Forget the imported module so the next ``import yt_dlp`` loads the new files."""
    for name in [m for m in sys.modules if m == "yt_dlp" or m.startswith("yt_dlp.")
                 or m == "yt_dlp_plugins" or m.startswith("yt_dlp_plugins.")]:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    apply_user_tools()


# ---------------------------------------------------------------- update
@dataclass
class Result:
    ok: bool
    message: str
    version: Optional[str] = None


def _run(cmd: list, run: Callable, timeout: float = 240):
    return run(cmd, capture_output=True, text=True, timeout=timeout)


def _tail(proc) -> str:
    text = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
    return text[-1][:200] if text else f"exit {proc.returncode}"


def _update_pip(run: Callable) -> Result:
    proc = _run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], run)
    if proc.returncode != 0:
        blob = (proc.stderr or "") + (proc.stdout or "")
        if "externally-managed" in blob:
            return Result(False, "This Python is managed by the system. Install the app with pipx "
                                 "or in a virtual environment to update yt-dlp from here.")
        return Result(False, f"pip failed: {_tail(proc)}")
    return Result(True, "yt-dlp updated with pip.")


def _update_separate_cli(run: Callable) -> Optional[str]:
    """A yt-dlp program that is not inside this Python (e.g. ``pipx install yt-dlp``):
    mpv uses it, so it has to be updated too. Returns a warning or None."""
    exe = shutil.which("yt-dlp")
    if not exe:
        return None
    real = os.path.realpath(exe).replace("\\", "/").lower()
    if os.path.realpath(sys.prefix).replace("\\", "/").lower() in real:
        return None                       # same environment, pip already did it
    try:
        if "/pipx/" in real or "/.local/bin/" in exe.replace("\\", "/"):
            pipx = shutil.which("pipx")
            if pipx:
                proc = _run([pipx, "upgrade", "yt-dlp"], run)
                return None if proc.returncode == 0 else f"pipx upgrade yt-dlp failed: {_tail(proc)}"
        proc = _run([exe, "-U"], run)
        return None if proc.returncode == 0 else f"yt-dlp -U failed: {_tail(proc)}"
    except Exception as exc:
        return f"could not update the yt-dlp program: {exc}"


def _download(url: str, fetch: Callable[[str], bytes], attempts: int = 4) -> bytes:
    last: Exception = RuntimeError("no attempt")
    for i in range(attempts):
        try:
            return fetch(url)
        except Exception as exc:          # GitHub answers 504 now and then
            last = exc
            time.sleep(min(2 * (i + 1), 8) if fetch is _http_get else 0)
    raise last


def _update_frozen(fetch: Callable[[str], bytes]) -> Result:
    if os.name != "nt":
        return Result(False, "Updating the bundled yt-dlp is only supported on Windows.")
    tag = json.loads(_download(GITHUB_LATEST, fetch))["tag_name"]
    sums = _download(GITHUB_ASSET.format(tag=tag, name="SHA2-256SUMS"), fetch).decode("utf-8", "replace")
    want = {}
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2:
            want[parts[1].lstrip("*")] = parts[0].lower()
    os.makedirs(tools_dir(), exist_ok=True)
    for name in ("yt-dlp.exe", "yt-dlp"):         # the program for mpv, the zipapp for the module
        data = _download(GITHUB_ASSET.format(tag=tag, name=name), fetch)
        if name in want and hashlib.sha256(data).hexdigest() != want[name]:
            return Result(False, f"Checksum mismatch for {name}; nothing was replaced.")
        dest = os.path.join(tools_dir(), name)
        tmp = dest + ".new"
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            os.replace(tmp, dest)
        except PermissionError:
            return Result(False, "yt-dlp is in use right now. Try again between two tracks.")
    return Result(True, f"yt-dlp {tag} downloaded.", tag)


def update(install: Optional[Install] = None, *, run: Callable = subprocess.run,
           fetch: Callable[[str], bytes] = _http_get) -> Result:
    """Update yt-dlp for this install and load it at once. Never raises."""
    inst = install or detect_install()
    try:
        if inst.kind == "frozen":
            res = _update_frozen(fetch)
        elif inst.kind in ("pipx", "venv", "pip"):
            res = _update_pip(run)
            if res.ok:
                warn = _update_separate_cli(run)
                if warn:
                    res = Result(True, f"{res.message} ({warn})")
        elif inst.kind == "cli":
            warn = _update_separate_cli(run)
            res = Result(warn is None, "yt-dlp program updated." if warn is None else warn)
        else:
            return Result(False, "yt-dlp is not installed (pip install \"Spotify-TUI[ytmusic]\").")
    except Exception as exc:
        logger.exception("yt-dlp update failed")
        return Result(False, f"Update failed: {exc}")
    if res.ok:
        reload_ytdlp()
        res.version = installed_version(run) or res.version
        try:
            os.remove(_check_file())      # the cached "latest" is stale now
        except OSError:
            pass
    return res
