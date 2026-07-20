"""Progressive paint for big playlists.

`_open_playlist_table` painted only the first page and then kept the rest of the
rows in memory until `check_saved_tracks` had finished for the whole playlist, so
a large playlist looked half-loaded until the hearts resolved. These tests pin
the per-page paint and the fact that the likes call never gates the rows.

Pagination and the likes call are gated with threading.Event (no fixed sleeps).

Run standalone:  python tests/test_playlist_progressive.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class PlFake:
    """Spotify fake serving a paginated playlist plus a gateable likes call."""
    def __init__(self, total=250):
        self.total = total
        self.lock = threading.Lock()
        self.page_calls = 0
        self.offsets = []
        self.page_entered = {}       # call index -> Event (set on entry)
        self.page_gate = {}          # call index -> Event (blocks until set)
        self.likes_entered = threading.Event()
        self.likes_gate = None
        self.likes_ids = []

    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def _normalize_track_id(self, x): return x
    def fmt_duration(self, ms):
        s = int((ms or 0) / 1000); return f"{s // 60}:{s % 60:02d}"

    def playlist_items(self, pid, limit=100, offset=0, fields=None):
        with self.lock:
            self.page_calls += 1; idx = self.page_calls
            self.offsets.append(offset)
        self.page_entered.setdefault(idx, threading.Event()).set()
        g = self.page_gate.get(idx)
        if g is not None:
            g.wait(10)
        end = min(self.total, offset + limit)
        items = [{"added_at": "2024-01-01T00:00:00Z", "track": {
                    "id": f"t{i}", "uri": f"spotify:track:t{i}", "type": "track",
                    "name": f"song {i}", "duration_ms": 1000,
                    "artists": [{"name": "a"}], "album": {"name": "al"}}}
                 for i in range(offset, end)]
        return {"items": items, "total": self.total}

    def check_saved_tracks(self, ids, on_batch=None):
        self.likes_ids = list(ids)
        self.likes_entered.set()
        if self.likes_gate is not None:
            self.likes_gate.wait(10)
        vals = [True] * len(ids)
        if on_batch is not None and vals:
            on_batch(0, vals)
        return vals


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake


PL = {"id": "pl1", "name": "Big One", "uri": "spotify:playlist:pl1"}


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

async def poll(fn, timeout=5.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.01)
    try: return fn()
    except Exception: return None

async def wait_event(e, timeout=5.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end and not e.is_set():
        await asyncio.sleep(0.01)
    return e.is_set()

async def page_entered(fake, idx, timeout=5.0):
    return await wait_event(fake.page_entered.setdefault(idx, threading.Event()), timeout)

def table_by_id(app, tid):
    from textual.widgets import DataTable
    try:
        for w in app.query(DataTable):
            if getattr(w, "id", "") == tid:
                return w
    except Exception:
        pass
    return None

def nrows(app):
    t = table_by_id(app, "tracks_table")
    return len(getattr(t, "_model_rows", []) or []) if t is not None else -1


async def test_pages_paint_before_likes():
    """The whole track list must be on screen before the likes call returns."""
    fake = PlFake(total=250); fake.likes_gate = threading.Event()
    app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_playlist_table(dict(PL))
        entered = await wait_event(fake.likes_entered, timeout=5.0)
        check("likes check runs after the pages", entered)
        # Likes are still blocked here: every row must already be painted.
        await poll(lambda: nrows(app) == 250, timeout=3.0)
        check("all 250 rows painted while likes are still pending", nrows(app) == 250,
              f"n={nrows(app)}")
        fake.likes_gate.set()
        await poll(lambda: all(getattr(table_by_id(app, "tracks_table"), "_liked_map", {}).values())
                   and len(getattr(table_by_id(app, "tracks_table"), "_liked_map", {})) == 250,
                   timeout=5.0)
        tbl = table_by_id(app, "tracks_table")
        check("hearts fill in after the likes call",
              len(getattr(tbl, "_liked_map", {})) == 250 and all(tbl._liked_map.values()))


async def test_paint_per_page():
    """Page 2 shows up without waiting for page 3."""
    fake = PlFake(total=250)
    fake.page_gate[3] = threading.Event()        # block the 3rd page
    app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_playlist_table(dict(PL))
        await page_entered(fake, 3)              # pages 1+2 done, 3 blocked
        await poll(lambda: nrows(app) == 200, timeout=3.0)
        check("second page painted before the third arrives", nrows(app) == 200, f"n={nrows(app)}")
        fake.page_gate[3].set()
        await poll(lambda: nrows(app) == 250, timeout=5.0)
        check("last page streamed in", nrows(app) == 250, f"n={nrows(app)}")


async def test_single_page_playlist_still_renders():
    fake = PlFake(total=40); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_playlist_table(dict(PL))
        await poll(lambda: nrows(app) == 40, timeout=5.0)
        check("single-page playlist renders all rows", nrows(app) == 40, f"n={nrows(app)}")
        check("single-page playlist fetched exactly one page", fake.page_calls == 1,
              f"calls={fake.page_calls}")


async def test_cursor_preserved_across_page_paints():
    fake = PlFake(total=250)
    fake.page_gate[2] = threading.Event()
    app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_playlist_table(dict(PL))
        await poll(lambda: nrows(app) == 100, timeout=5.0)
        tbl = table_by_id(app, "tracks_table")
        tbl.cursor_coordinate = (7, 0)
        fake.page_gate[2].set()
        await poll(lambda: nrows(app) == 250, timeout=5.0)
        tbl = table_by_id(app, "tracks_table")
        row = getattr(getattr(tbl, "cursor_coordinate", None), "row", None)
        check("cursor row kept while later pages paint", row == 7, f"row={row}")


ALL = [test_pages_paint_before_likes, test_paint_per_page,
       test_single_page_playlist_still_renders, test_cursor_preserved_across_page_paints]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (playlist progressive) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
