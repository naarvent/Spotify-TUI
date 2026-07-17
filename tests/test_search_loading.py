"""Phase 6: search loading feedback.

Verifies the on-submit search state: immediate 'Searching for: <query>' before
any (possibly slow) response is released, no internal 'worker' wording, the
prolonged state only when still active, stale A dropped when B supersedes,
leaving Search cancels any repaint, clean teardown, and error vs zero-results.

Network is faked and gated with threading.Event (no fixed sleeps).
Run standalone:  python tests/test_search_loading.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from textual.widgets import DataTable

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class SearchInner:
    def __init__(self, o): self.o = o
    def search(self, q, type='track', limit=25): return self.o._search(q, type, limit)

class SearchFake:
    def __init__(self):
        self.inner = SearchInner(self)
        self.gate = None        # set to a threading.Event to block the search
        self.mode = "ok"        # ok | empty | error
        self.calls = 0
        self.lock = threading.Lock()
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self.inner
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def check_saved_tracks(self, ids): return [False] * len(ids)
    def _search(self, q, type, limit):
        with self.lock: self.calls += 1
        g = self.gate
        if g is not None: g.wait(10)
        if self.mode == "error": raise RuntimeError("boom")
        key = {'track': 'tracks', 'album': 'albums', 'artist': 'artists',
               'playlist': 'playlists', 'episode': 'episodes', 'show': 'shows'}[type]
        if self.mode == "empty": return {key: {"items": []}}
        if type == 'track':
            return {key: {"items": [{"id": "t1", "uri": "spotify:track:t1", "name": "Song",
                                     "artists": [{"name": "A"}], "album": {"name": "Al"},
                                     "duration_ms": 1000}]}}
        return {key: {"items": []}}


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def right_text(app):
    return str(getattr(app.right_panel, "_Static__content", "") or "")

def stbl(app):
    for w in app.query(DataTable):
        if getattr(w, "id", "") == "search_table":
            return w
    return None

async def poll(fn, timeout=5.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.01)
    try: return fn()
    except Exception: return None

async def pump(pilot, n=20):
    for _ in range(n):
        try: await pilot.pause()
        except Exception: await asyncio.sleep(0.01)


async def test_immediate_feedback_before_blocked_response():
    fake = SearchFake(); fake.gate = threading.Event()   # block the search
    app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("daft punk")
        await pilot.pause()
        txt = right_text(app)
        check("immediate 'Searching for: <query>' before any response", "Searching for:" in txt and "daft punk" in txt,
              f"txt={txt!r}")
        check("query is visible in the searching state", "daft punk" in txt, f"txt={txt!r}")
        check("no internal 'worker' wording shown", "worker" not in txt.lower(), f"txt={txt!r}")
        check("no results table while the response is blocked", stbl(app) is None)
        fake.gate.set()
        await poll(lambda: stbl(app) is not None, timeout=5.0)
        check("results replace the searching state", stbl(app) is not None and "Searching for:" not in right_text(app),
              f"txt={right_text(app)!r}")


async def test_prolonged_state_only_when_active():
    fake = SearchFake(); fake.gate = threading.Event()
    app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("query")
        await pilot.pause()
        token = app._last_search_worker
        # Simulate the watchdog firing while the search is still active.
        app._begin_search_feedback  # touch to ensure attribute exists
        # active -> prolonged text should paint
        # (call the same guard logic the timer uses)
        active = (not getattr(app, "_closing", False)
                  and getattr(app, "_searching_token", None) == token
                  and getattr(app, "_last_search_worker", None) == token
                  and getattr(app, "_search_rendered_token", None) != token)
        check("watchdog would fire while search is active", active is True)
        # after results, the same guard must be false
        fake.gate.set()
        await poll(lambda: stbl(app) is not None, timeout=5.0)
        stale = (getattr(app, "_searching_token", None) == token
                 and getattr(app, "_search_rendered_token", None) != token)
        check("watchdog is suppressed once results are rendered", stale is False)


async def test_stale_A_dropped_when_B_supersedes():
    fake = SearchFake(); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        gA = threading.Event(); fake.gate = gA
        app._dispatch_search("AAAA")             # A blocks
        await pilot.pause()
        tokenA = app._last_search_worker
        fake.gate = None
        app._dispatch_search("BBBB")             # B supersedes and renders
        await poll(lambda: stbl(app) is not None, timeout=5.0)
        tokenB = app._last_search_worker
        check("B token is newer than A", tokenB > tokenA)
        # A's late render must be a no-op.
        before = app._search_rendered_token
        app._render_search_results({"tracks": {"items": [{"id": "t1", "uri": "u", "name": "x",
                                    "artists": [], "album": {}, "duration_ms": 1}]}}, "AAAA", None, tokenA)
        after = app._search_rendered_token
        check("stale A render is dropped (does not replace B)", after == before == tokenB,
              f"before={before} after={after} B={tokenB}")
        gA.set(); await pump(pilot, 10)


async def test_leaving_search_cancels_repaint():
    fake = SearchFake(); fake.gate = threading.Event()
    app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("query")
        await pilot.pause()
        token = app._last_search_worker
        app.action_escape_to_menu()              # leave Search
        await pilot.pause()
        check("leaving Search clears the searching token", getattr(app, "_searching_token", None) != token)
        fake.gate.set()                          # blocked worker finishes now
        await pump(pilot, 30)
        check("no results table painted over the menu after leaving", stbl(app) is None,
              f"txt={right_text(app)!r}")


async def test_error_vs_zero_results():
    # zero results (forced type) -> distinct 'No results'
    fake = SearchFake(); fake.mode = "empty"; app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("/TRK nothingxyz")
        await poll(lambda: "No results" in right_text(app), timeout=5.0)
        check("zero results shows a clear 'No results' state", "No results" in right_text(app),
              f"txt={right_text(app)!r}")
        check("zero results is not an error message", "failed" not in right_text(app).lower())
    # error -> distinct 'failed' + retry, no traceback / internals
    fake2 = SearchFake(); fake2.mode = "error"; app2 = TApp(fake2)
    async with app2.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app2)
        app2._dispatch_search("netdownquery")
        await poll(lambda: "failed" in right_text(app2).lower(), timeout=5.0)
        txt = right_text(app2)
        check("search error shows a clear failure + retry hint", "failed" in txt.lower() and "try again" in txt.lower(),
              f"txt={txt!r}")
        check("error does not leak a traceback / internals", "Traceback" not in txt and "boom" not in txt,
              f"txt={txt!r}")


async def test_no_worker_thread_accumulation():
    # Back-to-back searches must not leave the app painting stale states; the
    # newest token is the only current one.
    fake = SearchFake(); app = TApp(fake)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        for i in range(5):
            app._dispatch_search(f"q{i}")
            await pilot.pause()
        await poll(lambda: stbl(app) is not None, timeout=5.0)
        check("only the last search is current", app._last_search_worker == app._search_rendered_token,
              f"last={app._last_search_worker} rendered={app._search_rendered_token}")
        check("exactly one search_table mounted", sum(1 for w in app.query(DataTable)
              if getattr(w, "id", "") == "search_table") == 1)


ALL = [test_immediate_feedback_before_blocked_response, test_prolonged_state_only_when_active,
       test_stale_A_dropped_when_B_supersedes, test_leaving_search_cancels_repaint,
       test_error_vs_zero_results, test_no_worker_thread_accumulation]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (search loading) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
