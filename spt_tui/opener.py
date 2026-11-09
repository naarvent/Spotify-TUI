"""Open a URL in the user's browser — one helper for everything that needs it
(librespot's first-time authorisation, Spotify's
OAuth link).

Order: the configured ``open_command`` / ``SPT_OPEN_COMMAND`` (default
``abrir``, which hands the URL to the compositor) if it is on PATH, started
detached; otherwise Python's ``webbrowser`` as a last resort. Failure raises
``OpenURLError`` with a message the status line can show — never silent.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import webbrowser
from typing import Callable, List, Optional

from . import config


class OpenURLError(RuntimeError):
    pass


def _cfg_str(key: str, env: str, default):
    v = os.getenv(env)
    if v is not None and v.strip():
        return v.strip()
    cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
    val = cfg.get(key)
    return val if isinstance(val, str) and val else default


def opener_command() -> List[str]:
    return shlex.split(_cfg_str("open_command", "SPT_OPEN_COMMAND", "abrir"))


def open_url(url: str, *, opener: Optional[List[str]] = None,
             which: Callable[[str], Optional[str]] = shutil.which,
             spawn: Callable = subprocess.Popen,
             fallback: Optional[Callable[[str], bool]] = webbrowser.open) -> str:
    """Open ``url``; returns how ("abrir", "browser"…). Raises OpenURLError."""
    cmd = list(opener) if opener is not None else opener_command()
    if cmd and which(cmd[0]):
        try:
            # Detached: the opener must neither block us nor die with us.
            spawn(cmd + [url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, start_new_session=True)
            return cmd[0]
        except Exception as exc:
            first = f"'{cmd[0]}' failed ({type(exc).__name__})"
    else:
        first = f"'{cmd[0] if cmd else ''}' not found (set open_command / SPT_OPEN_COMMAND)"
    if fallback is not None:
        try:
            if fallback(url):
                return "browser"
        except Exception:
            pass
    raise OpenURLError(f"Could not open the browser: {first}, and no fallback browser worked.")
