"""Entry point: ``python -m spt_tui`` (and the ``spt`` / ``spt-tui`` commands)."""

from __future__ import annotations

import sys

from . import __version__

def main() -> None:
    # Answer --version/-V before importing the app or touching config/logging,
    # so the frozen `spt.exe --version` used as a CI smoke test stays cheap and
    # side-effect free.
    argv = sys.argv[1:]
    if any(a in ("--version", "-V") for a in argv):
        print(f"spt-tui {__version__}")
        return

    from .config import logger, LOG_PATH
    from .app import SptPy
    logger.info("Running app; log in: %s", LOG_PATH)
    SptPy().run()

if __name__ == "__main__":
    main()
