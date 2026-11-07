"""Provider abstraction: what a music backend can do and how the UI talks to it.

The UI was written against the Spotify Web API, so it consumes *Spotify-shaped*
dicts (a track has ``id``/``uri``/``name``/``artists``/``album``/``duration_ms``,
a page has ``items``/``total``/``next``, playback state has ``item``/
``progress_ms``/``is_playing``...). Every provider speaks that same shape, which
lets the existing views render Subsonic or YouTube Music results unchanged.

A provider exposes two surfaces:

* the high-level methods the UI calls directly (``user_playlists``,
  ``playlist_items``, ``check_saved_tracks``, ``start_playback``, ``pause``...),
  mirroring the historical ``SpotifyClient`` API; and
* ``ensure()``, which for Spotify returns the spotipy client and for every other
  provider returns a :class:`SpotifyShapedAdapter` — a small object with the
  handful of spotipy method names the views call (``search``, ``album_tracks``,
  ``artist_albums``, ``current_playback``...). Anything the adapter does not
  implement raises ``AttributeError``, so the UI's existing ``hasattr`` probes
  and ``try/except`` fallbacks treat it as "not available" instead of crashing.

:class:`Capabilities` tells the UI which actions make sense; it hides or
disables the rest with a status-line notice (see ``CoreMixin._require_cap``).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from ..config import logger


class ProviderError(RuntimeError):
    """A provider-level failure with a message that is safe to show the user."""


class ProviderUnsupported(ProviderError):
    """The active provider does not support this operation."""


@dataclass(frozen=True)
class Capabilities:
    """What the active provider supports. The UI reads these flags; it never
    checks the provider's type.

    ``can_play`` means "can start playback of a chosen item" (Enter on a row);
    ``can_control_playback`` covers play/pause/next/prev on whatever is playing.
    """
    can_search: bool = True
    can_play: bool = True
    can_control_playback: bool = True
    can_seek: bool = True
    can_set_volume: bool = True
    can_shuffle_repeat: bool = True
    can_control_queue: bool = True
    can_like: bool = True
    can_edit_playlists: bool = True
    can_list_devices: bool = True
    can_browse_library: bool = True
    # Podcasts / episodes (saved shows and episodes): Spotify only.
    can_podcasts: bool = True
    # Following artists (Saved Artists): Spotify's account, Subsonic stars,
    # or the local library (YouTube Music).
    can_follow_artists: bool = True
    # Artist page (Enter on an artist): albums/singles come from
    # artist_albums for every provider; these say whether the provider can
    # also give the artist's top tracks and related/similar artists.
    can_artist_top_tracks: bool = False
    can_related_artists: bool = False
    # Radio / discovery: an endless-ish list of tracks like a seed track,
    # artist or playlist (Provider.radio). Opt-in.
    can_radio: bool = False
    # Offline copies of tracks (spt_tui/offline.py): download_track /
    # remove_download / is_downloaded. Opt-in (YouTube Music, Subsonic).
    can_download: bool = False
    # librespot (local Spotify Connect device) only makes sense for Spotify.
    supports_local_player: bool = False
    # Whether the Spotify OAuth flow (client id/secret, redirect URL) is needed.
    needs_spotify_auth: bool = False
    # Likes / saved items / playlists are kept in the on-disk local library
    # (spt_tui.local_library) instead of a remote account.
    local_library: bool = False
    # "Import Playlists" (add a public playlist by URL/id) makes sense.
    can_import_playlists: bool = True
    # "Import from Spotify" into this provider's local library (needs a
    # public catalogue search: YouTube Music).
    can_import_from_spotify: bool = False
    # Create Playlist asks "Public?" / "Collaborative?" (Spotify's model) and
    # for a description only where the provider can store them.
    playlist_visibility: bool = True
    playlist_description: bool = True

    def replace(self, **changes) -> "Capabilities":
        return dataclasses.replace(self, **changes)

    def as_dict(self) -> Dict[str, bool]:
        return dataclasses.asdict(self)


# Human-readable names for the capability notices on the status line.
CAPABILITY_LABELS = {
    "can_search": "Search",
    "can_play": "Playback",
    "can_control_playback": "Playback control",
    "can_seek": "Seeking",
    "can_set_volume": "Volume control",
    "can_shuffle_repeat": "Shuffle / repeat",
    "can_control_queue": "The queue",
    "can_like": "Likes",
    "can_edit_playlists": "Editing playlists",
    "can_list_devices": "Devices",
    "can_browse_library": "This library view",
    "can_podcasts": "Podcasts and episodes",
    "can_follow_artists": "Following artists",
    "can_artist_top_tracks": "Artist top tracks",
    "can_related_artists": "Related artists",
    "can_radio": "Radio",
    "can_download": "Offline downloads",
}

# What a radio can start from (Provider.radio seed_kind).
RADIO_SEEDS = ("track", "artist", "playlist")


def _items_of(page) -> List[dict]:
    if isinstance(page, dict):
        return [x for x in (page.get("items") or []) if isinstance(x, dict)]
    if isinstance(page, list):
        return [x for x in page if isinstance(x, dict)]
    return []


def build_artist_overview(provider, artist_id: str, artist_name: Optional[str] = None) -> dict:
    """Everything the artist page shows, from the provider's per-section calls.

    Returns ``{"top_tracks", "albums", "singles", "related": [Spotify-shaped
    dicts], "notes": [str]}``. A section the provider cannot give (raises
    :class:`ProviderUnsupported`) or that fails comes back empty with a short
    note for the status line; it never raises, so one dead endpoint does not
    blank the whole page. Works with any object exposing ``ensure()`` (the
    UI's test fakes) — the per-section methods are looked up with getattr.
    """
    out = {"top_tracks": [], "albums": [], "singles": [], "related": [], "notes": []}
    pname = getattr(provider, "display_name", None) or "this provider"

    def run(key, label, fn):
        if fn is None:
            out["notes"].append(f"{label} not available with {pname}.")
            return
        try:
            out[key] = _items_of(fn())
        except ProviderUnsupported as exc:
            out["notes"].append(str(exc) or f"{label} not available with {pname}.")
        except Exception as exc:
            logger.warning("artist page: %s failed: %s", label, type(exc).__name__)
            out["notes"].append(f"{label} could not be loaded ({type(exc).__name__}).")

    top = getattr(provider, "artist_top_tracks", None)
    rel = getattr(provider, "related_artists", None)
    run("top_tracks", "Top tracks",
        (lambda: top(artist_id, artist_name=artist_name)) if callable(top) else None)
    try:
        sp = provider.ensure()
    except Exception:
        sp = None
    albums = getattr(sp, "artist_albums", None) if sp is not None else None
    if callable(albums):
        run("albums", "Albums", lambda: albums(artist_id, album_type="album", limit=50))
        run("singles", "Singles", lambda: albums(artist_id, album_type="single", limit=50))
    else:
        out["notes"].append(f"The discography is not available with {pname}.")
    run("related", "Related artists",
        (lambda: rel(artist_id, artist_name=artist_name)) if callable(rel) else None)
    # The same release can come back in both lists (Subsonic without release
    # types, a provider ignoring album_type): keep it once, as an album.
    seen = {a.get("id") for a in out["albums"] if a.get("id")}
    out["singles"] = [a for a in out["singles"] if a.get("id") not in seen]
    return out


def empty_page(key: Optional[str] = None) -> dict:
    page = {"items": [], "total": 0, "next": None, "offset": 0, "limit": 0}
    return {key: page} if key else page


def make_page(items: List[dict], *, offset: int = 0, limit: Optional[int] = None,
              total: Optional[int] = None) -> dict:
    """Slice ``items`` into a Spotify-style page (``next`` is a truthy marker
    when more items follow; the UI only tests it for truthiness)."""
    items = list(items or [])
    total = len(items) if total is None else int(total)
    offset = max(0, int(offset or 0))
    if limit is None:
        chunk = items[offset:]
    else:
        chunk = items[offset:offset + max(0, int(limit))]
    nxt = (offset + len(chunk)) < total and len(chunk) > 0
    return {"items": chunk, "total": total, "offset": offset,
            "limit": len(chunk) if limit is None else int(limit),
            "next": f"offset={offset + len(chunk)}" if nxt else None}


class Provider:
    """Base class for every music backend. Subclasses override what they
    support; the defaults are safe no-ops / empty results so a partially
    capable provider never crashes the UI."""

    name: str = "base"
    display_name: str = "Provider"

    def __init__(self) -> None:
        self._last_create_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Identity, availability and capabilities
    # ------------------------------------------------------------------ #
    @property
    def capabilities(self) -> Capabilities:
        return Capabilities()

    def unavailable_reason(self) -> Optional[str]:
        """None when the provider can be used; otherwise a short explanation
        (missing optional dependency, missing binary, missing config)."""
        return None

    def status_notice(self) -> Optional[str]:
        """A persistent "degraded mode" explanation for the status line, if any."""
        return None

    def unsupported_message(self, capability: str) -> str:
        label = CAPABILITY_LABELS.get(capability, capability)
        return f"{label} is not available with {self.display_name}."

    def refresh_account(self) -> None:
        """Hook to (re)detect account limits. Called once on a worker thread
        after startup. Default: nothing to detect."""
        return None

    def close(self) -> None:
        """Release resources (subprocesses, sockets). Idempotent."""
        return None

    # ------------------------------------------------------------------ #
    # Auth (Spotify OAuth only; others have nothing to authorise)
    # ------------------------------------------------------------------ #
    def has_cached_token(self) -> bool:
        return True

    def prepare_authorize_url(self) -> Optional[str]:
        return None

    def finish_authorization(self, redirected_url_or_code: str) -> bool:
        return False

    def ensure(self):
        """Spotipy-compatible object for the views' direct API calls."""
        adapter = getattr(self, "_adapter", None)
        if adapter is None:
            adapter = self._adapter = SpotifyShapedAdapter(self)
        return adapter

    def ensure_app(self):
        return self.ensure()

    # ------------------------------------------------------------------ #
    # Formatting helpers (shared by every provider; the UI calls them)
    # ------------------------------------------------------------------ #
    @staticmethod
    def fmt_duration(ms: int) -> str:
        s = int(round((ms or 0) / 1000))
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

    @staticmethod
    def fmt_date(iso_str: Optional[str]) -> str:
        return iso_str[:10] if iso_str else ""

    @staticmethod
    def playlist_entry_track(entry):
        """Return the track/episode dict of one playlist-items entry.

        Spotify's February 2026 API change renamed the per-entry wrapper from
        `track` to `item` (and deprecated `track`). Accept both so the app works
        against either shape."""
        if not isinstance(entry, dict):
            return {}
        return entry.get("item") or entry.get("track") or {}

    def _normalize_track_id(self, any_id: Optional[str]) -> Optional[str]:
        if not any_id:
            return None
        s = str(any_id).strip()
        if ":" in s:
            s = s.split(":")[-1]
        if "/" in s:
            s = s.rsplit("/", 1)[-1]
        return s or None

    # ------------------------------------------------------------------ #
    # Search and catalogue (Spotify-shaped pages)
    # ------------------------------------------------------------------ #
    def search(self, query: str, kind: str = "track", limit: int = 10, offset: int = 0) -> dict:
        """One page of ``kind`` results ('track', 'artist', 'album',
        'playlist'; Spotify also 'show' / 'episode')."""
        return empty_page()

    def track(self, track_id: str) -> dict:
        return {}

    def album_tracks(self, album_id: str, limit: int = 50, offset: int = 0) -> dict:
        return empty_page()

    def artist_albums(self, artist_id: str, album_type: Optional[str] = None,
                      limit: int = 50, offset: int = 0) -> dict:
        return empty_page()

    def playlist(self, playlist_id: str) -> dict:
        raise ProviderUnsupported(self.unsupported_message("can_browse_library"))

    # ------------------------------------------------------------------ #
    # Library and playlists
    # ------------------------------------------------------------------ #
    def user_playlists(self, limit=50, offset=0) -> dict:
        return make_page([], offset=offset, limit=limit)

    def playlist_items(self, playlist_id, limit=100, offset=0, fields=None) -> dict:
        return make_page([], offset=offset, limit=limit)

    def saved_tracks(self, limit=50, offset=0) -> dict:
        return make_page([], offset=offset, limit=limit)

    def saved_albums(self, limit=50, offset=0) -> dict:
        return make_page([], offset=offset, limit=limit)

    def recently_played(self, limit=50) -> dict:
        return {"items": []}

    def check_saved_tracks(self, ids: List[str], on_batch: Optional[Callable] = None) -> List[bool]:
        out = [False] * len(ids or [])
        if on_batch is not None and out:
            try:
                on_batch(0, list(out))
            except Exception:
                logger.exception("check_saved_tracks: on_batch callback failed")
        return out

    def save_tracks(self, ids: List[str]) -> bool:
        return False

    def remove_tracks(self, ids: List[str]) -> bool:
        return False

    def create_playlist(self, name: str, public: bool = True, collaborative: bool = False,
                        description: str = ""):
        self._last_create_error = self.unsupported_message("can_edit_playlists")
        return None

    def add_items_to_playlist(self, playlist_id: str, uris: List[str]) -> bool:
        return False

    def delete_playlist(self, playlist_id: str) -> bool:
        raise ProviderUnsupported(self.unsupported_message("can_edit_playlists"))

    def remove_playlist_positions(self, playlist_id: str, positions: List[int]) -> bool:
        raise ProviderUnsupported(self.unsupported_message("can_edit_playlists"))

    def follow_playlist(self, playlist_id: str) -> bool:
        """Add a (public) playlist to the user's list ("Import Playlists")."""
        raise ProviderUnsupported(self.unsupported_message("can_edit_playlists"))

    def remove_playlist_uris(self, playlist_id: str, uris: List[str]) -> bool:
        raise ProviderUnsupported(self.unsupported_message("can_edit_playlists"))

    def can_rename_playlist(self, playlist_id: str) -> bool:
        return False

    def rename_playlist(self, playlist_id: str, name: str) -> bool:
        raise ProviderUnsupported("Renaming this playlist is not available here.")

    # Saved albums / followed artists / followed playlists / shows / episodes.
    # ``kind`` is 'album', 'artist', 'playlist', 'show' or 'episode'. Defaults:
    # nothing is saved and saving is refused — never an AttributeError.
    def saved_contains(self, kind: str, ids: List[str]) -> List[bool]:
        return [False] * len(ids or [])

    def save_items(self, kind: str, ids: List[str]) -> bool:
        return False

    def remove_items(self, kind: str, ids: List[str]) -> bool:
        return False

    def followed_artists(self, limit: int = 50) -> dict:
        return make_page([], offset=0, limit=limit)

    # Artist page. Defaults: no top tracks / related artists (the page shows
    # the discography and says what is missing); providers override.
    def artist_top_tracks(self, artist_id: str, artist_name: Optional[str] = None,
                          limit: int = 10) -> List[dict]:
        raise ProviderUnsupported(self.unsupported_message("can_artist_top_tracks"))

    def related_artists(self, artist_id: str, artist_name: Optional[str] = None,
                        limit: int = 20) -> List[dict]:
        raise ProviderUnsupported(self.unsupported_message("can_related_artists"))

    def radio(self, seed_kind: str, seed_id: str, *, seed: Optional[dict] = None,
              limit: int = 50) -> dict:
        """Tracks "like" a seed: ``seed_kind`` is one of :data:`RADIO_SEEDS`.

        Returns ``{"tracks": [Spotify-shaped tracks], "notes": [str]}``; the
        seed track itself may come first (YouTube Music does that). Raises
        :class:`ProviderUnsupported` when the provider (or that seed kind)
        has no radio."""
        raise ProviderUnsupported(self.unsupported_message("can_radio"))

    # Offline copies. Defaults: none (see MpvBackedProvider for the real ones).
    def is_downloaded(self, track_id) -> bool:
        return False

    def download_track(self, track: dict) -> str:
        raise ProviderUnsupported(self.unsupported_message("can_download"))

    def remove_download(self, track_id) -> bool:
        return False

    def offline_usage(self):
        return None

    def offline_tracks(self) -> List[dict]:
        return []

    def offline_entries(self) -> List[dict]:
        return []

    def offline_backfill(self, should_stop=None) -> int:
        return 0

    def offline_missing_durations(self) -> int:
        return 0

    def artist_overview(self, artist_id: str, artist_name: Optional[str] = None) -> dict:
        """Top tracks, albums, singles and related artists in one dict (see
        :func:`build_artist_overview`). Providers that get it all from one
        request (YouTube Music, Subsonic) override this."""
        return build_artist_overview(self, artist_id, artist_name)

    def take_notices(self) -> List[str]:
        """Messages for the status line produced in the background (e.g. a
        track that turned out to be unavailable and was skipped)."""
        return []

    # ------------------------------------------------------------------ #
    # Playback
    # ------------------------------------------------------------------ #
    def start_playback(self, *, context_uri: Optional[str] = None,
                       uris: Optional[List[str]] = None, offset: Optional[dict] = None):
        raise ProviderUnsupported(self.unsupported_message("can_play"))

    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None

    def next(self) -> None:
        return None

    def prev(self) -> None:
        return None

    def shuffle(self, state: bool) -> None:
        return None

    def repeat(self, state: str) -> None:
        return None

    def set_volume(self, volume: int) -> None:
        return None

    def seek_ms(self, ms: int) -> None:
        return None

    def get_playback(self) -> dict:
        return {}

    def add_to_queue(self, uri: str) -> None:
        raise ProviderUnsupported(self.unsupported_message("can_control_queue"))

    def queue(self) -> dict:
        return {"currently_playing": None, "queue": []}

    def devices(self) -> List[dict]:
        return []

    def transfer(self, device_id: str, force_play: bool = False) -> None:
        return None


class SpotifyShapedAdapter:
    """The subset of spotipy's method names the views call through
    ``provider.ensure()``, mapped onto a non-Spotify provider.

    Unknown attributes raise ``AttributeError`` so ``hasattr`` probes in the UI
    (follow/unfollow, saved shows...) correctly report "not supported"."""

    _SEARCH_KEYS = {"track": "tracks", "artist": "artists", "album": "albums",
                    "playlist": "playlists", "show": "shows", "episode": "episodes"}

    def __init__(self, provider: Provider):
        self._p = provider

    def search(self, q, limit=10, offset=0, type="track", market=None, **_kw):
        out = {}
        for kind in str(type or "track").split(","):
            kind = kind.strip()
            key = self._SEARCH_KEYS.get(kind)
            if not key:
                continue
            if kind in ("show", "episode") and not self._p.capabilities.can_podcasts:
                out[key] = empty_page()
                continue
            out[key] = self._p.search(q, kind=kind, limit=limit, offset=offset)
        return out

    def track(self, track_id, market=None):
        return self._p.track(track_id)

    def album_tracks(self, album_id, limit=50, offset=0, market=None):
        return self._p.album_tracks(album_id, limit=limit, offset=offset)

    def artist_albums(self, artist_id, album_type=None, include_groups=None,
                      country=None, limit=50, offset=0):
        return self._p.artist_albums(artist_id, album_type=album_type or include_groups,
                                     limit=limit, offset=offset)

    def playlist(self, playlist_id, fields=None, market=None, additional_types=None):
        return self._p.playlist(playlist_id)

    def playlist_items(self, playlist_id, fields=None, limit=100, offset=0, **_kw):
        return self._p.playlist_items(playlist_id, limit=limit, offset=offset)

    def current_user_playlists(self, limit=50, offset=0):
        return self._p.user_playlists(limit=limit, offset=offset)

    def current_user_saved_tracks(self, limit=20, offset=0, market=None):
        return self._p.saved_tracks(limit=limit, offset=offset)

    def current_user_saved_tracks_contains(self, tracks=None):
        return self._p.check_saved_tracks(list(tracks or []))

    def current_user_saved_albums(self, limit=20, offset=0, market=None):
        return self._p.saved_albums(limit=limit, offset=offset)

    def current_user_followed_artists(self, limit=20, after=None):
        if after:
            return {"artists": empty_page()}
        return {"artists": self._p.followed_artists(limit=limit)}

    def current_user_saved_shows(self, limit=20, offset=0, market=None):
        return empty_page()

    def current_user_saved_episodes(self, limit=20, offset=0, market=None):
        return empty_page()

    def show_episodes(self, show_id, limit=50, offset=0, market=None):
        return empty_page()

    def episode(self, episode_id, market=None):
        return {}

    # -- saved-state checks and toggles (one family per kind) -------------- #
    def _save(self, kind, ids):
        if not self._p.save_items(kind, list(ids or [])):
            raise ProviderError(f"could not save the {kind}")

    def _remove(self, kind, ids):
        if not self._p.remove_items(kind, list(ids or [])):
            raise ProviderError(f"could not remove the {kind}")

    def current_user_saved_albums_contains(self, albums=None):
        return self._p.saved_contains("album", list(albums or []))

    def current_user_saved_albums_add(self, albums=None):
        self._save("album", albums)

    def current_user_saved_albums_delete(self, albums=None):
        self._remove("album", albums)

    current_user_saved_albums_save = current_user_saved_albums_add
    current_user_saved_albums_remove = current_user_saved_albums_delete

    def current_user_saved_shows_contains(self, shows=None):
        return self._p.saved_contains("show", list(shows or []))

    def current_user_saved_shows_add(self, shows=None):
        self._save("show", shows)

    def current_user_saved_shows_delete(self, shows=None):
        self._remove("show", shows)

    current_user_saved_shows_save = current_user_saved_shows_add
    current_user_saved_shows_remove = current_user_saved_shows_delete

    def current_user_saved_episodes_contains(self, episodes=None):
        return self._p.saved_contains("episode", list(episodes or []))

    def current_user_saved_episodes_add(self, episodes=None):
        self._save("episode", episodes)

    def current_user_saved_episodes_delete(self, episodes=None):
        self._remove("episode", episodes)

    current_user_saved_episodes_save = current_user_saved_episodes_add
    current_user_saved_episodes_remove = current_user_saved_episodes_delete

    def current_user_follow_artists(self, ids=None):
        self._save("artist", ids)

    def current_user_unfollow_artists(self, ids=None):
        self._remove("artist", ids)

    user_follow_artists = follow_artists = current_user_follow_artists
    user_unfollow_artists = unfollow_artists = current_user_unfollow_artists

    # Not spotipy names: playlist follow state for providers whose playlist
    # "follow" is local. Their absence on the Spotify client keeps Spotify's
    # behaviour unchanged (the UI probes them with hasattr).
    def current_user_follows_playlists_contains(self, ids=None):
        return self._p.saved_contains("playlist", list(ids or []))

    def local_follow_playlists(self, ids=None):
        self._save("playlist", ids)

    def local_unfollow_playlists(self, ids=None):
        self._remove("playlist", ids)

    def current_user_recently_played(self, limit=50, **_kw):
        return self._p.recently_played(limit=limit)

    def current_playback(self, market=None, additional_types=None):
        return self._p.get_playback()

    def currently_playing(self, market=None, additional_types=None):
        return self._p.get_playback()

    def queue(self):
        return self._p.queue()

    def devices(self):
        return {"devices": self._p.devices()}

    def current_user_unfollow_playlist(self, playlist_id):
        if not self._p.delete_playlist(playlist_id):
            raise ProviderError("could not delete the playlist")

    def current_user_follow_playlist(self, playlist_id, public=None):
        if not self._p.follow_playlist(playlist_id):
            raise ProviderError("could not add the playlist")

    def playlist_remove_all_occurrences_of_items(self, playlist_id, items, snapshot_id=None):
        if not self._p.remove_playlist_uris(playlist_id, list(items or [])):
            raise ProviderError("could not remove the item from the playlist")

    def playlist_remove_specific_occurrences_of_items(self, playlist_id, items, snapshot_id=None):
        positions = []
        for it in items or []:
            positions.extend(int(p) for p in (it or {}).get("positions") or [])
        if not positions or not self._p.remove_playlist_positions(playlist_id, positions):
            raise ProviderError("could not remove the item from the playlist")

    def __getattr__(self, name):
        raise AttributeError(f"{type(self._p).__name__} has no '{name}'")
