"""Phase 5 tests: the Help view scrolls with the keyboard (Up/Down/PageUp/
PageDown/Home/End) via a focusable scroll container whose bindings win over the
app's global cursor bindings; Escape still closes Help and restores navigation.

Run standalone:  python tests/test_help_scroll.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from textual.css.query import NoMatches

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

def help_scroll(app):
    try:
        return app.query_one("#help_scroll")
    except NoMatches:
        return None

async def press_settle(pilot, hs, key):
    """Press a key then wait for the scroll animation to settle (bounded)."""
    await pilot.press(key)
    prev = object()
    for _ in range(80):
        await asyncio.sleep(0.02)
        cur = hs.scroll_y
        if cur == prev:
            break
        prev = cur
    return hs.scroll_y


def test_help_keyboard_scroll():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(80, 14)) as pilot:   # small -> content overflows
            await pilot.pause(); pause_intervals(app)
            app.action_help()
            await pilot.pause()
            hs = help_scroll(app)
            check("help opens in a focusable scroll container", hs is not None and app.focused is hs)
            check("help content overflows (scrollable)", hs is not None and hs.max_scroll_y > 0,
                  f"max={getattr(hs,'max_scroll_y',None)}")

            y_down = await press_settle(pilot, hs, "down")
            check("Down increases scroll_y", y_down > 0, f"y={y_down}")
            y_pg = await press_settle(pilot, hs, "pagedown")
            check("PageDown scrolls further than a single line", y_pg > y_down, f"down={y_down} page={y_pg}")
            y_up = await press_settle(pilot, hs, "up")
            check("Up decreases scroll_y", y_up < y_pg, f"page={y_pg} up={y_up}")
            y_end = await press_settle(pilot, hs, "end")
            check("End jumps to the bottom", y_end == hs.max_scroll_y, f"y={y_end} max={hs.max_scroll_y}")
            y_home = await press_settle(pilot, hs, "home")
            check("Home returns to the top", y_home == 0, f"y={y_home}")
    asyncio.run(run())


def test_escape_closes_help_and_restores_nav():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(80, 14)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_help()
            await pilot.pause()
            check("help open before escape", help_scroll(app) is not None)
            await pilot.press("escape")
            await pilot.pause()
            check("escape closes help", help_scroll(app) is None)
            check("_help_on cleared", getattr(app, "_help_on", None) is False)
            check("focus no longer on the help scroller", app.focused is not None and getattr(app.focused, "id", "") != "help_scroll",
                  f"focused={app.focused}")
            # navigation still works: opening help again starts at the top
            app.action_help()
            await pilot.pause()
            hs = help_scroll(app)
            check("reopening help starts scrolled at the top", hs is not None and hs.scroll_y == 0,
                  f"y={getattr(hs,'scroll_y',None)}")
    asyncio.run(run())


def test_help_scroll_wins_over_global_cursor_bindings():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(80, 14)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_help()
            await pilot.pause()
            hs = help_scroll(app)
            before_level = app.level
            y = await press_settle(pilot, hs, "down")
            # If the app's global 'down' (cursor_down) had handled the key instead,
            # the help scroller would not have moved.
            check("Down scrolls help rather than triggering global cursor nav", y > 0, f"y={y}")
            check("still in the help view (nav not hijacked)", app.level == before_level, f"level={app.level}")
    asyncio.run(run())


ALL = [test_help_keyboard_scroll, test_escape_closes_help_and_restores_nav,
       test_help_scroll_wins_over_global_cursor_bindings]

def main():
    for fn in ALL:
        try: fn()
        except Exception: check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (help scroll) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
