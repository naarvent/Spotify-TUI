"""GraphQL «pathfinder» de Spotify: único sitio con los hashes de consultas persistidas.

Si Spotify rota un hash, `classify_response` lanza `ParsingError` (PersistedQueryNotFound) y se arregla
cambiando UNA línea de `OPERATIONS`. Sin E/S. Derivado de SpotifyScraper (MIT), ver NOTICE.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import urlencode

from .errors import NotFoundError, ParsingError, TokenError

PATHFINDER_URL = "https://api-partner.spotify.com/pathfinder/v1/query"

# Fecha en que se comprobaron los hashes contra Spotify de verdad. Actualízala al cambiarlos.
HASHES_VERIFIED = "pendiente (copiados de SpotifyScraper, 2026-11-06)"

_HINT = "Es posible que Spotify haya cambiado su API; hay que actualizar spt_tui/scraper."

PLAYLIST_PAGE = 100


@dataclass(frozen=True)
class Operation:
    name: str
    sha256: str
    build_variables: Callable[[str], Dict[str, Any]]


OPERATIONS: Dict[str, Operation] = {
    "track": Operation(
        "getTrack",
        "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294",
        lambda eid: {"uri": f"spotify:track:{eid}"},
    ),
    "playlist": Operation(
        "fetchPlaylist",
        "a65e12194ed5fc443a1cdebed5fabe33ca5b07b987185d63c72483867ad13cb4",
        lambda eid: {"uri": f"spotify:playlist:{eid}", "offset": 0, "limit": PLAYLIST_PAGE,
                     "enableWatchFeedEntrypoint": False},
    ),
}


def build_url(kind: str, entity_id: str, *, overrides: Optional[Mapping[str, Any]] = None) -> str:
    """URL GET de la consulta persistida de `kind` (`KeyError` si no está registrada)."""
    op = OPERATIONS[kind]
    variables = op.build_variables(entity_id)
    if overrides:
        variables.update(overrides)
    params = {
        "operationName": op.name,
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": op.sha256}},
                                 separators=(",", ":")),
    }
    return f"{PATHFINDER_URL}?{urlencode(params)}"


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "app-platform": "WebPlayer", "Accept": "application/json"}


def classify_response(status: int, body: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Devuelve `body["data"]` o lanza el error que corresponda.

    401 → `TokenError` (renovar token); sin JSON, `PersistedQueryNotFound` o sin `data` →
    `ParsingError`; `data` vacío o todo `null` → `NotFoundError`."""
    if status == 401:
        raise TokenError("Spotify rechazó el token anónimo (HTTP 401).")
    if not isinstance(body, Mapping):
        raise ParsingError(f"Pathfinder no devolvió JSON (HTTP {status}). {_HINT}")
    errors = body.get("errors")
    if isinstance(errors, list) and any(
            isinstance(e, Mapping) and e.get("message") == "PersistedQueryNotFound" for e in errors):
        raise ParsingError(f"Spotify cambió una consulta persistida (PersistedQueryNotFound). {_HINT}")
    data = body.get("data")
    if not isinstance(data, Mapping):
        raise ParsingError(f"La respuesta no trae 'data' (HTTP {status}). {_HINT}")
    if not data or all(v is None for v in data.values()):
        raise NotFoundError("Spotify no devolvió esa entidad (no existe o no es pública).")
    return dict(data)
