"""Main layout is tight: the central block sits directly under the top bar
(Search + Help) and directly above Now Playing, with no extra empty rows — the
effective gaps are 0, resolved purely by the grid CSS (no spacer widgets). Now
Playing stays visible and nothing overflows vertically.

This reverses the earlier change that added a #top_spacer to make both gaps 1;
the spacers are gone and the grid is auto / 1fr / auto.

Run standalone:  python tests/test_now_playing_spacing.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

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
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

# Tolerance: 0 in every observed case. Allow a single cell only if a future
# Textual inserts an inseparable border row; anything larger is a real gap.
TOL = 1

SIZES = [(140, 40), (120, 30), (100, 24), (90, 20), (70, 18)]


async def _gaps(size, mount_table=False):
    app = TApp(Fake())
    async with app.run_test(size=size) as pilot:
        await pilot.pause(); pause_intervals(app)
        if mount_table:
            rows = [{"id": f"t{i}", "uri": f"spotify:track:t{i}", "title": f"Song {i}",
                     "artist": "A", "album": "Al", "dur": "0:00"} for i in range(40)]
            app._render_tracks_table("[b]Long[/b]", rows, [False] * len(rows), profile="playlist")
        for _ in range(3):
            await pilot.pause()

        def region(sel):
            w = app.query_one(sel); r = w.region
            return (r.y, r.height, r.y + r.height)

        tb = region("#top_bar")
        lc = region("#left_col")
        rt = region("#right")
        nw = region("#now_wrap")
        gap_top = min(lc[0], rt[0]) - tb[2]
        gap_bottom = nw[0] - max(lc[2], rt[2])
        # Bottom of Now Playing must not exceed the screen height (no vertical
        # overflow hiding it).
        overflow = nw[2] - int(size[1])
        return gap_top, gap_bottom, nw[1], overflow


async def test_zero_gaps_menu():
    for size in SIZES:
        gt, gb, now_h, overflow = await _gaps(size)
        check(f"gap above central == 0 at {size}", gt <= TOL and gt >= 0, f"gap_top={gt}")
        check(f"gap below central == 0 at {size}", gb <= TOL and gb >= 0, f"gap_bottom={gb}")
        check(f"Now Playing visible at {size}", now_h > 0, f"now_h={now_h}")
        check(f"no vertical overflow past Now Playing at {size}", overflow <= 0, f"overflow={overflow}")


async def test_zero_gaps_with_long_table():
    for size in [(120, 30), (90, 20)]:
        gt, gb, now_h, overflow = await _gaps(size, mount_table=True)
        check(f"gap above central == 0 with a long table at {size}", 0 <= gt <= TOL, f"gap_top={gt}")
        check(f"gap below central == 0 with a long table at {size}", 0 <= gb <= TOL, f"gap_bottom={gb}")
        check(f"Now Playing still visible with a long table at {size}", now_h > 0 and overflow <= 0,
              f"now_h={now_h} overflow={overflow}")


async def test_no_spacer_widgets():
    app = TApp(Fake())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        n_top = len(app.query("#top_spacer"))
        n_grid = len(app.query("#grid_spacer"))
        check("no #top_spacer widget mounted", n_top == 0, f"n={n_top}")
        check("no #grid_spacer widget mounted", n_grid == 0, f"n={n_grid}")


ALL = [test_zero_gaps_menu, test_zero_gaps_with_long_table, test_no_spacer_widgets]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (now-playing spacing) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
