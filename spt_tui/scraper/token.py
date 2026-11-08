"""Token anónimo: lo da la página *embed* de cualquier canción pública.

`open.spotify.com/embed/track/<id>` lleva un `<script id="__NEXT_DATA__">` con
`props.pageProps.state.settings.session.{accessToken, accessTokenExpirationTimestampMs}`.
Derivado de SpotifyScraper (MIT), ver NOTICE.md. El token nunca aparece en `repr` ni en errores.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .errors import NetworkError, NotFoundError, ParsingError, TokenError
from .http import Http

BOOTSTRAP_ID = "4uLU6hMCjMI75M1A2tKUQC"      # cualquier canción pública sirve
EMBED_URL = "https://open.spotify.com/embed/track/{}"
EXPIRY_SKEW_MS = 60_000                      # se renueva 1 min antes de caducar

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_HINT = "Es posible que Spotify haya cambiado su página embed; hay que actualizar spt_tui/scraper."


@dataclass(frozen=True)
class EmbedSession:
    access_token: str
    expires_at_ms: int

    def __repr__(self) -> str:
        return f"EmbedSession(expires_at_ms={self.expires_at_ms})"


def extract_next_data(html: str) -> dict:
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        raise ParsingError(f"No hay __NEXT_DATA__ en la página embed. {_HINT}")
    try:
        data = json.loads(m.group(1))
    except ValueError as exc:
        raise ParsingError(f"__NEXT_DATA__ no es JSON válido. {_HINT}") from exc
    if not isinstance(data, dict):
        raise ParsingError(f"__NEXT_DATA__ no es un objeto. {_HINT}")
    return data


def get_session(next_data: Mapping[str, Any]) -> EmbedSession:
    """Saca el token y su caducidad de `__NEXT_DATA__`."""
    node: Any = next_data
    for key in ("props", "pageProps", "state", "settings", "session"):
        node = node.get(key) if isinstance(node, Mapping) else None
        if not isinstance(node, Mapping):
            raise ParsingError(f"Falta 'session' en la página embed (en '{key}'). {_HINT}")
    token = node.get("accessToken")
    expires = node.get("accessTokenExpirationTimestampMs")
    if not isinstance(token, str) or not token:
        raise ParsingError(f"La sesión embed no trae accessToken. {_HINT}")
    if not isinstance(expires, int) or isinstance(expires, bool):
        raise ParsingError(f"La sesión embed no trae la caducidad del token. {_HINT}")
    return EmbedSession(token, expires)


def is_stale(expires_at_ms: int, now_ms: int) -> bool:
    return now_ms >= expires_at_ms - EXPIRY_SKEW_MS


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class AnonymousTokenProvider:
    """Token anónimo en caché; lo renueva al caducar o con `invalidate()`. Seguro entre hilos."""

    def __init__(self, http: Http, *, bootstrap_id: str = BOOTSTRAP_ID,
                 now_ms: Callable[[], int] = _now_ms):
        self._http = http
        self._url = EMBED_URL.format(bootstrap_id)
        self._now_ms = now_ms
        self._session: Optional[EmbedSession] = None
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            s = self._session
            if s is None or is_stale(s.expires_at_ms, self._now_ms()):
                s = self._session = self._bootstrap()
            return s.access_token

    def invalidate(self) -> None:
        with self._lock:
            self._session = None

    def _bootstrap(self) -> EmbedSession:
        try:
            resp = self._http.get(self._url)
            if resp.status_code != 200:
                raise TokenError(f"La página embed respondió HTTP {resp.status_code}.")
            return get_session(extract_next_data(resp.text))
        except (NetworkError, NotFoundError, ParsingError) as exc:
            raise TokenError("No se pudo obtener un token anónimo de la página embed.") from exc

    def __repr__(self) -> str:
        return f"AnonymousTokenProvider(cached={self._session is not None})"
