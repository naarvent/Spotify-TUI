"""Help and the "Add to playlist" picker use the playlist frame (like Queue and Devices): the title sits
on #right's border (class table-view, no padding) and the content fills the panel, with no heading
or box nested inside.

Run standalone:  python tests/test_fullscreen_views.py
"""
import os, sys, asyncio, tempfile

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_fs_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_visualizer as tv
from spt_tui import config
from spt_tui.app import SptPy
from textual.widgets import ListView, Static

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class F(tv.Fake):
    def user_playlists(self, limit=50, offset=0):
        return {"items": [{"id": f"pl{i}", "name": f"List {i}", "uri": f"spotify:playlist:pl{i}", "type": "playlist",
                           "owner": {"id": "me"}, "tracks": {"total": 1}} for i in range(3)], "next": None}
    def current_user(self): return {"id": "me"}

class A(SptPy):
    def __init__(self): super().__init__(); self.spotify = F()


def fills(app, widget):
    cr, wr = app.right_panel.content_region, widget.region
    return wr.x == cr.x and wr.width == cr.width and wr.height == cr.height


async def main():
    config.LOCAL_CFG = {}
    app = A()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); tv.pause_intervals(app)
        await tv.poll(pilot, lambda: len(app.pl_list.children) > 0)

        # Help
        await pilot.press("question_mark"); await pilot.pause(); await pilot.pause()
        rp = app.right_panel
        check("Help: playlist frame (table-view)", rp.has_class("table-view"))
        check("Help: title on the frame", "Help & Keybindings" in str(rp.border_title), str(rp.border_title))
        txt = str(app.query_one("#help_text", Static).render())
        check("Help: no heading repeated inside", "Help & Keybindings" not in txt)
        check("Help: scroll fills the panel", fills(app, app.help_scroll),
              f"{app.help_scroll.region} vs {rp.content_region}")
        check("Help: no padding of its own on #right", rp.styles.padding.top == 0 and rp.styles.padding.left == 0)
        check("Help: scroll has the focus", app.focused is app.help_scroll)
        before = app.help_scroll.scroll_y
        await pilot.press("pagedown"); await pilot.pause()
        check("Help: keyboard scrolling still works", app.help_scroll.scroll_y > before)
        await pilot.press("escape"); await pilot.pause(); await pilot.pause()
        check("Help: Esc closes it and the frame is reset", not rp.has_class("table-view") and not rp.border_title
              and app._right_view is None)

        # Add to playlist
        app._show_playlists_for_adding(); await pilot.pause(); await pilot.pause()
        lv = app.query_one("#add_pl_list", ListView)
        check("Add to playlist: playlist frame", rp.has_class("table-view"))
        check("Add to playlist: title on the frame", "Add to playlist" in str(rp.border_title), str(rp.border_title))
        check("Add to playlist: no header text nested in the panel",
              not [w for w in rp.children if isinstance(w, Static) and "Select a playlist" in str(w.render())])
        check("Add to playlist: list fills the panel", fills(app, lv), f"{lv.region} vs {rp.content_region}")
        check("Add to playlist: no border of its own", lv.styles.border.top[0] in ("", "none"), str(lv.styles.border.top))
        check("Add to playlist: 3 playlists and the focus", len(lv.children) == 3 and app.focused is lv)
        await pilot.press("escape"); await pilot.pause(); await pilot.pause()
        check("Add to playlist: Esc leaves and resets the frame", not rp.has_class("table-view") and not rp.border_title)

        # Reference: Devices uses the same frame classes.
        app._open_devices_view(); await pilot.pause(); await pilot.pause()
        check("reference: Devices has the same frame class", rp.has_class("table-view"))
        await pilot.press("escape"); await pilot.pause()

if __name__ == "__main__":
    asyncio.run(main())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
