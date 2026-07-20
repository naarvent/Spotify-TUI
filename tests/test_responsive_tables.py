"""Phase 5: responsive per-view table layouts.

Columns auto-fit the panel width (no horizontal scroll at reasonable sizes),
Title/Name is weighted wider, manual mouse column-resize is gone, resize
recomputes widths while preserving cursor/scroll, and each view has an explicit
column profile.

Run standalone:  python tests/test_responsive_tables.py
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

class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def rows_n(n):
    return [{"id": f"id{i}", "uri": f"spotify:track:id{i}", "title": f"song number {i}",
             "artist": "some artist", "album": "some album", "dur": "3:21", "added": "2024-01-01",
             "type": "track", "raw": {}} for i in range(n)]

def col_labels(t):
    return [str(getattr(c, "label", "")) for c in t.ordered_columns]

def col_widths(t):
    out = []
    for c in t.ordered_columns:
        try: out.append(int(getattr(c, "width", 0) or 0))
        except Exception: out.append(0)
    return out

def scroll_y(table):
    return int(getattr(getattr(table, "scroll_offset", None), "y", 0) or 0)

def content_w(app):
    r = app.right_panel
    cs = getattr(r, "content_size", None)
    if cs is not None and getattr(cs, "width", 0):
        return int(cs.width)
    sz = getattr(r, "size", None)
    return int(getattr(sz, "width", 0) or 0)


async def test_no_horizontal_scrollbar_reasonable_sizes():
    # Many rows so the vertical scrollbar is present (the width worst case).
    for size in [(180, 40), (140, 30), (110, 25)]:
        app = TApp(Fake())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            t = app._render_tracks_table("[b]P[/b]", rows_n(60), [False] * 60, profile="playlist")
            for _ in range(4):
                await pilot.pause()
            check(f"tracks table has no horizontal scrollbar at {size}",
                  not bool(getattr(t, "show_horizontal_scrollbar", False)), f"widths={col_widths(t)}")
            app.action_escape_to_menu(); await pilot.pause()
            srows = [{"type": "track", "id": f"t{i}", "uri": "u", "title": "T", "artist": "a",
                      "album": "al", "dur": "3:21", "raw": {}} for i in range(60)]
            st = app._render_search_table("[b]S[/b]", srows, check_saved=False, layout="full")
            for _ in range(4):
                await pilot.pause()
            check(f"search table has no horizontal scrollbar at {size}",
                  not bool(getattr(st, "show_horizontal_scrollbar", False)), f"widths={col_widths(st)}")


async def test_flex_columns_use_the_whole_panel():
    # Columns used to stop at a cap (42/28/26) and leave the rest of the panel
    # empty; they now take all of it. Full coverage lives in test_table_fill.
    app = TApp(Fake())
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(3), [False] * 3, profile="playlist")
        for _ in range(3):
            await pilot.pause()
        w = col_widths(t)   # [heart, title, artist, album, dur, added]
        panel = content_w(app)
        check("columns reach the right edge of a wide panel",
              sum(w) + 4 + 2 * len(w) == panel, f"w={w} panel={panel}")
        check("Title grows past the old 42 cap on a wide terminal", w[1] > 42, f"title={w[1]}")


async def test_title_weighted_wider_than_artist():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(3), [False] * 3, profile="playlist")
        await pilot.pause()
        w = col_widths(t)   # [heart, title, artist, album, dur, added]
        check("Title is weighted wider than Artist", len(w) == 6 and w[1] > w[2],
              f"w={w}")


async def test_recompute_preserves_cursor_and_scroll():
    app = TApp(Fake())
    async with app.run_test(size=(140, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(40), [False] * 40, profile="playlist")
        await pilot.pause()
        t.move_cursor(row=25, animate=False)
        for _ in range(5): await pilot.pause()
        before_cursor, before_scroll, before_w = t.cursor_row, scroll_y(t), col_widths(t)
        # Simulate a resize: force the fitter to return different widths for the
        # same column set.
        orig = app._fit_columns
        app._fit_columns = lambda fields, labels, panel_w=None: (
            lambda r: (r[0], r[1], [x + 1 for x in r[2]]))(orig(fields, labels, panel_w))
        try:
            app._recompute_table_widths(t)
        finally:
            app._fit_columns = orig
        for _ in range(5): await pilot.pause()
        after_w = col_widths(t)
        check("resize recompute changes column widths", after_w != before_w, f"{before_w} -> {after_w}")
        check("resize recompute preserves the cursor row", t.cursor_row == before_cursor == 25,
              f"cursor {before_cursor} -> {t.cursor_row}")
        check("resize recompute preserves scroll position", scroll_y(t) == before_scroll and before_scroll > 0,
              f"scroll {before_scroll} -> {scroll_y(t)}")


async def test_no_manual_resize_widget():
    import spt_tui.widgets as w
    check("ResizableDataTable removed (no manual resize widget)", not hasattr(w, "ResizableDataTable"))
    app = TApp(Fake())
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(3), [False] * 3, profile="playlist")
        await pilot.pause()
        check("tracks table is a plain DataTable (no resize subclass)",
              type(t).__name__ == "DataTable", f"type={type(t).__name__}")


async def test_column_profiles():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        alb = app._render_tracks_table("[b]A[/b]", rows_n(2), None, profile="album")
        await pilot.pause()
        check("album profile drops Album and Added",
              col_labels(alb) == ["♥", "Title", "Artist", "Duration"], f"cols={col_labels(alb)}")
        app.action_escape_to_menu(); await pilot.pause()
        rec = app._render_tracks_table("[b]R[/b]", rows_n(2), None, profile="recent")
        await pilot.pause()
        check("recent profile shows a Played column",
              col_labels(rec) == ["♥", "Title", "Artist", "Album", "Duration", "Played"], f"cols={col_labels(rec)}")
        app.action_escape_to_menu(); await pilot.pause()
        arows = [{"type": "album", "id": "a", "uri": "u", "title": "N", "artist": "Ar", "album": "", "dur": "", "raw": {}}]
        sa = app._render_search_table("[b]SA[/b]", arows, check_saved=False, layout="albums")
        await pilot.pause()
        check("search albums profile: heart/Type/Name/Artist, no Duration",
              col_labels(sa) == ["♥", "Type", "Name", "Artist"], f"cols={col_labels(sa)}")
        prows = [{"type": "playlist", "id": "p", "uri": "u", "title": "PL", "artist": "Owner", "album": "", "dur": "", "raw": {}}]
        sp = app._render_search_table("[b]SP[/b]", prows, check_saved=False, layout="playlists")
        await pilot.pause()
        check("search playlists profile: heart/Type/Name/Owner, no Duration",
              col_labels(sp) == ["♥", "Type", "Name", "Owner"], f"cols={col_labels(sp)}")


ALL = [test_no_horizontal_scrollbar_reasonable_sizes, test_flex_columns_use_the_whole_panel,
       test_title_weighted_wider_than_artist, test_recompute_preserves_cursor_and_scroll,
       test_no_manual_resize_widget, test_column_profiles]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (responsive tables) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
