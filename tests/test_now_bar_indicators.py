"""Now Playing: no brackets around the progress bar; Repeat/Shuffle indicators."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spt_tui.app.playback import PlaybackMixin as P

R = []
def check(n, c, d=""): R.append(bool(c)); print("PASS" if c else "FAIL", "-", n, d if not c else "")

class X(P): pass
x = X()
_, bar, _ = x._progress_components(50, 100, 20)
check("barra sin corchetes", "[" not in bar and "]" not in bar and len(bar) == 20, bar)
_, bar0, _ = x._progress_components(0, 0, 20)
check("barra vacía sin corchetes", "[" not in bar0 and len(bar0) == 20, bar0)
for rep, shf, plain in (("off", False, "Repeat: ↻  Shuffle: ⇄"), ("context", True, "Repeat: ↻  Shuffle: ⇄"),
                        ("track", False, "Repeat: ↻¹  Shuffle: ⇄")):
    p, m = x._np_mode_indicators(rep, shf)
    check(f"indicadores {rep}/{shf}", p == plain, p)
    on_r = rep != "off"
    check(f"{rep}: repeat {'negrita' if on_r else 'tenue'}", ("[b]↻" in m) == on_r and ("[dim]↻" in m) == (not on_r), m)
    check(f"shuffle {shf}: {'negrita' if shf else 'tenue'}", ("[b]⇄" in m) == shf and ("[dim]⇄" in m) == (not shf), m)
print(f"{sum(R)}/{len(R)} checks passed"); sys.exit(0 if all(R) else 1)
