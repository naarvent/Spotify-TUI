"""Respuestas GraphQL de Spotify → dicts con la forma de la Web API (lo que ya usa la TUI).

Solo `track` y `playlist`. Si Spotify cambia el JSON, los campos obligatorios lanzan `ParsingError`
con la ruta que falta. Derivado de SpotifyScraper (MIT), ver NOTICE.md.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .errors import ParsingError

_HINT = "Es posible que Spotify haya cambiado el formato; hay que actualizar spt_tui/scraper."


# --- utilidades ---------------------------------------------------------- #
def id_from_uri(uri: str) -> str:
    return uri.rsplit(":", 1)[-1]


def _map(node: Any, key: str) -> Optional[Mapping[str, Any]]:
    v = node.get(key) if isinstance(node, Mapping) else None
    return v if isinstance(v, Mapping) else None


def _items(node: Any) -> List[Mapping[str, Any]]:
    items = node.get("items") if isinstance(node, Mapping) else None
    return [i for i in items if isinstance(i, Mapping)] if isinstance(items, list) else []


def _str(node: Mapping[str, Any], key: str, path: str) -> str:
    v = node.get(key)
    if not isinstance(v, str):
        raise ParsingError(f"Falta {path!r} en la respuesta. {_HINT}")
    return v


def _int(node: Any, key: str) -> Optional[int]:
    v = node.get(key) if isinstance(node, Mapping) else None
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _images(sources: Any) -> List[dict]:
    if not isinstance(sources, list):
        return []
    return [{"url": str(s["url"]), "width": s.get("width"), "height": s.get("height")}
            for s in sources if isinstance(s, Mapping) and s.get("url")]


def _artists(node: Any) -> List[dict]:
    out = []
    for it in _items(node):
        profile = _map(it, "profile")
        uri = str(it.get("uri") or "")
        out.append({"id": str(it.get("id") or id_from_uri(uri)), "uri": uri,
                    "name": str(profile.get("name", "")) if profile else "", "type": "artist"})
    return out


def _album(node: Optional[Mapping[str, Any]]) -> Optional[dict]:
    if node is None:
        return None
    uri = node.get("uri")
    if not isinstance(uri, str):
        return None
    cover = _map(node, "coverArt")
    return {"id": str(node.get("id") or id_from_uri(uri)), "uri": uri,
            "name": str(node.get("name") or ""), "type": "album",
            "images": _images(cover.get("sources")) if cover else []}


def _explicit(data: Mapping[str, Any]) -> bool:
    rating = _map(data, "contentRating")
    return bool(rating and rating.get("label") == "EXPLICIT")


# --- track --------------------------------------------------------------- #
def parse_track(union: Mapping[str, Any]) -> dict:
    """`data["trackUnion"]` → track de la Web API."""
    uri = _str(union, "uri", "trackUnion.uri")
    name = _str(union, "name", "trackUnion.name")
    duration = _map(union, "duration")
    if duration is None or _int(duration, "totalMilliseconds") is None:
        raise ParsingError(f"Falta 'trackUnion.duration.totalMilliseconds'. {_HINT}")
    artists = _artists(_map(union, "firstArtist")) + _artists(_map(union, "otherArtists"))
    playability = _map(union, "playability") or {}
    return {"id": str(union.get("id") or id_from_uri(uri)), "uri": uri, "type": "track", "name": name,
            "duration_ms": duration["totalMilliseconds"], "explicit": _explicit(union),
            "artists": artists, "album": _album(_map(union, "albumOfTrack")),
            "track_number": _int(union, "trackNumber"),
            "is_playable": bool(playability.get("playable", False))}


# --- playlist ------------------------------------------------------------ #
def _playlist_track(data: Mapping[str, Any]) -> dict:
    uri = _str(data, "uri", "playlistV2.content.items[].itemV2.data.uri")
    name = _str(data, "name", "playlistV2.content.items[].itemV2.data.name")
    duration = _map(data, "trackDuration") or {}
    playability = _map(data, "playability") or {}
    return {"id": id_from_uri(uri), "uri": uri, "type": "track", "name": name,
            "duration_ms": _int(duration, "totalMilliseconds") or 0, "explicit": _explicit(data),
            "artists": _artists(_map(data, "artists")), "album": _album(_map(data, "albumOfTrack")),
            "track_number": _int(data, "trackNumber"),
            "is_playable": bool(playability.get("playable", False))}


def raw_item_count(union: Mapping[str, Any]) -> int:
    """Elementos que trae la página, de cualquier tipo: Spotify pagina por este número."""
    content = _map(union, "content")
    items = content.get("items") if content else None
    return len(items) if isinstance(items, list) else 0


def parse_playlist_items(union: Mapping[str, Any]) -> List[dict]:
    """`playlistV2` → `[{"track", "added_at"}]`. Se omiten ficheros locales y episodios."""
    out = []
    for item in _items(_map(union, "content")):
        data = _map(_map(item, "itemV2") or {}, "data")
        if data is None or data.get("__typename") != "Track":
            continue
        added = _map(item, "addedAt")
        out.append({"track": _playlist_track(data),
                    "added_at": added.get("isoString") if added and isinstance(added.get("isoString"), str)
                    else None})
    return out


def parse_playlist(union: Mapping[str, Any]) -> dict:
    """`data["playlistV2"]` (primera página) → playlist de la Web API con `tracks.items`.

    `tracks.total` es el total de Spotify (incluye lo que se omite); `tracks.raw_total` lo mismo,
    para que el paginador compare contra el contador bruto."""
    uri = _str(union, "uri", "playlistV2.uri")
    name = _str(union, "name", "playlistV2.name")
    content = _map(union, "content")
    total = _int(content, "totalCount") if content else None
    owner = _map(_map(union, "ownerV2") or {}, "data")
    imgs = _items(_map(union, "images"))
    return {"id": id_from_uri(uri), "uri": uri, "type": "playlist", "name": name,
            "description": union.get("description") if isinstance(union.get("description"), str) else "",
            "owner": {"display_name": owner.get("name"), "uri": owner.get("uri")} if owner and
                     isinstance(owner.get("name"), str) else None,
            "images": _images(imgs[0].get("sources")) if imgs else [],
            "tracks": {"total": total if total is not None else 0, "items": parse_playlist_items(union)}}
