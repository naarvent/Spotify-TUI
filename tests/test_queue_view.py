"""The Queue view (Ctrl+C) with a provider that has its own queue (YouTube Music / Subsonic via
mpv): two parts like Spotify, "Next in queue" (what the user queued, Source "Queue") and
"Next from: <playlist>" (the rest of the context, Source = where it was played from). What
already played never shows, and the view refreshes by itself when the track changes.
Offline: a fake Provider, Textual Pilot.

Run standalone:  python tests/test_queue_view.py
"""
import os, sys, asyncio, time, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import DataTable
from spt_tui.app import SptPy
from spt_tui.providers.base import Provider

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def tr(n):
    return {"id": f"id{n}", "uri": f"ytmusic:track:id{n}", "name": n, "duration_ms": 200000,
            "artists": [{"name": "Joji"}], "album": {"name": "BALLADS 1"}}


class FakeProv(Provider):
    name = "fake"
    display_name = "Fake"

    def __init__(self):
        super().__init__()
        self.current = tr("Glimpse")
        self.mine = [tr("Slow")]                 # queued by the user
        self.rest = [tr("B"), tr("C")]           # rest of the context
        self.started = []
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
    def recently_played(self, limit=50): return {"items": []}
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)
    def devices(self): return []
    def get_playback(self):
        return {"is_playing": True, "progress_ms": 1000, "item": dict(self.current),
                "currently_playing_type": "track", "shuffle_state": False, "repeat_state": "off",
                "device": {"id": "x", "name": "mpv", "is_active": True, "volume_percent": 50}}
    def start_playback(self, *, context_uri=None, uris=None, offset=None):
        self.started.append((context_uri, uris, offset))
    def play_queue_position(self, position):
        self.jumps = getattr(self, "jumps", []) + [position]
    def queue(self):
        return {"currently_playing": self.current,
                "queue": [dict(t, _queued=True) for t in self.mine] + [dict(t) for t in self.rest]}


class TApp(SptPy):
    def __init__(self, prov):
        super().__init__()
        self.spotify = prov


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval", "_devices_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def poll(pilot, fn, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        await pilot.pause()
        try:
            if fn(): return True
        except Exception:
            pass
        await asyncio.sleep(0.05)
    return False


def rows(app):
    """(title, source) of every row of #queue_table, from its model."""
    t = app.query_one("#queue_table", DataTable)
    return [(r.get("title"), "HEADER" if r.get("_header") else r.get("source"))
            for r in (getattr(t, "_model_rows", None) or [])]


async def body():
    prov = FakeProv()
    app = TApp(prov)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)

        # Playing from a playlist view records its name for "Next from: ...".
        table = app._render_tracks_table("[b]Joji mix[/b]", [
            {"id": "idB", "uri": "ytmusic:track:idB", "title": "B", "artist": "Joji", "album": "", "dur": "", "added": ""}],
            [False], context_uri="ytmusic:playlist:pl1", context_uris=["ytmusic:track:idB"])
        await pilot.pause()
        app._play_row(0, table)
        ok = await poll(pilot, lambda: prov.started)
        check("playing a row starts the context", ok, str(prov.started))
        check("the context name is remembered for the queue view",
              (app._play_context or {}).get("name") == "Joji mix", str(app._play_context))

        app._now_internal_track_id = prov.current["id"]
        app.action_open_queue()
        want = [("Next in queue", "HEADER"), ("Slow", "Queue"),
                ("Next from: Joji mix", "HEADER"), ("B", "Playlist"), ("C", "Playlist")]
        ok = await poll(pilot, lambda: rows(app) == want)
        check("the queue view shows the two parts with their Source", ok, str(rows(app)))
        check("the playing track is not listed", "Glimpse" not in [r[0] for r in rows(app)], str(rows(app)))
        t = app.query_one("#queue_table", DataTable)
        check("header rows carry no uri (Enter / c on them do nothing)",
              t.row_to_uri.get(0) is None and t.row_to_uri.get(2) is None and t.row_to_uri.get(1) == "ytmusic:track:idSlow",
              str(t.row_to_uri))
        check("track rows are numbered 1..n, headers skipped",
              [str(c) for c in (t.get_row_at(1)[0], t.get_row_at(3)[0], t.get_row_at(4)[0])] == ["1", "2", "3"],
              str([t.get_row_at(i)[0] for i in range(t.row_count)]))

        # Enter on a queue row jumps to it (position among tracks, headers skipped); on a header: nothing.
        t.move_cursor(row=3); t.focus(); await pilot.pause()
        await pilot.press("enter")
        ok = await poll(pilot, lambda: getattr(prov, "jumps", None))
        check("Enter on a queue row plays that entry (B is the 2nd track)", ok and prov.jumps == [1], str(getattr(prov, "jumps", None)))
        t.move_cursor(row=2); await pilot.pause()
        await pilot.press("enter"); await pilot.pause(); await asyncio.sleep(0.2)
        check("Enter on a header does nothing", prov.jumps == [1], str(prov.jumps))

        # The queued song starts: it leaves the queue, and the view refreshes by itself on the
        # track change (the 5 s interval is paused here).
        prov.current, prov.mine = prov.mine[0], []
        app._sync_playback()
        want2 = [("Next from: Joji mix", "HEADER"), ("B", "Playlist"), ("C", "Playlist")]
        ok = await poll(pilot, lambda: rows(app) == want2)
        check("after the queued song starts, it is gone and the view refreshed by itself", ok, str(rows(app)))

        # A stale entry in the app's own record of queued items never shows with such a provider.
        app._local_queue.append({"uri": "ytmusic:track:idOld", "title": "Old", "source": "Playlist"})
        prov.current, prov.rest = prov.rest[0], prov.rest[1:]
        app._sync_playback()
        ok = await poll(pilot, lambda: rows(app) == [("Next from: Joji mix", "HEADER"), ("C", "Playlist")])
        check("next track: the list follows, and stale _local_queue items do not show", ok, str(rows(app)))

        prov.current, prov.rest = prov.rest[0], []
        app._sync_playback()
        ok = await poll(pilot, lambda: rows(app) == [])
        check("nothing left: the queue is empty (no headers)", ok, str(rows(app)))


async def devices_reopen():
    """Devices opened again while its table is still on screen (focus on the menu): _clear_right()
    is removing that table, which used to be reused and left the panel empty."""
    prov = FakeProv()
    prov.devices = lambda: [{"id": "d1", "name": "Desk", "type": "Computer", "is_active": True}]
    app = TApp(prov)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app.action_manage_devices()
        await poll(pilot, lambda: len(app.query("#devices_table")) == 1)
        app.level = app.LVL_SECTIONS
        app.action_manage_devices()
        ok = await poll(pilot, lambda: len(app.query("#devices_table")) == 1
                        and app.query_one("#devices_table", DataTable).row_count == 1)
        for _ in range(5): await pilot.pause()
        check("reopening Devices with its table on screen shows it again (not an empty panel)",
              ok and len(app.query("#devices_table")) == 1, f"tables={len(app.query('#devices_table'))}")


def main():
    for t in (body, devices_reopen):
        try:
            asyncio.run(t())
        except Exception:
            check(f"{t.__name__} (crashed)", False, traceback.format_exc(limit=8))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    for n, c, d in RESULTS:
        if not c: print("  FAIL:", n, "::", d)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
