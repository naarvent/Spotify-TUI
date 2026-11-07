"""Accent colour of the UI: one colour from the user, the rest of the palette derived from it.

The default (``None``) is the hand-picked teal the app always had. Any other colour gives a theme with
the same structure (frames, titles, selection tints, text) in that hue. Pure functions, no Textual
app needed: ``build_theme`` returns a ``textual.theme.Theme``."""
from __future__ import annotations

import colorsys
import re
from typing import Optional

from textual.theme import Theme

DEFAULT_ACCENT = "#27e1c1"

# name -> hex, shown in Settings > Appearance. "teal" is the original look.
PRESETS = {
    "teal": DEFAULT_ACCENT,
    "blue": "#4da3ff",
    "purple": "#b388ff",
    "pink": "#ff7eb6",
    "red": "#ff6b6b",
    "orange": "#ffa94d",
    "yellow": "#f2d95c",
    "green": "#5fd068",
    "white": "#e6e6e6",
}

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_SHORT = re.compile(r"^#?([0-9a-fA-F]{3})$")


def normalize_color(text: str) -> Optional[str]:
    """'#27E1C1', '27e1c1', '#2ec' or a preset name -> '#rrggbb' lowercase; None when invalid."""
    t = (text or "").strip()
    if t.lower() in PRESETS:
        return PRESETS[t.lower()]
    m = _HEX.match(t)
    if m:
        return "#" + m.group(1).lower()
    m = _SHORT.match(t)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1).lower())
    return None


def _hls(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _derive(accent: str) -> dict:
    """Palette for ``accent``. For the default teal the exact original colours are used."""
    if accent == DEFAULT_ACCENT:
        return dict(primary="#27e1c1", secondary="#93e1e1", accent="#6ef7d6", foreground="#a9d5d9",
                    background="#0b1e24", surface="#0d252c", panel="#123a40", dim="#1a3a40",
                    sel="#0e2d34", sel2="#12474f", sel3="#17606a", text_hi="#d7f7ef", muted="#5f8a8f")
    r, g, b = (int(accent[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    s = max(s, 0.05)
    grey = s < 0.15                      # white / grey accent: keep the tints neutral
    ts = 0.0 if grey else min(s, 0.55)   # saturation of the dark tints
    return dict(
        primary=accent,
        secondary=_hls(h, 0.74, 0.0 if grey else 0.55),
        accent=_hls(h, 0.72, 0.0 if grey else min(1.0, s + 0.1)),
        foreground=_hls(h, 0.78, 0.0 if grey else 0.30),
        background=_hls(h, 0.09, ts), surface=_hls(h, 0.11, ts), panel=_hls(h, 0.17, ts),
        dim=_hls(h, 0.19, ts * 0.8), sel=_hls(h, 0.15, ts), sel2=_hls(h, 0.21, ts), sel3=_hls(h, 0.28, ts),
        text_hi=_hls(h, 0.92, 0.0 if grey else 0.5), muted=_hls(h, 0.50, 0.0 if grey else 0.2),
    )


def build_theme(name: str, accent: Optional[str] = None) -> Theme:
    """The app's Textual theme for ``accent`` (None = the original teal). The extra values are
    exposed to the CSS as $spt-dim, $spt-sel, $spt-sel2, $spt-sel3, $spt-text-hi and $spt-muted."""
    a = normalize_color(accent or "") or DEFAULT_ACCENT
    p = _derive(a)
    return Theme(
        name=name, primary=p["primary"], secondary=p["secondary"], accent=p["accent"],
        foreground=p["foreground"], background=p["background"], surface=p["surface"], panel=p["panel"],
        success="#5fd068", warning="#ffb454", error="#ff6b6b", dark=True,
        variables={"spt-dim": p["dim"], "spt-sel": p["sel"], "spt-sel2": p["sel2"], "spt-sel3": p["sel3"],
                   "spt-text-hi": p["text_hi"], "spt-muted": p["muted"],
                   # The key-hints bar: keys in the accent colour, descriptions quiet, no blocks.
                   "footer-foreground": p["foreground"], "footer-background": p["surface"],
                   "footer-key-foreground": p["primary"], "footer-key-background": "transparent",
                   "footer-description-foreground": p["foreground"],
                   "footer-description-background": "transparent", "footer-item-background": "transparent"},
    )
