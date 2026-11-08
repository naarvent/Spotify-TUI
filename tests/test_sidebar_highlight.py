"""The first entry of a sidebar list (Playlists / Library) is highlighted as soon as the list gets focus.

Bug: entering Playlists left its first entry unhighlighted (the ListView's index was already 0, so
Textual never painted `-highlight`); it only showed after moving down and back up.

Run standalone:  python tests/test_sidebar_highlight.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_queue_view as Q

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class P(Q.FakeProv):
    def user_playlists(self, limit=50, offset=0):
        items = [{"id": f"p{i}", "name": f"Playlist {i}", "uri": f"x:{i}", "owner": {"display_name": "me"},
                  "tracks": {"total": 3}, "type": "playlist"} for i in range(5)]
        return {"items": items, "next": None, "total": 5}


def marks(lv):
    return [c.has_class("-highlight") for c in lv.children]


async def step(pilot, n=4):
    for _ in range(n):
        await pilot.pause(); await asyncio.sleep(0.05)


async def body():
    app = Q.TApp(P())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); Q.pause_intervals(app)
        app._load_playlists(force=True)
        for _ in range(10): await pilot.pause(); await asyncio.sleep(0.1)
        app._focus_section_by_idx(0)
        await pilot.press("down"); await step(pilot)          # Library
        lib_idx = app.section_idx
        await pilot.press("enter"); await step(pilot)
        check("Library: its first entry is highlighted on entering", marks(app.lib_list)[:1] == [True]
              and sum(marks(app.lib_list)) == 1, str(marks(app.lib_list)))
        await pilot.press("escape"); await step(pilot)
        app._focus_section_by_idx(2); await step(pilot)
        await pilot.press("enter"); await step(pilot)
        check("Playlists: focus is in the list", app.focused is app.pl_list, str(app.focused))
        check("Playlists: the FIRST entry is highlighted on entering (and only it)",
              marks(app.pl_list) == [True, False, False, False, False], str(marks(app.pl_list)))
        await pilot.press("down"); await step(pilot)
        check("down moves the highlight", marks(app.pl_list)[:2] == [False, True] and sum(marks(app.pl_list)) == 1)
        await pilot.press("up"); await step(pilot)
        check("up brings it back", marks(app.pl_list)[:2] == [True, False] and sum(marks(app.pl_list)) == 1)

        # Focus from elsewhere (mouse / Tab path): clicking the list also shows the current item.
        app.pl_list.blur(); await step(pilot)
        for c in app.pl_list.children: c.highlighted = False
        app.pl_list.focus(); await step(pilot)
        check("any focus path paints the current item", marks(app.pl_list)[0] is True, str(marks(app.pl_list)))


def main():
    try:
        asyncio.run(body())
    except Exception:
        check("sidebar highlight test (crashed)", False, traceback.format_exc(limit=8))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    for n, c, dd in RESULTS:
        if not c: print("  FAIL:", n, "::", dd)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
