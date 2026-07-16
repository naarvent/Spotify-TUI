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


ALL = [test_repaint_preserves_cursor_and_scroll, test_toggle_like_keeps_position]

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
