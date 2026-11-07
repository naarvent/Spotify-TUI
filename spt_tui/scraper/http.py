"""Cliente HTTP mínimo: sesión `requests`, pausa entre peticiones y reintentos.

Sustituye al transporte `httpx` de SpotifyScraper (MIT, ver NOTICE.md) con lo imprescindible.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, Mapping, Optional

import requests

from .errors import NetworkError, NotFoundError, RateLimitedError

USER_AGENTS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
)

DEFAULT_TIMEOUT = 10.0
MIN_INTERVAL = 0.25     # segundos entre peticiones
MAX_ATTEMPTS = 3
RETRY_STATUS = (500, 502, 503, 504)


class Http:
    """GET con límite de ritmo y reintentos. Seguro entre hilos."""

    def __init__(self, *, session: Optional[requests.Session] = None, user_agent: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT, min_interval: float = MIN_INTERVAL,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
        })
        self.timeout = timeout
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._last = 0.0

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + self.min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()

    def get(self, url: str, headers: Optional[Mapping[str, str]] = None) -> requests.Response:
        """Devuelve la respuesta (también 401 y otros 4xx: los clasifica quien llama).

        Lanza `NotFoundError` con 404, `RateLimitedError` con 429 agotados los reintentos y
        `NetworkError` con fallos de red o 5xx persistentes."""
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            try:
                resp = self.session.get(url, headers=dict(headers or {}), timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = NetworkError(f"Error de red: {exc.__class__.__name__}")
                last_exc.__cause__ = exc
                self._sleep(self._backoff(attempt))
                continue
            if resp.status_code == 404:
                raise NotFoundError(f"HTTP 404 en {url.split('?')[0]}")
            if resp.status_code == 429:
                retry_after = _retry_after(resp)
                last_exc = RateLimitedError("Spotify limita las peticiones (HTTP 429).", retry_after)
                if attempt + 1 < MAX_ATTEMPTS:
                    self._sleep(min(retry_after if retry_after is not None else self._backoff(attempt), 30.0))
                continue
            if resp.status_code in RETRY_STATUS:
                last_exc = NetworkError(f"HTTP {resp.status_code}")
                self._sleep(self._backoff(attempt))
                continue
            return resp
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(0.5 * (2 ** attempt), 8.0) + random.random() * 0.25

    def close(self) -> None:
        self.session.close()


def _retry_after(resp: requests.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After")
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except ValueError:
        return None
