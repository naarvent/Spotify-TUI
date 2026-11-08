"""Backward-compatible import path for the Spotify client.

The implementation moved to :mod:`spt_tui.providers.spotify` (as
``SpotifyProvider``) when the provider layer was introduced; this module keeps
``from spt_tui.spotify_client import SpotifyClient, RateLimiter`` working.
"""

from __future__ import annotations

import time  # noqa: F401  (tests monkeypatch ``spotify_client.time.time``)

from .providers.spotify import (  # noqa: F401
    RateLimiter,
    SpotifyClient,
    SpotifyProvider,
    _SpotifyProxy,
    is_premium_required_error,
)

__all__ = ["SpotifyClient", "SpotifyProvider", "RateLimiter"]
