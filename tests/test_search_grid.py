"""Combined (no-prefix) search renders a responsive 2x2 dashboard:
Songs / Artists on top, Albums / Playlists below. Prefix searches keep the
single specialized table. Covers panel counts, initial focus, empty panels,
navigation between/within panels, Enter routing, favourites/hearts on Songs,
Escape, Ctrl+R, fast A->B, resize (2x2 <-> stacked), no DuplicateIds, no
horizontal scrollbar and Now Playing staying visible.

Network is faked; A->B ordering is gated with threading.Event (no fixed sleep).
Run standalone:  python tests/test_search_grid.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from textual.widgets import DataTable
from textual.coordinate import Coordinate

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

HEART = "❤"


class Inner:
    def __init__(self, o): self.o = o
    def search(self, q, type='track', limit=25): return self.o._s(q, type, limit)

class GridFake:
    def __init__(self, counts=None):
        self.inner = Inner(self)
        self.counts = counts if counts is not None else {"track": 5, "album": 3, "artist": 2, "playlist": 2}
        self.saved = set()
        self.gate = None
        self.entered = None
        self.search_calls = []
        self.lock = threading.Lock()
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self.inner
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def check_saved_tracks(self, ids): return [i in self.saved for i in ids]
    def save_tracks(self, ids):
        for i in ids: self.saved.add(i)
        return True
    def remove_tracks(self, ids):
        for i in ids: self.saved.discard(i)
        return True
    def _item(self, q, type, i):
        b = {"id": f"{q}-{type}{i}", "uri": f"spotify:{type}:{q}{i}", "name": f"{q}-{type}-{i}"}
        if type == 'track': b.update({"artists": [{"name": "Ar"}], "album": {"name": "Al"}, "duration_ms": 1000})
        elif type == 'album': b.update({"artists": [{"name": "Ar"}]})
        elif type == 'playlist': b.update({"owner": {"display_name": "Ow"}})
        return b
    def _s(self, q, type, limit):
        with self.lock:
            self.search_calls.append((type, limit))
        if self.gate is not None:
            if self.entered is not None:
                self.entered.set()
            self.gate.wait(10)
        n = int(self.counts.get(type, 0))
        n = min(n, limit)
        key = {'track': 'tracks', 'album': 'albums', 'artist': 'artists', 'playlist': 'playlists'}.get(type)
        if key is None:
            return {}
        return {key: {"items": [self._item(q, type, i) for i in range(n)]}}


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def right_text(app):
    return str(getattr(app.right_panel, "_Static__content", "") or "")

def has_grid(app):
    return len(app.query("#search_grid")) > 0

def gp(app, pk):
    return app._grid_panel_table(pk)

def prows(app, pk):
    t = gp(app, pk)
    return int(getattr(t, "row_count", 0) or 0) if t is not None else 0

def isheart(t, r):
    try:
        return t.get_cell_at(Coordinate(r, 0)).plain == HEART
    except Exception:
        return None

async def poll(fn, timeout=5.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.01)
    try: return fn()
    except Exception: return None

async def pump(pilot, n=6):
    for _ in range(n):
        try: await pilot.pause()
        except Exception: await asyncio.sleep(0.01)

def _any_rows(app):
    return any(prows(app, pk) for pk in ("songs", "artists", "albums", "playlists"))

async def do_search(app, pilot, q):
    app._dispatch_search(q)
    await poll(lambda: has_grid(app), timeout=5.0)
    # The initial panel focus is deferred (call_after_refresh); wait for it to
    # actually land on a panel table before returning, so focus assertions are
    # deterministic rather than pump-count dependent.
    await poll(lambda: (getattr(app.focused, "id", "") or "").endswith("_table")
               or not _any_rows(app), timeout=5.0)
    await pump(pilot, 3)


async def test_grid_four_panels_and_counts():
    fake = GridFake({"track": 20, "album": 20, "artist": 20, "playlist": 20})
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "metallica")
        check("four panels present", all(gp(app, pk) is not None for pk in
              ("songs", "artists", "albums", "playlists")))
        check("songs capped at 10", prows(app, "songs") == 10, f"n={prows(app,'songs')}")
        check("artists capped at 3", prows(app, "artists") == 3, f"n={prows(app,'artists')}")
        check("albums capped at 5", prows(app, "albums") == 5, f"n={prows(app,'albums')}")
        check("playlists capped at 2", prows(app, "playlists") == 2, f"n={prows(app,'playlists')}")
        check("initial focus is Songs", app.focused is gp(app, "songs"))


async def test_initial_focus_first_nonempty_when_songs_empty():
    fake = GridFake({"track": 0, "album": 4, "artist": 2, "playlist": 1})
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "q")
        check("songs empty", prows(app, "songs") == 0)
        check("focus falls to first non-empty (artists)", app.focused is gp(app, "artists"),
              f"focused={getattr(app.focused,'id',None)}")


async def test_several_empty_panels_render():
    fake = GridFake({"track": 3, "album": 0, "artist": 0, "playlist": 0})
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "q")
        check("all four panels still mounted even when 3 are empty",
              all(gp(app, pk) is not None for pk in ("songs", "artists", "albums", "playlists")))
        check("focus on the only non-empty panel (songs)", app.focused is gp(app, "songs"))


async def test_all_empty_shows_no_results():
    fake = GridFake({"track": 0, "album": 0, "artist": 0, "playlist": 0})
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("q")
        await poll(lambda: "No results" in right_text(app), timeout=5.0)
        check("all empty -> No results", "No results" in right_text(app), f"txt={right_text(app)!r}")
        check("no grid mounted on no-results", not has_grid(app))


async def test_navigation_between_and_within_panels():
    fake = GridFake({"track": 5, "album": 4, "artist": 3, "playlist": 2})
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "q")
        await pilot.press("right"); await pump(pilot, 3)
        check("Right: Songs -> Artists", app.focused is gp(app, "artists"))
        await pilot.press("ctrl+down"); await pump(pilot, 3)
        check("Ctrl+Down: Artists -> Playlists", app.focused is gp(app, "playlists"))
        await pilot.press("left"); await pump(pilot, 3)
        check("Left: Playlists -> Albums", app.focused is gp(app, "albums"))
        await pilot.press("ctrl+up"); await pump(pilot, 3)
        check("Ctrl+Up: Albums -> Songs", app.focused is gp(app, "songs"))
        # within a panel: Down moves the row cursor, never leaves the panel
        await pilot.press("down"); await pump(pilot, 2)
        songs = gp(app, "songs")
        check("Down moves the row cursor inside Songs", getattr(songs, "cursor_row", 0) == 1
              and app.focused is songs, f"cur={getattr(songs,'cursor_row',None)}")
        # Tab cycles forward through the panels
        gp(app, "songs").focus(); await pump(pilot, 2)
        await pilot.press("tab"); await pump(pilot, 3)
        check("Tab cycles Songs -> Artists", app.focused is gp(app, "artists"))


async def test_left_from_left_edge_reaches_menu():
    fake = GridFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "q")
        check("start on Songs", app.focused is gp(app, "songs"))
        await pilot.press("left"); await pump(pilot, 3)
        check("Left from Songs returns to the main menu",
              getattr(app.focused, "id", "") == "left_col", f"focused={getattr(app.focused,'id',None)}")


async def test_enter_routing_per_panel():
    fake = GridFake({"track": 3, "album": 3, "artist": 3, "playlist": 3})
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        calls = []
        app._play_row = lambda row, table: calls.append(("play", table.row_to_id.get(row)))
        app._open_artist_table = lambda obj, **k: calls.append(("artist", obj.get("id")))
        app._open_album_table = lambda obj, **k: calls.append(("album", obj.get("id")))
        app._open_playlist_table = lambda obj, **k: calls.append(("playlist", obj.get("id")))
        await do_search(app, pilot, "q")

        async def enter_on(pk):
            calls.clear()
            gp(app, pk).focus(); await pump(pilot, 2)
            gp(app, pk).move_cursor(row=0, column=0)
            await pilot.press("enter"); await pump(pilot, 3)

        await enter_on("songs")
        check("Enter on a Song plays it", calls and calls[0][0] == "play", f"calls={calls}")
        await enter_on("artists")
        check("Enter on an Artist opens the artist", calls and calls[0][0] == "artist", f"calls={calls}")
        await enter_on("albums")
        check("Enter on an Album opens the album", calls and calls[0][0] == "album", f"calls={calls}")
        await enter_on("playlists")
        check("Enter on a Playlist opens the playlist", calls and calls[0][0] == "playlist", f"calls={calls}")


async def test_songs_hearts_and_favourite_toggle():
    fake = GridFake({"track": 4, "album": 0, "artist": 0, "playlist": 0})
    fake.saved = {"q-track0", "q-track2"}
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "q")
        songs = gp(app, "songs")
        await poll(lambda: getattr(gp(app, "songs"), "_liked_map", {}).get(0) is True)
        await pump(pilot, 3)
        songs = gp(app, "songs")
        check("saved song 0 shows a heart", isheart(songs, 0) is True, f"cell={isheart(songs,0)}")
        check("unsaved song 1 blank", isheart(songs, 1) is False)
        check("saved song 2 shows a heart", isheart(songs, 2) is True)
        # toggle favourite on an unsaved song
        songs.focus(); await pump(pilot, 2)
        songs.move_cursor(row=1, column=0)
        await pilot.press("f")
        await poll(lambda: isheart(gp(app, "songs"), 1) is True)
        check("favourite toggle paints the heart in Songs", isheart(gp(app, "songs"), 1) is True)
        check("favourite toggle saved the track upstream", "q-track1" in fake.saved)


async def test_prefix_keeps_single_table_not_grid():
    fake = GridFake({"track": 20})
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("/TRK metallica")
        t = await poll(lambda: next((w for w in app.query(DataTable)
                                     if getattr(w, "id", "") == "search_table"), None), timeout=5.0)
        await pump(pilot, 4)
        check("prefix search renders the single search_table", t is not None)
        check("prefix search does NOT render the grid", not has_grid(app))


async def test_escape_clears_grid_to_welcome():
    fake = GridFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "q")
        await pilot.press("escape"); await pump(pilot, 4)
        check("Escape removes the grid", not has_grid(app))
        check("Escape returns to Welcome", getattr(app, "_welcome_on", False))
        check("Escape invalidates the view token", getattr(app, "_right_view", "x") is None)


async def test_ctrl_r_repeats_search_no_duplicate_ids():
    fake = GridFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        # focus the search input so Ctrl+R repeats this query
        app.search_input.value = "q"
        app.search_input.focus(); await pump(pilot, 2)
        await do_search(app, pilot, "q")
        n_before = len(app.search_calls) if hasattr(app, "search_calls") else fake.search_calls[:]
        await pilot.press("ctrl+r")
        await pump(pilot, 8)
        check("exactly one #search_grid after Ctrl+R", len(app.query("#search_grid")) == 1,
              f"n={len(app.query('#search_grid'))}")
        for pk in ("songs", "artists", "albums", "playlists"):
            check(f"exactly one #{pk}_table after Ctrl+R",
                  len(app.query(f"#{pk}_table")) == 1, f"n={len(app.query(f'#{pk}_table'))}")


async def test_fast_A_then_B_stale_dropped():
    fake = GridFake()
    fake.gate = threading.Event()
    fake.entered = threading.Event()
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._dispatch_search("AAAA")            # A blocks in _s
        await poll(lambda: fake.entered.is_set(), timeout=5.0)
        fake.entered = threading.Event()
        app._dispatch_search("BBBB")            # B supersedes
        await poll(lambda: fake.entered.is_set(), timeout=5.0)
        fake.gate.set()                         # release both
        await poll(lambda: has_grid(app), timeout=5.0)
        await pump(pilot, 8)
        songs = gp(app, "songs")
        ids = list(getattr(songs, "row_to_id", {}).values())
        check("newer search B is the one rendered", ids and all(str(i).startswith("BBBB") for i in ids),
              f"ids={ids[:3]}")
        check("exactly one grid (A did not stack a second)", len(app.query("#search_grid")) == 1)


async def test_no_hscroll_nowplaying_visible():
    fake = GridFake({"track": 20, "album": 20, "artist": 20, "playlist": 20})
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "metallica")
        for pk in ("songs", "artists", "albums", "playlists"):
            t = gp(app, pk)
            check(f"{pk} panel has no horizontal scrollbar",
                  bool(getattr(t, "show_horizontal_scrollbar", False)) is False)
        nw = app.query_one("#now_wrap")
        r = nw.region
        check("Now Playing visible under the grid", r.height > 0 and r.y + r.height <= 30,
              f"y={r.y} h={r.height}")


async def test_resize_reflows_and_preserves_state():
    fake = GridFake({"track": 6, "album": 4, "artist": 3, "playlist": 2})
    app = TApp(fake)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await do_search(app, pilot, "q")
        grid = app.query_one("#search_grid")
        check("wide terminal: 2x2 (not stacked)", not grid.has_class("-stacked"))
        counts_before = {pk: prows(app, pk) for pk in ("songs", "artists", "albums", "playlists")}
        # focus Albums and select row 1, then shrink
        gp(app, "albums").focus(); await pump(pilot, 2)
        gp(app, "albums").move_cursor(row=1, column=0); await pump(pilot, 2)
        await pilot.resize_terminal(70, 30); await pump(pilot, 5)
        check("narrow terminal: stacked", app.query_one("#search_grid").has_class("-stacked"))
        check("rows preserved after shrink",
              {pk: prows(app, pk) for pk in counts_before} == counts_before)
        check("no re-query on resize (still 4 search calls)", len(fake.search_calls) == 4,
              f"calls={fake.search_calls}")
        await pilot.resize_terminal(140, 40); await pump(pilot, 5)
        check("back to 2x2 after growing", not app.query_one("#search_grid").has_class("-stacked"))
        check("rows still preserved after grow",
              {pk: prows(app, pk) for pk in counts_before} == counts_before)
        check("focused panel preserved (Albums)", app.focused is gp(app, "albums"),
              f"focused={getattr(app.focused,'id',None)}")
        check("selection preserved (Albums row 1)", getattr(gp(app, "albums"), "cursor_row", None) == 1)


ALL = [test_grid_four_panels_and_counts, test_initial_focus_first_nonempty_when_songs_empty,
       test_several_empty_panels_render, test_all_empty_shows_no_results,
       test_navigation_between_and_within_panels, test_left_from_left_edge_reaches_menu,
       test_enter_routing_per_panel, test_songs_hearts_and_favourite_toggle,
       test_prefix_keeps_single_table_not_grid, test_escape_clears_grid_to_welcome,
       test_ctrl_r_repeats_search_no_duplicate_ids, test_fast_A_then_B_stale_dropped,
       test_no_hscroll_nowplaying_visible, test_resize_reflows_and_preserves_state]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (search grid) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
