"""Entry point: ``python -m spt_tui``."""

from __future__ import annotations

from .config import logger, LOG_PATH
from .app import SptPy

def main() -> None:
    logger.info("Running app; log in: %s", LOG_PATH)
    SptPy().run()

if __name__ == "__main__":
    main()
