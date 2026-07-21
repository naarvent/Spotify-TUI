"""The playlist worker stops when you leave, and its hearts stream in.

Two things the first progressive pass left behind:

* the page loop never checked staleness, so leaving a big playlist kept
  downloading every remaining page and then ran check_saved_tracks over the
  whole list — work whose result is thrown away on arrival;
* the liked lookup batches 50 ids at a time but applied the result in one go,
  so no heart appeared until the last batch had returned.

Pagination and each liked batch are gated with threading.Event (no fixed sleeps).

Run standalone:  python tests/test_playlist_streaming.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from spt_tui.spotify_client import SpotifyClient

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class PlFake:
    """Playlist pages and liked batches, each individually gateable."""
    def __init__(self, total=500):
        self.total = total
        self.lock = threading.Lock()
        self.page_calls = 0
        self.page_entered = {}       # call index -> Event
        self.page_gate = {}          # call index -> Event
        self.likes_calls = 0
        self.likes_entered = {}      # call index -> Event
        self.likes_gate = {}         # call index -> Event
        self.likes_batches = []      # ids per call, in order

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
        """Mirrors the real batching (50 per call) so the test can gate a batch."""
        out = []
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            with self.lock:
                self.likes_calls += 1; idx = self.likes_calls
                self.likes_batches.append(list(batch))
            self.likes_entered.setdefault(idx, threading.Event()).set()
            g = self.likes_gate.get(idx)
            if g is not None:
                g.wait(10)
            vals = [True] * len(batch)
            out.extend(vals)
            if on_batch is not None:
                on_batch(i, vals)
        return out


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

async def entered(store, idx, timeout=5.0):
    return await wait_event(store.setdefault(idx, threading.Event()), timeout)

async def settle(seconds=0.5):
    loop = asyncio.get_event_loop(); end = loop.time() + seconds
    while loop.time() < end:
        await asyncio.sleep(0.02)

def table(app):
    from textual.widgets import DataTable
    try:
        for w in app.query(DataTable):
            if getattr(w, "id", "") == "tracks_table":
                return w
    except Exception:
        pass
    return None

def hearts(app):
    t = table(app)
    m = getattr(t, "_liked_map", {}) or {} if t is not None else {}
    return sum(1 for v in m.values() if v)


async def test_leaving_stops_the_page_fetch():
    fake = PlFake(total=500)
    fake.page_gate[2] = threading.Event()      # block the 2nd page
    app = TApp(fake)
    async with app.run_test(size=(120, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_playlist_table(dict(PL))
        await entered(fake.page_entered, 2)
        app.action_escape_to_menu()            # leave while page 2 is in flight
        await pilot.pause()
        fake.page_gate[2].set()
        await settle(0.6)
        check("the worker stops fetching pages once the view is gone",
              fake.page_calls <= 2, f"page_calls={fake.page_calls} (of 5)")
        check("the abandoned playlist never runs the liked lookup",
              fake.likes_calls == 0, f"likes_calls={fake.likes_calls}")


async def test_reopening_another_playlist_stops_the_first():
    fake = PlFake(total=500)
    fake.page_gate[2] = threading.Event()
    app = TApp(fake)
    async with app.run_test(size=(120, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_playlist_table(dict(PL))
        await entered(fake.page_entered, 2)
        app._open_playlist_table({"id": "pl2", "name": "Other", "uri": "spotify:playlist:pl2"})
        await pilot.pause()
        fake.page_gate[2].set()
        await poll(lambda: (table(app) is not None
                            and len(getattr(table(app), "_model_rows", []) or []) == 500),
                   timeout=6.0)
        t = table(app)
        check("the superseded worker does not keep paging",
              fake.page_calls <= 7, f"page_calls={fake.page_calls} (5 per playlist)")
        check("the newer playlist still loads fully",
              len(getattr(t, "_model_rows", []) or []) == 500,
              f"rows={len(getattr(t, '_model_rows', []) or [])}")


async def test_hearts_arrive_per_batch():
    fake = PlFake(total=200)
    fake.likes_gate[3] = threading.Event()     # block the 3rd liked batch
    app = TApp(fake)
    async with app.run_test(size=(120, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_playlist_table(dict(PL))
        await entered(fake.likes_entered, 3)
        # Batches 1 and 2 are back (100 ids); their hearts must already be on.
        await poll(lambda: hearts(app) >= 100, timeout=3.0)
        check("hearts from the finished batches show before the rest return",
              hearts(app) >= 100, f"hearts={hearts(app)} of 200")
        fake.likes_gate[3].set()
        await poll(lambda: hearts(app) == 200, timeout=5.0)
        check("every heart is on once the last batch returns", hearts(app) == 200,
              f"hearts={hearts(app)}")


async def test_check_saved_tracks_reports_batches_in_order():
    """The client-side contract the playlist worker relies on."""
    seen = []

    class Inner:
        def current_user_saved_tracks_contains(self, batch):
            return [True] * len(batch)

    c = SpotifyClient.__new__(SpotifyClient)
    c.ensure = lambda: Inner()
    ids = [f"t{i}" for i in range(120)]
    out = c.check_saved_tracks(ids, on_batch=lambda start, vals: seen.append((start, len(vals))))
    check("check_saved_tracks still returns one bool per id", len(out) == 120 and all(out),
          f"len={len(out)}")
    size = SpotifyClient.SAVED_BATCH
    expected = [(i, len(ids[i:i + size])) for i in range(0, len(ids), size)]
    check("on_batch reports each batch with its start offset", seen == expected,
          f"seen={seen} expected={expected}")
    check("check_saved_tracks works without a callback",
          c.check_saved_tracks(ids[:10]) == [True] * 10)


ALL = [test_leaving_stops_the_page_fetch, test_reopening_another_playlist_stops_the_first,
       test_hearts_arrive_per_batch, test_check_saved_tracks_reports_batches_in_order]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (playlist streaming) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
