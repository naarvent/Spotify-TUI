"""Spotify Free through MPRIS: drive the player the user already has open.

With a Free account the Web API refuses every playback call. This provider
keeps the Web API for what Free accounts *may* use (search, library, likes,
playlists — inherited from :class:`SpotifyProvider`) and, for playback, only
does what a media key would do on the user's own desktop:

* **control** the Spotify web player (or desktop client) that the user opened
  themselves, through MPRIS via ``playerctl``: play/pause, next/previous, seek,
  volume, and read "now playing" + position (for the bar and synced lyrics);
* **open** ``https://open.spotify.com/track/<id>`` (or the album / playlist
  page) with the user's opener command — ``abrir`` by default, configurable
  with ``open_command`` / ``SPT_OPEN_COMMAND`` — and let the user press play
  there.

Deliberately NOT done (it would break Spotify's terms of use): no browser
automation or headless browser, no ``sp_dc`` cookie, no internal/private
Spotify endpoints, no stream decryption and no ad skipping. Ads play normally
in the user's player. If ``playerctl`` is missing the provider still opens
links and shows a notice; it never falls back to anything else.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from typing import Callable, List, Optional

from .. import config
from ..config import logger
from .base import Capabilities, ProviderError
from .spotify import SpotifyProvider

# Field separator for playerctl's --format output (never appears in metadata).
_SEP = "\x1f"
_FORMAT = _SEP.join([
    "{{playerName}}", "{{status}}", "{{mpris:trackid}}", "{{xesam:url}}",
    "{{xesam:title}}", "{{xesam:artist}}", "{{xesam:album}}", "{{mpris:length}}",
    "{{position}}", "{{volume}}",
])


def _cfg_str(key: str, env: str, default: Optional[str]) -> Optional[str]:
    v = os.getenv(env)
    if v and v.strip():
        return v.strip()
    cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
    val = cfg.get(key)
    return val.strip() if isinstance(val, str) and val.strip() else default


def spotify_web_url(uri: str) -> Optional[str]:
    """``spotify:track:ID`` -> ``https://open.spotify.com/track/ID`` (also
    album / playlist / episode / show / artist). Already-web URLs pass through."""
    s = str(uri or "").strip()
    if s.startswith("https://open.spotify.com/"):
        return s
    parts = s.split(":")
    if len(parts) == 3 and parts[0] == "spotify" and parts[2]:
        kind = parts[1]
        if kind in ("track", "album", "playlist", "episode", "show", "artist"):
            return f"https://open.spotify.com/{kind}/{parts[2]}"
    return None


def _spotify_id_from(trackid: str, url: str) -> Optional[str]:
    """Spotify track id from MPRIS metadata: the desktop client reports
    ``/com/spotify/track/<id>`` or ``spotify:track:<id>``; the web player a
    ``https://open.spotify.com/track/<id>`` URL."""
    for raw in (url or "", trackid or ""):
        if "open.spotify.com/track/" in raw:
            return raw.split("open.spotify.com/track/")[-1].split("?")[0].split("/")[0] or None
        if raw.startswith("spotify:track:"):
            return raw.split(":")[-1] or None
        if "/com/spotify/track/" in raw:
            return raw.rsplit("/", 1)[-1] or None
    return None


class SpotifyMprisProvider(SpotifyProvider):
    name = "spotify_mpris"
    display_name = "Spotify (browser via MPRIS)"

    def __init__(self, *, run: Callable = subprocess.run,
                 spawn: Callable = subprocess.Popen,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 opener: Optional[List[str]] = None,
                 player: Optional[str] = None,
                 now: Callable[[], float] = time.monotonic):
        super().__init__()
        self._run = run
        self._spawn = spawn
        self._which = which
        cmd = _cfg_str("open_command", "SPT_OPEN_COMMAND", "abrir")
        self._opener = list(opener) if opener is not None else shlex.split(cmd)
        # Explicit MPRIS player name (playerctl -p); None = auto-detect.
        self._player_pref = player or _cfg_str("mpris_player", "SPT_MPRIS_PLAYER", None)
        self._now = now
        self._picked: Optional[tuple] = None   # (player, timestamp)

    # ------------------------------------------------------------------ #
    # Capabilities
    # ------------------------------------------------------------------ #
    def has_playerctl(self) -> bool:
        return bool(self._which("playerctl"))

    @property
    def capabilities(self) -> Capabilities:
        ctl = self.has_playerctl()
        return Capabilities(
            needs_spotify_auth=True, supports_local_player=False,
            can_play=bool(self._opener and self._which(self._opener[0])),
            can_control_playback=ctl, can_seek=ctl, can_set_volume=ctl,
            can_shuffle_repeat=False, can_control_queue=False, can_list_devices=False,
            # Same Web API as `spotify` (inherited artist_top_tracks/related_artists).
            can_artist_top_tracks=True, can_related_artists=True, can_radio=True)

    def status_notice(self) -> Optional[str]:
        parts = []
        if not self.has_playerctl():
            parts.append("playerctl was not found: install it to control the Spotify player "
                         "open in your browser (play/pause/next/seek). Links still open.")
        if not (self._opener and self._which(self._opener[0])):
            parts.append(f"The opener command '{self._opener[0] if self._opener else ''}' "
                         "was not found (set open_command / SPT_OPEN_COMMAND).")
        if not parts:
            return ("Spotify via MPRIS: Enter opens the track in your browser; the keys "
                    "control the player you have open there.")
        return " ".join(parts)

    def unsupported_message(self, capability: str) -> str:
        if capability == "can_download":
            from .spotify import DOWNLOAD_UNSUPPORTED
            return DOWNLOAD_UNSUPPORTED
        if capability in ("can_control_playback", "can_seek", "can_set_volume") \
                and not self.has_playerctl():
            return "playerctl is not installed: cannot control the browser player."
        if capability == "can_control_queue":
            return "The queue is not available in MPRIS mode (use the Spotify player itself)."
        if capability == "can_shuffle_repeat":
            return "Shuffle / repeat are set in the Spotify player itself in MPRIS mode."
        if capability == "can_list_devices":
            return "Devices are not used in MPRIS mode."
        return super(SpotifyProvider, self).unsupported_message(capability)

    # The Free/Premium switch of SpotifyProvider does not apply here: playback
    # never goes through the Web API player endpoints.
    def is_free_account(self) -> bool:
        return False

    # ------------------------------------------------------------------ #
    # playerctl
    # ------------------------------------------------------------------ #
    def _playerctl(self, *args: str, player: Optional[str] = None, check: bool = False) -> Optional[str]:
        if not self.has_playerctl():
            return None
        argv = ["playerctl"]
        if player:
            argv += ["-p", player]
        argv += list(args)
        try:
            p = self._run(argv, capture_output=True, text=True, timeout=3)
        except Exception:
            logger.exception("playerctl %s failed", args[:1])
            return None
        if p.returncode != 0:
            if check:
                raise ProviderError((p.stderr or "playerctl failed").strip())
            return None
        return (p.stdout or "").strip()

    def _player(self) -> Optional[str]:
        """The MPRIS player to drive: the configured one, else the first that
        is playing Spotify (desktop client, or a browser tab on
        open.spotify.com). Cached for a few seconds."""
        if self._player_pref:
            return self._player_pref
        cached = self._picked
        if cached and (self._now() - cached[1]) < 5.0:
            return cached[0]
        names = (self._playerctl("-l") or "").split()
        chosen = None
        for n in names:
            if "spotify" in n.lower():
                chosen = n
                break
        if chosen is None:
            for n in names:
                meta = self._playerctl("metadata", "--format", "{{xesam:url}} {{mpris:trackid}}", player=n) or ""
                if "open.spotify.com" in meta or "spotify" in meta.lower():
                    chosen = n
                    break
        self._picked = (chosen, self._now())
        return chosen

    def _ctl(self, *args: str) -> None:
        player = self._player()
        if player is None:
            raise ProviderError("No Spotify player found over MPRIS: open "
                                "open.spotify.com (or the Spotify app) first.")
        self._playerctl(*args, player=player, check=True)

    # ------------------------------------------------------------------ #
    # Playback
    # ------------------------------------------------------------------ #
    def open_url(self, url: str) -> None:
        # The shared helper (spt_tui/opener.py), with this provider's own
        # opener/which/spawn. No webbrowser fallback here: playing a track in
        # some random browser would not be the web player the user controls.
        from ..opener import OpenURLError, open_url as _open
        if not self._opener or not self._which(self._opener[0]):
            raise ProviderError(f"Cannot open {url}: opener command not found.")
        try:
            _open(url, opener=self._opener, which=self._which, spawn=self._spawn, fallback=None)
        except OpenURLError as exc:
            raise ProviderError(f"Could not open {url}: {exc}") from exc

    def start_playback(self, *, context_uri: Optional[str] = None,
                       uris: Optional[List[str]] = None, offset: Optional[dict] = None):
        """Open the chosen item in the user's browser. With a context and an
        offset, the selected *track* is opened (Spotify's web player keeps
        playing the album / playlist from there only if the user starts it so)."""
        target = None
        if uris:
            target = uris[0]
        elif context_uri:
            target = context_uri
            if isinstance(offset, dict) and offset.get("uri"):
                target = offset["uri"]
        url = spotify_web_url(target) if target else None
        if not url:
            raise ProviderError("This item cannot be opened in the Spotify web player.")
        self.open_url(url)

    def pause(self) -> None:
        self._ctl("pause")

    def resume(self) -> None:
        self._ctl("play")

    def next(self) -> None:
        self._ctl("next")

    def prev(self) -> None:
        self._ctl("previous")

    def seek_ms(self, ms: int) -> None:
        self._ctl("position", f"{max(0, int(ms or 0)) / 1000.0:.3f}")

    def set_volume(self, volume: int) -> None:
        self._ctl("volume", f"{max(0, min(100, int(volume))) / 100.0:.2f}")

    def shuffle(self, state: bool) -> None:
        return None

    def repeat(self, state: str) -> None:
        return None

    def devices(self) -> List[dict]:
        return []

    def transfer(self, device_id: str, force_play: bool = False) -> None:
        return None

    def add_to_queue(self, uri: str) -> None:
        raise ProviderError(self.unsupported_message("can_control_queue"))

    def get_playback(self) -> dict:
        """Now playing from MPRIS metadata, in the Web API's shape."""
        player = self._player()
        if player is None:
            return {}
        raw = self._playerctl("metadata", "--format", _FORMAT, player=player)
        if not raw:
            return {}
        f = raw.split(_SEP)
        if len(f) < 10:
            return {}
        name, status, trackid, url, title, artist, album, length, position, volume = f[:10]

        def _num(x, default=0.0):
            try:
                return float(x)
            except (TypeError, ValueError):
                return default
        tid = _spotify_id_from(trackid, url)
        item = {
            "id": tid,
            "uri": f"spotify:track:{tid}" if tid else None,
            "type": "track",
            "name": title or "(no title)",
            "artists": [{"name": a.strip()} for a in (artist or "").split(",") if a.strip()],
            "album": {"name": album or ""},
            # MPRIS reports microseconds.
            "duration_ms": int(_num(length) / 1000),
        }
        return {
            "is_playing": status == "Playing",
            "progress_ms": int(_num(position) / 1000),
            "item": item,
            "currently_playing_type": "track",
            "shuffle_state": False,
            "repeat_state": "off",
            "device": {"id": name, "name": name, "type": "Browser", "is_active": True,
                       "volume_percent": int(round(_num(volume) * 100))},
        }

    def recently_played(self, limit=50) -> dict:
        # The Web API endpoint works on Free accounts, so keep it.
        return super().recently_played(limit=limit)
