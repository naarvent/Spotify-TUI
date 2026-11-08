"""Offline copies of tracks (YouTube Music and Subsonic), with a size cap.

Where: ``<CACHE_DIR>/offline/<provider>/`` (by default
``~/Documents/naarvent's projects/Spotify_TUI/offline/ytmusic/`` and
``…/offline/subsonic/``), one audio file per track plus ``index.json``:

    {"version": 1, "tracks": {"<track id>": {"file": "<name>", "size": <bytes>,
                                             "added": <ts>, "used": <ts>,
                                             "track": {minimal track dict}}}}

Downloads are written to ``.partial/`` first and moved in only when complete,
so an interrupted download never looks like a finished one.

Cap: ``offline_max_bytes`` in ``spt_config.json`` (Settings > Offline
downloads, "1 GB" by default, 50 MB – 64 GB). When a new file would go over
it, the least recently used copies (``used`` = last played or added) are
deleted first. A single file bigger than the whole cap is refused.

Nothing here touches the network: the provider downloads (``_download_file``)
and hands the finished file to :meth:`OfflineStore.add`.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from . import config
from .config import logger
from .local_library import atomic_write_json, minimal_track

OFFLINE_DEFAULT_BYTES = 1024 * 1024 * 1024                 # 1 GB
OFFLINE_MIN_BYTES = 50 * 1024 * 1024                       # 50 MB
OFFLINE_MAX_BYTES_LIMIT = 64 * 1024 * 1024 * 1024          # 64 GB
CONFIG_KEY = "offline_max_bytes"
_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def probe_duration_ms(path: str, *, timeout: float = 10.0) -> int:
    """Duration of a local audio file in ms via ffprobe (0 if unknown, no
    ffprobe or any error). Reads the container header only: fast, no network."""
    exe = shutil.which("ffprobe")
    if not exe or not path or not os.path.isfile(path):
        return 0
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", path],
                             capture_output=True, text=True, timeout=timeout, check=False)
        val = float((out.stdout or "").strip().splitlines()[0])
        return int(round(val * 1000)) if val > 0 else 0
    except Exception:
        return 0


def parse_clock(text) -> int:
    """'3:05' / '1:02:03' -> ms (0 if not a clock)."""
    try:
        secs = 0
        for part in str(text or "").strip().split(":"):
            secs = secs * 60 + int(part)
        return secs * 1000 if ":" in str(text or "") else 0
    except ValueError:
        return 0


class OfflineError(RuntimeError):
    """A download could not be stored (safe to show)."""


def offline_root(provider_name: str) -> str:
    return os.path.join(config.CACHE_DIR, "offline", provider_name)


def configured_limit() -> int:
    """The cap from spt_config.json, clamped to the allowed range."""
    cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
    try:
        n = int(cfg.get(CONFIG_KEY) or OFFLINE_DEFAULT_BYTES)
    except (TypeError, ValueError):
        n = OFFLINE_DEFAULT_BYTES
    return max(OFFLINE_MIN_BYTES, min(OFFLINE_MAX_BYTES_LIMIT, n))


def safe_name(track_id: str) -> str:
    """A file-system safe, collision-free stem for a track id."""
    s = str(track_id or "")
    clean = _SAFE_RE.sub("_", s)[:80] or "track"
    if clean != s:
        clean += "-" + hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]
    return clean


class OfflineStore:
    def __init__(self, provider_name: str, *, root: Optional[str] = None,
                 max_bytes: Optional[int] = None, now: Callable[[], float] = time.time):
        self.provider = provider_name
        self.root = root or offline_root(provider_name)
        self._fixed_limit = max_bytes
        self._now = now
        self._lock = threading.RLock()
        self._index: Optional[Dict[str, dict]] = None

    # -- index ----------------------------------------------------------- #
    @property
    def index_path(self) -> str:
        return os.path.join(self.root, "index.json")

    @property
    def partial_dir(self) -> str:
        return os.path.join(self.root, ".partial")

    @property
    def limit(self) -> int:
        return int(self._fixed_limit) if self._fixed_limit else configured_limit()

    def _load(self) -> Dict[str, dict]:
        if self._index is not None:
            return self._index
        data = {}
        try:
            import json
            with open(self.index_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            data = raw.get("tracks") if isinstance(raw, dict) else {}
            data = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            data = {}
        except Exception:
            logger.exception("offline index unreadable; starting empty")
            data = {}
        self._index = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("file")}
        return self._index

    def _save(self) -> None:
        os.makedirs(self.root, exist_ok=True)
        atomic_write_json(self.index_path, {"version": 1, "tracks": self._load()})

    def _file(self, entry: dict) -> str:
        return os.path.join(self.root, os.path.basename(str(entry.get("file") or "")))

    # -- queries --------------------------------------------------------- #
    def path_for(self, track_id) -> Optional[str]:
        """The local file of ``track_id`` if it is really there (a file deleted
        by hand drops its index entry)."""
        if not track_id:
            return None
        with self._lock:
            e = self._load().get(str(track_id))
            if not e:
                return None
            p = self._file(e)
            if os.path.isfile(p):
                return p
            self._load().pop(str(track_id), None)
            try:
                self._save()
            except Exception:
                logger.exception("offline index save failed")
            return None

    def has(self, track_id) -> bool:
        if not track_id:
            return False
        with self._lock:
            return str(track_id) in self._load()

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._load().keys())

    def track(self, track_id) -> Optional[dict]:
        """Stored metadata of one copy (None if there is none)."""
        with self._lock:
            e = self._load().get(str(track_id or ""))
            return dict(e.get("track") or {"id": str(track_id)}) if e else None

    def tracks(self) -> List[dict]:
        """Stored tracks, most recently used first (for an offline listing)."""
        with self._lock:
            items = sorted(self._load().items(), key=lambda kv: -float(kv[1].get("used") or 0))
            return [dict(v.get("track") or {"id": k}) for k, v in items]

    def entries(self) -> List[dict]:
        """Every copy with its bookkeeping, newest download first:
        ``{"track", "added" (ts), "used" (ts), "size" (bytes)}``."""
        with self._lock:
            items = sorted(self._load().items(), key=lambda kv: -float(kv[1].get("added") or 0))
            return [{"track": dict(v.get("track") or {"id": k}), "added": float(v.get("added") or 0),
                     "used": float(v.get("used") or 0), "size": int(v.get("size") or 0)} for k, v in items]

    def total_bytes(self) -> int:
        with self._lock:
            return sum(int(e.get("size") or 0) for e in self._load().values())

    # -- changes --------------------------------------------------------- #
    def touch(self, track_id) -> None:
        with self._lock:
            e = self._load().get(str(track_id))
            if e is None:
                return
            e["used"] = self._now()
            try:
                self._save()
            except Exception:
                logger.debug("offline index touch failed")

    def new_partial(self, track_id) -> str:
        """Base path (no extension) for a download in progress."""
        os.makedirs(self.partial_dir, exist_ok=True)
        return os.path.join(self.partial_dir, safe_name(track_id))

    def add(self, track: dict, src_path: str) -> str:
        """Move a finished download in, record it and prune to the cap.
        Returns the stored path. Raises OfflineError if the file is missing
        or bigger than the whole cap (it is deleted then)."""
        tid = str((track or {}).get("id") or "")
        if not tid:
            raise OfflineError("track without id")
        if not src_path or not os.path.isfile(src_path):
            raise OfflineError("the download produced no file")
        size = os.path.getsize(src_path)
        if size <= 0:
            os.remove(src_path)
            raise OfflineError("the download is empty")
        if size > self.limit:
            os.remove(src_path)
            raise OfflineError(f"the file ({config.format_size(size)}) is bigger than the offline "
                               f"limit ({config.format_size(self.limit)})")
        ext = os.path.splitext(src_path)[1][:8] or ".audio"
        name = safe_name(tid) + ext
        with self._lock:
            os.makedirs(self.root, exist_ok=True)
            old = self._load().get(tid)
            if old and old.get("file") != name:
                self._unlink(old)
            dest = os.path.join(self.root, name)
            shutil.move(src_path, dest)
            now = self._now()
            self._load()[tid] = {"file": name, "size": size, "added": now, "used": now,
                                 "track": minimal_track(track)}
            removed = self.prune(keep=tid)
            self._save()
        if removed:
            logger.info("offline: pruned %d old copies to stay under %s", len(removed),
                        config.format_size(self.limit))
        return dest

    def _unlink(self, entry: dict) -> None:
        try:
            os.remove(self._file(entry))
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("offline: could not delete %s", entry.get("file"))

    def remove(self, track_id) -> bool:
        with self._lock:
            e = self._load().pop(str(track_id), None)
            if e is None:
                return False
            self._unlink(e)
            self._save()
            return True

    def prune(self, keep: Optional[str] = None, limit: Optional[int] = None) -> List[str]:
        """Delete least-recently-used copies until the total fits ``limit``
        (default: the cap). ``keep`` is never deleted. Returns removed ids."""
        cap = self.limit if limit is None else int(limit)
        removed = []
        with self._lock:
            idx = self._load()
            total = sum(int(e.get("size") or 0) for e in idx.values())
            for tid, e in sorted(idx.items(), key=lambda kv: float(kv[1].get("used") or 0)):
                if total <= cap:
                    break
                if tid == keep:
                    continue
                self._unlink(e)
                total -= int(e.get("size") or 0)
                removed.append(tid)
            for tid in removed:
                idx.pop(tid, None)
            if removed and limit is not None:
                self._save()
        return removed

    def missing_durations(self) -> List[str]:
        with self._lock:
            return [k for k, e in self._load().items()
                    if not int(((e.get("track") or {}).get("duration_ms")) or 0)]

    def set_duration(self, track_id, duration_ms: int, *, save: bool = True) -> bool:
        with self._lock:
            e = self._load().get(str(track_id))
            if not e or not duration_ms:
                return False
            e.setdefault("track", {"id": str(track_id)})["duration_ms"] = int(duration_ms)
            if save:
                self._save()
            return True

    def backfill_durations(self, probe: Callable[[str], int] = None,
                           should_stop: Callable[[], bool] = None) -> int:
        """Fill ``duration_ms`` of copies stored without it by probing their
        file (no re-download). Saves the index once at the end (same format).
        Returns how many entries got a duration."""
        probe = probe or probe_duration_ms
        done = 0
        for tid in self.missing_durations():
            if should_stop is not None and should_stop():
                break
            path = self.path_for(tid)
            ms = probe(path) if path else 0
            if ms and self.set_duration(tid, ms, save=False):
                done += 1
        if done:
            with self._lock:
                self._save()
        return done

    def clear_partials(self) -> None:
        shutil.rmtree(self.partial_dir, ignore_errors=True)
