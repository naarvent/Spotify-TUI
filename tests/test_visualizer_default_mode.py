"""Visualizer default mode: mirrored unless config says bars; Shift+M overrides for the session;
changing the default in Settings replaces the override."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spt_tui import config
from spt_tui.app.visualizer_view import VisualizerMixin

R = []
def check(n, c): R.append(bool(c)); print("PASS" if c else "FAIL", "-", n)

class V(VisualizerMixin):
    _vis_on = True
    def _notify(self, *a, **k): pass

old = config.LOCAL_CFG
try:
    config.LOCAL_CFG = {}
    v = V()
    check("sin config: mirrored por defecto", v._vis_is_mirror() is True)
    v.action_visualizer_mirror()
    check("Shift+M cambia a bars en la sesion", v._vis_is_mirror() is False)
    v.action_visualizer_mirror()
    check("Shift+M otra vez vuelve a mirrored", v._vis_is_mirror() is True)
    config.LOCAL_CFG = {"visualizer_mode": "bars"}
    v = V()
    check("config bars -> bars", v._vis_is_mirror() is False)
    v._apply_visualizer_mode(True)
    check("Settings -> mirrored sustituye el override", v._vis_is_mirror() is True)
    config.LOCAL_CFG = {"visualizer_mode": "basura"}
    check("valor raro -> mirrored", V()._vis_is_mirror() is True)
finally:
    config.LOCAL_CFG = old
print(f"{sum(R)}/{len(R)} checks passed")
sys.exit(0 if all(R) else 1)
