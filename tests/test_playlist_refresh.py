"""Phase 2: the playlists list must never be lost to a transient refresh.

A failed, malformed, partial or unexpectedly-empty response must not wipe a
valid list (memory or disk). Only a valid, complete result — or a confirmed
empty library (total == 0) — may replace it. An older worker can't overwrite a
newer one, and the disk cache is written atomically.

Run standalone:  python tests/test_playlist_refresh.py
"""
import os, sys, asyncio, threading, json, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def __init__(self):
        self.mode = "ok"          # ok | empty_confirmed | empty_suspicious | malformed | error
        self.n = 3
        self.gate = None          # optional threading.Event to block the first page
        self.entered = threading.Event()
        self.token = True
    def has_cached_token(self): return self.token
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def user_playlists(self, limit=50, offset=0):
        self.entered.set()
        g = self.gate
        if g is not None: g.wait(10)
        if self.mode == "error": raise RuntimeError("network down")
        if self.mode == "malformed": return {}                                  # no 'items'
        if self.mode == "empty_confirmed": return {"items": [], "next": None, "total": 0}
        if self.mode == "empty_suspicious": return {"items": [], "next": None, "total": 5}
        items = [{"name": f"PL {i}", "id": f"id{i}", "uri": f"spotify:playlist:id{i}"} for i in range(self.n)]
        return {"items": items, "next": None, "total": self.n}


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

async def poll(fn, timeout=3.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try:
            if fn(): return True
        except Exception: pass
        await asyncio.sleep(0.01)
    try: return bool(fn())
    except Exception: return False

async def run_load(app, pilot):
    threading.Thread(target=lambda: app._load_playlists(force=True), daemon=True).start()
    for _ in range(50):
        await pilot.pause(); await asyncio.sleep(0.01)

def _tmpfile():
    fd, path = tempfile.mkstemp(suffix="_pl.json"); os.close(fd)
    return path

def seed(app, names):
    app.playlists_cache = [{"name": n, "id": n, "uri": "u" + n} for n in names]
    app._save_playlists_disk(app.playlists_cache)

def disk(app):
    p = app._playlists_cache_path()
    if not os.path.exists(p): return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def names(cache):
    return [p.get("name") for p in (cache or [])]


async def _make(pilot_size=(120, 20)):
    app = TApp(Fake())
    path = _tmpfile()
    app._playlists_cache_path = lambda: path
    return app, path


async def test_network_failure_keeps_list():
    app, path = await _make()
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        seed(app, ["A", "B", "C"]); app.spotify.mode = "error"
        await run_load(app, pilot)
        check("network failure keeps the in-memory list", names(app.playlists_cache) == ["A", "B", "C"],
              f"cache={names(app.playlists_cache)}")
        check("network failure keeps the on-disk cache", names(disk(app)) == ["A", "B", "C"],
              f"disk={names(disk(app))}")
    os.remove(path)


async def test_unexpected_empty_keeps_list():
    app, path = await _make()
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        seed(app, ["A", "B", "C"]); app.spotify.mode = "empty_suspicious"
        await run_load(app, pilot)
        check("unexpected empty (total>0) keeps the list", names(app.playlists_cache) == ["A", "B", "C"],
              f"cache={names(app.playlists_cache)}")
        check("unexpected empty keeps disk cache", names(disk(app)) == ["A", "B", "C"], f"disk={names(disk(app))}")
    os.remove(path)


async def test_malformed_keeps_list():
    app, path = await _make()
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        seed(app, ["A", "B", "C"]); app.spotify.mode = "malformed"
        await run_load(app, pilot)
        check("malformed response keeps the list", names(app.playlists_cache) == ["A", "B", "C"],
              f"cache={names(app.playlists_cache)}")
    os.remove(path)


async def test_confirmed_empty_replaces():
    app, path = await _make()
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        seed(app, ["A", "B", "C"]); app.spotify.mode = "empty_confirmed"
        await run_load(app, pilot)
        check("confirmed empty (total==0) clears the list", app.playlists_cache == [],
              f"cache={names(app.playlists_cache)}")
        check("confirmed empty is persisted", disk(app) == [], f"disk={disk(app)}")
    os.remove(path)


async def test_valid_result_replaces():
    app, path = await _make()
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        seed(app, ["old"]); app.spotify.mode = "ok"; app.spotify.n = 3
        await run_load(app, pilot)
        check("valid non-empty result replaces the list", names(app.playlists_cache) == ["PL 0", "PL 1", "PL 2"],
              f"cache={names(app.playlists_cache)}")


async def test_atomic_write_no_tmp_left():
    app, path = await _make()
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app.spotify.mode = "ok"; app.spotify.n = 2
        await run_load(app, pilot)
        check("no .tmp file left after write (atomic replace)", not os.path.exists(path + ".tmp"))
        check("disk cache is valid JSON list", isinstance(disk(app), list) and len(disk(app)) == 2,
              f"disk={disk(app)}")
    os.remove(path)


async def test_old_worker_does_not_overwrite_new():
    app, path = await _make()
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        seed(app, ["old"])
        gA = threading.Event(); app.spotify.gate = gA; app.spotify.n = 2; app.spotify.entered.clear()
        threading.Thread(target=lambda: app._load_playlists(force=True), daemon=True).start()  # A (blocked)
        await poll(lambda: app.spotify.entered.is_set(), timeout=3.0)   # A has bumped gen and is blocking
        app.spotify.gate = None; app.spotify.n = 5
        await run_load(app, pilot)                                       # B supersedes with 5
        check("newer load (B) wins", len(app.playlists_cache) == 5, f"cache={len(app.playlists_cache)}")
        gA.set()                                                        # release A; it must discard its result
        for _ in range(40):
            await pilot.pause(); await asyncio.sleep(0.01)
        check("stale worker A does not overwrite B", len(app.playlists_cache) == 5,
              f"cache={len(app.playlists_cache)}")
    os.remove(path)


ALL = [test_network_failure_keeps_list, test_unexpected_empty_keeps_list, test_malformed_keeps_list,
       test_confirmed_empty_replaces, test_valid_result_replaces, test_atomic_write_no_tmp_left,
       test_old_worker_does_not_overwrite_new]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (playlist refresh) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
