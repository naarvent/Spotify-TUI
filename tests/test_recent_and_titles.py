"""Recently Played shows a loading state (it used to paint over a stale table,
so nothing appeared), and content tables carry their title in the integrated
border header instead of a separate Static above them.

Network is faked and gated with threading.Event (no fixed sleep).
Run standalone:  python tests/test_recent_and_titles.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from textual.widgets import DataTable

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def __init__(self):
        self.gate = None
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "3:00"
    def _normalize_track_id(self, x): return x
    def check_saved_tracks(self, ids): return [False] * len(ids)
    def recently_played(self, limit=50):
        if self.gate is not None:
            self.gate.wait(10)
        return {"items": [{"track": {"id": f"t{i}", "uri": f"spotify:track:t{i}",
                                     "name": f"Song {i}", "artists": [{"name": "A"}],
                                     "album": {"name": "Al"}, "duration_ms": 180000},
                           "played_at": "2024-01-01T00:00:00Z"} for i in range(4)]}

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

def ttbl(app):
    for w in app.query(DataTable):
        if getattr(w, "id", "") == "tracks_table":
            return w
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


async def test_recently_played_shows_loading():
    fake = Fake(); fake.gate = threading.Event()   # block the fetch
    app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        # Mount a stale content view first (as if returning from another view),
        # then open Recently Played — the loading state must still be visible.
        app._render_tracks_table("[b]Old view[/b]", [{"id": "x", "uri": "u", "title": "T",
                                 "artist": "a", "album": "al", "dur": "0:00"}], [False])
        await pilot.pause()
        app._open_recently_table()
        await pilot.pause()
        txt = right_text(app)
        check("Recently Played shows a loading state before results",
              "Loading Recently Played" in txt, f"txt={txt!r}")
        check("stale table cleared while loading", ttbl(app) is None, f"tbl={ttbl(app)}")
        fake.gate.set()
        await poll(lambda: ttbl(app) is not None, timeout=5.0)
        check("results replace the loading state", ttbl(app) is not None
              and "Loading Recently Played" not in right_text(app))


async def test_content_tables_have_border_title():
    fake = Fake(); app = TApp(fake)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]Recently Played[/b]", [{"id": "t0", "uri": "u",
                     "title": "S", "artist": "a", "album": "al", "dur": "0:00"}], [False], profile="recent")
        await pilot.pause()
        check("tracks table title is in its border header",
              str(t.border_title) == "Recently Played", f"bt={t.border_title!r}")
        check("no separate #tracks_title Static is mounted", len(app.query("#tracks_title")) == 0)
        s = app._render_search_table("[b]Saved Albums[/b]", [{"type": "album", "id": "a1",
                     "uri": "u", "title": "Alb", "artist": "Ar", "album": "", "dur": "",
                     "raw": {}, "saved": True}], check_saved=False, layout="albums")
        await pilot.pause()
        check("search table title is in its border header",
              str(s.border_title) == "Saved Albums", f"bt={s.border_title!r}")
        check("no separate #results_title Static is mounted", len(app.query("#results_title")) == 0)


ALL = [test_recently_played_shows_loading, test_content_tables_have_border_title]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (recent + titles) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
