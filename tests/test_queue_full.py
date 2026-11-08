"""Queue view (Ctrl+C) as a playlist-style list, filled to the panel.

* the table borrows #right's frame (title "Queue · N songs"), no inner box;
* sections stay as header rows; ↓ and ♥ columns; "/" filters it and Enter on
  a filtered row jumps to the right queue position;
* mpv-backed providers list up to `queue_context_limit` (default 300) tracks;
* Spotify: /me/player/queue (short, no paging) shown as "Spotify queue", then
  the rest of the playlist after the last listed track, marked "Deduced";
  nothing is deduced with shuffle on or without a playlist/album context.

All offline (requests and spotipy faked).  Run standalone:  python tests/test_queue_full.py
"""
import os, sys, asyncio, tempfile, time

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_qfull_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import DataTable, Static
from spt_tui import config
from spt_tui.app import SptPy
from spt_tui.app import queue_devices as qd
from spt_tui.providers.base import Provider
from spt_tui.providers.mpv_backed import MpvBackedProvider

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def tr(n, pre="ytmusic"):
    return {"id": f"id{n}", "uri": f"{pre}:track:id{n}", "name": f"Song {n}", "duration_ms": 200000,
            "artists": [{"name": "Joji"}], "album": {"name": "LP"}}


class FakeProv(Provider):
    name = "fake"; display_name = "Fake"
    def __init__(self, n=120):
        super().__init__()
        self.current = tr(0)
        self.rest = [tr(i) for i in range(1, n)]
        self.jumps = []
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
    def check_saved_tracks(self, ids, on_batch=None): return [i == "id3" for i in ids]
    def devices(self): return []
    def get_playback(self):
        return {"is_playing": True, "progress_ms": 1000, "item": dict(self.current), "shuffle_state": False}
    def play_queue_position(self, position): self.jumps.append(position)
    def queue(self):
        return {"currently_playing": self.current, "queue": [dict(t) for t in self.rest]}


class TApp(SptPy):
    def __init__(self, prov): super().__init__(); self.spotify = prov


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval", "_devices_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def poll(pilot, fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        await pilot.pause()
        try:
            if fn(): return True
        except Exception:
            pass
        await asyncio.sleep(0.05)
    return False


def qtable(app):
    try: return app.query_one("#queue_table", DataTable)
    except Exception: return None


async def test_playlist_style():
    config.LOCAL_CFG = {}
    prov = FakeProv()
    app = TApp(prov)
    async with app.run_test(size=(160, 44)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._play_context = {"name": "Joji mix", "source": "Playlist"}
        app.action_open_queue()
        ok = await poll(pilot, lambda: qtable(app) is not None and len(qtable(app)._model_rows) == 120)
        t = qtable(app)
        check("all 119 upcoming tracks (+ header) listed", ok, str(len(getattr(t, "_model_rows", []) or [])))
        pause_intervals(app)
        check("borrows #right's frame like a playlist", app.right_panel.has_class("table-view"))
        check("title on the frame with the count", "Queue · 119 songs" in str(app.right_panel.border_title),
              str(app.right_panel.border_title))
        check("no inner 'Queue' box", not [w for w in app.right_panel.children if isinstance(w, Static)
                                          and "Queue" in str(getattr(w, "content", ""))])
        labels = [str(c.label) for c in t.ordered_columns]
        check("columns: # ↓ ♥ Title…", labels[:4] == ["#", "↓", "♥", "Title"], str(labels))
        check("fills the panel (more rows than fit)", t.row_count > t.size.height, f"{t.row_count} rows, h={t.size.height}")
        check("liked state painted", any(str(t.get_row_at(i)[2]) == "❤" for i in range(5)))
        # Filter + Enter on a filtered row.
        t.focus(); await pilot.pause()
        await pilot.press("slash"); await pilot.pause()
        for ch in "song 42":
            await pilot.press("space" if ch == " " else ch)
        await poll(pilot, lambda: t.row_count == 1, timeout=2.0)
        await pilot.press("down"); await pilot.pause()
        await pilot.press("enter")
        await poll(pilot, lambda: prov.jumps)
        check("filtered queue row jumps to its real queue position", prov.jumps[-1:] == [41], str(prov.jumps))
        await pilot.press("escape"); await pilot.pause()
        check("Esc clears the queue filter", t.row_count == 120)
        # Refresh keeps the cursor.
        t.move_cursor(row=30); await pilot.pause()
        app._refresh_queue_table()
        await poll(pilot, lambda: not getattr(app, "_queue_refresh_running", False))
        await pilot.pause()
        check("5 s refresh keeps the cursor row", t.cursor_row == 30, str(t.cursor_row))
        # Opening twice does not remount.
        app.action_open_queue(); await pilot.pause()
        check("Ctrl+C again reuses the same table", qtable(app) is t)


def test_mpv_limit():
    class P(MpvBackedProvider):
        name = "p"; display_name = "P"
        def __init__(self): super().__init__(player=object())
    p = P()
    config.LOCAL_CFG = {}
    check("mpv queue limit: 300 by default", p._queue_context_limit() == 300)
    config.LOCAL_CFG = {"queue_context_limit": 5000}
    check("…configurable, clamped to 1000", p._queue_context_limit() == 1000)
    p.QUEUE_CONTEXT_LIMIT = 20
    check("…an explicit class/instance limit wins", p._queue_context_limit() == 20)
    config.LOCAL_CFG = {}


class SpFake:
    """Spotify-like (not a Provider): the view uses /me/player/queue + context."""
    name = "spotify"
    def __init__(self, shuffle=False, ctx="playlist"):
        self.shuffle_state, self.ctx = shuffle, ctx
        self.pl = [tr(i, "spotify") for i in range(60)]
        class Mgr:
            def get_cached_token(self): return {"access_token": "tok"}
        self._auth_manager = Mgr()
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0):
        return {"items": [{"id": "pl1", "name": "Mix", "uri": "spotify:playlist:pl1", "type": "playlist",
                           "owner": {"display_name": "me"}, "tracks": {"total": 60}}], "next": None}
    def devices(self): return []
    def ensure(self): return self
    def _normalize_track_id(self, u): return (u or "").split(":")[-1] or None
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)
    def get_playback(self):
        ctx = {"type": self.ctx, "uri": f"spotify:{self.ctx}:pl1"} if self.ctx else None
        return {"is_playing": True, "item": dict(self.pl[10]), "shuffle_state": self.shuffle_state, "context": ctx}
    def playlist_items(self, pid, limit=100, offset=0, fields=None):
        chunk = self.pl[offset:offset + limit]
        return {"items": [{"item": t} for t in chunk], "next": "x" if offset + limit < len(self.pl) else None}


class FakeResp:
    status_code = 200
    def __init__(self, items): self._items = items
    def json(self): return {"currently_playing": None, "queue": self._items}


async def test_spotify_queue():
    real_get = qd.requests.get
    try:
        for shuffle in (False, True):
            config.LOCAL_CFG = {}
            sp = SpFake(shuffle=shuffle)
            api = [sp.pl[i] for i in range(11, 31)]           # Spotify lists 20
            qd.requests.get = lambda *a, **k: FakeResp(api)
            app = TApp(sp)
            async with app.run_test(size=(160, 44)) as pilot:
                await pilot.pause(); pause_intervals(app)
                app.playlists_cache = [{"id": "pl1", "name": "Mix"}]
                app.action_open_queue()
                ok = await poll(pilot, lambda: qtable(app) is not None and qtable(app)._model_rows)
                pause_intervals(app)
                m = qtable(app)._model_rows if ok else []
                heads = [r["title"] for r in m if r.get("_header")]
                spq = [r for r in m if r.get("source") == "Spotify queue"]
                ded = [r for r in m if r.get("source") == "Deduced"]
                if not shuffle:
                    check("Spotify: its 20 queue items, labelled", len(spq) == 20 and heads[0].startswith("Spotify queue"),
                          str(heads))
                    check("…then the rest of the playlist after the last listed one, deduced",
                          [r["id"] for r in ded] == [f"id{i}" for i in range(31, 60)] and
                          any("deduced" in h for h in heads), f"{[r['id'] for r in ded][:3]} {heads}")
                    check("…the title says what is deduced", "deduced" in str(app.right_panel.border_title).lower())
                else:
                    check("shuffle on: nothing deduced, only Spotify's queue", not ded and len(spq) == 20)
                    check("…and the title says why", "shuffle is on" in str(app.right_panel.border_title),
                          str(app.right_panel.border_title))
        sp = SpFake(ctx=None)
        app = TApp(sp)
        rest, header, note = app._spotify_queue_continuation([])
        check("no playlist/album context: nothing deduced", rest == [] and header is None and "no playlist" in note)
    finally:
        qd.requests.get = real_get


if __name__ == "__main__":
    asyncio.run(test_playlist_style())
    test_mpv_limit()
    asyncio.run(test_spotify_queue())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
