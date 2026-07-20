"""Runtime configuration: paths, logging and Spotify credentials.

This module owns all the mutable process-wide state (credentials, loaded
config) so that every other module reads/writes a single source of truth via
attribute access (``config.CLIENT_ID`` etc.). Never do ``from .config import
CLIENT_ID`` for the mutable values or you will capture a stale copy.
"""

from __future__ import annotations

import os
import re
import json
import logging
from logging.handlers import RotatingFileHandler

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
USER_HOME = os.path.expanduser("~")
USER_DOCS = os.path.join(USER_HOME, "Documents")
CACHE_DIR = os.path.join(USER_DOCS, "naarvent's projects", "Spotify_TUI")
os.makedirs(CACHE_DIR, exist_ok=True)

CACHE_PATH = os.path.join(CACHE_DIR, ".cache_spotify_token")
CONFIG_PATH = os.path.join(CACHE_DIR, "spt_config.json")

LOG_DIR = CACHE_DIR
LOG_PATH = os.path.join(LOG_DIR, "spt_py_textual_spotify.log")

# --------------------------------------------------------------------------- #
# Logging (configured first so config loading can log failures safely)
# --------------------------------------------------------------------------- #
try:
    LOG_MAX_BYTES = int(os.getenv("SPT_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
except Exception:
    LOG_MAX_BYTES = 5 * 1024 * 1024
try:
    LOG_BACKUP_COUNT = int(os.getenv("SPT_LOG_BACKUP_COUNT", "5"))
except Exception:
    LOG_BACKUP_COUNT = 5

_env_level = os.getenv("SPT_LOG_LEVEL", "INFO").upper()
ROOT_LOG_LEVEL = getattr(logging, _env_level, logging.INFO)

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_handler = RotatingFileHandler(LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
_handler.setFormatter(_formatter)
_handler.setLevel(ROOT_LOG_LEVEL)

root_logger = logging.getLogger()
root_logger.setLevel(ROOT_LOG_LEVEL)
root_logger.addHandler(_handler)

logger = logging.getLogger("spt_tui")
logger.setLevel(ROOT_LOG_LEVEL)

for _noisy in ("spotipy", "requests", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# --------------------------------------------------------------------------- #
# Local config file
# --------------------------------------------------------------------------- #
def load_local_config() -> dict:
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        logger.exception("load_local_config failed")
    return {}

def save_local_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg or {}, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("save_local_config failed")

LOCAL_CFG = load_local_config()
CONFIG_LOADED = bool(LOCAL_CFG)

# --------------------------------------------------------------------------- #
# Human-readable size parsing (lyrics cache size cap)
#
# The app stores the cap internally as a plain byte count (matching the existing
# ``_LYRICS_CACHE_MAX_BYTES`` field), so the user-facing setting is parsed into
# bytes on save and formatted back to a human-readable string on display. Units
# are binary (1 MB = 1024*1024 bytes) to stay consistent with the current
# byte-based cap. An explicit MB/GB suffix is REQUIRED — a bare number is
# rejected so the unit is never ambiguous.
# --------------------------------------------------------------------------- #
_SIZE_UNITS = {"MB": 1024 * 1024, "GB": 1024 * 1024 * 1024}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)\s*$")

LYRICS_CACHE_DEFAULT_BYTES = 2 * 1024 * 1024              # matches the in-app default
LYRICS_CACHE_MIN_BYTES = 1 * 1024 * 1024                  # 1 MB floor
LYRICS_CACHE_MAX_BYTES_LIMIT = 8 * 1024 * 1024 * 1024     # 8 GB ceiling

def parse_size(text: str) -> "int | None":
    """Parse a human-readable size like ``'200 MB'`` or ``'1 GB'`` into a byte
    count. Requires an explicit MB/GB suffix (case-insensitive); a bare number,
    an unsupported unit, a non-positive value or any other malformed input
    returns ``None``."""
    if not isinstance(text, str):
        return None
    m = _SIZE_RE.match(text)
    if not m:
        return None
    value, unit = m.group(1), m.group(2).upper()
    mult = _SIZE_UNITS.get(unit)
    if mult is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return int(round(n * mult))

def format_size(num_bytes: int) -> str:
    """Format a byte count back into a human-readable ``'N MB'`` / ``'N GB'``
    string, always with an explicit unit. Uses GB once the value is at least
    1 GB, MB otherwise; drops a trailing ``.0`` for whole numbers."""
    try:
        n = int(num_bytes)
    except (TypeError, ValueError):
        n = 0
    gb = n / (1024 * 1024 * 1024)
    if gb >= 1:
        return (f"{gb:.0f} GB" if gb == int(gb) else f"{gb:.1f} GB")
    mb = n / (1024 * 1024)
    return (f"{mb:.0f} MB" if mb == int(mb) else f"{mb:.1f} MB")

# --------------------------------------------------------------------------- #
# Spotify credentials (env vars take precedence over the local config file)
# --------------------------------------------------------------------------- #
CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID") or (LOCAL_CFG.get("client_id") if isinstance(LOCAL_CFG, dict) else None)
CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET") or (LOCAL_CFG.get("client_secret") if isinstance(LOCAL_CFG, dict) else None)
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI") or (LOCAL_CFG.get("redirect_uri") if isinstance(LOCAL_CFG, dict) else None)

SCOPE = " ".join([
    "user-read-private",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-read-recently-played",
    "user-read-playback-position",
    "app-remote-control",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
    "user-follow-read",
    "user-follow-modify",
    "user-top-read",
    "user-library-read",
    "user-library-modify",
])

