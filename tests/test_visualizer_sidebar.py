"""Visualizer -> open a sidebar playlist (Enter) must leave the visualizer and show the playlist.

Cause: the Visualizer has no focusable widget and sets level=LVL_VIEW, so the focus stayed in the
sidebar list; Enter then went to action_open / on_list_view_selected, which only acted at
level == LVL_SECTION_CONTENT and silently ignored it. Also covers _back_one_level without the
removed _restore_view / _view_stack.

Run standalone:  python tests/test_visualizer_sidebar.py
"""
import os, sys, asyncio, tempfile, time

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_vissb_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_visualizer as tv
from spt_tui import config, visualizer as viz
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class F(tv.Fake):
    def user_playlists(self, limit=50, offset=0):
        return {"items": [{"id": "pl1", "name": "Mine", "uri": "spotify:playlist:pl1", "type": "playlist",
                           "owner": {"id": "me", "display_name": "me"}, "tracks": {"total": 0}}], "next": None}
    def current_user(self): return {"id": "me"}
    def playlist_items(self, *a, **k): return {"items": [], "next": None, "total": 0}
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)

class A(SptPy):
    def __init__(self): super().__init__(); self.spotify = F()


async def scenario(name, where):
    config.LOCAL_CFG = {}
    app = A()
    opened = []
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); tv.pause_intervals(app)
        await tv.poll(pilot, lambda: len(app.pl_list.children) > 0)
        orig = app._open_playlist_table
        app._open_playlist_table = lambda d: (opened.append(d.get("id")), orig(d))[1]
        if where == "list":
            # The common way: Playlists list focused, then v with the focus still there.
            app._focus_section_by_idx(app.section_order.index("pl")); await pilot.pause()
            await pilot.press("enter"); await pilot.pause()
            check(f"{name}: focus is in the Playlists list", app.focused is app.pl_list)
            await pilot.press("v")
        else:
            await pilot.press("v")
        await tv.poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None)
        pid = app._vis_proc.proc.pid
        if where == "tab":
            # From the visualizer, Tab -> sidebar stops, then into the list.
            app._focus_section_by_idx(app.section_order.index("pl")); await pilot.pause()
            await pilot.press("enter"); await pilot.pause()
        check(f"{name}: visualizer is up and the list has the focus",
              app._vis_on and app.focused is app.pl_list, str(app.focused))
        await pilot.press("enter"); await pilot.pause(); await pilot.pause()
        check(f"{name}: Enter opens the playlist exactly once", opened == ["pl1"], str(opened))
        check(f"{name}: the view is the playlist, not the visualizer",
              app._right_view and app._right_view[0] == "playlist" and not app._vis_on, str(app._right_view))
        time.sleep(0.1)
        check(f"{name}: cava is dead", not tv.alive(pid))


async def main():
    orig = viz.find_cava
    viz.find_cava = lambda: tv.FAKE
    try:
        await scenario("focus in the list", "list")
        await scenario("focus moved to the list afterwards", "tab")

        # Focus in the right panel (nothing focusable there): the sidebar still works.
        config.LOCAL_CFG = {}
        app = A()
        async with app.run_test(size=(140, 34)) as pilot:
            await pilot.pause(); tv.pause_intervals(app)
            await tv.poll(pilot, lambda: len(app.pl_list.children) > 0)
            app.set_focus(app.right_panel) if app.right_panel.can_focus else None
            await pilot.press("v")
            await tv.poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None)
            app._focus_section_by_idx(app.section_order.index("pl")); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(); await pilot.press("enter")
            await pilot.pause(); await pilot.pause()
            check("focus was on the panel: Enter in the list opens the playlist",
                  app._right_view and app._right_view[0] == "playlist" and not app._vis_on, str(app._right_view))

        # Library entry from the visualizer too (same guard).
        app = A()
        async with app.run_test(size=(140, 34)) as pilot:
            await pilot.pause(); tv.pause_intervals(app)
            app._focus_section_by_idx(app.section_order.index("lib")); await pilot.pause()
            await pilot.press("enter"); await pilot.pause()
            await pilot.press("v")
            await tv.poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None)
            await pilot.press("enter"); await pilot.pause(); await pilot.pause()
            check("Library list: Enter leaves the visualizer", not app._vis_on and
                  (app._right_view or ("",))[0] != "visualizer", str(app._right_view))

        # _back_one_level: no view stack, no swallowed AttributeError.
        app = A()
        async with app.run_test(size=(140, 34)) as pilot:
            await pilot.pause(); tv.pause_intervals(app)
            check("no _restore_view / _view_stack left", not hasattr(app, "_restore_view") and not hasattr(app, "_view_stack"))
            app._new_view_token("devices", ""); app.level = app.LVL_VIEW
            import logging
            recs = []
            h = logging.Handler(); h.emit = lambda r: recs.append(r)
            logging.getLogger().addHandler(h)
            try:
                app._back_one_level(); await pilot.pause()
            finally:
                logging.getLogger().removeHandler(h)
            check("_back_one_level: back to the section list, nothing logged as an error",
                  app.level == app.LVL_SECTION_CONTENT and not [r for r in recs if r.levelno >= logging.ERROR],
                  str([r.getMessage() for r in recs]))
    finally:
        viz.find_cava = orig
        config.LOCAL_CFG = {}


if __name__ == "__main__":
    asyncio.run(main())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
