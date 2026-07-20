"""Reopening a playlist shows its tracks instantly.

Only the playlist *list* was cached (paint_playlists_from_disk); a playlist's
contents were re-downloaded from scratch on every open, so going back into a big
playlist meant staring at "Loading…" again. A session cache paints the last
known rows immediately and the background refresh replaces them.

Run standalone:  python tests/test_playlist_cache.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class PlFake:
    def __init__(self, total=250):
        self.total = total
        self.lock = threading.Lock()
        self.page_calls = 0
        self.first_gate = None       # blocks the first page of every open
        self.title_prefix = "song"

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
            self.page_calls += 1
        if offset == 0 and self.first_gate is not None:
            self.first_gate.wait(10)
        end = min(self.total, offset + limit)
        items = [{"added_at": "2024-01-01T00:00:00Z", "track": {
                    "id": f"{pid}-t{i}", "uri": f"spotify:track:{pid}-t{i}", "type": "track",
                    "name": f"{self.title_prefix} {i}", "duration_ms": 1000,
                    "artists": [{"name": "a"}], "album": {"name": "al"}}}
                 for i in range(offset, end)]
        return {"items": items, "total": self.total}

    def check_saved_tracks(self, ids, on_batch=None):
        vals = [True] * len(ids)
        if on_batch is not None and vals:
            on_batch(0, vals)
        return vals


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake


def pl(i=1):
    return {"id": f"pl{i}", "name": f"List {i}", "uri": f"spotify:playlist:pl{i}"}


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

async def poll(fn, timeout=6.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.01)
    try: return fn()
    except Exception: return None

async def settle(seconds=0.4):
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

def rows(app):
    t = table(app)
    return list(getattr(t, "_model_rows", []) or []) if t is not None else []

def titles(app):
    return [r.get("title", "") for r in rows(app)]


async def load_once(app, pilot, item, n=250):
    """Open a playlist and wait for *its own* rows (the previous playlist's table
    has the same row count, so the count alone cannot tell them apart)."""
    pid = item["id"]
    app._open_playlist_table(item)
    await poll(lambda: len(rows(app)) == n and rows(app)[0].get("id", "").startswith(pid + "-"),
               timeout=8.0)
    await poll(lambda: pid in (getattr(app, "_playlist_cache", {}) or {}), timeout=8.0)


async def test_reopen_paints_before_the_network_returns():
    fake = PlFake(total=250); app = TApp(fake)
    async with app.run_test(size=(140, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await load_once(app, pilot, pl(), 250)
        check("first open loads every row", len(rows(app)) == 250, f"n={len(rows(app))}")
        app.action_escape_to_menu(); await pilot.pause()

        fake.first_gate = threading.Event()      # block the refresh's first page
        app._open_playlist_table(pl())
        await poll(lambda: len(rows(app)) == 250, timeout=3.0)
        check("reopening paints the cached rows while the refresh is blocked",
              len(rows(app)) == 250, f"n={len(rows(app))}")
        check("cached rows keep their hearts",
              all(getattr(table(app), "_liked_map", {}).values()),
              f"hearts={sum(1 for v in getattr(table(app), '_liked_map', {}).values() if v)}")
        fake.first_gate.set()
        await settle(0.5)
        check("no duplicated rows once the refresh lands", len(rows(app)) == 250,
              f"n={len(rows(app))}")


async def test_reopen_still_refreshes_from_the_api():
    fake = PlFake(total=120); app = TApp(fake)
    async with app.run_test(size=(140, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await load_once(app, pilot, pl(), 120)
        calls_after_first = fake.page_calls
        app.action_escape_to_menu(); await pilot.pause()

        fake.title_prefix = "renamed"            # the playlist changed upstream
        app._open_playlist_table(pl())
        await poll(lambda: titles(app) and titles(app)[0].startswith("renamed"), timeout=6.0)
        check("the cache does not stop the background refresh",
              fake.page_calls > calls_after_first, f"calls={fake.page_calls}")
        check("the refreshed rows replace the cached ones",
              titles(app)[:1] == ["renamed 0"], f"first={titles(app)[:1]}")


async def test_cache_is_per_playlist():
    fake = PlFake(total=120); app = TApp(fake)
    async with app.run_test(size=(140, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await load_once(app, pilot, pl(1), 120)
        await load_once(app, pilot, pl(2), 120)
        cache = getattr(app, "_playlist_cache", {}) or {}
        check("both playlists are cached", "pl1" in cache and "pl2" in cache,
              f"keys={sorted(cache)}")
        check("each entry holds its own rows",
              cache["pl1"][0][0]["id"].startswith("pl1") and
              cache["pl2"][0][0]["id"].startswith("pl2"),
              f"pl1={cache['pl1'][0][0]['id']} pl2={cache['pl2'][0][0]['id']}")


async def test_cache_is_bounded():
    fake = PlFake(total=20); app = TApp(fake)
    async with app.run_test(size=(140, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        limit = getattr(app, "_PLAYLIST_CACHE_MAX", 8)
        for i in range(limit + 3):
            await load_once(app, pilot, pl(i), 20)
        cache = getattr(app, "_playlist_cache", {}) or {}
        check(f"the cache keeps at most {limit} playlists", len(cache) <= limit,
              f"n={len(cache)} keys={sorted(cache)}")
        check("the most recent playlist survives eviction", f"pl{limit + 2}" in cache,
              f"keys={sorted(cache)}")


ALL = [test_reopen_paints_before_the_network_returns, test_reopen_still_refreshes_from_the_api,
       test_cache_is_per_playlist, test_cache_is_bounded]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (playlist cache) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
