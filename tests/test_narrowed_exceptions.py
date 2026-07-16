"""Guard tests for the narrowed exception handlers (commit: Narrow expected
exceptions). These assert the *expected* error is still handled gracefully after
tightening `except Exception` to the concrete type — behaviour must be identical
for the widget-absent / bad-number cases.

Run standalone:  python tests/test_narrowed_exceptions.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from spt_tui import config

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
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x

class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval","_now_tick_interval","_now_interval","_devices_interval","_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


def test_seek_step_ms_bad_value_falls_back():
    app = TApp(Fake())
    saved = config.LOCAL_CFG
    try:
        config.LOCAL_CFG = {"seek_seconds_track": "not-a-number", "seek_seconds_episode": None}
        # int("not-a-number") raises ValueError -> must fall back to defaults.
        ms_track = app._seek_step_ms("track")
        ms_ep = app._seek_step_ms("episode")
        check("seek_step_ms falls back on bad numeric config",
              ms_track == 5000 and ms_ep == 15000, f"track={ms_track} ep={ms_ep}")
    finally:
        config.LOCAL_CFG = saved


async def _mounted(fn):
    app = TApp(Fake())
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)
        fn(app)

def test_refresh_playing_highlight_no_table():
    def body(app):
        # No #tracks_table mounted -> NoMatches must be swallowed, no crash.
        ok = True
        try:
            app._refresh_playing_highlight()
        except Exception:
            ok = False; traceback.print_exc()
        check("_refresh_playing_highlight is a no-op when table absent", ok)
    asyncio.run(_mounted(body))

def test_save_seek_setting_rejects_non_numeric():
    def body(app):
        res = app._save_seek_setting("abc", "seek_seconds_track", "track jump seconds")
        check("_save_seek_setting returns False on non-numeric input", res is False, f"res={res}")
    asyncio.run(_mounted(body))


ALL = [test_seek_step_ms_bad_value_falls_back,
       test_refresh_playing_highlight_no_table,
       test_save_seek_setting_rejects_non_numeric]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (narrowed exceptions) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
