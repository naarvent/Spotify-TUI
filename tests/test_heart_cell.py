"""A heart update has to reach the cell, not just the model.

`_set_heart_icon` passed integer column *indices* to `DataTable.update_cell`,
which wants a column *key*. Both the call and its fallback raised, and both were
swallowed by a bare `except`, so the function silently did nothing: the model
said the track was liked and the row still showed no heart.

Everything that fills a heart in place went through it — the per-batch hearts of
a loading playlist, the `f` favourite toggle, and the liked-column
revalidation — and none of them repainted. It went unnoticed because the checks
looked at `_liked_map` (the model) rather than at the rendered cell, and because
a later full repaint eventually painted the right thing.

Run standalone:  python tests/test_heart_cell.py
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
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def rows_n(n):
    return [{"id": f"i{i}", "uri": f"spotify:track:i{i}", "title": f"track {i}",
             "artist": "artist", "album": "album", "dur": "3:21", "added": "2024"}
            for i in range(n)]

def heart_at(table, row):
    """The heart column's rendered text for a row."""
    try:
        return str(getattr(table.get_cell_at((row, 0)), "plain",
                           table.get_cell_at((row, 0))))
    except Exception as e:
        return f"<err {e}>"

def screen_text(app):
    return "\n".join("".join(seg.text for seg in strip)
                     for strip in app.screen._compositor.render_strips(app.screen.size))


async def test_setting_a_heart_paints_the_cell():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(10), [False] * 10, profile="playlist")
        for _ in range(3):
            await pilot.pause()
        check("no heart to start with", heart_at(t, 0) == "", f"cell={heart_at(t, 0)!r}")

        app._set_heart_icon(t, 0, True)
        for _ in range(3):
            await pilot.pause()
        check("setting a heart paints it in the cell", "❤" in heart_at(t, 0),
              f"cell={heart_at(t, 0)!r}")
        check("and it is on screen", "❤" in screen_text(app))
        check("other rows are untouched", heart_at(t, 1) == "", f"cell={heart_at(t, 1)!r}")

        app._set_heart_icon(t, 0, False)
        for _ in range(3):
            await pilot.pause()
        check("clearing it empties the cell again", heart_at(t, 0) == "",
              f"cell={heart_at(t, 0)!r}")


async def test_hearts_paint_on_a_table_whose_columns_were_dropped():
    """The heart is column 0 whatever the fit dropped, but check it holds."""
    app = TApp(Fake())
    async with app.run_test(size=(95, 25)) as pilot:   # narrow: Added/Album drop
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(10), [False] * 10, profile="playlist")
        for _ in range(4):
            await pilot.pause()
        app._set_heart_icon(t, 3, True)
        for _ in range(3):
            await pilot.pause()
        check("the heart still lands in column 0 on a narrow table",
              "❤" in heart_at(t, 3), f"cell={heart_at(t, 3)!r}")


async def test_per_batch_hearts_reach_the_screen():
    """The playlist loader's incremental hearts, end to end."""
    class PlFake(Fake):
        def __init__(self): self.batches = []
        def playlist_items(self, pid, limit=100, offset=0, fields=None):
            end = min(250, offset + limit)
            return {"items": [{"added_at": "2024-01-01T00:00:00Z", "track": {
                        "id": f"t{i}", "uri": f"spotify:track:t{i}", "type": "track",
                        "name": f"song {i}", "duration_ms": 1000,
                        "artists": [{"name": "a"}], "album": {"name": "al"}}}
                    for i in range(offset, end)], "total": 250}
        def check_saved_tracks(self, ids, on_batch=None):
            out = []
            for i in range(0, len(ids), 50):
                vals = [True] * len(ids[i:i + 50])
                out.extend(vals)
                if on_batch is not None:
                    on_batch(i, vals)
            return out

    app = TApp(PlFake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_playlist_table({"id": "pl1", "name": "L", "uri": "spotify:playlist:pl1"})
        loop = asyncio.get_event_loop(); end = loop.time() + 8
        t = None
        while loop.time() < end:
            from textual.widgets import DataTable
            found = [w for w in app.query(DataTable) if getattr(w, "id", "") == "tracks_table"]
            t = found[0] if found else None
            if t is not None and "❤" in heart_at(t, 0):
                break
            await asyncio.sleep(0.02)
        check("a loaded playlist ends up with painted hearts",
              t is not None and "❤" in heart_at(t, 0),
              f"cell={heart_at(t, 0) if t is not None else '<no table>'!r}")
        check("and they are on screen", "❤" in screen_text(app))


async def test_favourite_toggle_repaints_the_row():
    """Pressing `f` went through the same broken path: the model flipped and the
    row kept showing the old heart."""
    class FavFake(Fake):
        def __init__(self): self.saved = []
        def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)
        def save_tracks(self, ids): self.saved.append(tuple(ids)); return True
        def remove_tracks(self, ids): return True

    fake = FavFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(6), [False] * 6, profile="playlist")
        for _ in range(3):
            await pilot.pause()
        t.focus()
        t.move_cursor(row=2, animate=False)
        for _ in range(3):
            await pilot.pause()
        check("the row starts with no heart", heart_at(t, 2) == "", f"cell={heart_at(t, 2)!r}")

        app.action_toggle_favorite()
        loop = asyncio.get_event_loop(); end = loop.time() + 5
        while loop.time() < end and "❤" not in heart_at(t, 2):
            await asyncio.sleep(0.02)
        check("liking the row paints its heart", "❤" in heart_at(t, 2),
              f"cell={heart_at(t, 2)!r} saved={fake.saved}")
        check("the model agrees", bool(getattr(t, "_liked_map", {}).get(2)),
              f"map={getattr(t, '_liked_map', {}).get(2)}")
        check("neighbouring rows are untouched", heart_at(t, 1) == "" and heart_at(t, 3) == "",
              f"rows={[heart_at(t, 1), heart_at(t, 3)]}")


async def test_liked_column_revalidation_paints():
    """The podcast view renders its rows with no hearts and relies entirely on
    `_revalidate_liked_column` to fill them, so a silent no-op left every saved
    episode looking unsaved. Unlike the `f` toggle — which happens to be
    followed by a full repaint from `_sync_liked_state_across_tables` and so was
    never affected — this path only sets the cells."""
    class RevalFake(Fake):
        def check_saved_tracks(self, ids, on_batch=None): return [True] * len(ids)

    app = TApp(RevalFake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]Podcast[/b]", rows_n(5), None, profile="album")
        for _ in range(3):
            await pilot.pause()
        check("rows render unliked", heart_at(t, 0) == "", f"cell={heart_at(t, 0)!r}")

        app._revalidate_liked_column(t, max_rows=10, force=True)
        for _ in range(4):
            await pilot.pause()
        check("revalidation paints the hearts it found",
              "❤" in heart_at(t, 0) and "❤" in heart_at(t, 4),
              f"cells={[heart_at(t, i) for i in range(5)]}")


ALL = [test_setting_a_heart_paints_the_cell,
       test_hearts_paint_on_a_table_whose_columns_were_dropped,
       test_per_batch_hearts_reach_the_screen,
       test_favourite_toggle_repaints_the_row,
       test_liked_column_revalidation_paints]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (heart cell) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
