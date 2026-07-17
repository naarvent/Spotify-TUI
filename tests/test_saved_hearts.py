"""Unified saved/liked/followed indicator = a heart.

Every table whose first column marks an item as saved (liked track, saved
album, followed artist, saved show, saved episode, saved-state search result)
must render a heart when saved and a blank cell when not — no floppy-disk
glyph, no 'S' header. The per-type SEMANTICS are unchanged (each type keeps its
own endpoint / Type label); only the glyph is unified.

Run standalone:  python tests/test_saved_hearts.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from spt_tui.constants import GLYPHS
from textual.widgets import DataTable
from textual.coordinate import Coordinate

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

HEART = "❤"
FLOPPY = "💾"


class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def check_saved_tracks(self, ids): return [False] * len(ids)

class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def headers(table):
    return [str(getattr(c, "label", "")) for c in table.ordered_columns]

def cell0(table, row):
    try:
        c = table.get_cell_at(Coordinate(row, 0))
        return getattr(c, "plain", str(c))
    except Exception:
        return None


async def test_no_floppy_and_heart_header():
    app = TApp(Fake())
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause(); pause_intervals(app)
        check("floppy glyph removed from GLYPHS", "disk" not in GLYPHS)
        # One row of each saved type, all saved=True.
        rows = [
            {"type": "track", "id": "t", "uri": "u", "title": "T", "artist": "a", "album": "al", "dur": "0:00", "raw": {}, "saved": True},
            {"type": "album", "id": "al", "uri": "u", "title": "Alb", "artist": "Ar", "album": "", "dur": "", "raw": {}, "saved": True},
            {"type": "artist", "id": "ar", "uri": "u", "title": "Art", "artist": "", "album": "", "dur": "", "raw": {}, "saved": True},
            {"type": "podcast", "id": "sh", "uri": "u", "title": "Show", "artist": "Pub", "album": "", "dur": "", "raw": {}, "saved": True},
            {"type": "episode", "id": "ep", "uri": "u", "title": "Ep", "artist": "Show", "album": "Show", "dur": "0:00", "raw": {}, "saved": True},
        ]
        t = app._render_search_table("[b]All saved[/b]", rows, check_saved=False, layout="full")
        await pilot.pause()
        h = headers(t)
        check("first header is a heart, not 'S'", h[0] == "♥", f"headers={h}")
        check("no 'S' header anywhere", "S" not in h, f"headers={h}")
        for i, r in enumerate(rows):
            check(f"saved {r['type']} shows heart", cell0(t, i) == HEART, f"cell={cell0(t,i)!r}")
        # No floppy in any first-column cell.
        any_floppy = any((cell0(t, i) or "") == FLOPPY for i in range(len(rows)))
        check("no floppy glyph in any saved cell", not any_floppy)
        # Type labels stay distinct (semantics preserved visually).
        types = [str(t.get_cell_at(Coordinate(i, 1))) for i in range(len(rows))]
        check("per-type labels preserved (TRK/ALB/ART/PDC/EPS)",
              types == ["TRK", "ALB", "ART", "PDC", "EPS"], f"types={types}")


async def test_unsaved_rows_blank():
    app = TApp(Fake())
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause(); pause_intervals(app)
        rows = [
            {"type": "album", "id": "a1", "uri": "u", "title": "Saved", "artist": "Ar", "album": "", "dur": "", "raw": {}, "saved": True},
            {"type": "album", "id": "a2", "uri": "u", "title": "NotSaved", "artist": "Ar", "album": "", "dur": "", "raw": {}, "saved": False},
        ]
        t = app._render_search_table("[b]Albums[/b]", rows, check_saved=False, layout="albums")
        await pilot.pause()
        check("saved album -> heart", cell0(t, 0) == HEART, f"cell={cell0(t,0)!r}")
        check("unsaved album -> blank", (cell0(t, 1) or "") == "", f"cell={cell0(t,1)!r}")
        check("albums header is a heart", headers(t)[0] == "♥", f"headers={headers(t)}")


async def test_track_tables_still_heart():
    """The tracks-table hearts were already hearts; confirm they still are and
    match the same glyph the search table now uses."""
    app = TApp(Fake())
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause(); pause_intervals(app)
        rows = [{"id": "t0", "uri": "spotify:track:t0", "title": "A", "artist": "x", "album": "al", "dur": "0:00"},
                {"id": "t1", "uri": "spotify:track:t1", "title": "B", "artist": "y", "album": "al", "dur": "0:00"}]
        t = app._render_tracks_table("[b]Tracks[/b]", rows, [True, False], profile="playlist")
        await pilot.pause()
        check("tracks header is a heart", headers(t)[0] == "♥", f"headers={headers(t)}")
        check("liked track -> heart", cell0(t, 0) == HEART, f"cell={cell0(t,0)!r}")
        check("unliked track -> blank", (cell0(t, 1) or "") == "", f"cell={cell0(t,1)!r}")


ALL = [test_no_floppy_and_heart_header, test_unsaved_rows_blank, test_track_tables_still_heart]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (saved hearts) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
