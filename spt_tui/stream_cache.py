"""Resolve YouTube watch URLs to direct audio URLs ahead of time, so skipping tracks is instant.

mpv resolves a ``music.youtube.com/watch?v=…`` URL through yt-dlp every time an entry starts
(~1–1.6 s). Handing mpv the direct stream URL instead starts it in ~0.7 s. This module only
*produces* those URLs; it knows nothing about mpv or the queue.

* ``prefetch(urls)`` states which URLs are wanted NOW (the next few in the queue). Anything
  queued earlier that is not in that list is dropped, so changing the queue (shuffle, new
  playlist, skip) never leaves useless work behind. A resolution already running finishes and
  is cached (it may be wanted again).
* ``get(url)`` returns a fresh direct URL or ``None`` — never blocks, never raises.
* A failed resolution is remembered for ``NEGATIVE_TTL`` seconds so a broken video is not retried
  in a loop. Direct URLs are bound to the IP and expire (``expire=`` param, ~6 h): they are kept
  only ``MAX_AGE`` seconds or until their own expiry minus a margin.
* At most ``workers`` resolutions run at once and ``capacity`` URLs are kept (LRU).
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, Iterable, List, Optional

from .config import logger

MAX_AGE = 5 * 3600          # direct URLs live ~6 h; never trust one for longer than this
EXPIRY_MARGIN = 600         # …and drop it 10 min before its own ``expire=``
NEGATIVE_TTL = 120          # do not retry a failed resolution for 2 min
DEFAULT_WORKERS = 2
DEFAULT_CAPACITY = 20
_EXPIRE_RE = re.compile(r"[?&/]expire[=/](\d+)")


def ytdlp_extractor(timeout: float = 15.0) -> Callable[[str], str]:
    """Extractor using the yt-dlp library in-process (lighter than spawning the CLI each time).
    A new ``YoutubeDL`` per call: instances are not thread-safe."""
    def extract(url: str) -> str:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
                "format": "bestaudio/best", "socket_timeout": timeout, "retries": 1,
                "extractor_retries": 1, "cachedir": False}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        direct = (info or {}).get("url")
        if not direct:      # a format list, not a single pick: take the chosen one
            fmts = (info or {}).get("requested_formats") or []
            direct = fmts[0].get("url") if fmts else None
        if not direct or not str(direct).startswith("http"):
            raise RuntimeError("yt-dlp returned no direct audio URL")
        return str(direct)
    return extract


class StreamResolver:
    def __init__(self, extractor: Optional[Callable[[str], str]] = None, *,
                 workers: int = DEFAULT_WORKERS, capacity: int = DEFAULT_CAPACITY,
                 now: Callable[[], float] = time.time):
        self._extract = extractor or ytdlp_extractor()
        self._workers = max(1, int(workers))
        self._capacity = max(1, int(capacity))
        self._now = now
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._cache: "OrderedDict[str, tuple]" = OrderedDict()   # url -> (direct, valid_until)
        self._failed: Dict[str, float] = {}                      # url -> retry_after
        self._wanted: List[str] = []                             # pending, in priority order
        self._running: set = set()                               # being resolved right now
        self._threads: List[threading.Thread] = []
        self._closed = False
        self.resolved = 0                                        # stats (tests, debugging)
        self.failures = 0

    # -- public ---------------------------------------------------------- #
    def get(self, url: str) -> Optional[str]:
        """A fresh direct URL for ``url`` or None. Never blocks."""
        with self._lock:
            hit = self._cache.get(url)
            if hit is None:
                return None
            direct, valid_until = hit
            if self._now() >= valid_until:
                del self._cache[url]
                return None
            self._cache.move_to_end(url)
            return direct

    def prefetch(self, urls: Iterable[str]) -> None:
        """Replace the wish list with ``urls`` (priority order). Already cached, failed-recently
        and in-flight URLs are skipped; pending ones no longer listed are forgotten."""
        with self._lock:
            if self._closed:
                return
            now = self._now()
            want = []
            for u in urls or []:
                if not u or u in want:
                    continue
                if u in self._cache and now < self._cache[u][1]:
                    continue
                if u in self._running or self._failed.get(u, 0) > now:
                    continue
                want.append(u)
            self._wanted = want
            if want:
                self._spawn_locked()
                self._wake.notify_all()

    def invalidate(self, url: str, *, failed: bool = False) -> None:
        """Forget ``url``'s direct URL (it stopped working). ``failed=True`` also keeps it from
        being re-resolved for NEGATIVE_TTL."""
        with self._lock:
            self._cache.pop(url, None)
            if failed:
                self._failed[url] = self._now() + NEGATIVE_TTL

    def cancel_pending(self) -> None:
        """Drop everything not started yet (queue replaced / provider switched)."""
        with self._lock:
            self._wanted = []

    def clear(self) -> None:
        with self._lock:
            self._wanted = []
            self._cache.clear()
            self._failed.clear()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._wanted = []
            self._wake.notify_all()

    def resolve_now(self, url: str) -> Optional[str]:
        """Blocking resolve (cache first). Used when a track must start right now."""
        direct = self.get(url)
        if direct:
            return direct
        return self._resolve(url)

    # -- internals ------------------------------------------------------- #
    def _spawn_locked(self) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        while len(self._threads) < self._workers:
            t = threading.Thread(target=self._worker, name="spt-stream-resolver", daemon=True)
            self._threads.append(t)
            t.start()

    def _worker(self) -> None:
        while True:
            with self._lock:
                while not self._closed and not self._wanted:
                    if not self._wake.wait(timeout=30.0) and not self._wanted:
                        return      # idle for 30 s: let the thread go, prefetch() restarts it
                if self._closed:
                    return
                url = self._wanted.pop(0)
                if url in self._running:
                    continue
                self._running.add(url)
            try:
                self._resolve(url)
            finally:
                with self._lock:
                    self._running.discard(url)

    def _resolve(self, url: str) -> Optional[str]:
        try:
            direct = self._extract(url)
        except Exception as exc:        # network, removed video, yt-dlp changed…
            logger.debug("stream resolve failed for %s: %s", url, type(exc).__name__)
            with self._lock:
                self.failures += 1
                self._failed[url] = self._now() + NEGATIVE_TTL
            return None
        with self._lock:
            valid_until = self._now() + MAX_AGE
            m = _EXPIRE_RE.search(direct)
            if m:
                valid_until = min(valid_until, int(m.group(1)) - EXPIRY_MARGIN)
            if valid_until <= self._now():
                return None
            self._cache[url] = (direct, valid_until)
            self._cache.move_to_end(url)
            while len(self._cache) > self._capacity:
                self._cache.popitem(last=False)
            self._failed.pop(url, None)
            self.resolved += 1
        return direct
