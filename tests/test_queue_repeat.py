"""Ctrl+C (open the Queue) pressed twice in a row or held down (auto-repeat)
must not crash with DuplicateIds('queue_table'), must leave exactly one
#queue_table and a single queue refresh interval. Offline: fake client, the
network refresh is stubbed.

Run standalone:  python tests/test_queue_repeat.py
"""
import os, sys, asyncio, tempfile, traceback

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_qrep_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import DataTable
from spt_tui import config
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
    def _normalize_track_id(self, x): return x
    def fmt_duration(self, ms):
        s = int((ms or 0) / 1000); return f"{s // 60}:{s % 60:02d}"
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)


class TApp(SptPy):
    def __init__(self, fake):
        super().__init__(); self.spotify = fake
        self.queue_refreshes = 0
        self.queue_intervals = []          # every Timer handed to _queue_interval
        self.errors = []

    def _refresh_queue_table(self):        # never hit the network
        self.queue_refreshes += 1

    def set_interval(self, interval, callback=None, *a, **kw):
        t = super().set_interval(interval, callback, *a, **kw)
        if getattr(callback, "__name__", "") == "_refresh_queue_table":
            self.queue_intervals.append(t)
        return t

    def _handle_exception(self, error):
        self.errors.append(repr(error))
        super()._handle_exception(error)


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


def live_queue_intervals(app):
    # Paused/stopped timers no longer fire; count the ones still active.
    return [t for t in app.queue_intervals
            if not getattr(t, "_active", None) is None and t._active.is_set()]


async def run_case(n):
    config.LOCAL_CFG = {}
    app = TApp(Fake())
    try:
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await pilot.press(*(["ctrl+c"] * n))     # no pause between presses
            for _ in range(5):
                await pilot.pause()
            tables = [w for w in app.query(DataTable) if w.id == "queue_table"]
            live = live_queue_intervals(app)
            check(f"[{n}x] no exception", not app.errors, "; ".join(app.errors))
            check(f"[{n}x] exactly one #queue_table", len(tables) == 1, f"found {len(tables)}")
            check(f"[{n}x] one live queue refresh interval", len(live) == 1,
                  f"live={len(live)} created={len(app.queue_intervals)}")
            check(f"[{n}x] _queue_interval is that one interval",
                  live and app._queue_interval is live[0])
            check(f"[{n}x] view is the queue", (app._right_view or (None,))[0] == "queue")
    except Exception:
        check(f"[{n}x] app survived", False, traceback.format_exc().splitlines()[-1])
    if app.errors and not any(n_ == f"[{n}x] no exception" for n_, _, _ in RESULTS):
        check(f"[{n}x] no exception", False, "; ".join(app.errors))


async def main():
    for n in (2, 30):
        await run_case(n)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return passed == len(RESULTS) and len(RESULTS) > 0

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
