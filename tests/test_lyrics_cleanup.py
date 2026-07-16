"""Phase 3 tests: leaving the lyrics view for any other view runs a single
idempotent teardown (_leave_lyrics_mode): the 0.4s tick stops, the lyrics-mode
CSS is removed, the widget is dropped and any in-flight load is invalidated so a
late worker never paints.

Run standalone:  python tests/test_lyrics_cleanup.py
"""
import os, sys, asyncio, threading, tempfile, shutil, traceback

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
    def __init__(self):
        self.gate = threading.Event(); self.gate.set()
        self.entered = threading.Event()
        self.calls = 0
    def get(self, url, params=None, timeout=None, headers=None):
        self.calls += 1; self.entered.set(); self.gate.wait(10)
        return FakeResp(404, {}) if url.endswith("/get") else FakeResp(200, [])


class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def saved_tracks(self, limit=50, offset=0): return {"items": [], "next": None}
    def current_user_saved_albums(self, limit=50, offset=0): return {"items": [], "next": None}

class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval","_now_tick_interval","_now_interval","_devices_interval","_queue_interval","_lyrics_interval"):
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

class FakeInput:
    def __init__(self, inp, value): self.input = inp; self.value = value

def is_clean(app):
    return (app._lyrics_on is False
            and getattr(app, "_lyrics_interval", None) is None
            and app.lyrics_box is None
            and not app.right_panel.has_class("lyrics-mode"))

async def open_lyrics(app, pilot):
    app._bar_last_track = {"id": "A", "name": "SongA", "artists": [{"name": "Art"}],
                           "album": {"name": "Al"}, "duration_ms": 200000}
    app._now_internal_track_id = "A"
    app._open_lyrics_view()
    await pilot.pause()
    # sanity: lyrics mode really on
    return app._lyrics_on and app.lyrics_box is not None and app.right_panel.has_class("lyrics-mode")


def run_exit_case(label, trigger):
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake)
    req = GatedReq(); lv.requests = req
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                on = await open_lyrics(app, pilot)
                check(f"[{label}] lyrics view opened", on)
                trigger(app)
                await pilot.pause()
                check(f"[{label}] leaving lyrics fully cleaned up", is_clean(app),
                      f"on={app._lyrics_on} iv={getattr(app,'_lyrics_interval',None)} box={app.lyrics_box} "
                      f"css={app.right_panel.has_class('lyrics-mode')}")
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


def test_exit_to_search():
    run_exit_case("search", lambda app: app.on_input_submitted(FakeInput(app.search_input, "query")))
def test_exit_to_playlist():
    run_exit_case("playlist", lambda app: app._open_playlist_table({"id": "p1", "name": "P", "uri": "spotify:playlist:p1"}))
def test_exit_to_library():
    run_exit_case("library", lambda app: app._open_library_item("Liked Songs"))
def test_exit_to_queue():
    run_exit_case("queue", lambda app: app.action_open_queue())
def test_exit_to_devices():
    run_exit_case("devices", lambda app: app.action_manage_devices())
def test_exit_to_help():
    run_exit_case("help", lambda app: app.action_help())
def test_exit_to_menu_escape():
    run_exit_case("escape", lambda app: app.action_escape_to_menu())


def test_idempotent_and_double_call():
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake); req = GatedReq(); lv.requests = req
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                await open_lyrics(app, pilot)
                app._leave_lyrics_mode()
                app._leave_lyrics_mode()          # second call must be a safe no-op
                await pilot.pause()
                check("leave helper is idempotent (twice, no error)", is_clean(app))
                # and calling it when lyrics were never open is fine
                app._leave_lyrics_mode()
                check("leave helper safe when lyrics never open", is_clean(app))
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


def test_worker_does_not_paint_after_leave():
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake); req = GatedReq(); lv.requests = req
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                req.gate.clear()                  # block the fetch so it's still in flight
                await open_lyrics(app, pilot)
                await poll(lambda: req.entered.is_set(), timeout=3.0)
                app._open_library_item("Liked Songs")   # leave while loading
                await pilot.pause()
                gen_before = app._lyrics_gen
                req.gate.set()                    # let the stale worker finish
                for _ in range(20): await pilot.pause()
                check("stale lyrics worker did not re-enable lyrics mode", app._lyrics_on is False,
                      f"on={app._lyrics_on}")
                check("stale lyrics worker did not touch the dropped box (gen unchanged)",
                      app._lyrics_gen == gen_before and app.lyrics_box is None,
                      f"gen {gen_before}->{app._lyrics_gen} box={app.lyrics_box}")
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


def test_toggle_still_works():
    tmp = tempfile.mkdtemp(prefix="spt_lyr_"); old = config.CACHE_DIR; config.CACHE_DIR = tmp
    fake = Fake(); app = TApp(fake); req = GatedReq(); lv.requests = req
    try:
        async def run():
            async with app.run_test(size=(90, 20)) as pilot:
                await pilot.pause(); pause_intervals(app)
                app._bar_last_track = {"id": "A", "name": "S", "artists": [{"name": "Ar"}],
                                       "album": {"name": "Al"}, "duration_ms": 1000}
                app._now_internal_track_id = "A"
                app.action_toggle_lyrics()        # on
                await pilot.pause()
                check("L toggles lyrics on", app._lyrics_on is True and app.lyrics_box is not None)
                app.action_toggle_lyrics()        # off
                await pilot.pause()
                check("L toggles lyrics off (clean)", is_clean(app))
        asyncio.run(run())
    finally:
        config.CACHE_DIR = old; shutil.rmtree(tmp, ignore_errors=True)


ALL = [test_exit_to_search, test_exit_to_playlist, test_exit_to_library, test_exit_to_queue,
       test_exit_to_devices, test_exit_to_help, test_exit_to_menu_escape,
       test_idempotent_and_double_call, test_worker_does_not_paint_after_leave, test_toggle_still_works]

def main():
    orig = lv.requests
    try:
        for fn in ALL:
            try: fn()
            except Exception: check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    finally:
        lv.requests = orig
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (lyrics cleanup) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
