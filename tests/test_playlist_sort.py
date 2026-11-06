"""View-only sorting of the open playlist (`s` / `S`).

* the pure ordering rules (artist = first artist, case-insensitive, album and
  title as tie-breaks; rows with missing data never raise);
* the saved preference falls back to the playlist order when missing/invalid;
* in the app: sorting keeps row maps, likes and context URIs aligned, playing a
  sorted row uses its *playlist* position, Ctrl+D/queue resolve the row's URI,
  and pages streaming into an already-sorted view end up sorted.

Run standalone:  python tests/test_playlist_sort.py
"""
import os, sys, asyncio, tempfile, traceback

# Never touch the user's real spt_config.json when run standalone.
os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_sort_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config, playlist_sort
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def R(pos, title, artist, album="", dur=0, added=""):
    return {"_pos": pos, "uri": f"spotify:track:t{pos}", "title": title, "artist": artist,
            "_artist0": artist.split(", ")[0] if artist else "", "album": album,
            "_dur_ms": dur, "_added_at": added}


def test_pure_rules():
    rows = [R(0, "b", "beta", "x"), R(1, "a", "Alpha", "z"), R(2, "c", "alpha", "y"),
            R(3, "a", "alpha", "y"), {"_pos": 4, "uri": "spotify:local:x"}]
    by_artist = [r["_pos"] for r in playlist_sort.sort_rows(rows, "artist")]
    check("artist: no artist first, then case-insensitive, album/title tie-break",
          by_artist == [4, 3, 2, 1, 0], str(by_artist))
    check("default restores playlist order",
          [r["_pos"] for r in playlist_sort.sort_rows(list(reversed(rows)), "default")] == [0, 1, 2, 3, 4])
    check("default descending reverses the playlist order",
          [r["_pos"] for r in playlist_sort.sort_rows(rows, "default", True)] == [4, 3, 2, 1, 0])
    same = [R(i, "same", "same") for i in range(4)]
    check("ties keep playlist order, also descending",
          [r["_pos"] for r in playlist_sort.sort_rows(same, "title", True)] == [0, 1, 2, 3])
    durs = [R(0, "a", "x", dur=300), R(1, "b", "x", dur=None), R(2, "c", "x", dur=100)]
    check("duration with a missing value", [r["_pos"] for r in playlist_sort.sort_rows(durs, "duration")] == [1, 2, 0])
    added = [R(0, "a", "x", added="2024-05-01T00:00:00Z"), R(1, "b", "x", added=None),
             R(2, "c", "x", added="2023-01-01T00:00:00Z")]
    check("date added, newest first", [r["_pos"] for r in playlist_sort.sort_rows(added, "added", True)] == [0, 2, 1])
    check("unknown key behaves like default",
          [r["_pos"] for r in playlist_sort.sort_rows(rows[::-1], "bogus")] == [0, 1, 2, 3, 4])
    check("s cycles through every criterion and wraps",
          [playlist_sort.next_key(k) for k in playlist_sort.SORT_KEYS] ==
          list(playlist_sort.SORT_KEYS[1:]) + ["default"])


def test_pref():
    check("missing config -> default", playlist_sort.load_pref({}) == ("default", False))
    check("not a dict -> default", playlist_sort.load_pref(None) == ("default", False))
    check("invalid values -> default",
          playlist_sort.load_pref({"playlist_sort": "nope", "playlist_sort_desc": "yes"}) == ("default", False))
    check("valid values are used",
          playlist_sort.load_pref({"playlist_sort": "artist", "playlist_sort_desc": True}) == ("artist", True))
    check("no title suffix for the plain playlist order", playlist_sort.label("default", False) is None)


# --------------------------------------------------------------------------- #
# App-level
# --------------------------------------------------------------------------- #
NAMES = ["delta", "Alpha", "charlie", "bravo", "echo"]

class PlFake:
    def __init__(self, total=5):
        self.total = total
        self.played = []
        self.specific = []; self.all_removed = []
    def playlist_remove_specific_occurrences_of_items(self, pid, items, snapshot_id=None):
        self.specific.append((pid, items)); return True
    def playlist_remove_all_occurrences_of_items(self, pid, uris):
        self.all_removed.append((pid, uris)); return True
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
    def check_saved_tracks(self, ids, on_batch=None):
        vals = [i.endswith("1") for i in ids]       # t1, t11, ... are liked
        if on_batch: on_batch(0, vals)
        return vals
    def playlist_items(self, pid, limit=100, offset=0, fields=None):
        end = min(self.total, offset + limit)
        return {"total": self.total, "items": [{"added_at": f"2024-01-{(i % 28) + 1:02d}T00:00:00Z", "track": {
            "id": f"t{i}", "uri": f"spotify:track:t{i}", "type": "track",
            "name": f"{NAMES[i % 5]} {i:03d}", "duration_ms": (i * 7919) % 300000,
            "artists": [{"name": NAMES[(i * 3) % 5]}], "album": {"name": NAMES[(i * 2) % 5]}}}
            for i in range(offset, end)]}


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

PL = {"id": "pl1", "name": "Sorty", "uri": "spotify:playlist:pl1"}

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

def aligned(t):
    m = t._model_rows
    return (all(t.row_to_uri[i] == r["uri"] for i, r in enumerate(m))
            and t._context_uris == [r["uri"] for r in m]
            and all(t._liked_map[i] == r["id"].endswith("1") for i, r in enumerate(m)))


async def test_sort_in_app():
    config.LOCAL_CFG = {}
    fake = PlFake(total=5)
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._pl_sort = None
        app._open_playlist_table(dict(PL))
        await poll(lambda: loaded(app, 5))
        t = table(app)
        t.focus(); await pilot.pause()
        t.move_cursor(row=3); await pilot.pause()        # "bravo 003"
        await pilot.press("s"); await pilot.pause()      # -> Title
        titles = [r["title"] for r in t._model_rows]
        check("s sorts by title (case-insensitive)",
              titles == sorted(titles, key=str.casefold), str(titles))
        check("sorting jumps the cursor to the top row", app._get_cursor_row(t) == 0)
        check("row maps, likes and context URIs follow the new order", aligned(t))
        check("title shows the criterion", "sort: Title ↑" in str(t._view_title), str(t._view_title))
        check("choice saved to spt_config.json",
              config.load_local_config().get("playlist_sort") == "title")

        await pilot.press("S"); await pilot.pause()
        titles = [r["title"] for r in t._model_rows]
        check("S reverses", titles == sorted(titles, key=str.casefold, reverse=True), str(titles))
        check("direction saved", config.load_local_config().get("playlist_sort_desc") is True)

        await pilot.press("s"); await pilot.pause()      # -> Artist, ascending
        check("next criterion is artist, ascending", app._pl_sort == ("artist", False))

        # Play a row that is not at its playlist position.
        row = next(i for i, r in enumerate(t._model_rows) if r["_pos"] != i)
        want = t._model_rows[row]
        t.move_cursor(row=row); await pilot.pause()
        app._play_row(row, t)
        await poll(lambda: fake.played)
        got = fake.played[-1] if fake.played else {}
        check("playing a sorted row uses its playlist position",
              got.get("offset") == {"position": want["_pos"]} and got.get("context_uri") == PL["uri"],
              f"{got} want pos {want['_pos']}")

        app.action_delete(); await pilot.pause()
        pend = getattr(app, "_pending_remove_track", None) or {}
        check("Ctrl+D targets the highlighted track's URI", pend.get("uri") == want["uri"], str(pend))
        check("Ctrl+D carries the row's real playlist position",
              pend.get("position") == want["_pos"], str(pend))
        app._cancel_remove_track()

        # Confirming removes only that occurrence, never "all occurrences".
        fake.specific = []; fake.all_removed = []
        app.action_delete(); await pilot.pause()
        app.action_delete(); await pilot.pause()         # second Ctrl+D confirms
        await poll(lambda: fake.specific or fake.all_removed)
        check("removal uses the specific occurrence at its position",
              fake.specific and fake.specific[-1][1] == [{"uri": want["uri"], "positions": [want["_pos"]]}]
              and not fake.all_removed, f"{fake.specific} {fake.all_removed}")


async def test_streaming_into_sorted_view():
    config.LOCAL_CFG = {"playlist_sort": "artist", "playlist_sort_desc": False, "client_id": "keep"}
    fake = PlFake(total=250)
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._pl_sort = None
        app._open_playlist_table(dict(PL))
        await poll(lambda: loaded(app, 250), timeout=8.0)
        t = table(app)
        m = t._model_rows if t is not None else []
        keys = [(r["_artist0"].casefold(), r["album"].casefold(), r["title"].casefold()) for r in m]
        check("saved criterion applied on open", app._pl_sort == ("artist", False))
        check("all pages arrive, sorted", len(m) == 250 and keys == sorted(keys), f"rows={len(m)}")
        check("no duplicate rows after streaming", len({r["_pos"] for r in m}) == 250)
        check("maps aligned after streaming", t is not None and aligned(t))
        check("title shows the saved criterion", "sort: Artist ↑" in str(getattr(t, "_view_title", "")))
        check("other config keys untouched", config.LOCAL_CFG.get("client_id") == "keep")


async def main():
    test_pure_rules(); test_pref()
    for fn in (test_sort_in_app, test_streaming_into_sorted_view):
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " raised", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
