"""Frozen-build entry point.

PyInstaller runs its entry script as top-level ``__main__`` with no parent
package, so pointing it straight at ``spt_tui/__main__.py`` breaks that module's
relative imports (``from . import ...``). This tiny launcher imports the package
normally and calls into it, so the relative imports resolve. Not used by the
``spt`` / ``spt-tui`` console scripts or ``python -m spt_tui`` — only by the
frozen executable (see ``spt.spec``).
"""

import os
import sys


def _expose_bundled_tools() -> None:
    """Put the install folder first on this process's PATH (frozen build only).

    The installer ships mpv.exe, yt-dlp.exe and librespot.exe beside spt.exe.
    Prepending that folder lets every ``shutil.which("mpv")`` / ``which("yt-dlp")``
    in the app, and mpv's own lookup of yt-dlp, find the bundled copies without
    touching the user's global PATH. Process-local: nothing persists.
    """
    if not getattr(sys, "frozen", False):
        return
    here = os.path.dirname(os.path.abspath(sys.executable))
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if here not in parts:
        os.environ["PATH"] = os.pathsep.join([here] + parts)


_expose_bundled_tools()

from spt_tui.__main__ import main  # noqa: E402  (after PATH setup on purpose)
from spt_tui import ytdlp_update  # noqa: E402

# A yt-dlp updated from Settings lives in the data folder and must win over the bundled one.
ytdlp_update.apply_user_tools()

if __name__ == "__main__":
    main()
