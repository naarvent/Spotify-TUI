"""View-only ordering of an open playlist's track table.

Pure functions, no Textual: the playlist itself is never reordered on Spotify,
only the rows shown. Every row carries ``_pos`` (its position in the playlist as
loaded), which is the stable final tie-break and how "Default" restores the
playlist order.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Cycle order for `s`. "default" = the playlist's own order.
SORT_KEYS = ("default", "title", "artist", "album", "duration", "added")
SORT_LABELS = {
    "default": "Default",
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "duration": "Duration",
    "added": "Date added",
}

CFG_KEY = "playlist_sort"
CFG_DESC_KEY = "playlist_sort_desc"

# Library > Downloaded: same criteria plus file size; its own saved preference.
# "added" there is the download date. Rows without the value (no duration yet,
# unknown size) always go last instead of first.
DOWNLOADED_SORT_KEYS = ("default", "title", "artist", "album", "duration", "added", "size")
SORT_LABELS["size"] = "Size"
DOWNLOADED_CFG_KEY = "downloaded_sort"
DOWNLOADED_CFG_DESC_KEY = "downloaded_sort_desc"

# profile -> (keys, config key, config desc key, missing values last)
PROFILES = {
    "playlist": (SORT_KEYS, CFG_KEY, CFG_DESC_KEY, False),
    "downloaded": (DOWNLOADED_SORT_KEYS, DOWNLOADED_CFG_KEY, DOWNLOADED_CFG_DESC_KEY, True),
}


def profile(name: str):
    return PROFILES.get(name or "playlist", PROFILES["playlist"])


def _s(v) -> str:
    return v.casefold() if isinstance(v, str) else ""


def _pos(r: Dict) -> int:
    p = r.get("_pos")
    return p if isinstance(p, int) else 0


def _sort_tuple(r: Dict, key: str):
    """Key for one row. Text compares case-insensitively; empty values (local
    tracks, missing album) sort as empty strings / 0 and never raise."""
    title, album = _s(r.get("title")), _s(r.get("album"))
    artist = _s(r.get("_artist0") if r.get("_artist0") is not None else r.get("artist"))
    if key == "title":
        return (title, artist, album)
    if key == "artist":
        return (artist, album, title)
    if key == "album":
        return (album, artist, title)
    if key == "duration":
        d = r.get("_dur_ms")
        return (d if isinstance(d, (int, float)) else 0, title)
    if key == "added":
        # ISO-8601 timestamps sort correctly as strings.
        a = r.get("_added_at")
        return (a if isinstance(a, str) else "",)
    if key == "size":
        z = r.get("_size")
        return (z if isinstance(z, (int, float)) else 0, title)
    return ()


def _missing(r: Dict, key: str) -> bool:
    if key == "duration":
        return not r.get("_dur_ms")
    if key == "added":
        return not r.get("_added_at")
    if key == "size":
        return not r.get("_size")
    return False


def sort_rows(rows: List[Dict], key: str, desc: bool = False, *, keys=SORT_KEYS,
              missing_last: bool = False) -> List[Dict]:
    """Return a new list of ``rows`` in view order.

    ``default`` is playlist order (``desc`` reverses it). Ties always fall back
    to the original position (ascending), so the result is deterministic.
    ``missing_last``: rows without a value for ``key`` go after the others in
    both directions (Downloaded: songs whose duration is not known yet)."""
    if key not in keys:
        key = "default"
    if key == "default":
        return sorted(rows, key=_pos, reverse=bool(desc))
    tail = []
    if missing_last:
        tail = sorted((r for r in rows if _missing(r, key)), key=_pos)
        rows = [r for r in rows if not _missing(r, key)]
    # Two stable passes: position ascending first, then the criterion — so
    # reversing the criterion does not also reverse the tie order.
    out = sorted(rows, key=_pos)
    out.sort(key=lambda r: _sort_tuple(r, key), reverse=bool(desc))
    return out + tail


def next_key(key: str, keys=SORT_KEYS) -> str:
    try:
        i = keys.index(key)
    except ValueError:
        return keys[0]
    return keys[(i + 1) % len(keys)]


def load_pref(cfg, *, keys=SORT_KEYS, cfg_key=CFG_KEY, cfg_desc_key=CFG_DESC_KEY) -> Tuple[str, bool]:
    """(key, desc) from the local config; anything missing/invalid -> default."""
    if not isinstance(cfg, dict):
        return "default", False
    key = cfg.get(cfg_key)
    if key not in keys:
        key = "default"
    desc = cfg.get(cfg_desc_key)
    return key, desc is True


def label(key: str, desc: bool) -> Optional[str]:
    """Short title suffix, or None when the playlist order is shown as-is."""
    if key not in SORT_LABELS or (key == "default" and not desc):
        return None
    return f"sort: {SORT_LABELS[key]} {'↓' if desc else '↑'}"
