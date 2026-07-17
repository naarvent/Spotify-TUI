"""Phase 6: the Now Playing block is separated from the central block by the
same vertical gap as the top bar (Search + Help) is — a uniform layout gap,
resolved purely in the grid CSS. Now Playing stays visible.

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


async def _gaps(size):
    app = TApp(Fake())
    async with app.run_test(size=size) as pilot:
        await pilot.pause(); pause_intervals(app)
        for _ in range(3):
            await pilot.pause()

        def region(sel):
            w = app.query_one(sel); r = w.region
            return (r.y, r.height, r.y + r.height)

        tb = region("#top_bar")
        lc = region("#left_col")
        rt = region("#right")
        nw = region("#now_wrap")
        top_bottom = tb[2]
        central_top = min(lc[0], rt[0])
        central_bottom = max(lc[2], rt[2])
        now_top = nw[0]
        return (central_top - top_bottom, now_top - central_bottom, nw[1])


async def test_uniform_gap_multiple_sizes():
    for size in [(120, 30), (100, 24), (90, 20)]:
        gap_top, gap_bottom, now_h = await _gaps(size)
        check(f"gap above central == gap below central at {size}",
              abs(gap_top - gap_bottom) <= 1 and gap_top >= 1, f"top={gap_top} bottom={gap_bottom}")
        check(f"Now Playing stays visible at {size}", now_h > 0, f"now_h={now_h}")


ALL = [test_uniform_gap_multiple_sizes]

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
