"""Spotify provider: Spotify Web API wrapper, rate limiting and OAuth handling.

This is the historical ``SpotifyClient`` (``spt_tui/spotify_client.py`` still
re-exports it under that name), now a :class:`Provider`. On top of the original
behaviour it detects the account tier: with a Free account (``product !=
"premium"``) the Web API refuses every playback-control call, so the provider
reports a degraded :class:`Capabilities` set instead of letting each keypress
fail — search, library, likes and playlists keep working.
"""

from __future__ import annotations

import os
import time
import threading
import traceback
from typing import List, Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials

from .. import config
from ..config import logger, CACHE_PATH, SCOPE
from .base import Capabilities, Provider, ProviderError, ProviderUnsupported

# Capabilities of a Premium (or not yet detected) account.
# Top tracks / related artists exist in the Web API but Spotify removed them for
# development-mode apps (related artists and recommendations: 27 Nov 2024;
# artist top tracks: February 2026 migration guide). They are tried once and,
# on 403/404, reported as unavailable for the rest of the session.
_PREMIUM_CAPS = Capabilities(supports_local_player=True, needs_spotify_auth=True,
                             can_artist_top_tracks=True, can_related_artists=True,
                             can_radio=True)
# Free account: the Web API answers 403 PREMIUM_REQUIRED to every player
# endpoint, and librespot also needs Premium.
_FREE_CAPS = _PREMIUM_CAPS.replace(
    can_play=False, can_control_playback=False, can_seek=False,
    can_set_volume=False, can_shuffle_repeat=False, can_control_queue=False,
    can_list_devices=False, supports_local_player=False)

FREE_NOTICE = ("Spotify Free account: playback control needs Premium, so play/pause/"
               "seek/volume are disabled. Search, library, likes and playlists still "
               "work. Tip: set the provider to 'spotify_mpris' to control the Spotify "
               "web player in your browser.")


def is_premium_required_error(exc: BaseException) -> bool:
    """True for spotipy's 403 PREMIUM_REQUIRED (or its text form)."""
    reason = str(getattr(exc, "reason", "") or "")
    return "PREMIUM_REQUIRED" in reason or "PREMIUM_REQUIRED" in str(exc) \
        or "Premium required" in str(exc)

class RateLimiter:
    def __init__(self, max_calls: int = 25, period: float = 1.0) -> None:
        self.max_calls = max(1, int(max_calls))
        self.period = float(period)
        self._lock = threading.Lock()
        self._tokens = self.max_calls
        self._last = time.time()

    def acquire(self) -> None:
        with self._lock:
            now = time.time()
            elapsed = now - self._last
            if elapsed >= self.period:
                self._tokens = self.max_calls
                self._last = now

            if self._tokens > 0:
                self._tokens -= 1
                return

            to_wait = max(0.0, self.period - (now - self._last))
        time.sleep(to_wait)
        return self.acquire()

class _SpotifyProxy:
    def __init__(self, sp, rate_limiter: Optional[RateLimiter] = None):
        self._sp = sp
        self._rl = rate_limiter

    def __getattr__(self, name):
        attr = getattr(self._sp, name)
        if callable(attr):
            def wrapped(*args, **kwargs):
                try:
                    if self._rl:
                        self._rl.acquire()
                except Exception:
                    pass
                return attr(*args, **kwargs)
            return wrapped
        return attr

# What to say when Spotify answers 403/404 to an endpoint it retired for
# development-mode apps (Extended Quota apps may still have it).
RETIRED_ENDPOINTS = {
    "top_tracks": ("Artist top tracks: Spotify removed this endpoint for development-mode "
                   "apps (Feb 2026)."),
    "related": ("Related artists: Spotify no longer gives this to development-mode apps "
                "(since Nov 2024)."),
    "recommendations": ("Spotify recommendations: removed for development-mode apps "
                        "(since Nov 2024)."),
}


# Checked 2026-11-07: the Web API has no download endpoint, and Premium's
# offline downloads exist only inside Spotify's own apps, encrypted (DRM).
# Saving librespot's decrypted stream would be circumventing that, so it is
# not done. Pending: revisit if Spotify ever offers a sanctioned way.
DOWNLOAD_UNSUPPORTED = ("Offline downloads are not possible with Spotify: the Web API has no download "
                        "endpoint and Premium downloads only work inside Spotify's own apps (DRM). "
                        "Pending; use YouTube Music or Subsonic for offline copies.")


def is_retired_endpoint_error(exc: BaseException) -> bool:
    """403/404 from spotipy: the endpoint is gone (or forbidden) for this app."""
    status = getattr(exc, "http_status", None)
    return status in (403, 404) and not is_premium_required_error(exc)


class SpotifyProvider(Provider):
    name = "spotify"
    display_name = "Spotify"

    def __init__(self):
        super().__init__()
        # Account tier from current_user()['product']; None until detected.
        self._product: Optional[str] = None
        self.sp: Optional[spotipy.Spotify] = None
        self.sp_app: Optional[spotipy.Spotify] = None
        self._auth_manager: Optional[SpotifyOAuth] = None
        self._last_create_error: Optional[str] = None
        self._dev_cache = None  # (device_id, timestamp) — short-lived active-device cache
        self._ensure_lock = threading.Lock()  # guards lazy client creation across threads
        try:
            max_calls = int(os.getenv('SPT_MAX_CALLS', '25'))
        except Exception:
            max_calls = 25
        try:
            period = float(os.getenv('SPT_RATE_PERIOD', '1.0'))
        except Exception:
            period = 1.0
        try:
            self._rate_limiter = RateLimiter(max_calls=max_calls, period=period)
        except Exception:
            self._rate_limiter = RateLimiter(max_calls=5, period=1.0)

    # ------------------------------------------------------------------ #
    # Capabilities and the Free-account degraded mode
    # ------------------------------------------------------------------ #
    @property
    def product(self) -> Optional[str]:
        return getattr(self, "_product", None)

    def is_free_account(self) -> bool:
        p = self.product
        return bool(p) and str(p).lower() != "premium"

    @property
    def capabilities(self) -> Capabilities:
        return _FREE_CAPS if self.is_free_account() else _PREMIUM_CAPS

    def status_notice(self) -> Optional[str]:
        return FREE_NOTICE if self.is_free_account() else None

    def unsupported_message(self, capability: str) -> str:
        if capability == "can_download":
            return DOWNLOAD_UNSUPPORTED
        if self.is_free_account():
            return "Spotify Premium is required for playback control (Free account)."
        return super().unsupported_message(capability)

    def refresh_account(self) -> None:
        """Read the account tier (``product``) once. Never raises: if it cannot
        be read we keep the full capability set and the API decides."""
        try:
            user = self.ensure().current_user() or {}
        except Exception:
            logger.exception("SpotifyProvider.refresh_account: current_user failed")
            return
        product = user.get("product") if isinstance(user, dict) else None
        if product:
            self._product = str(product).lower()
            logger.info("Spotify account product: %s", self._product)

    def _mark_free(self) -> None:
        if not self.is_free_account():
            logger.info("Spotify answered PREMIUM_REQUIRED: switching to Free mode")
            self._product = "free"

    def ensure(self) -> spotipy.Spotify:
        if self.sp is not None:
            return self.sp
        with self._ensure_lock:
            if self.sp is not None:
                return self.sp
            try:
                open_browser_flag = threading.current_thread() is threading.main_thread()
                logger.debug("SpotifyClient.ensure: creating/refreshing SpotifyOAuth (open_browser=%s, thread=%s)", open_browser_flag, threading.current_thread().name)
                if getattr(self, "_auth_manager", None) is None:
                    self._auth_manager = SpotifyOAuth(
                        client_id=config.CLIENT_ID,
                        client_secret=config.CLIENT_SECRET,
                        redirect_uri=config.REDIRECT_URI,
                        scope=SCOPE,
                        cache_path=CACHE_PATH,
                        open_browser=open_browser_flag,
                        show_dialog=False,
                    )
                else:
                    try:
                        self._auth_manager.client_id = config.CLIENT_ID
                        self._auth_manager.client_secret = config.CLIENT_SECRET
                        self._auth_manager.redirect_uri = config.REDIRECT_URI
                    except Exception:
                        self._auth_manager = SpotifyOAuth(
                            client_id=config.CLIENT_ID,
                            client_secret=config.CLIENT_SECRET,
                            redirect_uri=config.REDIRECT_URI,
                            scope=SCOPE,
                            cache_path=CACHE_PATH,
                            open_browser=open_browser_flag,
                            show_dialog=False,
                        )

                try:
                    token_info = None
                    if hasattr(self._auth_manager, "get_cached_token"):
                        try:
                            token_info = self._auth_manager.get_cached_token()
                        except Exception:
                            token_info = None

                    if token_info and hasattr(self._auth_manager, "is_token_expired") and self._auth_manager.is_token_expired(token_info):
                        try:
                            refresh_token = token_info.get("refresh_token")
                            if refresh_token:
                                logger.debug("SpotifyClient.ensure: token expired, attempting refresh using refresh_token")
                                self._auth_manager.refresh_access_token(refresh_token)
                                logger.debug("SpotifyClient.ensure: token refreshed successfully")
                        except Exception:
                            logger.exception("SpotifyClient.ensure: refresh_access_token failed")

                except Exception:
                    logger.exception("SpotifyClient.ensure: error checking/refreshing cached token")

                self.sp = spotipy.Spotify(auth_manager=self._auth_manager)
                try:
                    self.sp = _SpotifyProxy(self.sp, getattr(self, '_rate_limiter'))
                except Exception:
                    pass
                logger.debug("SpotifyClient.ensure: Spotify client created successfully")
            except Exception:
                logger.exception("SpotifyClient.ensure: failed creating the Spotify client")
                try:
                    self.sp = spotipy.Spotify()
                    try:
                        self.sp = _SpotifyProxy(self.sp, getattr(self, '_rate_limiter'))
                    except Exception:
                        pass
                except Exception:
                    logger.exception("SpotifyClient.ensure: could not create fallback client")
                    raise
        return self.sp

    def has_cached_token(self) -> bool:
        try:
            return os.path.exists(CACHE_PATH) and os.path.getsize(CACHE_PATH) > 0
        except Exception:
            return False

    def prepare_authorize_url(self) -> Optional[str]:
        try:
            auth_manager = SpotifyOAuth(
                client_id=config.CLIENT_ID,
                client_secret=config.CLIENT_SECRET,
                redirect_uri=config.REDIRECT_URI,
                scope=SCOPE,
                cache_path=CACHE_PATH,
                open_browser=False,
                show_dialog=True,
            )
            self._pending_auth_manager = auth_manager
            return auth_manager.get_authorize_url()
        except Exception:
            logger.exception("prepare_authorize_url failed")
            return None

    def finish_authorization(self, redirected_url_or_code: str) -> bool:
        try:
            mgr = getattr(self, "_pending_auth_manager", None)
            if mgr is None:
                mgr = SpotifyOAuth(
                    client_id=config.CLIENT_ID,
                    client_secret=config.CLIENT_SECRET,
                    redirect_uri=config.REDIRECT_URI,
                    scope=SCOPE,
                    cache_path=CACHE_PATH,
                    open_browser=False,
                    show_dialog=True,
                )
                self._pending_auth_manager = mgr

            code = redirected_url_or_code
            try:
                from urllib.parse import urlparse, parse_qs
                if "http" in redirected_url_or_code:
                    q = urlparse(redirected_url_or_code).query
                    params = parse_qs(q)
                    if params.get("code"):
                        code = params.get("code")[0]
            except Exception:
                pass

            try:
                mgr.get_access_token(code)
            except TypeError:
                mgr.get_access_token(code, as_dict=True)
            try:
                self._auth_manager = mgr
            except Exception:
                pass

            self.sp = spotipy.Spotify(auth_manager=mgr)
            try:
                self.sp = _SpotifyProxy(self.sp, getattr(self, '_rate_limiter'))
            except Exception:
                pass
            try:
                del self._pending_auth_manager
            except Exception:
                pass
            return True
        except Exception:
            logger.exception("finish_authorization failed")
            return False

    def ensure_app(self) -> spotipy.Spotify:
        if self.sp_app is None:
            self.sp_app = spotipy.Spotify(
                auth_manager=SpotifyClientCredentials(
                    client_id=config.CLIENT_ID,
                    client_secret=config.CLIENT_SECRET,
                )
            )
            try:
                self.sp_app = _SpotifyProxy(self.sp_app, getattr(self, '_rate_limiter'))
            except Exception:
                pass
        return self.sp_app

    def _normalize_track_id(self, any_id: Optional[str]) -> Optional[str]:
        if not any_id:
            return None
        s = str(any_id).strip()
        if ":" in s:
            s = s.split(":")[-1]
        if "/" in s:
            s = s.rsplit("/", 1)[-1]
        return s or None

    @staticmethod
    def fmt_duration(ms: int) -> str:
        s = int(round((ms or 0) / 1000))
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

    @staticmethod
    def fmt_date(iso_str: Optional[str]) -> str:
        return iso_str[:10] if iso_str else ""

    # ------------------------------------------------------------------ #
    # Artist page
    # ------------------------------------------------------------------ #
    def _retired(self) -> set:
        r = getattr(self, "_retired_endpoints", None)
        if r is None:
            r = self._retired_endpoints = set()
        return r

    def _call_maybe_retired(self, key: str, fn):
        """Run a Web API call that Spotify may have retired for this app.
        A 403/404 marks it retired for the session and raises
        ProviderUnsupported with the explanation; other errors propagate."""
        if key in self._retired():
            raise ProviderUnsupported(RETIRED_ENDPOINTS[key])
        try:
            return fn()
        except ProviderUnsupported:
            raise
        except Exception as exc:
            if is_retired_endpoint_error(exc):
                self._retired().add(key)
                logger.info("spotify: %s endpoint unavailable (%s)", key,
                            getattr(exc, "http_status", "?"))
                raise ProviderUnsupported(RETIRED_ENDPOINTS[key]) from exc
            raise

    def artist_top_tracks(self, artist_id, artist_name=None, limit=10):
        res = self._call_maybe_retired(
            "top_tracks", lambda: self.ensure().artist_top_tracks(artist_id, country="from_token")) or {}
        return [t for t in (res.get("tracks") or []) if isinstance(t, dict)][:int(limit or 10)]

    def related_artists(self, artist_id, artist_name=None, limit=20):
        res = self._call_maybe_retired(
            "related", lambda: self.ensure().artist_related_artists(artist_id)) or {}
        return [a for a in (res.get("artists") or []) if isinstance(a, dict)][:int(limit or 20)]

    # Spotify's recommendations take at most 5 seeds in total.
    RADIO_MAX_SEEDS = 5

    def radio(self, seed_kind, seed_id, *, seed=None, limit=50):
        """GET /recommendations (retired for development-mode apps since
        Nov 2024: tried once, then reported as unavailable)."""
        limit = max(1, min(int(limit or 50), 100))
        kw = {}
        notes = []
        if seed_kind == "track":
            kw["seed_tracks"] = [self._normalize_track_id(seed_id)]
        elif seed_kind == "artist":
            kw["seed_artists"] = [self._normalize_track_id(seed_id)]
        elif seed_kind == "playlist":
            page = self.playlist_items(seed_id, limit=50, offset=0) or {}
            ids = []
            for e in page.get("items") or []:
                t = self.playlist_entry_track(e)
                if t.get("id") and t.get("type", "track") == "track" and t["id"] not in ids:
                    ids.append(t["id"])
                if len(ids) >= self.RADIO_MAX_SEEDS:
                    break
            if not ids:
                raise ProviderUnsupported("This playlist is empty: nothing to start a radio from.")
            kw["seed_tracks"] = ids
            notes.append(f"Playlist radio: seeded with its first {len(ids)} tracks.")
        else:
            raise ProviderUnsupported("Radio starts from a track, an artist or a playlist.")
        res = self._call_maybe_retired(
            "recommendations", lambda: self.ensure().recommendations(limit=limit, **kw)) or {}
        tracks = [t for t in (res.get("tracks") or []) if isinstance(t, dict) and t.get("uri")]
        return {"tracks": tracks, "notes": notes}

    def user_playlists(self, limit=50, offset=0):
        return self.ensure().current_user_playlists(limit=limit, offset=offset)

    def create_playlist(self, name: str, public: bool = True, collaborative: bool = False, description: str = ""):
        try:
            self._last_create_error = None
            user = self.ensure().current_user() or {}
            user_id = user.get("id")
            if not user_id:
                msg = "create_playlist: could not determine current user id"
                logger.error(msg)
                self._last_create_error = msg
                return None
            try:
                pl = self.ensure().user_playlist_create(user_id, name, public=public, collaborative=collaborative, description=description)
            except Exception as e:
                try:
                    status = getattr(e, 'http_status', None)
                except Exception:
                    status = None
                msg = f"create_playlist failed (status={status}): {repr(e)}"
                logger.exception(msg)
                self._last_create_error = msg
                return None
            return pl
        except Exception:
            logger.exception("Error creating playlist")
            try:
                self._last_create_error = traceback.format_exc()
            except Exception:
                self._last_create_error = "unknown error"
            return None

    def add_items_to_playlist(self, playlist_id: str, uris: List[str]):
        try:
            for i in range(0, len(uris), 100):
                chunk = uris[i:i+100]
                try:
                    self.ensure().playlist_add_items(playlist_id, chunk)
                except Exception:
                    items_to_try = []
                    for u in chunk:
                        if not u:
                            continue
                        norm = self._normalize_track_id(u) or u
                        items_to_try.append(u)
                        if not u.startswith('spotify:'):
                            items_to_try.append(f'spotify:track:{norm}')
                            items_to_try.append(f'spotify:episode:{norm}')
                    seen = set(); final_items = []
                    for it in items_to_try:
                        if it not in seen:
                            final_items.append(it); seen.add(it)
                    if final_items:
                        self.ensure().playlist_add_items(playlist_id, final_items)
            return True
        except Exception:
            logger.exception("Error adding items to playlist %s", playlist_id)
            return False

    @staticmethod
    def playlist_entry_track(entry):
        """Return the track/episode dict of one playlist-items entry.

        Spotify's February 2026 API change renamed the per-entry wrapper from
        `track` to `item` (and deprecated `track`). Accept both so the app works
        against either shape."""
        if not isinstance(entry, dict):
            return {}
        return entry.get("item") or entry.get("track") or {}

    def playlist_items(self, playlist_id, limit=100, offset=0, fields=None):
        if fields:
            return self.ensure().playlist_items(playlist_id, limit=limit, offset=offset, fields=fields)
        return self.ensure().playlist_items(playlist_id, limit=limit, offset=offset)

    def saved_tracks(self, limit=50, offset=0):
        return self.ensure().current_user_saved_tracks(limit=limit, offset=offset)

    def recently_played(self, limit=50):
        return self.ensure().current_user_recently_played(limit=limit)

    # Max ids per call to the saved-tracks endpoints. spotipy routes all three
    # through `me/library` with the ids in the query string, and that endpoint
    # answers `400 Too many uris requested` above 40 — measured against the live
    # API: 40 succeeds, 41 does not. It used to be 50 here, so *every* batch was
    # rejected and fell through to the per-id retry below: 51 requests instead of
    # 1, ~612 for a 600-track playlist, and a logged traceback per batch.
    SAVED_BATCH = 40

    def check_saved_tracks(self, ids: List[str], on_batch=None) -> List[bool]:
        """Return one bool per id (aligned to `ids`). Never raises: a single
        bad id (local track, episode, unavailable) in a batch would otherwise
        make the whole call fail — which zeroed the hearts on big playlists.

        The API takes 50 ids per call, so a long playlist means many sequential
        calls. `on_batch(start_index, values)` is invoked as each batch lands, so
        a caller can show those hearts instead of waiting for the last batch.
        """
        out: List[bool] = []
        for i in range(0, len(ids), self.SAVED_BATCH):
            batch = ids[i:i + self.SAVED_BATCH]
            vals: List[bool] = []
            try:
                res = self.ensure().current_user_saved_tracks_contains(batch)
                if res is None or len(res) != len(batch):
                    raise ValueError("unexpected saved_tracks_contains response")
                vals = [bool(x) for x in res]
            except Exception as exc:
                logger.warning("check_saved_tracks: batch of %d failed (%s), "
                               "retrying one id at a time", len(batch),
                               str(exc).split(" - ")[0])
                for tid in batch:
                    try:
                        r = self.ensure().current_user_saved_tracks_contains([tid])
                        vals.append(bool(r[0]) if r else False)
                    except Exception:
                        vals.append(False)
            out.extend(vals)
            if on_batch is not None:
                try:
                    on_batch(i, vals)
                except Exception:
                    logger.exception("check_saved_tracks: on_batch callback failed")
        return out

    def save_tracks(self, ids: List[str]) -> bool:
        """Return True only if the save actually went through (callers rely on
        this to avoid showing a confirmed state on failure)."""
        try:
            for chunk in range(0, len(ids), self.SAVED_BATCH):
                self.ensure().current_user_saved_tracks_add(ids[chunk:chunk + self.SAVED_BATCH])
            return True
        except Exception:
            logger.exception("Error save_tracks")
            return False

    def remove_tracks(self, ids: List[str]) -> bool:
        try:
            for chunk in range(0, len(ids), self.SAVED_BATCH):
                self.ensure().current_user_saved_tracks_delete(ids[chunk:chunk + self.SAVED_BATCH])
            return True
        except Exception:
            logger.exception("Error remove_tracks")
            return False

    def devices(self):
        return self.ensure().devices().get("devices", [])

    def transfer(self, device_id: str, force_play: bool = False):
        self._dev_cache = None  # active device is changing
        self.ensure().transfer_playback(device_id, force_play=force_play)
        time.sleep(0.25)
        try:
            pb = self.ensure().current_playback() or {}
            if force_play and not bool(pb.get("is_playing")):
                self.ensure().start_playback()
        except Exception:
            logger.exception("Fallback start_playback failed")

    def _active_device_id(self) -> Optional[str]:
        # Cache the resolved id for a few seconds so rapid volume/seek presses
        # don't each fire a devices() round-trip. Invalidated by transfer().
        cached = self._dev_cache
        if cached and (time.time() - cached[1]) < 5.0:
            return cached[0]
        devs = self.devices()
        dev_id = None
        for d in devs:
            if d.get("is_active"):
                dev_id = d.get("id"); break
        if dev_id is None and devs:
            dev_id = devs[0].get("id")
        self._dev_cache = (dev_id, time.time())
        return dev_id

    def start_playback(self, *, context_uri: Optional[str] = None, uris: Optional[List[str]] = None, offset: Optional[dict] = None):
        device_id = self._active_device_id()
        if device_id is None:
            raise RuntimeError("No devices found. Open Spotify and try again.")
        try:
            return self.ensure().start_playback(
                device_id=device_id,
                context_uri=context_uri,
                uris=uris,
                offset=offset
            )
        except Exception as exc:
            # A Free account learns its tier here if refresh_account() could
            # not run: switch to the degraded mode and say why, once.
            if is_premium_required_error(exc):
                self._mark_free()
                raise ProviderError(self.unsupported_message("can_play")) from exc
            raise

    def pause(self):
        """True if Spotify accepted the pause (callers may ignore it)."""
        try:
            self.ensure().pause_playback()
            return True
        except Exception:
            logger.exception("Error in pause_playback")
            return False

    def resume(self):
        try: self.ensure().start_playback()
        except Exception: logger.exception("Error to resume playback")

    def next(self):
        try: self.ensure().next_track()
        except Exception: logger.exception("Error next_track")

    def prev(self):
        try: self.ensure().previous_track()
        except Exception: logger.exception("Error previous_track")

    def shuffle(self, state: bool):
        try: self.ensure().shuffle(state)
        except Exception: logger.exception("Error shuffle")

    def repeat(self, state: str):
        try: self.ensure().repeat(state)
        except Exception: logger.exception("Error repeat")

    def set_volume(self, volume: int):
        try:
            vol = max(0, min(100, volume))
            device_id = self._active_device_id()
            if not device_id:
                raise RuntimeError("No active devices found. Open Spotify and try again.")
            self.ensure().volume(vol, device_id=device_id)
        except Exception:
            logger.exception("Error set_volume")

    def get_playback(self):
        try: return self.ensure().current_playback() or {}
        except Exception:
            logger.exception("Error get_playback"); return {}

    def seek_ms(self, ms: int):
        try:
            device_id = None
            try:
                device_id = self._active_device_id()
            except Exception:
                device_id = None
            if device_id:
                try:
                    self.ensure().seek_track(ms, device_id=device_id)
                except TypeError:
                    self.ensure().seek_track(ms)
            else:
                self.ensure().seek_track(ms)
        except Exception:
            logger.exception("Error seek_ms")

    def add_to_queue(self, uri: str):
        try:
            device_id = self._active_device_id()
            if not device_id:
                raise RuntimeError("No active devices found. Open Spotify and try again.")
            self.ensure().add_to_queue(uri, device_id=device_id)
        except Exception:
            logger.exception("Error add_to_queue"); raise


# Historical name: the rest of the code base (and the tests) still import
# ``SpotifyClient``; it is the same class.
SpotifyClient = SpotifyProvider
