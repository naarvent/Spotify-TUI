"""One physical Esc = one action (real key presses through Textual's pilot).

Esc used to run action_escape_to_menu twice (from on_key AND the "escape"
binding), so every "Esc cancels" also left the view. For each flow: the
first Esc cancels and the view stays, the next Esc goes to the menu.

Run standalone:  python tests/test_esc_flows.py
"""
import os, sys, asyncio, tempfile, time

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_esc_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import DataTable
from spt_tui import config
from spt_tui.app import SptPy
from spt_tui.app import navigation as nav

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


ROWS = [{"_pos": i, "id": f"t{i}", "uri": f"spotify:track:t{i}", "title": f"Song {i}", "artist": "A",
         "album": "L", "dur": "1:00"} for i in range(4)]


class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0):
        return {"items": [{"id": "pl1", "name": "Mine", "uri": "spotify:playlist:pl1", "type": "playlist",
                           "owner": {"id": "me", "display_name": "me"}, "tracks": {"total": 4}}], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)
    def current_user(self): return {"id": "me"}


class TApp(SptPy):
    def __init__(self): super().__init__(); self.spotify = Fake()


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval", "_devices_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def poll(pilot, fn, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        await pilot.pause()
        try:
            if fn(): return True
        except Exception:
            pass
        await asyncio.sleep(0.03)
    return False


def at_menu(app):
    return getattr(app, "_right_view", None) is None


async def open_playlist_table(app, pilot):
    app._new_view_token("playlist", "pl1")
    t = app._render_tracks_table("[b]Mine[/b]", [dict(r) for r in ROWS], [False] * 4,
                                 context_uri="spotify:playlist:pl1", context_uris=[r["uri"] for r in ROWS])
    t._pl_base_title = "[b]Mine[/b]"
    await pilot.pause(); await pilot.pause()
    t.focus(); t.move_cursor(row=1); await pilot.pause()
    return t


async def flow(name, setup, armed):
    config.LOCAL_CFG = {}
    app = TApp()
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await setup(app, pilot)
        check(f"{name}: armed", armed(app))
        await pilot.press("escape"); await pilot.pause(); await pilot.pause()
        check(f"{name}: first Esc cancels", not armed(app))
        check(f"{name}: …and stays in the view", not at_menu(app), str(getattr(app, "_right_view", None)))
        await pilot.press("escape"); await pilot.pause(); await pilot.pause()
        check(f"{name}: second Esc goes to the menu", at_menu(app), str(getattr(app, "_right_view", None)))


async def setup_remove(app, pilot):
    await open_playlist_table(app, pilot)
    await pilot.press("ctrl+d"); await pilot.pause()


async def setup_multi(app, pilot):
    await open_playlist_table(app, pilot)
    await pilot.press("ctrl+l"); await pilot.pause()


async def setup_filter(app, pilot):
    await open_playlist_table(app, pilot)
    await pilot.press("slash"); await pilot.pause()
    await pilot.press("s", "o", "n", "g", "space", "2")
    await poll(pilot, lambda: app.query_one("#tracks_table").row_count == 1)
    await pilot.press("down"); await pilot.pause()


async def setup_bulk(app, pilot):
    await open_playlist_table(app, pilot)
    app._confirm_bulk_add([r["uri"] for r in ROWS], "Big album")
    await pilot.pause()


async def setup_delete_pl(app, pilot):
    await poll(pilot, lambda: len(app.pl_list.children) > 0)
    app.pl_list.focus(); app.pl_list.index = 0; await pilot.pause()
    await pilot.press("ctrl+d"); await pilot.pause(); await pilot.pause()
    # The name prompt has focus (an Input): Esc must still cancel, not type.


async def setup_import(app, pilot):
    await open_playlist_table(app, pilot)
    state = {"running": True}
    def cancel():
        if state["running"]:
            state["running"] = False
            return True
        return False
    app._cancel_spotify_import = cancel
    app._esc_test_import = state


async def main():
    await flow("Ctrl+D on a track", setup_remove, lambda a: bool(getattr(a, "_pending_remove_track", None)))
    await flow("Multi-Add", setup_multi, lambda a: bool(getattr(a, "_multi_add_mode", False)))
    await flow("/ filter", setup_filter, lambda a: bool(getattr(a.query_one("#tracks_table"), "_filter_query", "")))
    await flow("big-container add confirmation", setup_bulk, lambda a: bool(getattr(a, "_pending_container_add", None)))
    await flow("delete a sidebar playlist (type the name)", setup_delete_pl,
               lambda a: bool(getattr(a, "_pending_delete_playlist", None)))
    await flow("running Spotify import", setup_import, lambda a: a._esc_test_import["running"])

    # A single Esc runs the action exactly once.
    calls = []
    orig = nav.NavigationMixin.action_escape_to_menu
    def spy(self):
        calls.append(1); return orig(self)
    nav.NavigationMixin.action_escape_to_menu = spy
    try:
        config.LOCAL_CFG = {}
        app = TApp()
        async with app.run_test(size=(140, 34)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await open_playlist_table(app, pilot)
            await pilot.press("escape"); await pilot.pause()
            check("one physical Esc = one action_escape_to_menu", len(calls) == 1, str(len(calls)))
            app.search_input.focus(); await pilot.pause()
            calls.clear()
            await pilot.press("escape"); await pilot.pause()
            check("…also with the focus in a text field", len(calls) == 1, str(len(calls)))
    finally:
        nav.NavigationMixin.action_escape_to_menu = orig


if __name__ == "__main__":
    asyncio.run(main())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
