"""Phase 1: Ctrl+R (action_refresh) must be CONTEXTUAL.

With leftover text in the search box (e.g. "radiohead"), Ctrl+R must refresh the
active right-hand view, not re-run the search. Search only repeats when Search
is the active view or the input is genuinely focused. Welcome refreshes only the
playlists menu.

Routing is asserted by spying on the target methods (no network).
Run standalone:  python tests/test_refresh_routing.py
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

SPY_METHODS = ("_open_playlist_table", "_open_liked_table", "_open_recently_table",
               "_open_saved_albums", "_open_saved_podcasts", "_open_saved_episodes",
               "_open_saved_artists", "_open_album_table", "_open_artist_table",
               "_open_podcast_table", "_refresh_queue_table", "_refresh_devices_table",
               "_dispatch_search", "_load_playlists")

def install_spies(app):
    calls = []
    def make(name):
        def f(*a, **k): calls.append(name)
        return f
    for name in SPY_METHODS:
        setattr(app, name, make(name))
    return calls

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

async def poll(fn, timeout=2.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try:
            if fn(): return True
        except Exception: pass
        await asyncio.sleep(0.01)
    try: return bool(fn())
    except Exception: return False


async def _route(rv, *, focus_search=False, value="radiohead", devices_mounted=False):
    """Set up state, fire action_refresh, return the recorded calls list."""
    app = TApp(Fake())
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        try: app.search_input.value = value
        except Exception: pass
        if devices_mounted:
            app._open_devices_view()   # mounts #devices_table (real)
            await pilot.pause()
        app._right_view = rv
        if focus_search:
            app.set_focus(app.search_input)
        else:
            try: app.set_focus(None)
            except Exception: pass
        await pilot.pause()
        calls = install_spies(app)
        app.action_refresh()
        await poll(lambda: len(calls) > 0, timeout=2.0)
        await pilot.pause()
        return list(calls)


async def test_welcome_does_not_search():
    calls = await _route(None)
    check("Welcome + Ctrl+R refreshes playlists (not search)",
          "_load_playlists" in calls and "_dispatch_search" not in calls, f"calls={calls}")


async def test_view_context_wins_over_search_text():
    cases = [
        (("recent", "", 1, None), "_open_recently_table"),
        (("liked", "", 1, None), "_open_liked_table"),
        (("albums", "", 1, None), "_open_saved_albums"),
        (("podcasts", "", 1, None), "_open_saved_podcasts"),
        (("episodes", "", 1, None), "_open_saved_episodes"),
        (("artists", "", 1, None), "_open_saved_artists"),
        (("playlist", "pl1", 1, {"id": "pl1", "name": "P", "uri": "u"}), "_open_playlist_table"),
        (("album", "al1", 1, {"id": "al1", "name": "A"}), "_open_album_table"),
        (("artist", "ar1", 1, {"id": "ar1", "name": "R"}), "_open_artist_table"),
        (("podcast", "sh1", 1, {"id": "sh1", "name": "S"}), "_open_podcast_table"),
        (("queue", "", 1, None), "_refresh_queue_table"),
    ]
    for rv, want in cases:
        calls = await _route(rv)
        check(f"{rv[0]} + Ctrl+R refreshes the view (not search)",
              want in calls and "_dispatch_search" not in calls, f"calls={calls} want={want}")


async def test_search_view_reruns_search():
    calls = await _route(("search", "radiohead", 1, []))
    check("active Search view + Ctrl+R re-runs the search",
          "_dispatch_search" in calls, f"calls={calls}")


async def test_focused_input_reruns_search():
    calls = await _route(None, focus_search=True)
    check("search input focused + Ctrl+R re-runs the search",
          "_dispatch_search" in calls, f"calls={calls}")


async def test_devices_view_refreshes_devices():
    # devices carries no view token; a stale rv must NOT win over the open devices view.
    calls = await _route(("albums", "", 1, None), devices_mounted=True)
    check("devices view + Ctrl+R refreshes devices (beats stale rv, no search)",
          "_refresh_devices_table" in calls and "_open_saved_albums" not in calls
          and "_dispatch_search" not in calls, f"calls={calls}")


ALL = [test_welcome_does_not_search, test_view_context_wins_over_search_text,
       test_search_view_reruns_search, test_focused_input_reruns_search,
       test_devices_view_refreshes_devices]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (refresh routing) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
