"""Frozen-build entry point.

PyInstaller runs its entry script as top-level ``__main__`` with no parent
package, so pointing it straight at ``spt_tui/__main__.py`` breaks that module's
relative imports (``from . import ...``). This tiny launcher imports the package
normally and calls into it, so the relative imports resolve. Not used by the
``spt`` / ``spt-tui`` console scripts or ``python -m spt_tui`` — only by the
frozen executable (see ``spt.spec``).
"""

from spt_tui.__main__ import main

if __name__ == "__main__":
    main()
