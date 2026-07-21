"""Tab / Shift+Tab cycle the top-level focus stops, never diving into content.

The stops, in visual order, are Search, Help, Library, Playlists and the open
main-content view. Tab moves between them without entering a section's list;
when no content view is open, the content stop is skipped. Textual's own
Tab-moves-focus-anywhere default is overridden (prevent_default in on_key), and
the search grid's panel cycling still runs (its widget consumes Tab first).

Run standalone:  python tests/test_tab_focus.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from textual.widgets import DataTable

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
    def saved_tracks(self, limit=50, offset=0):
        if offset: return {"items": [], "next": None}
        return {"items": [{"track": {"id": f"l{i}", "uri": f"spotify:track:l{i}", "type": "track",
                                     "name": f"s{i}", "duration_ms": 1000, "artists": [{"name": "a"}],
                                     "album": {"name": "al"}}} for i in range(3)], "next": None}
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)


class TApp(SptPy):
    def __init__(self): super().__init__(); self.spotify = Fake()


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def content_table(app):
    for _ in range(200):
        for w in app.query(DataTable):
            if getattr(w, "id", "") == "tracks_table":
                return w
        await asyncio.sleep(0.02)
    return None


async def test_tab_cycles_sections_skipping_absent_content():
    app = TApp()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._focus_section_by_idx(0)   # search
        await pilot.pause()
        seq = []
        for _ in range(5):
            await pilot.press("tab"); await pilot.pause()
            seq.append(app._current_tab_stop())
        check("forward cycle skips content when nothing is open",
              seq == ["help", "lib", "pl", "search", "help"], f"{seq}")


async def test_shift_tab_reverses():
    app = TApp()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._focus_section_by_idx(0)   # search
        await pilot.pause()
        seq = []
        for _ in range(4):
            await pilot.press("shift+tab"); await pilot.pause()
            seq.append(app._current_tab_stop())
        # search -> (back) pl -> lib -> help -> search  (content skipped)
        check("reverse cycle", seq == ["pl", "lib", "help", "search"], f"{seq}")


async def test_tab_does_not_enter_section_content():
    app = TApp()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._focus_section_by_idx(0)
        await pilot.pause()
        await pilot.press("tab"); await pilot.pause()   # help
        await pilot.press("tab"); await pilot.pause()   # lib
        check("landed on Library at section level", app._current_tab_stop() == "lib"
              and app.level == app.LVL_SECTIONS, f"stop={app._current_tab_stop()} lvl={app.level}")
        # "Not diving in" means the list itself is not the focused widget — focus
        # sits on the section proxy (#left_col), so Up/Down still pick sections.
        check("did not dive into the library list (list not focused)",
              app.focused is not app.lib_list, f"focused={type(app.focused).__name__}")


async def test_open_content_is_a_stop_both_ways():
    app = TApp()
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_liked_table()
        t = await content_table(app)
        check("content view opened", t is not None)
        app._focus_right_view()
        await pilot.pause()
        check("focus is on content", app._current_tab_stop() == "content", f"{app._current_tab_stop()}")
        await pilot.press("tab"); await pilot.pause()
        check("Tab from content wraps to Search", app._current_tab_stop() == "search",
              f"{app._current_tab_stop()}")
        # go pl then Tab should reach content now that it exists
        app._focus_section_by_idx(app.section_order.index("pl"))
        await pilot.pause()
        await pilot.press("tab"); await pilot.pause()
        check("Tab from Playlists reaches the open content", app._current_tab_stop() == "content",
              f"{app._current_tab_stop()}")


ALL = [test_tab_cycles_sections_skipping_absent_content, test_shift_tab_reverses,
       test_tab_does_not_enter_section_content, test_open_content_is_a_stop_both_ways]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (tab focus) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
