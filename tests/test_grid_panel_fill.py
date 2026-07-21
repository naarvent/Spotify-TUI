"""The 2x2 search grid panels fit their quadrant exactly.

The panels sized their columns by hand from `_grid_col_width` (panel width minus
a flat 4) instead of going through the shared fitter. That flat 4 reserved a
border the panels do not have and did not cover the DataTable's per-column cell
padding, so the two-column Songs panel overflowed its quadrant by 2 while the
one-column panels left a gap — and neither reserved room for the vertical
scrollbar a full panel gets.

Run standalone:  python tests/test_grid_panel_fill.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from spt_tui.widgets import SearchPanel

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x

class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def rows(kind, n=30):
    return [{"type": "track", "id": f"{kind}{i}", "uri": "u",
             "title": f"a fairly long {kind} title number {i}",
             "artist": "some long artist name", "album": "al", "dur": "3:21",
             "raw": {}} for i in range(n)]

def widths(t):
    out = []
    for c in t.ordered_columns:
        try: out.append(int(getattr(c, "width", 0) or 0))
        except Exception: out.append(0)
    return out

def panel_w(t):
    cs = getattr(t, "content_size", None)
    if cs is not None and getattr(cs, "width", 0):
        return int(cs.width)
    return int(getattr(getattr(t, "size", None), "width", 0) or 0)

def gap(t):
    """Unused width inside a panel. 0 is exact fit; negative means overflow."""
    w = widths(t)
    # Per-column cell padding (2 each) + room for the vertical scrollbar (2).
    return panel_w(t) - (sum(w) + 2 * len(w) + 2)

async def render_grid(app, pilot):
    app._render_search_grid(rows("song"), rows("artist"), rows("album"),
                            rows("playlist"), app._new_view_token("search", ""))
    for _ in range(6):
        await pilot.pause()
    return list(app.query(SearchPanel))


async def test_every_panel_fits_its_quadrant():
    for size in [(200, 40), (160, 30), (120, 25)]:
        app = TApp(Fake())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            panels = await render_grid(app, pilot)
            check(f"grid renders four panels at {size}", len(panels) == 4, f"n={len(panels)}")
            for p in panels:
                check(f"{p.id} fits its quadrant at {size}", gap(p) == 0,
                      f"gap={gap(p)} cols={widths(p)} panel={panel_w(p)}")
                check(f"{p.id} has no horizontal scrollbar at {size}",
                      not bool(getattr(p, "show_horizontal_scrollbar", False)),
                      f"cols={widths(p)}")


async def test_songs_panel_keeps_its_fixed_heart():
    app = TApp(Fake())
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        panels = await render_grid(app, pilot)
        songs = [p for p in panels if p.id == "songs_table"]
        check("songs panel is present", len(songs) == 1)
        if songs:
            w = widths(songs[0])
            check("songs panel heart column stays 3 wide", len(w) == 2 and w[0] == 3, f"w={w}")
            check("songs panel title column takes the rest", len(w) == 2 and w[1] > 3, f"w={w}")


async def test_stacked_layout_fits_too():
    # Narrow enough that the grid reflows to a single stacked column.
    app = TApp(Fake())
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        panels = await render_grid(app, pilot)
        for p in panels:
            check(f"{p.id} fits when stacked", gap(p) == 0,
                  f"gap={gap(p)} cols={widths(p)} panel={panel_w(p)}")


async def test_resize_refits_the_panels():
    app = TApp(Fake())
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        panels = await render_grid(app, pilot)
        before = [widths(p) for p in panels]
        await pilot.resize_terminal(130, 30)
        for _ in range(10):
            await pilot.pause()
        panels = list(app.query(SearchPanel))
        after = [widths(p) for p in panels]
        check("resize changes the panel column widths", before != after, f"{before} -> {after}")
        for p in panels:
            check(f"{p.id} still fits after resize", gap(p) == 0,
                  f"gap={gap(p)} cols={widths(p)} panel={panel_w(p)}")


ALL = [test_every_panel_fits_its_quadrant, test_songs_panel_keeps_its_fixed_heart,
       test_stacked_layout_fits_too, test_resize_refits_the_panels]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (grid panel fill) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
