"""spt_tui - a Spotify TUI built with Textual."""

from __future__ import annotations

# Single source of truth for the version: pyproject.toml reads it from here
# (hatchling dynamic version), the installer/CI read it via
# ``spt_tui.__version__``, and ``spt --version`` prints it.
__version__ = "0.2.1"

from .app import SptPy

__all__ = ["SptPy", "__version__"]
