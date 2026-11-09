"""Music providers and the choice of the active one.

The active provider is chosen, in priority order, by:

1. the ``SPT_PROVIDER`` environment variable;
2. ``"provider"`` in ``spt_config.json`` (also written by the Settings screen);
3. the default, ``"spotify"``.

Known names: ``spotify``, ``subsonic`` (Subsonic / OpenSubsonic servers such as
Navidrome) and ``ytmusic`` (YouTube Music).

A provider whose optional dependency, binary or configuration is missing is
never fatal: :func:`create_provider` falls back to Spotify and returns a notice
that the UI shows on the status line.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from .. import config
from ..config import logger
from .base import (  # noqa: F401
    CAPABILITY_LABELS,
    Capabilities,
    Provider,
    ProviderError,
    ProviderUnsupported,
    SpotifyShapedAdapter,
    empty_page,
    make_page,
)

DEFAULT_PROVIDER = "spotify"

# name -> (module, class). Imported lazily so a provider's optional
# dependencies are only touched when that provider is chosen.
_REGISTRY = {
    "spotify": ("spotify", "SpotifyProvider"),
    "subsonic": ("subsonic", "SubsonicProvider"),
    "ytmusic": ("ytmusic", "YTMusicProvider"),
}

# Accepted spellings for the user-facing setting.
_ALIASES = {
    "spotify": "spotify",
    "subsonic": "subsonic", "navidrome": "subsonic", "opensubsonic": "subsonic",
    "ytmusic": "ytmusic", "youtube": "ytmusic", "youtube_music": "ytmusic",
    "yt": "ytmusic",
}

PROVIDER_NAMES = tuple(_REGISTRY)


def normalize_provider_name(value) -> Optional[str]:
    """Canonical provider name for ``value``, or None if it is not known."""
    if not isinstance(value, str):
        return None
    return _ALIASES.get(value.strip().lower().replace(" ", "_"))


def resolve_provider_name() -> str:
    """The configured provider name (env > config > default)."""
    env = os.getenv("SPT_PROVIDER")
    if env and env.strip():
        name = normalize_provider_name(env)
        if name:
            return name
        logger.warning("Unknown SPT_PROVIDER=%r; using %s", env, DEFAULT_PROVIDER)
        return DEFAULT_PROVIDER
    cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
    raw = cfg.get("provider")
    if raw:
        name = normalize_provider_name(raw)
        if name:
            return name
        logger.warning("Unknown provider %r in config; using %s", raw, DEFAULT_PROVIDER)
    return DEFAULT_PROVIDER


def provider_class(name: str):
    import importlib
    mod_name, cls_name = _REGISTRY[name]
    mod = importlib.import_module(f"{__name__}.{mod_name}")
    return getattr(mod, cls_name)


def create_provider(name: Optional[str] = None) -> Tuple[Provider, Optional[str]]:
    """Instantiate the chosen provider.

    Returns ``(provider, notice)``. ``notice`` is None normally; when the
    chosen provider cannot be used it explains why, and the provider returned
    is Spotify instead. Never raises for a missing optional dependency."""
    name = normalize_provider_name(name) if name else resolve_provider_name()
    name = name or DEFAULT_PROVIDER
    if name != DEFAULT_PROVIDER:
        reason = None
        try:
            prov = provider_class(name)()
            reason = prov.unavailable_reason()
            if not reason:
                logger.info("Active provider: %s", name)
                return prov, None
            try:
                prov.close()
            except Exception:
                logger.exception("closing unavailable provider %s failed", name)
        except Exception as exc:
            logger.exception("Could not create provider %s", name)
            reason = str(exc) or type(exc).__name__
        notice = (f"Provider '{name}' is unavailable: {reason} "
                  f"Falling back to Spotify.")
        logger.warning(notice)
        return provider_class(DEFAULT_PROVIDER)(), notice
    return provider_class(DEFAULT_PROVIDER)(), None


def create_named(name: str) -> Provider:
    """Instantiate exactly ``name`` (no fallback). Raises ProviderError with
    the reason when it is unknown or cannot be used. Used by the hot switch."""
    canon = normalize_provider_name(name)
    if not canon:
        raise ProviderError(f"Unknown provider '{name}'.")
    try:
        prov = provider_class(canon)()
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"could not start: {exc or type(exc).__name__}") from exc
    reason = prov.unavailable_reason()
    if reason:
        try:
            prov.close()
        except Exception:
            logger.exception("closing unavailable provider %s failed", canon)
        raise ProviderError(reason)
    return prov


def env_override() -> Optional[str]:
    """The provider forced by SPT_PROVIDER (canonical name), if set and valid."""
    env = os.getenv("SPT_PROVIDER")
    return normalize_provider_name(env) if env and env.strip() else None


PROVIDER_DESCRIPTIONS = {
    "spotify": "Spotify (Web API, Premium for playback)",
    "subsonic": "Subsonic / Navidrome server",
    "ytmusic": "YouTube Music (no account, local library)",
}


def provider_status(name: str, *, which=None, find_spec=None, secret_store=None) -> dict:
    """Cheap, side-effect-free readiness check for the provider picker.

    Returns ``{"name", "usable", "issues"}``: ``usable`` is False when the
    provider cannot start at all (missing package / configuration); ``issues``
    lists what is missing, including things that only disable playback (mpv,
    yt-dlp). No network, no subprocess other than a keyring lookup."""
    import shutil
    import importlib.util
    which = which or shutil.which
    find_spec = find_spec or importlib.util.find_spec
    cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
    issues, usable = [], True

    def _has_module(mod):
        try:
            return find_spec(mod) is not None
        except Exception:
            return False

    spotify_ok = bool(config.CLIENT_ID and config.CLIENT_SECRET) or (
        os.path.exists(config.CACHE_PATH) and os.path.getsize(config.CACHE_PATH) > 0)
    if name == "spotify" and not spotify_ok:
        issues.append("Spotify credentials not set (sign-in needed)")
    if name == "subsonic":
        url = os.getenv("SPT_SUBSONIC_URL") or cfg.get("subsonic_url")
        user = os.getenv("SPT_SUBSONIC_USER") or cfg.get("subsonic_user")
        if not url or not user:
            usable = False
            issues.append("missing config (server URL / user)")
        else:
            pw = os.getenv("SPT_SUBSONIC_PASSWORD")
            if not pw:
                try:
                    from ..secret_store import default_store
                    from .subsonic import password_account
                    store = secret_store or default_store()
                    pw = store.get(password_account(str(url).rstrip("/"), user))
                except Exception:
                    logger.exception("provider_status: password lookup failed")
                    pw = None
            if not pw:
                usable = False
                issues.append("missing password (Settings or SPT_SUBSONIC_PASSWORD)")
        if not which("mpv"):
            issues.append("missing mpv (no playback)")
    elif name == "ytmusic":
        if not _has_module("ytmusicapi"):
            usable = False
            issues.append("missing ytmusicapi")
        if not which("mpv"):
            issues.append("missing mpv (no playback)")
        if not which("yt-dlp"):
            issues.append("missing yt-dlp (no playback)")
    return {"name": name, "usable": usable, "issues": issues}


def save_provider_choice(name: str) -> bool:
    """Persist ``name`` as the configured provider. False if it is unknown."""
    canon = normalize_provider_name(name)
    if not canon:
        return False
    if not isinstance(config.LOCAL_CFG, dict):
        config.LOCAL_CFG = {}
    config.LOCAL_CFG["provider"] = canon
    config.save_local_config(config.LOCAL_CFG)
    return True
