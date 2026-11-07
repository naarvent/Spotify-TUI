"""Esc while a search is running: the Search bar must not stay on "searching…".

Bug: Esc -> _clear_right() reset _searching_token, so the results were discarded, but
_search_rendered_token never got set and _last_search_worker still equalled the token:
the spinner ticked forever. Now Esc cancels the search first (one Esc = one action, the
second goes to the menu), and the spinner also stops by itself if anything else clears
the panel.

Run standalone:  python tests/test_search_cancel.py
"""
import os, sys, asyncio, tempfile, threading, time

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_scancel_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


GATE = threading.Event()

class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def current_user(self): return {"id": "me"}
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)
    def search(self, q, type=None, limit=25, **k):
        GATE.wait(8)                       # a search that is still "in the network"
        return {"tracks": {"items": [{"id": "t1", "uri": "spotify:track:t1", "name": "Late", "type": "track",
                                      "duration_ms": 1000, "artists": [{"name": "A"}], "album": {"name": "L"}}]}}

class TApp(SptPy):
    def __init__(self): super().__init__(); self.spotify = Fake()


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval", "_devices_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def subtitle(app):
    return str(app.query_one("#search_wrap").border_subtitle or "")

def busy(app):
    return app.query_one("#search_wrap").has_class("-searching")


async def start_search(app, pilot):
    GATE.clear()
    app.search_input.focus(); await pilot.pause()
    app.search_input.value = "/TRK slow"
    await pilot.press("enter"); await pilot.pause(); await pilot.pause()


async def main():
    config.LOCAL_CFG = {}
    # 1. Esc from the Search field while searching.
    app = TApp()
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await start_search(app, pilot)
        check("searching: the bar is busy", busy(app) and "searching" in subtitle(app), subtitle(app))
        await pilot.press("escape"); await pilot.pause(); await pilot.pause()
        check("first Esc: spinner class gone", not busy(app))
        check("first Esc: no 'searching…' on the bar", "searching" not in subtitle(app), subtitle(app))
        check("first Esc: spinner timer stopped", getattr(app, "_spin_timer", None) is None)
        check("first Esc cancels only the search (panel not reset to the welcome)",
              not getattr(app, "_welcome_on", False))
        check("panel says it was cancelled", "cancelled" in str(app.right_panel.render()).lower())
        await asyncio.sleep(0.4); await pilot.pause()
        check("still at rest after more ticks", "searching" not in subtitle(app) and not busy(app))
        GATE.set()                                    # the late results arrive...
        await asyncio.sleep(0.6); await pilot.pause(); await pilot.pause()
        check("late results are discarded (no table, bar still at rest)",
              len(app.query("#search_table")) == 0 and "searching" not in subtitle(app) and not busy(app)
              and "results" not in subtitle(app))
        await pilot.press("escape"); await pilot.pause(); await pilot.pause()
        check("second Esc goes to the menu", getattr(app, "_right_view", None) is None)

    # 2. Esc with the focus outside Search (content / list), same result.
    app = TApp()
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await start_search(app, pilot)
        app._focus_section_by_idx(app.section_order.index("lib")); await pilot.pause()
        await pilot.press("escape"); await pilot.pause(); await pilot.pause()
        check("Esc from the sidebar also cancels", not busy(app) and "searching" not in subtitle(app))
        GATE.set()

    # 3. Safety net: anything else clearing the panel stops the spinner (no Esc involved).
    app = TApp()
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await start_search(app, pilot)
        app._clear_right()
        await asyncio.sleep(0.4); await pilot.pause()
        check("panel cleared by another view: spinner stops by itself", not busy(app) and getattr(app, "_spin_timer", None) is None)
        GATE.set()

    # 4. A search that completes normally is untouched; Esc afterwards goes to the menu directly.
    app = TApp()
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await start_search(app, pilot)
        GATE.set()
        end = time.time() + 5
        while time.time() < end and len(app.query("#search_table")) == 0:
            await pilot.pause(); await asyncio.sleep(0.03)
        check("normal search: results table shown", len(app.query("#search_table")) > 0)
        await asyncio.sleep(0.4); await pilot.pause()
        check("normal search: ✓ results then no spinner", not busy(app))
        check("nothing in flight afterwards", not app._search_in_flight())
        await pilot.press("escape"); await pilot.pause(); await pilot.pause()
        check("Esc after finished search goes straight to the menu", getattr(app, "_right_view", None) is None)

if __name__ == "__main__":
    asyncio.run(main())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
