"""Tables fill the panel width, and shed columns instead of squeezing them.

Columns used to stop at a per-field cap (Title 42 / Artist 28 / Album 26), so a
wide terminal showed an unused gap on the right; below ~90 columns every
flexible column collapsed to its 6-character floor instead. These tests pin the
exact fill, the fixed-width columns, the narrow-terminal drop order, and the
column coming back on a widening resize.

Run standalone:  python tests/test_table_fill.py
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
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def rows_n(n):
    return [{"id": f"id{i}", "uri": f"spotify:track:id{i}", "title": f"song number {i}",
             "artist": "some artist", "album": "some album", "dur": "3:21",
             "added": "2024-01-01", "type": "track", "raw": {}} for i in range(n)]

def labels(t):
    return [str(getattr(c, "label", "")) for c in t.ordered_columns]

def widths(t):
    out = []
    for c in t.ordered_columns:
        try: out.append(int(getattr(c, "width", 0) or 0))
        except Exception: out.append(0)
    return out

def content_w(app):
    r = app.right_panel
    cs = getattr(r, "content_size", None)
    if cs is not None and getattr(cs, "width", 0):
        return int(cs.width)
    sz = getattr(r, "size", None)
    return int(getattr(sz, "width", 0) or 0)

def overhead(t):
    # Round border (2) + a permanent vertical-scrollbar reserve (2) + the
    # DataTable's 2 cells of padding per column.
    return 4 + 2 * len(list(t.ordered_columns))

def fill_gap(app, t):
    """Columns unused width: 0 means the table reaches the right edge."""
    return content_w(app) - (sum(widths(t)) + overhead(t))

def scroll_y(table):
    return int(getattr(getattr(table, "scroll_offset", None), "y", 0) or 0)


async def test_tracks_table_fills_every_width():
    for size in [(200, 40), (160, 30), (120, 25), (100, 25)]:
        app = TApp(Fake())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            t = app._render_tracks_table("[b]P[/b]", rows_n(60), [False] * 60, profile="playlist")
            for _ in range(4):
                await pilot.pause()
            check(f"tracks table reaches the right edge at {size}", fill_gap(app, t) == 0,
                  f"gap={fill_gap(app, t)} widths={widths(t)} panel={content_w(app)}")
            check(f"tracks table has no horizontal scrollbar at {size}",
                  not bool(getattr(t, "show_horizontal_scrollbar", False)), f"widths={widths(t)}")


async def test_search_table_fills_every_width():
    srows = [{"type": "track", "id": f"t{i}", "uri": "u", "title": "T", "artist": "a",
              "album": "al", "dur": "3:21", "raw": {}} for i in range(60)]
    for size in [(200, 40), (160, 30), (120, 25), (100, 25)]:
        app = TApp(Fake())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            st = app._render_search_table("[b]S[/b]", srows, check_saved=False, layout="full")
            for _ in range(4):
                await pilot.pause()
            check(f"search table reaches the right edge at {size}", fill_gap(app, st) == 0,
                  f"gap={fill_gap(app, st)} widths={widths(st)} panel={content_w(app)}")
            check(f"search table has no horizontal scrollbar at {size}",
                  not bool(getattr(st, "show_horizontal_scrollbar", False)), f"widths={widths(st)}")


def width_of(t, label):
    """Width of a column by label, or None when the fit dropped it."""
    for c, w in zip(labels(t), widths(t)):
        if c == label:
            return w
    return None


async def test_fixed_columns_never_change_width():
    for size in [(200, 40), (140, 30), (110, 25)]:
        app = TApp(Fake())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            t = app._render_tracks_table("[b]P[/b]", rows_n(5), [False] * 5, profile="playlist")
            for _ in range(3):
                await pilot.pause()
            w = widths(t)
            check(f"heart stays 3 wide at {size}", width_of(t, "♥") == 3, f"w={w}")
            check(f"duration stays 9 wide at {size}", width_of(t, "Duration") == 9, f"w={w}")
            added = width_of(t, "Added")
            check(f"added is 12 wide or dropped at {size}", added in (None, 12), f"w={w}")


async def test_title_stays_weighted_over_artist():
    for size in [(200, 40), (140, 30), (110, 25)]:
        app = TApp(Fake())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            t = app._render_tracks_table("[b]P[/b]", rows_n(5), [False] * 5, profile="playlist")
            await pilot.pause()
            w = widths(t)
            ratio = (w[1] / w[2]) if w[2] else 0
            check(f"Title wider than Artist at {size}", w[1] > w[2], f"w={w}")
            check(f"Title/Artist ratio stays near its weight at {size}", 1.2 <= ratio <= 1.6,
                  f"ratio={ratio:.2f} w={w}")


async def test_no_flex_column_is_squeezed_to_uselessness():
    # The left menu takes 40 columns, so the panel is (terminal width - 40).
    for size in [(200, 40), (140, 25), (110, 25), (95, 25), (85, 25)]:
        app = TApp(Fake())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            t = app._render_tracks_table("[b]P[/b]", rows_n(5), [False] * 5, profile="playlist")
            for _ in range(3):
                await pilot.pause()
            ls, w = labels(t), widths(t)
            flex = [w[i] for i, l in enumerate(ls) if l in ("Title", "Artist", "Album")]
            check(f"every flexible column is at least 12 wide at {size}",
                  all(x >= 12 for x in flex), f"labels={ascii(ls)} w={w}")
            check(f"table still reaches the right edge at {size}", fill_gap(app, t) == 0,
                  f"gap={fill_gap(app, t)} w={w}")


async def test_drop_order_on_narrow_terminals():
    # (terminal size, labels that must be gone). Panel = terminal width - 40.
    for size, gone in [((200, 40), []), ((140, 25), []),
                       ((110, 25), ["Added"]), ((95, 25), ["Added", "Album"])]:
        app = TApp(Fake())
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            t = app._render_tracks_table("[b]P[/b]", rows_n(5), [False] * 5, profile="playlist")
            for _ in range(3):
                await pilot.pause()
            ls = labels(t)
            check(f"dropped columns at {size}: {gone}", all(g not in ls for g in gone), f"labels={ascii(ls)}")
            check(f"heart/Title/Duration always survive at {size}",
                  "♥" in ls and "Title" in ls and "Duration" in ls, f"labels={ascii(ls)}")


async def test_dropped_column_returns_on_widening_resize():
    app = TApp(Fake())
    async with app.run_test(size=(110, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(60), [False] * 60, profile="playlist")
        for _ in range(3):
            await pilot.pause()
        check("Added is dropped on a narrow terminal", "Added" not in labels(t), f"labels={ascii(labels(t))}")
        t.cursor_coordinate = (25, 0)
        for _ in range(3):
            await pilot.pause()
        before_scroll = scroll_y(t)
        await pilot.resize_terminal(200, 40)
        for _ in range(15):
            await pilot.pause()
        t = app.query_one("#tracks_table", DataTable)
        check("Added comes back once the terminal is wide again", "Added" in labels(t),
              f"labels={ascii(labels(t))}")
        check("widened table reaches the right edge", fill_gap(app, t) == 0,
              f"gap={fill_gap(app, t)} w={widths(t)}")
        check("resize across a column change preserves the cursor row", t.cursor_row == 25,
              f"cursor={t.cursor_row}")
        check("resize across a column change preserves scroll", scroll_y(t) == before_scroll,
              f"{before_scroll} -> {scroll_y(t)}")


async def test_album_profile_fills_too():
    app = TApp(Fake())
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]A[/b]", rows_n(10), [False] * 10, profile="album")
        await pilot.pause()
        check("album profile reaches the right edge", fill_gap(app, t) == 0,
              f"gap={fill_gap(app, t)} w={widths(t)} labels={ascii(labels(t))}")


async def test_queue_table_fills_and_is_field_driven():
    app = TApp(Fake())
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app.action_open_queue()
        for _ in range(4):
            await pilot.pause()
        t = app.query_one("#queue_table", DataTable)
        check("queue table reaches the right edge", fill_gap(app, t) == 0,
              f"gap={fill_gap(app, t)} w={widths(t)} labels={ascii(labels(t))}")
        check("queue table records its field list", bool(getattr(t, "_queue_fields", None)),
              f"fields={getattr(t, '_queue_fields', None)}")


ALL = [test_tracks_table_fills_every_width, test_search_table_fills_every_width,
       test_fixed_columns_never_change_width, test_title_stays_weighted_over_artist,
       test_no_flex_column_is_squeezed_to_uselessness, test_drop_order_on_narrow_terminals,
       test_dropped_column_returns_on_widening_resize, test_album_profile_fills_too,
       test_queue_table_fills_and_is_field_driven]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (table fill) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
