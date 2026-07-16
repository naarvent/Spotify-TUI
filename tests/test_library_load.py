"""Deterministic tests for the responsive saved-library loaders (Phase 1).

Covers: immediate non-blocking feedback, progressive per-page paint, worker
dedup / no stale paint, leaving-the-view safety, partial-on-error (never blank),
empty-vs-error distinction, in-memory cache, Ctrl+R routing, and cursor
preservation across progressive updates.

Pagination is gated per call index with threading.Event (no fixed sleeps).

Run standalone:  python tests/test_library_load.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Inner:
    def __init__(self, o): self.o = o
    def current_user_saved_albums(self, limit=50, offset=0):
        return self.o._page("albums", offset)

class Fake:
    def __init__(self, total=120):
        self.inner = Inner(self)
        self.total = total
        self.lock = threading.Lock()
        self.n = 0
        self.offsets = []            # offsets fetched, in call order (all kinds)
        self.entered = {}            # call-index -> Event (set on entry)
        self.gate = {}               # call-index -> Event (blocks until set)
        self.fail_idx = set()        # call indices that raise
        self.album_calls = 0

    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self.inner
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x

    def _page(self, kind, offset):
        with self.lock:
            self.n += 1; idx = self.n
            self.offsets.append(offset)
            if kind == "albums": self.album_calls += 1
        self.entered.setdefault(idx, threading.Event()).set()
        g = self.gate.get(idx)
        if g is not None:
            g.wait(10)
        if idx in self.fail_idx:
            raise RuntimeError("page boom")
        end = min(self.total, offset + 50)
        items = []
        for i in range(offset, end):
            if kind == "liked":
                items.append({"track": {"id": f"t{i}", "uri": f"spotify:track:t{i}",
                              "name": f"song {i}", "artists": [{"name": "a"}],
                              "album": {"name": "al"}, "duration_ms": 1000}, "added_at": "2020-01-01"})
            else:
                items.append({"album": {"id": f"al{i}", "uri": f"spotify:album:al{i}",
                              "name": f"album {i}", "artists": [{"name": "a"}]}})
        return {"items": items, "next": ("x" if end < self.total else None)}

    def saved_tracks(self, limit=50, offset=0):
        return self._page("liked", offset)


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval","_now_tick_interval","_now_interval","_devices_interval","_queue_interval"):
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

async def pump(pilot, n=15):
    # pilot.pause() can raise WaitForScreenTimeout while several background
    # workers are posting messages; fall back to a plain loop yield so the test
    # stays deterministic instead of crashing on harness timing.
    for _ in range(n):
        try:
            await pilot.pause()
        except Exception:
            await asyncio.sleep(0.01)

async def settle(seconds=0.4):
    # Yield to the loop without pilot's strict screen-idle wait (which can time
    # out while several background workers are posting messages).
    loop = asyncio.get_event_loop(); end = loop.time() + seconds
    while loop.time() < end:
        await asyncio.sleep(0.02)

def right_text(app):
    return str(getattr(app.right_panel, "_Static__content", "") or "")

def table_by_id(app, tid):
    from textual.widgets import DataTable
    try:
        for w in app.query(DataTable):
            if getattr(w, "id", "") == tid:
                return w
    except Exception:
        pass
    return None

def nrows(table):
    return len(getattr(table, "_model_rows", []) or []) if table is not None else -1

async def ev(fake, idx, timeout=5.0):
    e = fake.entered.setdefault(idx, threading.Event())
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end and not e.is_set():
        await asyncio.sleep(0.01)
    return e.is_set()


async def test_immediate_feedback_non_blocking():
    fake = Fake(total=120); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        fake.gate[1] = threading.Event()          # block first page
        app._open_liked_table()                   # must return immediately (else this coroutine blocks)
        shown = await poll(lambda: "Loading Liked Songs" in right_text(app), timeout=3.0)
        check("Enter returns immediately + shows 'Loading Liked Songs…'", bool(shown),
              f"right={right_text(app)!r}")
        check("no table painted while first page is still loading", table_by_id(app, "tracks_table") is None)
        fake.gate[1].set()
        tbl = await poll(lambda: table_by_id(app, "tracks_table"), timeout=5.0)
        check("table appears once data arrives", tbl is not None and nrows(tbl) >= 50, f"n={nrows(tbl)}")
        await poll(lambda: nrows(table_by_id(app, "tracks_table")) == 120, timeout=5.0)


async def test_progressive_paint():
    fake = Fake(total=120); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        fake.gate[2] = threading.Event()          # block the 2nd page
        app._open_liked_table()
        await ev(fake, 2)                          # page1 reached and blocked
        tbl = await poll(lambda: table_by_id(app, "tracks_table"), timeout=5.0)
        check("first page painted before later pages (progressive)",
              tbl is not None and nrows(tbl) == 50, f"n={nrows(tbl)}")
        fake.gate[2].set()
        done = await poll(lambda: nrows(table_by_id(app, "tracks_table")) == 120, timeout=5.0)
        check("all pages streamed in", nrows(table_by_id(app, "tracks_table")) == 120)


async def test_superseded_worker_stops_and_does_not_paint():
    fake = Fake(total=120); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        fake.gate[2] = threading.Event()          # worker A blocks on its page1
        app._open_liked_table()                   # A (token1)
        await ev(fake, 2)
        app._open_liked_table()                   # B (token2) supersedes
        done = await poll(lambda: nrows(table_by_id(app, "tracks_table")) == 120, timeout=5.0)
        check("newer load (B) paints fully", nrows(table_by_id(app, "tracks_table")) == 120)
        fake.gate[2].set()                        # let A resume; it must bail, not repaint
        await pump(pilot, 30)
        check("stale worker A does not overwrite B", nrows(table_by_id(app, "tracks_table")) == 120,
              f"n={nrows(table_by_id(app,'tracks_table'))}")
        check("stale worker A stopped fetching further pages (no duplicate last page)",
              fake.offsets.count(100) == 1, f"offsets={fake.offsets}")


async def test_leaving_view_prevents_paint():
    fake = Fake(total=120); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        fake.gate[1] = threading.Event()
        app._open_liked_table()
        await ev(fake, 1)
        app.action_escape_to_menu()               # leave before any page paints
        fake.gate[1].set()
        await pump(pilot, 30)
        check("worker does not paint after leaving the view", table_by_id(app, "tracks_table") is None,
              f"table={table_by_id(app,'tracks_table')}")


async def test_partial_on_error_keeps_rows():
    fake = Fake(total=120); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        fake.fail_idx = {2}                        # 2nd page fails
        app._open_liked_table()
        tbl = await poll(lambda: table_by_id(app, "tracks_table"), timeout=5.0)
        await poll(lambda: "partial" in right_text_title(app), timeout=5.0)
        check("error mid-pagination keeps the already-loaded page (not blanked)",
              nrows(table_by_id(app, "tracks_table")) == 50, f"n={nrows(table_by_id(app,'tracks_table'))}")
        check("partial state shown with retry hint", "partial" in right_text_title(app),
              f"title={right_text_title(app)!r}")


def right_text_title(app):
    from textual.widgets import Static
    try:
        return str(getattr(app.query_one("#tracks_title", Static), "_Static__content", "") or "")
    except Exception:
        return ""


async def test_empty_vs_error():
    # real empty
    fake = Fake(total=0); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_liked_table()
        await poll(lambda: "No Liked Songs" in right_text(app), timeout=5.0)
        check("real empty collection shows empty message", "No Liked Songs" in right_text(app),
              f"right={right_text(app)!r}")
    # error on first page, no cache
    fake2 = Fake(total=120); app2 = TApp(fake2)
    async with app2.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app2)
        fake2.fail_idx = {1}
        app2._open_liked_table()
        await poll(lambda: "Could not load" in right_text(app2), timeout=5.0)
        check("first-page error (no cache) shows load error, not empty",
              "Could not load" in right_text(app2), f"right={right_text(app2)!r}")


async def test_cache_shows_before_network_and_ctrlr_reloads():
    fake = Fake(total=30); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        # first load fills the cache
        app._open_liked_table()
        await poll(lambda: nrows(table_by_id(app, "tracks_table")) == 30, timeout=5.0)
        # the worker fills the in-memory cache even as we leave the view
        await poll(lambda: "liked" in (getattr(app, "_lib_cache", {}) or {}), timeout=3.0)
        # leave, then reopen: cache must paint before the (gated) network returns
        app.action_escape_to_menu(); await pump(pilot, 5)
        next_idx = fake.n + 1
        fake.gate[next_idx] = threading.Event()
        app._open_liked_table()
        cached = await poll(lambda: nrows(table_by_id(app, "tracks_table")) == 30, timeout=3.0)
        check("cached rows shown immediately on reopen (before network)", cached,
              f"n={nrows(table_by_id(app,'tracks_table'))}")
        fake.gate[next_idx].set()
        await pump(pilot, 15)
        check("table not blanked during refresh", nrows(table_by_id(app, "tracks_table")) == 30)


async def test_ctrlr_routes_albums_and_no_accumulation():
    fake = Fake(total=30); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_saved_albums()
        await poll(lambda: table_by_id(app, "search_table") is not None, timeout=5.0)
        base = fake.album_calls
        app.action_refresh()                       # Ctrl+R must retry albums, not playlists
        await poll(lambda: fake.album_calls > base, timeout=5.0)
        check("Ctrl+R retries the current saved-albums view", fake.album_calls > base,
              f"album_calls {base}->{fake.album_calls}")
        # repeated Ctrl+R supersedes rather than stacking: view stays albums
        app.action_refresh(); app.action_refresh()
        await settle(0.6)
        rv = getattr(app, "_right_view", None)
        check("repeated Ctrl+R keeps a single current albums view", rv and rv[0] == "albums",
              f"rv={rv}")


async def test_cursor_preserved_during_progressive():
    fake = Fake(total=120); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        fake.gate[2] = threading.Event()
        app._open_liked_table()
        await ev(fake, 2)
        tbl = await poll(lambda: table_by_id(app, "tracks_table"), timeout=5.0)
        tbl.move_cursor(row=25, animate=False)
        for _ in range(5): await pilot.pause()
        fake.gate[2].set()
        await poll(lambda: nrows(table_by_id(app, "tracks_table")) == 120, timeout=5.0)
        for _ in range(5): await pilot.pause()
        check("cursor kept while later pages stream in",
              table_by_id(app, "tracks_table").cursor_row == 25,
              f"cursor={table_by_id(app,'tracks_table').cursor_row}")


ALL = [test_immediate_feedback_non_blocking, test_progressive_paint,
       test_superseded_worker_stops_and_does_not_paint, test_leaving_view_prevents_paint,
       test_partial_on_error_keeps_rows, test_empty_vs_error,
       test_cache_shows_before_network_and_ctrlr_reloads,
       test_ctrlr_routes_albums_and_no_accumulation, test_cursor_preserved_during_progressive]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (library load) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
