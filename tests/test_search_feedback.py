"""Phase 4 tests: activating search gives a short, self-terminating outline
pulse + text hint, driven by Textual timers only (no threads), that restarts
cleanly and never stacks timers.

Run standalone:  python tests/test_search_feedback.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

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

async def poll(fn, timeout=4.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.01)
    try: return fn()
    except Exception: return None

def pulsing(app):
    try:
        return app.query_one("#search_wrap").has_class("-search-pulse")
    except Exception:
        return False

def title_text(app):
    return str(getattr(app.search_title, "_Static__content", "") or "")


def test_focus_starts_pulse():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_focus_search()
            await pilot.pause()
            check("search focus starts the outline pulse", pulsing(app))
            check("a pulse timer is created", getattr(app, "_search_pulse_timer", None) is not None)
            check("colour-independent text hint shown ('Search •')", "Search" in title_text(app) and "•" in title_text(app),
                  f"title={title_text(app)!r}")
            check("search input receives focus", app.focused is app.search_input, f"focused={app.focused}")
    asyncio.run(run())


def test_pulse_self_terminates():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_focus_search()
            await pilot.pause()
            # drive the ticks deterministically instead of waiting ~1.3s
            for _ in range(app._SEARCH_PULSE_STEPS):
                app._search_pulse_tick()
            check("pulse stops after its fixed number of steps", getattr(app, "_search_pulse_timer", None) is None)
            check("outline class removed when the pulse ends", not pulsing(app))
            check("title hint restored after the pulse", "•" not in title_text(app), f"title={title_text(app)!r}")
    asyncio.run(run())


def test_repeated_activation_no_stacking():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_focus_search()
            await pilot.pause()
            t1 = app._search_pulse_timer
            app._search_pulse_count = 3          # simulate mid-pulse
            app.action_focus_search()            # re-activate
            await pilot.pause()
            t2 = app._search_pulse_timer
            check("re-activation replaces the timer (no accumulation)", t1 is not t2 and t2 is not None,
                  f"t1={t1} t2={t2}")
            check("re-activation restarts the pulse from the beginning", app._search_pulse_count == 0,
                  f"count={app._search_pulse_count}")
    asyncio.run(run())


def test_leaving_cancels_pulse():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_focus_search()
            await pilot.pause()
            check("pulse active before leaving", pulsing(app))
            app.action_escape_to_menu()
            await pilot.pause()
            check("escape cancels the pulse timer", getattr(app, "_search_pulse_timer", None) is None)
            check("escape removes the pulse outline class", not pulsing(app))
    asyncio.run(run())


def test_slash_key_routes_to_focus_search():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await pilot.press("slash")            # "/" (Shift+7 on Windows produces the same key)
            started = await poll(lambda: getattr(app, "_search_pulse_timer", None) is not None or pulsing(app), timeout=2.0)
            check("pressing '/' activates search feedback", bool(started),
                  f"timer={getattr(app,'_search_pulse_timer',None)} pulsing={pulsing(app)}")
    asyncio.run(run())


def test_teardown_safe():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_focus_search()
            await pilot.pause()
        # after the app exits, cancelling again must be a safe no-op
        try:
            app._stop_search_pulse()
            check("stop pulse is safe after teardown (idempotent)", True)
        except Exception:
            check("stop pulse is safe after teardown (idempotent)", False, traceback.format_exc())
    asyncio.run(run())


ALL = [test_focus_starts_pulse, test_pulse_self_terminates, test_repeated_activation_no_stacking,
       test_leaving_cancels_pulse, test_slash_key_routes_to_focus_search, test_teardown_safe]

def main():
    for fn in ALL:
        try: fn()
        except Exception: check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (search feedback) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
