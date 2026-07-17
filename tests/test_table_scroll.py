"""Regression tests: a full table repaint must preserve the cursor row and the
vertical scroll position. Previously `_repaint_rows_from_model` reset both to
the top (cursor_row has no setter; scroll_to_row does not exist), so loading
likes, toggling a like, or the playing-row highlight moving would yank the user
back to the start of a playlist.

Run standalone:  python tests/test_table_scroll.py
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

def pause_intervals(app):
    for a in ("_now_sync_interval","_now_tick_interval","_now_interval","_devices_interval","_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def rows_n(n):
    return [{"id": f"id{i}", "uri": f"spotify:track:id{i}", "title": f"song {i}",
             "artist": "a", "album": "al", "dur": "0:00", "added": ""} for i in range(n)]

def scroll_y(table):
    return int(getattr(getattr(table, "scroll_offset", None), "y", 0) or 0)


def test_repaint_preserves_cursor_and_scroll():
    async def body():
        app = TApp(Fake())
        async with app.run_test(size=(120, 20)) as pilot:
            await pilot.pause(); pause_intervals(app)
            rows = rows_n(40)
            table = app._render_tracks_table("[b]P[/b]", rows, [False]*40,
                                             context_uris=[r["uri"] for r in rows])
            await pilot.pause()
            table.move_cursor(row=25, animate=False)
            for _ in range(5): await pilot.pause()
            before = (table.cursor_row, scroll_y(table))
            # Simulate the "likes loaded" repaint.
            table._liked_map = {i: True for i in range(40)}
            app._repaint_rows_from_model(table)
            for _ in range(5): await pilot.pause()
            after = (table.cursor_row, scroll_y(table))
            return before, after
    before, after = asyncio.run(body())
    check("cursor row preserved across repaint", after[0] == before[0] == 25,
          f"before={before} after={after}")
    check("scroll position preserved across repaint (not yanked to top)",
          after[1] == before[1] and after[1] > 0, f"before={before} after={after}")


def test_toggle_like_keeps_position():
    async def body():
        app = TApp(Fake())
        async with app.run_test(size=(120, 20)) as pilot:
            await pilot.pause(); pause_intervals(app)
            rows = rows_n(40)
            table = app._render_tracks_table("[b]P[/b]", rows, [False]*40,
                                             context_uris=[r["uri"] for r in rows])
            await pilot.pause()
            table.move_cursor(row=30, animate=False)
            for _ in range(5): await pilot.pause()
            before = (table.cursor_row, scroll_y(table))
            # Toggling a like repaints via _sync_liked_state_across_tables.
            app._sync_liked_state_across_tables("id30", True)
            for _ in range(5): await pilot.pause()
            after = (table.cursor_row, scroll_y(table))
            return before, after
    before, after = asyncio.run(body())
    check("like toggle keeps cursor row", after[0] == before[0] == 30, f"before={before} after={after}")
    check("like toggle keeps scroll position", after[1] == before[1] and after[1] > 0,
          f"before={before} after={after}")


def _col_labels(table):
    return [str(getattr(c, "label", "")) for c in table.ordered_columns]

def _col_widths(table):
    out = []
    for c in table.ordered_columns:
        try: out.append(int(getattr(c, "width", 0) or 0))
        except Exception: out.append(0)
    return out


def test_playlist_columns_drop_source():
    async def body():
        app = TApp(Fake())
        async with app.run_test(size=(120, 20)) as pilot:
            await pilot.pause(); pause_intervals(app)
            rows = rows_n(5)
            pt = app._render_tracks_table("[b]PL[/b]", rows, [False] * 5,
                                          context_uris=[r["uri"] for r in rows], show_source=False)
            await pilot.pause()
            pl_labels, pl_widths = _col_labels(pt), _col_widths(pt)
            # a repaint (e.g. likes loaded) must keep the playlist layout
            pt._liked_map = {i: True for i in range(5)}
            app._repaint_rows_from_model(pt)
            await pilot.pause()
            rep_labels = _col_labels(pt)
            # a non-playlist view keeps Source (not removed globally)
            app.action_escape_to_menu(); await pilot.pause()
            gt = app._render_tracks_table("[b]G[/b]", rows, [False] * 5,
                                          context_uris=[r["uri"] for r in rows])
            await pilot.pause()
            gen_labels = _col_labels(gt)
            return pl_labels, pl_widths, rep_labels, gen_labels
    pl, pw, rep, gen = asyncio.run(body())
    check("playlist table drops the Source column",
          pl == ["♥", "Title", "Artist", "Album", "Duration", "Added"], f"cols={pl}")
    check("playlist layout survives a repaint (still no Source)", rep == pl, f"cols={rep}")
    check("other views keep Source (not removed globally)", "Source" in gen, f"cols={gen}")
    # heart/Duration/Added compact & fixed; Title/Artist/Album flexible (share space)
    if len(pw) == 6:
        heart, title, artist, album, dur, added = pw
        check("heart column is a small fixed width", heart <= 4, f"w={pw}")
        check("Duration/Added stay compact", dur <= 11 and added <= 14, f"w={pw}")
        check("Title/Artist/Album are flexible (wider than Duration)",
              min(title, artist, album) > dur, f"w={pw}")


def test_playlist_long_values_do_not_break():
    async def body():
        app = TApp(Fake())
        async with app.run_test(size=(120, 20)) as pilot:
            await pilot.pause(); pause_intervals(app)
            rows = [{"id": "id0", "uri": "spotify:track:id0",
                     "title": "A very very very long track title " * 4,
                     "artist": "An extremely long artist name " * 3,
                     "album": "A ridiculously long album name " * 3,
                     "dur": "1:23:45", "added": "2024-01-01"}]
            t = app._render_tracks_table("[b]PL[/b]", rows, [False], show_source=False,
                                         context_uris=["spotify:track:id0"])
            await pilot.pause()
            return _col_labels(t), len(getattr(t, "_model_rows", []))
    labels, n = body_run(body)
    check("long values keep the 6-column playlist layout", len(labels) == 6 and "Source" not in labels,
          f"cols={labels}")
    check("row still added with long values", n == 1, f"n={n}")


def body_run(body):
    return asyncio.run(body())


ALL = [test_repaint_preserves_cursor_and_scroll, test_toggle_like_keeps_position,
       test_playlist_columns_drop_source, test_playlist_long_values_do_not_break]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (table scroll) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
