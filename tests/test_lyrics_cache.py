"""Phase 2 tests: instant lyrics metadata on song change + safe lyrics cache.

Network is mocked; the cache directory is redirected to a temp dir. UI-driven
tests use Textual's pilot; pure cache tests call the helpers directly.

Run standalone:  python tests/test_lyrics_cache.py
"""
import os, sys, json, time, asyncio, threading, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spt_tui.app.lyrics_view as lv
from spt_tui.app import SptPy
from spt_tui import config

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeResp:
    def __init__(self, status, payload): self.status_code = status; self._p = payload
    def json(self): return self._p

import requests as _real_requests

class GatedReq:
    exceptions = _real_requests.exceptions
    def __init__(self, found=True):
        self.calls = []
        self.gate = threading.Event(); self.gate.set()   # open by default
        self.entered = threading.Event()
        self.found = found
    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(url)
        self.entered.set()
        self.gate.wait(10)
        if url.endswith("/get"):
            return FakeResp(200, {"syncedLyrics": "[00:01.00]la la"}) if self.found else FakeResp(404, {})
        return FakeResp(200, [{"duration": 100, "syncedLyrics": "[00:01.00]la la"}] if self.found else [])


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

def item(name, artist, tid, dur=200000, album="Alb"):
    return {"id": tid, "name": name, "artists": [{"name": artist}],
            "album": {"name": album}, "duration_ms": dur}

async def poll(fn, timeout=5.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.01)
    try: return fn()
    except Exception: return None

def box_text(app):
    return str(getattr(app.lyrics_box, "_Static__content", "") or "") if app.lyrics_box else ""

def pause_intervals(app):
    for a in ("_now_sync_interval","_now_tick_interval","_now_interval","_devices_interval","_queue_interval","_lyrics_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


# --------------------------------------------------------------------------- #
# Pure cache-helper tests
# --------------------------------------------------------------------------- #
def make_app():
    return SptPy.__new__(SptPy)

def with_tmp_cache(fn):
    tmp = tempfile.mkdtemp(prefix="spt_lyr_")
    old = config.CACHE_DIR
    config.CACHE_DIR = tmp
    try:
        fn(tmp)
    finally:
        config.CACHE_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_cache_found_roundtrip():
    def body(tmp):
        app = make_app(); app._lyrics_cache_data = {}
        key = app._lyrics_cache_key("t1", "Song", "Artist", 200000, "Alb")
        app._lyrics_cache_put(key, [(1000, "hi"), (2000, "bye")], "found")
        got = app._lyrics_cache_get("t1", "Song", "Artist", 200000, "Alb")
        check("found cache round-trips to tuples", got == [(1000, "hi"), (2000, "bye")], f"got={got}")
        # persisted to disk atomically as valid JSON
        p = os.path.join(tmp, "lyrics_cache.json")
        check("cache persisted as valid JSON", os.path.exists(p) and isinstance(json.load(open(p, encoding="utf-8")), dict))
        check("no leftover .tmp file after atomic write",
              not any(f.endswith(".tmp") for f in os.listdir(tmp)), f"dir={os.listdir(tmp)}")
    with_tmp_cache(body)


def test_cache_notfound_ttl():
    def body(tmp):
        app = make_app(); app._lyrics_cache_data = {}
        key = app._lyrics_cache_key(None, "X", "Y", 100000, "")
        app._lyrics_cache_put(key, [], "notfound")
        fresh = app._lyrics_cache_get(None, "X", "Y", 100000, "")
        check("fresh 'not found' returns [] (skip refetch)", fresh == [], f"got={fresh!r}")
        # expire it
        app._lyrics_cache_data[key]["ts"] = int(time.time()) - (app._LYRICS_NEG_TTL + 100)
        expired = app._lyrics_cache_get(None, "X", "Y", 100000, "")
        check("expired 'not found' returns None (refetch)", expired is None, f"got={expired!r}")
    with_tmp_cache(body)


def test_cache_error_never_stored():
    def body(tmp):
        app = make_app(); app._lyrics_cache_data = {}
        key = app._lyrics_cache_key("terr", "S", "A", 1000, "")
        app._lyrics_cache_put(key, [], "error")
        check("error status is never cached", app._lyrics_cache_get("terr", "S", "A", 1000, "") is None)
    with_tmp_cache(body)


def test_cache_key_duration_bucket():
    app = make_app()
    k_same_a = app._lyrics_cache_key(None, "Song", "Artist", 200000, "")
    k_same_b = app._lyrics_cache_key(None, "Song", "Artist", 202000, "")   # <5s diff
    k_diff = app._lyrics_cache_key(None, "Song", "Artist", 260000, "")     # >5s diff
    check("near-equal durations share a key", k_same_a == k_same_b, f"{k_same_a} vs {k_same_b}")
    check("clearly different durations get different keys", k_same_a != k_diff, f"{k_same_a} vs {k_diff}")
    check("track id takes precedence over the query key",
          app._lyrics_cache_key("abc", "S", "A", 1, "") == "id:abc")


def test_corrupt_json_recovers():
    def body(tmp):
        with open(os.path.join(tmp, "lyrics_cache.json"), "w", encoding="utf-8") as f:
            f.write("{ this is : not json ]")
        app = make_app(); app._lyrics_cache_data = None
        loaded = app._lyrics_cache_load()
        check("corrupt cache JSON recovers to empty dict (no crash)", loaded == {}, f"loaded={loaded!r}")
    with_tmp_cache(body)


# --------------------------------------------------------------------------- #
# UI-driven metadata / cache tests
# --------------------------------------------------------------------------- #
async def _open_lyrics(app, pilot, it):
    app._bar_last_track = it
    app._now_internal_track_id = it["id"]
    app._open_lyrics_view()
    await pilot.pause()
    if getattr(app, "_lyrics_interval", None):
        try: app._lyrics_interval.pause()
        except Exception: pass


def test_immediate_metadata_on_change():
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake)
    req = GatedReq(found=True); lv.requests = req
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                req.gate.clear()                                  # block the fetch
                await _open_lyrics(app, pilot, item("SongA", "ArtistA", "A"))
                await poll(lambda: "SongA" in box_text(app), timeout=3.0)
                check("A header + loading shown while A fetch is blocked",
                      "SongA" in box_text(app) and "loading" in box_text(app).lower(),
                      f"box={box_text(app)!r}")
                # switch to B while A is still loading
                app._bar_last_track = item("SongB", "ArtistB", "B")
                app._now_internal_track_id = "B"
                app._update_lyrics_highlight()
                await poll(lambda: "SongB" in box_text(app), timeout=3.0)
                check("B header appears immediately on change (before B fetch)", "SongB" in box_text(app),
                      f"box={box_text(app)!r}")
                check("A lyrics/metadata cleared on change", "SongA" not in box_text(app),
                      f"box={box_text(app)!r}")
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


def test_fast_switch_only_c_and_late_discarded():
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake)
    req = GatedReq(found=True); lv.requests = req
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                req.gate.clear()                                  # all fetches blocked
                await _open_lyrics(app, pilot, item("SongA", "ArtistA", "A"))
                app._bar_last_track = item("SongB", "ArtistB", "B"); app._now_internal_track_id = "B"
                app._update_lyrics_highlight()
                app._bar_last_track = item("SongC", "ArtistC", "C"); app._now_internal_track_id = "C"
                app._update_lyrics_highlight()
                await poll(lambda: "SongC" in box_text(app), timeout=3.0)
                req.gate.set()                                    # release all workers
                await poll(lambda: "la la" in box_text(app), timeout=4.0)
                txt = box_text(app)
                check("A->B->C fast: only C's lyrics visible (A/B discarded)",
                      "SongC" in txt and "la la" in txt and "SongA" not in txt and "SongB" not in txt,
                      f"box={txt!r}")
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


def test_notfound_keeps_metadata():
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake)
    req = GatedReq(found=False); lv.requests = req                # LRCLIB answers empty
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                await _open_lyrics(app, pilot, item("LonelySong", "Nobody", "Z"))
                await poll(lambda: "not found" in box_text(app).lower(), timeout=4.0)
                txt = box_text(app)
                check("'not found' keeps the correct song header", "LonelySong" in txt and "not found" in txt.lower(),
                      f"box={txt!r}")
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


def test_cache_hit_no_second_network():
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake)
    req = GatedReq(found=True); lv.requests = req
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                await _open_lyrics(app, pilot, item("CachedSong", "Art", "CACHE1"))
                await poll(lambda: "la la" in box_text(app), timeout=4.0)
                first_calls = len(req.calls)
                check("first load hits LRCLIB", first_calls >= 1, f"calls={first_calls}")
                # load the same song again: must come from cache, no new network
                app._start_lyrics_load()
                await poll(lambda: "la la" in box_text(app), timeout=4.0)
                check("same song again uses cache (no new LRCLIB call)",
                      len(req.calls) == first_calls, f"calls {first_calls}->{len(req.calls)}")
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


def test_ctrlr_forces_retry_over_negative_cache():
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake)
    req = GatedReq(found=False); lv.requests = req                # first: not found
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                await _open_lyrics(app, pilot, item("MaybeSong", "Art", "MS1"))
                await poll(lambda: "not found" in box_text(app).lower(), timeout=4.0)
                calls_after_first = len(req.calls)
                req.found = True                                  # lyrics now available
                app.action_refresh()                              # Ctrl+R forces retry
                await poll(lambda: "la la" in box_text(app), timeout=4.0)
                check("Ctrl+R forces a fresh fetch past the negative cache",
                      "la la" in box_text(app) and len(req.calls) > calls_after_first,
                      f"box={box_text(app)!r} calls {calls_after_first}->{len(req.calls)}")
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


def test_cache_bounded_by_count():
    def body(tmp):
        app = make_app(); app._lyrics_cache_data = {}
        app._LYRICS_CACHE_MAX = 10
        # Insert more than the cap; oldest (lowest ts) must be evicted.
        for i in range(25):
            app._lyrics_cache_data[f"k{i}"] = {"status": "found", "lines": [[1, "x"]], "ts": i}
        app._lyrics_cache_save()
        c = app._lyrics_cache_data
        check("cache never exceeds the entry cap", len(c) <= 10, f"n={len(c)}")
        check("oldest entries evicted, newest kept", "k24" in c and "k0" not in c, f"keys={sorted(c)[:5]}")
        on_disk = json.load(open(os.path.join(tmp, "lyrics_cache.json"), encoding="utf-8"))
        check("on-disk cache also bounded", len(on_disk) <= 10, f"n={len(on_disk)}")
    with_tmp_cache(body)


def test_cache_bounded_by_bytes():
    def body(tmp):
        app = make_app(); app._lyrics_cache_data = {}
        app._LYRICS_CACHE_MAX = 100000            # let the byte cap be the binding limit
        app._LYRICS_CACHE_MAX_BYTES = 50 * 1024   # 50 KiB
        big = "L" * 4000                            # ~4 KB of "lyrics" per entry
        for i in range(100):
            app._lyrics_cache_data[f"k{i}"] = {"status": "found", "lines": [[1, big]], "ts": i}
        app._lyrics_cache_save()
        p = os.path.join(tmp, "lyrics_cache.json")
        size = os.path.getsize(p)
        check("cache file stays within the byte cap (with slack)", size <= 60 * 1024, f"size={size}")
        c = app._lyrics_cache_data
        check("byte cap dropped the oldest entries", "k99" in c and "k0" not in c, f"n={len(c)}")
        check("cache not emptied entirely", len(c) >= 1, f"n={len(c)}")
    with_tmp_cache(body)


PURE = [test_cache_found_roundtrip, test_cache_notfound_ttl, test_cache_error_never_stored,
        test_cache_key_duration_bucket, test_corrupt_json_recovers,
        test_cache_bounded_by_count, test_cache_bounded_by_bytes]
UI = [test_immediate_metadata_on_change, test_fast_switch_only_c_and_late_discarded,
      test_notfound_keeps_metadata, test_cache_hit_no_second_network,
      test_ctrlr_forces_retry_over_negative_cache]

def main():
    orig_req = lv.requests
    try:
        for fn in PURE:
            try: fn()
            except Exception: check(fn.__name__ + " (crashed)", False, traceback.format_exc())
        for fn in UI:
            try: fn()
            except Exception: check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    finally:
        lv.requests = orig_req
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (lyrics cache/metadata) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
