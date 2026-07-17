"""Album track liked-state rendering.

Reproduces and pins the fix for hearts on album tracks:
  * opening an album resolves each track's liked state in the BACKGROUND
    (never on the UI thread) and paints a heart on the rows that are saved;
  * the album table appears immediately (blank hearts) before the liked
    lookup returns — the lookup is gated with a threading.Event;
  * a stale album (A) whose liked lookup finishes after the user switched to
    album B can NOT paint its hearts over B (view-token guard);
  * liked state is mapped by track id, so a track without an id stays blank
    and one bad id does not blank the whole album;
  * exactly ONE batched liked lookup per open (no per-row queries);
  * a repaint preserves hearts, cursor and scroll;
  * toggling a favourite updates that row's heart;
  * reopening the album reflects updated saved state.

Synchronisation is Event/poll based (no fixed sleep as the sole mechanism).
Run standalone:  python tests/test_album_likes.py
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


def _album(album_id, track_ids):
    """Build an album_tracks page. A track id of None models a local/unavailable
    track (no id)."""
    items = []
    for i, tid in enumerate(track_ids):
        items.append({
            "id": tid,
            "uri": f"spotify:track:{tid}" if tid else f"spotify:local:{album_id}:{i}",
            "name": f"{album_id}-track-{i}",
            "artists": [{"name": "Artist"}],
            "duration_ms": 1000,
        })
    return {"items": items}


class AlbumInner:
    def __init__(self, o): self.o = o
    def album_tracks(self, album_id, limit=50, offset=0):
        return self.o.albums.get(album_id, {"items": []})


class AlbumFake:
    def __init__(self):
        self.inner = AlbumInner(self)
        self.albums = {}            # album_id -> album_tracks page
        self.saved_ids = set()      # ids currently "liked"
        self.check_gate = None      # optional threading.Event blocking the lookup
        self.check_entered = None   # optional Event set when a lookup begins
        self.check_calls = []       # list of id-lists, one per batched lookup
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self.inner
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def check_saved_tracks(self, ids):
        ids = list(ids)
        self.check_calls.append(ids)
        if self.check_entered is not None:
            self.check_entered.set()
        g = self.check_gate
        if g is not None:
            g.wait(10)
        return [i in self.saved_ids for i in ids]
    # favourite-toggle support
    def save_tracks(self, ids):
        for i in ids: self.saved_ids.add(i)
        return True
    def remove_tracks(self, ids):
        for i in ids: self.saved_ids.discard(i)
        return True


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


def atbl(app):
    for w in app.query(DataTable):
        if getattr(w, "id", "") == "tracks_table":
            return w
    return None


def heart_at(table, row):
    try:
        cell = table.get_cell_at(Coordinate(row, 0))
        return getattr(cell, "plain", str(cell))
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


async def pump(pilot, n=20):
    for _ in range(n):
        try: await pilot.pause()
        except Exception: await asyncio.sleep(0.01)


HEART = "❤"


async def test_hearts_reflect_saved_state():
    fake = AlbumFake()
    fake.albums["A"] = _album("A", ["t0", "t1", "t2"])
    fake.saved_ids = {"t0", "t2"}
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_album_table({"id": "A", "name": "Album A"})
        tbl = await poll(lambda: atbl(app), timeout=5.0)
        check("album table appears", tbl is not None)
        # wait for background liked resolution
        ok = await poll(lambda: getattr(atbl(app), "_liked_map", {}).get(0) is True, timeout=5.0)
        tbl = atbl(app)
        lm = getattr(tbl, "_liked_map", {})
        check("saved rows liked in model", lm.get(0) is True and lm.get(2) is True and lm.get(1) is False,
              f"lm={lm}")
        check("saved row 0 shows heart", heart_at(tbl, 0) == HEART, f"cell={heart_at(tbl,0)!r}")
        check("unsaved row 1 shows blank", (heart_at(tbl, 1) or "") == "", f"cell={heart_at(tbl,1)!r}")
        check("saved row 2 shows heart", heart_at(tbl, 2) == HEART, f"cell={heart_at(tbl,2)!r}")
        check("exactly one batched liked lookup", len(fake.check_calls) == 1, f"calls={fake.check_calls}")
        check("lookup batched all ids at once", fake.check_calls and len(fake.check_calls[0]) == 3,
              f"calls={fake.check_calls}")


async def test_album_appears_before_lookup_returns():
    fake = AlbumFake()
    fake.albums["A"] = _album("A", ["t0", "t1"])
    fake.saved_ids = {"t0"}
    fake.check_gate = threading.Event()      # block the liked lookup
    fake.check_entered = threading.Event()
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_album_table({"id": "A", "name": "Album A"})
        tbl = await poll(lambda: atbl(app), timeout=5.0)
        check("album visible while liked lookup is blocked", tbl is not None)
        await poll(lambda: fake.check_entered.is_set(), timeout=5.0)
        check("hearts blank before lookup returns",
              (heart_at(tbl, 0) or "") == "" and getattr(tbl, "_liked_map", {}).get(0) is False,
              f"cell={heart_at(tbl,0)!r} lm={getattr(tbl,'_liked_map',{})}")
        fake.check_gate.set()
        ok = await poll(lambda: getattr(atbl(app), "_liked_map", {}).get(0) is True, timeout=5.0)
        check("heart fills in once lookup returns", heart_at(atbl(app), 0) == HEART,
              f"cell={heart_at(atbl(app),0)!r}")


async def test_stale_album_does_not_overwrite_newer():
    fake = AlbumFake()
    fake.albums["A"] = _album("A", ["a0", "a1"])   # both saved
    fake.albums["B"] = _album("B", ["b0", "b1"])   # neither saved
    fake.saved_ids = {"a0", "a1"}
    fake.check_gate = threading.Event()            # block BOTH lookups
    fake.check_entered = threading.Event()
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_album_table({"id": "A", "name": "Album A"})
        await poll(lambda: fake.check_entered.is_set(), timeout=5.0)   # A is inside the lookup
        fake.check_entered = threading.Event()
        app._open_album_table({"id": "B", "name": "Album B"})          # switch to B
        tbl = await poll(lambda: (atbl(app) if getattr(atbl(app), "_model_rows", None)
                                  and atbl(app)._model_rows[0].get("id") == "b0" else None), timeout=5.0)
        check("switched to album B", tbl is not None)
        await poll(lambda: fake.check_entered.is_set(), timeout=5.0)   # B is inside the lookup too
        fake.check_gate.set()                                          # release A and B
        await pump(pilot, 40)
        cur = atbl(app)
        lm = getattr(cur, "_liked_map", {})
        check("visible table is still B", cur is not None and cur._model_rows[0].get("id") == "b0",
              f"first={cur._model_rows[0].get('id') if cur else None}")
        check("stale album A did NOT paint saved hearts over B",
              lm.get(0) is False and lm.get(1) is False, f"lm={lm}")
        check("B rows still blank", (heart_at(cur, 0) or "") == "", f"cell={heart_at(cur,0)!r}")


async def test_track_without_id_stays_blank():
    fake = AlbumFake()
    fake.albums["A"] = _album("A", ["t0", None, "t2"])   # middle track has no id
    fake.saved_ids = {"t0", "t2"}
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_album_table({"id": "A", "name": "Album A"})
        await poll(lambda: getattr(atbl(app), "_liked_map", {}).get(0) is True, timeout=5.0)
        tbl = atbl(app)
        lm = getattr(tbl, "_liked_map", {})
        check("id-less track stays blank", lm.get(1) is False and (heart_at(tbl, 1) or "") == "",
              f"lm={lm} cell={heart_at(tbl,1)!r}")
        check("only real ids were looked up", fake.check_calls and None not in fake.check_calls[0],
              f"calls={fake.check_calls}")


async def test_repaint_preserves_hearts_and_cursor():
    fake = AlbumFake()
    fake.albums["A"] = _album("A", ["t0", "t1", "t2", "t3"])
    fake.saved_ids = {"t1", "t3"}
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_album_table({"id": "A", "name": "Album A"})
        await poll(lambda: getattr(atbl(app), "_liked_map", {}).get(1) is True, timeout=5.0)
        tbl = atbl(app)
        try: tbl.move_cursor(row=2, column=0, animate=False)
        except Exception: pass
        app._repaint_rows_from_model(tbl)
        await pump(pilot, 5)
        check("hearts preserved after repaint",
              heart_at(tbl, 1) == HEART and heart_at(tbl, 3) == HEART and (heart_at(tbl, 0) or "") == "",
              f"r0={heart_at(tbl,0)!r} r1={heart_at(tbl,1)!r}")
        try: cur_row = int(getattr(tbl.cursor_coordinate, "row", -1))
        except Exception: cur_row = -1
        check("cursor row preserved after repaint", cur_row == 2, f"cur_row={cur_row}")


async def test_toggle_updates_heart():
    fake = AlbumFake()
    fake.albums["A"] = _album("A", ["t0", "t1"])
    fake.saved_ids = set()      # nothing saved initially
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_album_table({"id": "A", "name": "Album A"})
        await poll(lambda: atbl(app) is not None and getattr(atbl(app), "_liked_map", None) is not None, timeout=5.0)
        tbl = atbl(app)
        check("row 0 blank before toggle", (heart_at(tbl, 0) or "") == "")
        app._toggle_track_favorite("t0", tbl, 0)
        ok = await poll(lambda: getattr(tbl, "_liked_map", {}).get(0) is True, timeout=5.0)
        check("toggle marks row liked in model", getattr(tbl, "_liked_map", {}).get(0) is True)
        check("toggle paints the heart", heart_at(tbl, 0) == HEART, f"cell={heart_at(tbl,0)!r}")
        check("toggle actually saved the track upstream", "t0" in fake.saved_ids)


async def test_reopen_reflects_updated_state():
    fake = AlbumFake()
    fake.albums["A"] = _album("A", ["t0", "t1"])
    fake.saved_ids = set()
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_album_table({"id": "A", "name": "Album A"})
        await poll(lambda: atbl(app) is not None and getattr(atbl(app), "_liked_map", None) is not None, timeout=5.0)
        check("initially blank", (heart_at(atbl(app), 0) or "") == "")
        fake.saved_ids = {"t0"}                       # user liked it elsewhere
        app._open_album_table({"id": "A", "name": "Album A"})
        ok = await poll(lambda: getattr(atbl(app), "_liked_map", {}).get(0) is True, timeout=5.0)
        check("reopen shows updated heart", heart_at(atbl(app), 0) == HEART, f"cell={heart_at(atbl(app),0)!r}")


ALL = [test_hearts_reflect_saved_state, test_album_appears_before_lookup_returns,
       test_stale_album_does_not_overwrite_newer, test_track_without_id_stays_blank,
       test_repaint_preserves_hearts_and_cursor, test_toggle_updates_heart,
       test_reopen_reflects_updated_state]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (album likes) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
