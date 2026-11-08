"""Static UI constants: welcome banner, glyphs and library menu items."""

from __future__ import annotations

import os

# ASCII fallbacks are used on Windows terminals by default (many don't render
# the unicode glyphs correctly). Override with SPT_TUI_ASCII=0 / 1.
USE_ASCII = os.getenv("SPT_TUI_ASCII", "1" if os.name == "nt" else "0") == "1"

GLYPHS = {
    "heart": "*" if USE_ASCII else "❤",
    "dot_on": "[*]" if USE_ASCII else "◉",
    "dot_off": "[ ]" if USE_ASCII else "○",
    "play": ">" if USE_ASCII else "▶",
    "pause": "||" if USE_ASCII else "⏸",
    "sep": "-" if USE_ASCII else "—",
}

LIBRARY_ITEMS = [
    "Create Playlist",
    "Import Playlists",
    "Recently Played",
    "Liked Songs",
    "Saved Artists",
    "Saved Albums",
    "Saved Podcasts",
    "Saved Episodes",
]

# --- Welcome components (for the responsive _render_welcome levels) ---
WELCOME_AUTHOR = "by naarvent_ :)"

# "SPOTIFY" and "TUI" in the same block font (pyfiglet's "ANSI Shadow"-style with ▒ shading).
WELCOME_SPOTIFY_ART = r"""  █████████  ███████████     ███████    ███████████ █████ ███████████ █████ █████
 ███▒▒▒▒▒███▒▒███▒▒▒▒▒███  ███▒▒▒▒▒███ ▒█▒▒▒███▒▒▒█▒▒███ ▒▒███▒▒▒▒▒▒█▒▒███ ▒▒███
▒███    ▒▒▒  ▒███    ▒███ ███     ▒▒███▒   ▒███  ▒  ▒███  ▒███   █ ▒  ▒▒███ ███
▒▒█████████  ▒██████████ ▒███      ▒███    ▒███     ▒███  ▒███████     ▒▒█████
 ▒▒▒▒▒▒▒▒███ ▒███▒▒▒▒▒▒  ▒███      ▒███    ▒███     ▒███  ▒███▒▒▒█      ▒▒███
 ███    ▒███ ▒███        ▒▒███     ███     ▒███     ▒███  ▒███  ▒        ▒███
▒▒█████████  █████        ▒▒▒███████▒      █████    █████ █████          █████
 ▒▒▒▒▒▒▒▒▒  ▒▒▒▒▒           ▒▒▒▒▒▒▒       ▒▒▒▒▒    ▒▒▒▒▒ ▒▒▒▒▒          ▒▒▒▒▒"""

WELCOME_TUI_ART = r"""███████████ █████  █████ █████
▒█▒▒▒███▒▒▒█▒▒███  ▒▒███ ▒▒███
▒   ▒███  ▒  ▒███   ▒███  ▒███
    ▒███     ▒███   ▒███  ▒███
    ▒███     ▒███   ▒███  ▒███
    ▒███     ▒███   ▒███  ▒███
    █████    ▒▒████████   █████
   ▒▒▒▒▒      ▒▒▒▒▒▒▒▒   ▒▒▒▒▒"""

# The headphones portrait (braille), de-indented.
WELCOME_HEADPHONES = r"""          ⢀⣤⠖⠂⠉⠉⠉ ⠒⠤⣀
      ⢀ ⣶⡟⢀⣴⣶⣿⣾⣶⣶⣄⡀⠈⠑⢤⡀
    ⡴⣫⣼⡿⣴⡟⠛⠉⠉⠛⠛⠿⣿⣿⣷⣦⡀⠙⢄
    ⣼⢁⣟⡟⣷⠁        ⠙⢿⣿⣷⣆⠈⢣⡀
    ⢰⣿⢼⣿⣷⠇           ⠹⣿⣿⡆ ⢱
    ⢸⡵⣾⣇⣸             ⠘⣿⣧  ⢧
    ⠘⣴⣿⢯⡏              ⠹⡿⠛⠉⠹⡆
  ⢀⣼⣿⣧⠟⠁⢀⢀⣀⡀           ⢯⣴⣶⣴⡇
 ⢸⣿⣼⣿⣋⣉   ⠈⠙⠦⡀        ⠈⣿⣿⣷⣷⡀
⢸⠁⠊⣿⠛⢛⢟⣦⡀   ⠈⢆    ⢀⠔⣨⣶⡜⠂⠈⠽⣧⡀
⠸⣶⣾⡯⠤⢄⡀⠵⢿⣦⡀   ⡷⡄ ⡰⢁⣾⣿⣿⣿   ⣿⡹⡄
 ⣿⣡⠦⢄⡀⠈⠳⣬⣹⣿⣆  ⢉⠻⣴⠇⣾⣿⡟⢻⠁   ⣿⠁⡇
 ⣿⡭⡀ ⠈⠲⣦⣸⣿⣿⣿⣧⣀⠈⡔⣜⣴⣿⡟⢀⡎⡈  ⢰⡿⢠⣷
 ⢸⣿⣄⣒⡀⡀⣿⣷⡿⣿⢿⣿⣷⡰⡸⣯⣏⣿⡷⢋⣼⣁⡢⢠⠟ ⣼⣿
  ⠻⣷⣈⣁⣮⢻⢸⡇⢨⣿⣿⣿⣷⢶⣿⣏⣩⣶⣿⣿⣿⣿⡯⣤⣴⣿⠃
   ⠘⠿⣿⣿⣽⣽⣷⣿⣿⣿⣿⣿⡶⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟⠁
     ⠉⠙⠿⢿⣿⣿⣿⣿⠟⠁ ⠘⠿⣿⣿⣿⠿⠟⠉"""


def _block(lines, width=None, align="left"):
    """Pad ``lines`` to one common width so a centred block keeps its shape (the panel centres every
    line on its own: lines of different lengths would shear the figlet art)."""
    lines = [l.rstrip() for l in lines]
    w = width if width is not None else max((len(l) for l in lines), default=0)
    out = []
    for l in lines:
        out.append(l.center(w) if align == "center" else l.ljust(w))
    return out


def _title_block():
    """Spotify over TUI (both left-aligned, same font) with the author line on TUI's middle row,
    centred in the free space to the right of TUI."""
    sp = [l.rstrip() for l in WELCOME_SPOTIFY_ART.split("\n")]
    w = max(len(l) for l in sp)
    tui = [l.rstrip() for l in WELCOME_TUI_ART.split("\n")]
    tw = max(len(l) for l in tui)
    tui = [l.ljust(tw) for l in tui]
    mid = len(tui) // 2
    free = w - tw
    start = tw + max(0, (free - len(WELCOME_AUTHOR)) // 2)
    row = tui[mid].ljust(start) + WELCOME_AUTHOR
    tui[mid] = row
    return _block(sp, w) + _block([""], w) + _block(tui, w)


def build_welcome(variant: str) -> str:
    """The welcome screen for a layout level. Every line has the same length (see ``_block``)."""
    if variant == "large":
        left = _title_block()
        hp = WELCOME_HEADPHONES.split("\n")
        lw, hw = max(len(l) for l in left), max(len(l) for l in hp)
        # Both blocks centred vertically on the taller one (the headphones sit to the right of the title).
        n = max(len(left), len(hp))
        lt, ht = (n - len(left)) // 2, (n - len(hp)) // 2
        left = [" " * lw] * lt + left + [" " * lw] * (n - len(left) - lt)
        hp = [""] * ht + hp + [""] * (n - len(hp) - ht)
        return "\n".join(a.ljust(lw) + "   " + b.ljust(hw) for a, b in zip(left, hp))
    if variant == "medium":
        return "\n".join(_title_block())
    if variant == "compact":
        sp = WELCOME_SPOTIFY_ART.split("\n")
        w = max(len(l) for l in sp)
        return "\n".join(_block(sp, w) + _block([""], w) + _block([WELCOME_AUTHOR], w, "center"))
    if variant == "small":
        return "\n".join(_block(["SPT-TUI", "Spotify in your terminal", " ", WELCOME_AUTHOR], None, "center"))
    return "\n".join(_block(["SPT-TUI", WELCOME_AUTHOR], None, "center"))


def welcome_size(variant: str):
    """(width, height) of a variant's art: the panel must be at least this big to show it."""
    lines = build_welcome(variant).split("\n")
    return max(len(l) for l in lines), len(lines)


# What the app shows first / when the layout is unknown: the full welcome.
WELCOME = build_welcome("large")
