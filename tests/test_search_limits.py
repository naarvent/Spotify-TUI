"""Default (no-prefix) combined search asks Spotify for only what it shows:
10 tracks, 5 albums, 3 artists, 2 playlists (20 max) and NO podcasts/episodes.
Prefix searches keep their own larger limit and are never squeezed into the
combined caps. Partial failures still show the good types; a total failure is a
distinct error; all-empty is 'No results'.

The limits are asserted at the network boundary (a fake records every
(type, limit) it is asked for), so we verify what is actually requested, not
just what is displayed.

Run standalone:  python tests/test_search_limits.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from textual.widgets import DataTable

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class SInner:
    def __init__(self, o): self.o = o
    def search(self, q, type='track', limit=25): return self.o._search(q, type, limit)

class SFake:
    def __init__(self):
        self.inner = SInner(self)
        self.calls = []          # (type, limit) actually requested
        self.mode = "ok"         # ok | empty | error | fail_one
        self.fail_type = None
        self.avail = 25          # items the backend could return per type
        self.lock = threading.Lock()
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self.inner
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def check_saved_tracks(self, ids): return [False] * len(ids)
    def _item(self, type, i):
        b = {"id": f"{type}{i}", "uri": f"spotify:{type}:{i}", "name": f"{type}-{i}"}
        if type == 'track': b.update({"artists": [{"name": "A"}], "album": {"name": "Al"}, "duration_ms": 1000})
        elif type == 'album': b.update({"artists": [{"name": "A"}]})
        elif type == 'playlist': b.update({"owner": {"display_name": "O"}})
        elif type == 'show': b.update({"publisher": "P"})
        elif type == 'episode': b.update({"show": {"name": "S"}, "duration_ms": 1000})
        return b
    def _search(self, q, type, limit):
        with self.lock: self.calls.append((type, limit))
        if self.mode == "error": raise RuntimeError("boom")
        if self.mode == "fail_one" and type == self.fail_type: raise RuntimeError("boom-" + type)
        key = {'track': 'tracks', 'album': 'albums', 'artist': 'artists',
               'playlist': 'playlists', 'episode': 'episodes', 'show': 'shows'}[type]
        if self.mode == "empty": return {key: {"items": []}}
        n = min(limit, self.avail)
        return {key: {"items": [self._item(type, i) for i in range(n)]}}


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

async def pump(pilot, n=25):
    for _ in range(n):
        try: await pilot.pause()
        except Exception: await asyncio.sleep(0.01)


async def test_combined_requests_exact_limits():
    fake = SFake(); app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("metallica")
        await poll(lambda: stbl(app) is not None, timeout=5.0)
        await pump(pilot, 5)
        calls = list(fake.calls)
        check("combined asks track=10, album=5, artist=3, playlist=2 in order",
              calls == [('track', 10), ('album', 5), ('artist', 3), ('playlist', 2)],
              f"calls={calls}")
        check("combined issues exactly 4 requests (not 6)", len(calls) == 4, f"calls={calls}")
        types = [t for t, _ in calls]
        check("no podcast/show request in combined search", 'show' not in types, f"types={types}")
        check("no episode request in combined search", 'episode' not in types, f"types={types}")


async def test_combined_result_caps():
    fake = SFake(); app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("metallica")
        t = await poll(lambda: stbl(app), timeout=5.0)
        await pump(pilot, 5)
        rows = getattr(t, "_model_rows", [])
        counts = {}
        for r in rows:
            counts[r.get("type")] = counts.get(r.get("type"), 0) + 1
        check("<=10 tracks shown", counts.get("track", 0) <= 10, f"counts={counts}")
        check("<=3 artists shown", counts.get("artist", 0) <= 3, f"counts={counts}")
        check("<=5 albums shown", counts.get("album", 0) <= 5, f"counts={counts}")
        check("<=2 playlists shown", counts.get("playlist", 0) <= 2, f"counts={counts}")
        check("zero podcasts shown", counts.get("podcast", 0) == 0, f"counts={counts}")
        check("zero episodes shown", counts.get("episode", 0) == 0, f"counts={counts}")
        check("total combined rows <= 20", len(rows) <= 20, f"n={len(rows)}")


async def test_prefix_keeps_larger_limit():
    fake = SFake(); app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("/TRK metallica")
        t = await poll(lambda: stbl(app), timeout=5.0)
        await pump(pilot, 5)
        check("prefix /TRK issues a single request", len(fake.calls) == 1, f"calls={fake.calls}")
        check("prefix /TRK asks for the larger limit (25, not 10)",
              fake.calls == [('track', 25)], f"calls={fake.calls}")
        rows = getattr(t, "_model_rows", [])
        check("prefix /TRK not squeezed to 10 (shows >10)", len(rows) > 10, f"n={len(rows)}")


async def test_prefix_album_single_request():
    fake = SFake(); app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("/ALB metallica")
        await poll(lambda: stbl(app), timeout=5.0)
        await pump(pilot, 5)
        check("prefix /ALB asks album=25 only", fake.calls == [('album', 25)], f"calls={fake.calls}")


async def test_partial_error_shows_good_types():
    fake = SFake(); fake.mode = "fail_one"; fake.fail_type = "artist"
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("metallica")
        t = await poll(lambda: stbl(app), timeout=5.0)
        await pump(pilot, 5)
        check("partial failure still renders a table", t is not None)
        check("partial failure is not shown as 'Search failed'",
              "Search failed" not in right_text(app), f"txt={right_text(app)!r}")
        types = {r.get("type") for r in getattr(t, "_model_rows", [])}
        check("good types present when one type fails",
              "track" in types and "album" in types and "artist" not in types, f"types={types}")


async def test_total_error():
    fake = SFake(); fake.mode = "error"; app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("metallica")
        await poll(lambda: "Search failed" in right_text(app), timeout=5.0)
        check("all four failing -> 'Search failed'", "Search failed" in right_text(app), f"txt={right_text(app)!r}")
        check("no table on total failure", stbl(app) is None)


async def test_no_results():
    fake = SFake(); fake.mode = "empty"; app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("metallica")
        await poll(lambda: "No results" in right_text(app), timeout=5.0)
        check("all empty -> 'No results'", "No results" in right_text(app), f"txt={right_text(app)!r}")
        check("no table on no-results", stbl(app) is None)


ALL = [test_combined_requests_exact_limits, test_combined_result_caps,
       test_prefix_keeps_larger_limit, test_prefix_album_single_request,
       test_partial_error_shows_good_types, test_total_error, test_no_results]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (search limits) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
