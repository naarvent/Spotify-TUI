"""Cliente anónimo de Spotify (solo lectura, sin credenciales) para `track` y `playlist`.

    from spt_tui.scraper import ScraperClient
    with ScraperClient() as c:
        pl = c.playlist("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", max_tracks=None)

Devuelve dicts con la forma de la Web API de Spotify. Solo playlists públicas. Derivado de
SpotifyScraper (MIT), ver NOTICE.md.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from . import parse
from .errors import NotFoundError, ParsingError, ScraperError, TokenError, URLError
from .http import Http
from .pathfinder import PLAYLIST_PAGE, auth_headers, build_url, classify_response
from .token import AnonymousTokenProvider

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"[0-9A-Za-z]{22}")
_INTL_RE = re.compile(r"intl-[A-Za-z]+(?:-[A-Za-z]+)?")
_HOSTS = ("open.spotify.com", "play.spotify.com")
_KINDS = ("track", "album", "artist", "playlist", "episode", "show")
MAX_PAGES = 200            # tope de seguridad: 200 × 100 = 20.000 pistas


def parse_ref(value: str, kind_hint: Optional[str] = None) -> Tuple[str, str]:
    """URL, URI `spotify:<tipo>:<id>` o ID de 22 caracteres → `(tipo, id)`.

    Un ID suelto necesita `kind_hint`."""
    text = (value or "").strip()
    if not text:
        raise URLError("Referencia de Spotify vacía.")
    if _ID_RE.fullmatch(text):
        if kind_hint not in _KINDS:
            raise URLError(f"El ID {text!r} necesita saber de qué tipo es.")
        return kind_hint, text
    if text.startswith("spotify:"):
        parts = text.split(":")
        if len(parts) != 3 or parts[1] not in _KINDS or not _ID_RE.fullmatch(parts[2]):
            raise URLError(f"URI de Spotify no reconocida: {value!r}")
        return parts[1], parts[2]
    split = urlsplit(text if "://" in text else f"https://{text}")
    if split.scheme not in ("http", "https") or split.hostname not in _HOSTS:
        raise URLError(f"No es una URL de Spotify: {value!r}")
    segs = [s for s in split.path.split("/") if s]
    if segs and segs[0] == "embed":
        segs.pop(0)
    if segs and _INTL_RE.fullmatch(segs[0]):
        segs.pop(0)
    if len(segs) != 2 or segs[0] not in _KINDS or not _ID_RE.fullmatch(segs[1]):
        raise URLError(f"Ruta de Spotify no reconocida: {value!r}")
    return segs[0], segs[1]


class ScraperClient:
    def __init__(self, *, http: Optional[Http] = None, tokens: Optional[AnonymousTokenProvider] = None):
        self.http = http or Http()
        self.tokens = tokens or AnonymousTokenProvider(self.http)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "ScraperClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- API pública ------------------------------------------------------ #
    def track(self, value: str) -> dict:
        _, eid = self._ref(value, "track")
        union = self._union("track", eid, "trackUnion")
        return parse.parse_track(union)

    def playlist(self, value: str, *, max_tracks: Optional[int] = 100) -> dict:
        """Playlist pública con sus pistas. `max_tracks=None` trae todas.

        Si una página intermedia falla, devuelve lo recogido y deja `tracks["partial"] = True`."""
        _, eid = self._ref(value, "playlist")
        union = self._union("playlist", eid, "playlistV2")
        pl = parse.parse_playlist(union)
        items: List[dict] = pl["tracks"]["items"]
        total_raw = pl["tracks"]["total"]
        offset = parse.raw_item_count(union)
        pages = 1
        while (max_tracks is None or len(items) < max_tracks) and offset < total_raw and pages < MAX_PAGES:
            try:
                page = self._union("playlist", eid, "playlistV2",
                                   overrides={"offset": offset, "limit": PLAYLIST_PAGE})
                got = parse.raw_item_count(page)
                if got == 0:
                    break
                items.extend(parse.parse_playlist_items(page))
            except ScraperError as exc:
                log.warning("Paginación de la playlist %s detenida en %s: %s", eid, offset, exc)
                pl["tracks"]["partial"] = True
                break
            offset += got
            pages += 1
        if max_tracks is not None:
            del items[max_tracks:]
        return pl

    def playlist_tracks(self, value: str, *, max_tracks: Optional[int] = None) -> List[dict]:
        """Solo las pistas (formato de `SpotifySource.playlist_tracks`: dicts de pista)."""
        pl = self.playlist(value, max_tracks=max_tracks)
        return [it["track"] for it in pl["tracks"]["items"]]

    # -- interno ---------------------------------------------------------- #
    @staticmethod
    def _ref(value: str, kind: str) -> Tuple[str, str]:
        k, eid = parse_ref(value, kind)
        if k != kind:
            raise URLError(f"Se esperaba un enlace de {kind} y es de {k}.")
        return k, eid

    def _union(self, kind: str, eid: str, union_key: str, *,
               overrides: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        """Una consulta persistida; si el token está caducado lo renueva y reintenta una vez."""
        url = build_url(kind, eid, overrides=overrides)
        for attempt in (0, 1):
            resp = self.http.get(url, headers=auth_headers(self.tokens.token()))
            try:
                body = resp.json()
            except ValueError:
                body = None
            try:
                data = classify_response(resp.status_code, body)
            except TokenError:
                self.tokens.invalidate()
                if attempt:
                    raise
                continue
            union = data.get(union_key)
            if not isinstance(union, Mapping):
                raise ParsingError(f"La respuesta no trae {union_key!r}. "
                                   "Es posible que Spotify haya cambiado su API.")
            if union.get("__typename") == "NotFound":
                raise NotFoundError(str(union.get("message") or "Spotify no encuentra esa entidad."))
            return union
        raise TokenError("No se pudo autenticar con el token anónimo.")   # inalcanzable
