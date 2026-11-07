"""Liking / following / saving something from the combined-search grid must not empty its panel.

Bug: after `f` on an album (or artist / playlist) the whole Albums (Artists, Playlists) panel went
blank: `_repaint_rows_from_model` knew the Songs panel but sent the one-column Albums / Artists /
Playlists panels through the multi-column track layout, where every row failed to add.

Run standalone:  python tests/test_grid_panel_like.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_grid_panel_fill as G
from spt_tui.widgets import SearchPanel

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def mk(kind, n=5):
    return [{"type": kind, "id": f"{kind}{i}", "uri": f"x:{kind}:{i}", "title": f"{kind} {i}",
             "artist": "Joji", "album": "", "dur": "", "raw": {"k": i}} for i in range(n)]


def lines(t):
    return [str(t.get_row_at(i)[0]) for i in range(t.row_count)]


async def body():
    app = G.TApp(G.Fake())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); G.pause_intervals(app)
        app._render_search_grid(mk("track"), mk("artist"), mk("album"), mk("playlist"),
                                app._new_view_token("search", ""))
        for _ in range(6): await pilot.pause()
        for key, kind in (("albums", "album"), ("artists", "artist"), ("playlists", "playlist")):
            t = app.query_one(f"#{key}_table", SearchPanel)
            before = lines(t)
            app._paint_saved_rows(t, f"{kind}2", True)      # what a successful like / follow / save does
            await pilot.pause()
            check(f"{key}: the panel keeps all its rows after a like", t.row_count == 5 and lines(t) == before,
                  f"{t.row_count} {lines(t)}")
            check(f"{key}: row → id / type / uri still resolve",
                  t.row_to_id.get(2) == f"{kind}2" and t.row_to_type.get(2) == kind and t.row_to_uri.get(2) == f"x:{kind}:2"
                  and t.row_to_obj.get(2) == {"k": 2}, str((t.row_to_id, t.row_to_type)))
        songs = app.query_one("#songs_table", SearchPanel)
        app._paint_saved_rows(songs, "track1", True)
        await pilot.pause()
        check("songs: unchanged, still 5 rows", songs.row_count == 5)


def main():
    try:
        asyncio.run(body())
    except Exception:
        check("grid like test (crashed)", False, traceback.format_exc(limit=8))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    for n, c, dd in RESULTS:
        if not c: print("  FAIL:", n, "::", dd)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
