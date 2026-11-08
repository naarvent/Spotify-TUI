"""The "/" filter over the rows already loaded in a content table.

* pure matching: every word, title/artist/album, case- and accent-insensitive;
* in the app: "/" on a playlist opens the filter box (no new search), typing
  narrows the rows, Esc restores them, Enter/↓ keep the filter and go back;
* the highlighted filtered row maps back to its model row, so playing it uses
  its real playlist position (also with the `s` sort), `f`/Ctrl+D address the
  right track, and pages that stream in later are filtered too;
* "/" with no content table still goes to Search.

Run standalone:  python tests/test_view_filter.py
"""
import os, sys, asyncio, tempfile

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_filter_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.app import SptPy
from spt_tui.app.tables import TablesMixin
from spt_tui.widgets import ViewFilterInput

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def test_pure_matching():
    m = TablesMixin._row_matches_filter
    tok = TablesMixin._filter_tokens
    row = {"title": "Crazy in Love", "artist": "Beyoncé, JAY-Z", "album": "Dangerously in Love"}
    check("empty filter matches everything", m(row, tok("")))
    check("title word matches", m(row, tok("crazy")))
    check("accent-insensitive artist", m(row, tok("beyonce")))
    check("case-insensitive album", m(row, tok("DANGEROUSLY")))
    check("every word must match (AND)", m(row, tok("love jay")) and not m(row, tok("love drake")))
    check("non-dict rows never match", not m(None, tok("x")))
    check("missing fields do not raise", m({"title": None}, tok("")) and not m({"title": None}, tok("a")))


NAMES = ["delta", "Alpha", "charlie", "bravo", "echo"]

class PlFake:
    def __init__(self, total=10):
        self.total = total
        self.played = []
        self.pages_hold = None
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def _normalize_track_id(self, x): return x
    def fmt_duration(self, ms):
        s = int((ms or 0) / 1000); return f"{s // 60}:{s % 60:02d}"
    def shuffle(self, *a, **k): pass
    def start_playback(self, **kw): self.played.append(kw)
    def search(self, *a, **k):
        self.searched = True
        return {}
    def check_saved_tracks(self, ids, on_batch=None):
        vals = [False for _ in ids]
        if on_batch: on_batch(0, vals)
        return vals
    def playlist_items(self, pid, limit=100, offset=0, fields=None):
        end = min(self.total, offset + limit)
        return {"total": self.total, "items": [{"added_at": "2024-01-01T00:00:00Z", "track": {
            "id": f"t{i}", "uri": f"spotify:track:t{i}", "type": "track",
            "name": f"{NAMES[i % 5]} {i:03d}", "duration_ms": 1000 * i,
            "artists": [{"name": f"Artist{i % 2}"}], "album": {"name": "Álbum"}}}
            for i in range(offset, end)]}


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

PL = {"id": "pl1", "name": "Filt", "uri": "spotify:playlist:pl1"}

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

async def poll(fn, timeout=5.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.02)
    try: return fn()
    except Exception: return None

def table(app):
    try: return app._open_playlist_tracks_table()
    except Exception: return None

def loaded(app, n):
    t = table(app)
    return t is not None and len(getattr(t, "_model_rows", []) or []) == n and \
        "loading" not in str(getattr(t, "_view_title", "")) and \
        "checking" not in str(getattr(t, "_view_title", ""))

def filter_box(app):
    try:
        return app.query_one(ViewFilterInput)
    except Exception:
        return None


async def test_filter_in_app():
    config.LOCAL_CFG = {}
    fake = PlFake(total=10)
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._pl_sort = None
        app._open_playlist_table(dict(PL))
        await poll(lambda: loaded(app, 10))
        t = table(app)
        t.focus(); await pilot.pause()

        await pilot.press("slash"); await pilot.pause()
        box = filter_box(app)
        check("/ on a playlist opens the filter box", box is not None)
        check("the filter box has focus", app.focused is box, repr(app.focused))
        check("/ on a table did not start Search capture",
              not getattr(app, "_search_capture_next", False))

        for ch in "bravo":
            await pilot.press(ch)
        await poll(lambda: t.row_count == 2, timeout=2.0)
        check("typing narrows to the matching rows (bravo 003, bravo 008)",
              t.row_count == 2 and t._filter_rows == [3, 8], f"{t.row_count} {t._filter_rows}")
        check("letters typed in the box do not trigger bindings (no sort)",
              app._pl_sort in (None, ("default", False)), str(app._pl_sort))
        check("model, maps and context URIs keep every row",
              len(t._model_rows) == 10 and len(t.row_to_uri) == 10 and len(t._context_uris) == 10)
        check("the frame title shows the filter", "filter “bravo”: 2 of 10" in str(app.right_panel.border_title),
              str(app.right_panel.border_title))

        await pilot.press("down"); await pilot.pause()
        check("Down goes back to the table and keeps the filter",
              app.focused is t and t.row_count == 2, repr(app.focused))
        t.move_cursor(row=1); await pilot.pause()
        check("the second filtered row is model row 8", app._get_cursor_row(t) == 8, str(app._get_cursor_row(t)))

        await pilot.press("enter")
        await poll(lambda: fake.played)
        got = fake.played[-1] if fake.played else {}
        check("Enter plays the filtered row at its real playlist position",
              got.get("offset") == {"position": 8} and got.get("context_uri") == PL["uri"], str(got))

        # f toggles the highlighted (model) track.
        calls = []
        app._toggle_favorite_dispatch = lambda rtype, item_id, uri, table, row: calls.append((item_id, row))
        await pilot.press("f"); await pilot.pause()
        check("f on a filtered row targets its track", calls == [("t8", 8)], str(calls))

        app.action_delete(); await pilot.pause()
        pend = getattr(app, "_pending_remove_track", None) or {}
        check("Ctrl+D targets the filtered row's track", pend.get("uri") == "spotify:track:t8", str(pend))
        app._cancel_remove_track(); await pilot.pause()

        # Sort while filtered: the filter survives and the mapping follows.
        await pilot.press("S"); await pilot.pause()      # default order, descending
        check("sorting keeps the filter", t.row_count == 2 and len(t._model_rows) == 10,
              f"{t.row_count}")
        vis_pos = [t._model_rows[i]["_pos"] for i in t._filter_rows]
        check("filtered rows follow the new order", vis_pos == [8, 3], str(vis_pos))
        t.move_cursor(row=0); await pilot.pause()
        fake.played.clear(); app._last_played = None
        app._play_row(app._get_cursor_row(t), t)
        await poll(lambda: fake.played)
        got = fake.played[-1] if fake.played else {}
        check("sorted + filtered row still plays its playlist position",
              got.get("offset") == {"position": 8}, str(got))
        await pilot.press("S"); await pilot.pause()      # back to ascending

        # Esc on the table clears the filter (first Esc), the view stays.
        await pilot.press("escape"); await pilot.pause()
        check("Esc clears the filter and shows every row", t.row_count == 10 and t._filter_rows is None,
              f"{t.row_count}")
        check("the filter box is gone", await poll(lambda: filter_box(app) is None, timeout=2.0))
        check("the view is still the playlist", table(app) is t)
        check("the cursor stays on the same track",
              t._model_rows[app._get_cursor_row(t)]["_pos"] in (3, 8), str(app._get_cursor_row(t)))

        # Esc inside the box also clears.
        t.focus(); await pilot.press("slash"); await pilot.pause()
        for ch in "echo":
            await pilot.press(ch)
        await poll(lambda: t.row_count == 2, timeout=2.0)
        await pilot.press("escape"); await pilot.pause()
        check("Esc in the box clears it", t.row_count == 10 and t._filter_query == "")

        # A filter that matches nothing.
        t.focus(); await pilot.press("slash"); await pilot.pause()
        for ch in "zzz":
            await pilot.press(ch)
        await poll(lambda: t.row_count == 0, timeout=2.0)
        check("no match leaves an empty table", t.row_count == 0)
        check("no cursor row when nothing matches", app._get_cursor_row(t) is None)
        await pilot.press("enter"); await pilot.pause()
        check("Enter on an empty filtered table plays nothing", len(fake.played) == 1, str(fake.played))
        await pilot.press("escape"); await pilot.pause()
        await pilot.press("escape"); await pilot.pause()

        # Empty filter + Enter closes the box.
        t.focus(); await pilot.press("slash"); await pilot.pause()
        await pilot.press("enter"); await pilot.pause()
        check("Enter with an empty filter closes the box",
              await poll(lambda: filter_box(app) is None, timeout=2.0) and t.row_count == 10)


async def test_filter_streaming_pages():
    """Rows appended while a filter is on are filtered as they arrive."""
    config.LOCAL_CFG = {}
    fake = PlFake(total=3)
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._pl_sort = None
        app._open_playlist_table(dict(PL))
        await poll(lambda: loaded(app, 3))
        t = table(app)
        app._apply_view_filter(t, "alpha", announce=False)
        await pilot.pause()
        check("one alpha in the first page", t._filter_rows == [1], str(t._filter_rows))
        extra = [{"_pos": i, "id": f"t{i}", "uri": f"spotify:track:t{i}", "title": f"{NAMES[i % 5]} {i:03d}",
                  "artist": "x", "album": "", "dur": ""} for i in range(3, 8)]
        app._append_track_rows(t, extra)
        await pilot.pause()
        check("an appended matching row is shown, others hidden",
              t._filter_rows == [1, 6] and t.row_count == 2, f"{t._filter_rows} {t.row_count}")
        check("appended rows are still in the maps", t.row_to_uri.get(7) == "spotify:track:t7")
        # Heart repaint for a hidden row is a no-op, never a wrong row.
        app._set_heart_icon(t, 4, True)
        check("a heart for a hidden row does not paint a visible one",
              str(t.get_row_at(0)[0]) == "" and str(t.get_row_at(1)[0]) == "")
        app._set_heart_icon(t, 6, True)
        check("a heart for a visible row lands on its screen row", str(t.get_row_at(1)[0]) == "❤")


async def test_filter_search_results():
    """Search results / artist view (search_table): the filtered row opens or
    plays the right item, and a new result set drops the old filter."""
    config.LOCAL_CFG = {}
    fake = PlFake()
    app = TApp(fake)
    rows = [{"type": "track", "id": "a", "uri": "spotify:track:a", "title": "Uno", "artist": "X", "album": ""},
            {"type": "album", "id": "b", "uri": "spotify:album:b", "title": "Dos", "artist": "Y", "album": "",
             "raw": {"id": "b", "name": "Dos"}},
            {"type": "track", "id": "c", "uri": "spotify:track:c", "title": "Tres", "artist": "Y", "album": ""}]
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_search_table("Results", rows, check_saved=False)
        await pilot.pause(); await pilot.pause()
        t.focus(); await pilot.pause()
        await pilot.press("slash"); await pilot.pause()
        check("/ on search results opens the filter box", filter_box(app) is not None)
        await pilot.press("t", "r", "e", "s")
        await poll(lambda: t.row_count == 1, timeout=2.0)
        await pilot.press("down"); await pilot.pause()
        check("filtered search row maps to model row 2", app._get_cursor_row(t) == 2)
        await pilot.press("enter")
        await poll(lambda: fake.played)
        check("Enter plays the filtered search track", fake.played and
              fake.played[-1].get("uris") == ["spotify:track:c"], str(fake.played))
        t2 = app._render_search_table("Results 2", rows, check_saved=False)
        await pilot.pause()
        check("a new result set in the reused table drops the filter",
              t2 is t and t.row_count == 3 and t._filter_rows is None and
              await poll(lambda: filter_box(app) is None, timeout=2.0))


async def test_slash_without_table_goes_to_search():
    config.LOCAL_CFG = {}
    app = TApp(PlFake())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await pilot.press("slash"); await pilot.pause()
        check("/ from the menu still focuses Search", app.focused is app.search_input, repr(app.focused))
        check("no filter box", filter_box(app) is None)


if __name__ == "__main__":
    test_pure_matching()
    for t in (test_filter_in_app, test_filter_streaming_pages, test_filter_search_results,
              test_slash_without_table_goes_to_search):
        asyncio.run(t())
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"{ok}/{len(RESULTS)} checks passed")
    sys.exit(0 if ok == len(RESULTS) else 1)
