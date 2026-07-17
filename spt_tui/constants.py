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

WELCOME = r"""

                                                                ⢀⣤⠖⠂⠉⠉⠉⠀⠒⠤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
                                                            ⢀⠀⣶⡟⢀⣴⣶⣿⣾⣶⣶⣄⡀⠈⠑⢤⡀⠀⠀⠀⠀⠀⠀⠀⠀
                                                      ⠀⠀⠀⠀⡴⣫⣼⡿⣴⡟⠛⠉⠉⠛⠛⠿⣿⣿⣷⣦⡀⠙⢄⠀⠀⠀⠀⠀⠀⠀
       _________              __  .__  _____           ⠀⠀⠀⣼⢁⣟⡟⣷⠁⠀⠀⠀⠀⠀⠀⠀⠀⠙⢿⣿⣷⣆⠈⢣⡀⠀⠀⠀⠀⠀
      /   _____/_____   _____/  |_|__|/ ____\__.__.     ⠀⠀⢰⣿⢼⣿⣷⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠹⣿⣿⡆⠀⢱⠀⠀⠀⠀⠀
      \_____  \\____ \ /  _ \   __\  \   __<   |  |     ⠀⠀⢸⡵⣾⣇⣸⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣿⣧⠀⠀⢧⠀⠀⠀⠀
      /        \  |_> >  <_> )  | |  ||  |  \___  |     ⠀⠀⠘⣴⣿⢯⡏⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠹⡿⠛⠉⠹⡆⠀⠀⠀
     /_______  /   __/ \____/|__| |__||__|  / ____|     ⢀⣼⣿⣧⠟⠁⢀⢀⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢯⣴⣶⣴⡇⠀⠀⠀
             \/|__|                         \/         ⢸⣿⣼⣿⣋⣉⠀⠀⠀⠈⠙⠦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠈⣿⣿⣷⣷⡀⠀⠀
                                                      ⢸⠁⠊⣿⠛⢛⢟⣦⡀⠀⠀⠀⠈⢆⠀⠀⠀⠀⢀⠔⣨⣶⡜⠂⠈⠽⣧⡀⠀
     by naarvent_ :)                                  ⠸⣶⣾⡯⠤⢄⡀⠵⢿⣦⡀⠀⠀⠀⡷⡄⠀⡰⢁⣾⣿⣿⣿⠀⠀⠀⣿⡹⡄
                                                      ⠀⣿⣡⠦⢄⡀⠈⠳⣬⣹⣿⣆⠀⠀⢉⠻⣴⠇⣾⣿⡟⢻⠁⠀⠀⠀⣿⠁⡇
                                                       ⣿⡭⡀⠀⠈⠲⣦⣸⣿⣿⣿⣧⣀⠈⡔⣜⣴⣿⡟⢀⡎⡈⠀⠀⢰⡿⢠⣷
                                                      ⠀⢸⣿⣄⣒⡀⡀⣿⣷⡿⣿⢿⣿⣷⡰⡸⣯⣏⣿⡷⢋⣼⣁⡢⢠⠟⠀⣼⣿
                                                      ⠀⠀⠻⣷⣈⣁⣮⢻⢸⡇⢨⣿⣿⣿⣷⢶⣿⣏⣩⣶⣿⣿⣿⣿⡯⣤⣴⣿⠃
                                                      ⠀⠀⠀⠘⠿⣿⣿⣽⣽⣷⣿⣿⣿⣿⣿⡶⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟⠁⠀
                                                      ⠀⠀⠀⠀⠀⠉⠙⠿⢿⣿⣿⣿⣿⠟⠁⠀⠘⠿⣿⣿⣿⠿⠟⠉⠀⠀⠀⠀



"""

# --- Welcome components (for the responsive _render_welcome levels) ---
WELCOME_AUTHOR = "by naarvent_ :)"

# The 'Spotify' figlet block on its own (used by the medium level, without the
# right-hand portrait art).
WELCOME_SPOTIFY_ART = r""" _________              __  .__  _____
/   _____/_____   _____/  |_|__|/ ____\__.__.
\_____  \____ \ /  _ \   __\  \   __<   |  |
/        \  |_> >  <_> )  | |  ||  |  \___  |
/_______  /   __/ \____/|__| |__||__|  / ____|
        \/|__|                         \/"""
