"""Where provider passwords live: never in ``spt_config.json`` in clear text.

Lookup order for a password:

1. an environment variable (e.g. ``SPT_SUBSONIC_PASSWORD``) — always honoured;
2. the system keyring through the ``keyring`` Python package, if installed;
3. libsecret's ``secret-tool`` command (GNOME Keyring / KWallet's Secret
   Service), if it is on PATH.

Storing uses (2) or (3). If neither exists the password is *not* stored and the
caller tells the user to export the environment variable instead.

Every external touch point (the keyring module, the command runner, ``which``)
is injectable so the tests need no real keyring.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, Optional

from .config import logger

SERVICE = "spt-tui"


def _import_keyring():
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:
        return None


class SecretStore:
    def __init__(self, *, keyring_module=None, use_keyring: bool = True,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 run: Callable = subprocess.run, service: str = SERVICE):
        self._keyring = keyring_module if keyring_module is not None else (
            _import_keyring() if use_keyring else None)
        self._which = which
        self._run = run
        self._service = service

    # ------------------------------------------------------------------ #
    def backend(self) -> Optional[str]:
        """'keyring', 'secret-tool' or None (nothing to store passwords in)."""
        if self._keyring is not None:
            return "keyring"
        if self._which("secret-tool"):
            return "secret-tool"
        return None

    def get(self, account: str, *, env: Optional[str] = None) -> Optional[str]:
        if env:
            v = os.getenv(env)
            if v:
                return v
        if not account:
            return None
        if self._keyring is not None:
            try:
                v = self._keyring.get_password(self._service, account)
                if v:
                    return v
            except Exception:
                logger.exception("keyring lookup failed")
        if self._which("secret-tool"):
            # `account` is what this app stores with; `username` is the attribute the
            # Python keyring package (and `keyring set`) uses, so both are tried.
            for attr in ("account", "username"):
                try:
                    p = self._run(["secret-tool", "lookup", "service", self._service,
                                   attr, account],
                                  capture_output=True, text=True, timeout=10)
                    if p.returncode == 0:
                        v = (p.stdout or "").rstrip("\n")
                        if v:
                            return v
                except Exception:
                    logger.exception("secret-tool lookup failed")
        return None

    def set(self, account: str, password: str) -> Optional[str]:
        """Store ``password``; return the backend used, or None if there is no
        secure place for it (the caller must then not persist it at all)."""
        if not account or password is None:
            return None
        if self._keyring is not None:
            try:
                self._keyring.set_password(self._service, account, password)
                return "keyring"
            except Exception:
                logger.exception("keyring store failed")
        if self._which("secret-tool"):
            try:
                # The secret goes through stdin, never through argv (which other
                # local users could read from /proc).
                p = self._run(["secret-tool", "store", f"--label=SPT-TUI ({account})",
                               "service", self._service, "account", account],
                              input=password, capture_output=True, text=True, timeout=10)
                if p.returncode == 0:
                    return "secret-tool"
                logger.warning("secret-tool store failed: %s", (p.stderr or "").strip())
            except Exception:
                logger.exception("secret-tool store failed")
        return None


_default: Optional[SecretStore] = None


def default_store() -> SecretStore:
    global _default
    if _default is None:
        _default = SecretStore()
    return _default
