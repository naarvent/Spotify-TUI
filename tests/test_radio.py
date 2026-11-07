"""Radio / discovery (`R`).

* YouTube Music: get_watch_playlist(videoId, radio=True) for a track, the
  artist's radioId (or their top song), playlistId+radio for a playlist and
  the first song of a local playlist; "length" durations parsed;
* Subsonic: getSimilarSongs (track, seed first), getSimilarSongs2 (artist),
  playlist = similar songs of its first tracks minus the playlist itself;
  an empty answer explains Last.fm;
* Spotify: /recommendations with the right seeds; a 404 (retired for
  development-mode apps) becomes a clear "unavailable" and is not retried;
* base: no radio -> ProviderUnsupported; Capabilities.can_radio opt-in;
* UI: R on a track row / artist row / sidebar playlist / nothing focused
  (the playing track) opens "Radio: …"; Enter plays from the row with the
  rest of the radio after it; a provider without radio shows the notice and
  makes no call; Ctrl+R reloads the radio view.

All offline.  Run standalone:  python tests/test_radio.py
"""
import os, sys, asyncio, tempfile
from urllib.parse import urlparse

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_radio_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.providers.base import Provider, ProviderUnsupported, ProviderError, Capabilities
from spt_tui.providers.spotify import SpotifyProvider, RETIRED_ENDPOINTS
from spt_tui.providers.ytmusic import YTMusicProvider
from spt_tui.providers.subsonic import SubsonicProvider
from spt_tui.local_library import LocalLibrary
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakePlayer:
    def __getattr__(self, name):
        return lambda *a, **k: None


def test_base():
    raised = False
    try:
        Provider().radio("track", "x")
    except ProviderUnsupported:
        raised = True
    check("base provider: radio unsupported", raised)
    check("can_radio is opt-in", Capabilities().can_radio is False)


# --------------------------------------------------------------------------- #
# YouTube Music
# --------------------------------------------------------------------------- #
class YT:
    def __init__(self, radio_id=True):
        self.calls = []
        self.radio_id = radio_id
    def get_watch_playlist(self, videoId=None, playlistId=None, radio=False, limit=25, **k):
        self.calls.append(("watch", videoId, playlistId, radio, limit))
        return {"tracks": [
            {"videoId": "seed" if videoId else "w0", "title": "First", "artists": [{"name": "A", "id": "UC1"}],
             "length": "3:05", "album": {"name": "LP", "id": "MPRE1"}},
            {"videoId": "w1", "title": "Next", "artists": [{"name": "B"}], "length": "1:00:01"},
            {"title": "no id"}]}
    def get_artist(self, cid):
        self.calls.append(("artist", cid))
        out = {"name": "A", "songs": {"results": [{"videoId": "top1", "title": "Top"}]}}
        if self.radio_id:
            out["radioId"] = "RDEM123"
        return out


def yt_provider(yt, lib=None):
    tmp = tempfile.mkdtemp(prefix="spt_radio_yt_")
    lib = lib or LocalLibrary("ytmusic", path=os.path.join(tmp, "lib.json"))
    return YTMusicProvider(client=yt, player=FakePlayer(), stream_resolver=None, library=lib)


def test_ytmusic():
    yt = YT()
    p = yt_provider(yt)
    check("ytmusic: can_radio", p.capabilities.can_radio)
    r = p.radio("track", "seed")
    check("track radio: get_watch_playlist(videoId, radio=True)", yt.calls[-1][:4] == ("watch", "seed", None, True))
    check("tracks mapped, entries without id dropped", [t["id"] for t in r["tracks"]] == ["seed", "w1"])
    check("'length' durations parsed (m:ss and h:mm:ss)",
          r["tracks"][0]["duration_ms"] == 185000 and r["tracks"][1]["duration_ms"] == 3601000)
    p.radio("playlist", "VLPLabc")
    check("playlist radio: playlistId (VL stripped) + radio", yt.calls[-1][:4] == ("watch", None, "PLabc", True))
    p.radio("artist", "UC1")
    check("artist radio: the artist's radioId", yt.calls[-1][:3] == ("watch", None, "RDEM123"))
    yt2 = YT(radio_id=False)
    r = yt_provider(yt2).radio("artist", "UC1")
    check("artist without radioId: radio from their top song",
          yt2.calls[-1][:4] == ("watch", "top1", None, True) and r["notes"], str(yt2.calls))
    # Local playlist: no YouTube id, radio from its first song.
    p = yt_provider(YT())
    pl = p.create_playlist("Mine")
    p.add_items_to_playlist(pl["id"], ["ytmusic:track:abc"]) if hasattr(p, "add_items_to_playlist") else None
    entries = p.local_playlist_entries(pl["id"]) or []
    if entries:
        p._client.calls.clear()
        r = p.radio("playlist", pl["id"])
        check("local playlist radio: from its first song", p._client.calls and p._client.calls[-1][1] == "abc"
              and any("Local playlist" in n for n in r["notes"]), str(p._client.calls))
    else:
        raised = False
        try:
            p.radio("playlist", pl["id"])
        except ProviderUnsupported:
            raised = True
        check("empty local playlist: clear unsupported", raised)
    raised = False
    try:
        p.radio("album", "x")
    except ProviderUnsupported:
        raised = True
    check("album seed refused with a clear message", raised)

    class Broken(YT):
        def get_watch_playlist(self, **k):
            raise ConnectionError("offline")
    raised = None
    try:
        yt_provider(Broken()).radio("track", "x")
    except ProviderError as exc:
        raised = str(exc)
    check("network failure is a ProviderError the UI can show", raised and "radio failed" in raised)


# --------------------------------------------------------------------------- #
# Subsonic
# --------------------------------------------------------------------------- #
class Resp:
    def __init__(self, body): self.body, self.status_code = body, 200
    def raise_for_status(self): pass
    def json(self): return self.body

def ok(**kw):
    return {"subsonic-response": {"status": "ok", "version": "1.16.1", **kw}}

def S(i):
    return {"id": f"s{i}", "title": f"Song {i}", "artist": "X", "duration": 100}


class SubSession:
    def __init__(self, empty=False):
        self.calls, self.empty = [], empty
    def get(self, url, params=None, timeout=None):
        ep = urlparse(url).path.rsplit("/", 1)[-1]
        q = dict(params or [])
        self.calls.append((ep, q))
        songs = [] if self.empty else None
        if ep == "getSimilarSongs":
            sid = q.get("id")
            return Resp(ok(similarSongs={"song": songs if songs is not None else
                                         [S(10 + int(sid[1:])), S(1), S(20)]}))
        if ep == "getSimilarSongs2":
            return Resp(ok(similarSongs2={"song": songs if songs is not None else [S(30), S(31)]}))
        if ep == "getSong":
            return Resp(ok(song=S(int(q["id"][1:]))))
        if ep == "getPlaylist":
            return Resp(ok(playlist={"id": "p1", "entry": [S(1), S(2), S(3), S(4)]}))
        return Resp(ok())


def sub(sess):
    return SubsonicProvider(url="https://music.example.org", user="me", password="pw",
                            session=sess, player=FakePlayer())


def test_subsonic():
    sess = SubSession()
    p = sub(sess)
    check("subsonic: can_radio", p.capabilities.can_radio)
    r = p.radio("track", "s5")
    check("track radio uses getSimilarSongs with the song id",
          ("getSimilarSongs", "s5") in [(ep, q.get("id")) for ep, q in sess.calls])
    check("…and starts with the seed song", r["tracks"][0]["id"] == "s5", str([t["id"] for t in r["tracks"]]))
    r = p.radio("artist", "ar1")
    check("artist radio uses getSimilarSongs2", sess.calls[-1][0] == "getSimilarSongs2"
          and [t["id"] for t in r["tracks"]] == ["s30", "s31"])
    sess.calls.clear()
    r = p.radio("playlist", "p1")
    seeds = [q.get("id") for ep, q in sess.calls if ep == "getSimilarSongs"]
    check("playlist radio: similar songs of its first 3 tracks", seeds == ["s1", "s2", "s3"], str(seeds))
    ids = [t["id"] for t in r["tracks"]]
    check("…without the playlist's own songs, without duplicates",
          not ({"s1", "s2", "s3", "s4"} & set(ids)) and len(ids) == len(set(ids)) and "s20" in ids, str(ids))
    r = sub(SubSession(empty=True)).radio("artist", "ar1")
    check("empty answer: note about Last.fm", r["tracks"] == [] and any("Last.fm" in n for n in r["notes"]))


# --------------------------------------------------------------------------- #
# Spotify
# --------------------------------------------------------------------------- #
class HttpErr(Exception):
    def __init__(self, status):
        super().__init__(f"http status: {status}"); self.http_status = status


class SpInner:
    def __init__(self, status=None):
        self.status, self.calls = status, []
    def recommendations(self, **kw):
        self.calls.append(kw)
        if self.status:
            raise HttpErr(self.status)
        return {"tracks": [{"id": "r1", "uri": "spotify:track:r1", "name": "Rec"}]}
    def playlist_items(self, pid, **kw):
        return {"items": [{"item": {"id": f"t{i}", "type": "track"}} for i in range(8)]}


def sp(inner):
    p = SpotifyProvider(); p.sp = inner
    return p


def test_spotify():
    inner = SpInner()
    p = sp(inner)
    r = p.radio("track", "spotify:track:abc")
    check("track seed normalised", inner.calls[-1].get("seed_tracks") == ["abc"])
    check("tracks returned", [t["id"] for t in r["tracks"]] == ["r1"])
    p.radio("artist", "ar9")
    check("artist seed", inner.calls[-1].get("seed_artists") == ["ar9"])
    p.radio("playlist", "pl1")
    check("playlist seeds: at most 5 tracks", inner.calls[-1].get("seed_tracks") == [f"t{i}" for i in range(5)])
    inner = SpInner(status=404)
    p = sp(inner)
    msg = None
    try:
        p.radio("track", "abc")
    except ProviderUnsupported as exc:
        msg = str(exc)
    check("404: retired-endpoint explanation", msg == RETIRED_ENDPOINTS["recommendations"], str(msg))
    n = len(inner.calls)
    try:
        p.radio("track", "abc")
    except ProviderUnsupported:
        pass
    check("not called again in the session", len(inner.calls) == n)


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
class UiFake:
    display_name = "Fake"
    def __init__(self, caps=None):
        self.played, self.radio_calls = [], []
        if caps is not None:
            self.capabilities = caps
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0):
        return {"items": [{"id": "pl1", "name": "Mix", "uri": "x:playlist:pl1", "type": "playlist",
                           "owner": {"display_name": "me"}, "tracks": {"total": 0}}], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def search(self, *a, **k): return {}
    def fmt_duration(self, ms):
        s = int((ms or 0) / 1000); return f"{s // 60}:{s % 60:02d}"
    def shuffle(self, *a, **k): pass
    def start_playback(self, **kw): self.played.append(kw)
    def check_saved_tracks(self, ids, on_batch=None): return [i == "r2" for i in ids]
    def current_user_saved_tracks_contains(self, ids): return [False] * len(ids)
    def radio(self, kind, sid, seed=None, limit=50):
        self.radio_calls.append((kind, sid))
        return {"tracks": [{"id": f"r{i}", "uri": f"x:track:r{i}", "name": f"Rad {i}",
                            "artists": [{"name": "Z"}], "album": {"name": "Q"}, "duration_ms": 60000}
                           for i in range(1, 5)], "notes": ["test radio note"]}


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake


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


def radio_table(app):
    try:
        t = app.query_one("#tracks_table")
    except Exception:
        return None
    rv = getattr(app, "_right_view", None)
    return t if rv and rv[0] == "radio" and getattr(t, "_radio_seed", None) else None


def status(app):
    try:
        return str(app.status_line.render())
    except Exception:
        return ""


async def test_ui():
    config.LOCAL_CFG = {}
    fake = UiFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        rows = [{"type": "track", "id": "t7", "uri": "x:track:t7", "title": "Seed", "artist": "A", "album": ""},
                {"type": "artist", "id": "ar1", "uri": "x:artist:ar1", "title": "Band", "artist": "", "album": "",
                 "raw": {"id": "ar1", "name": "Band"}},
                {"type": "album", "id": "al1", "uri": "x:album:al1", "title": "LP", "artist": "", "album": "",
                 "raw": {"id": "al1", "name": "LP"}}]
        t = app._render_search_table("Results", rows, check_saved=False)
        await pilot.pause(); await pilot.pause()
        t.focus(); await pilot.pause()
        await pilot.press("R")
        rt = await poll(lambda: radio_table(app))
        check("R on a track row opens a radio view", rt is not None and fake.radio_calls[-1] == ("track", "t7"),
              str(fake.radio_calls))
        if rt is None:
            return
        check("radio title names the seed", "Radio: Seed" in str(app.right_panel.border_title),
              str(app.right_panel.border_title))
        check("four radio rows", rt.row_count == 4)
        check("provider notes shown", await poll(lambda: "test radio note" in status(app), timeout=2.0), status(app))
        check("likes looked up", await poll(lambda: rt._liked_map.get(1) is True, timeout=2.0))
        check("does not start playing by itself", fake.played == [])
        rt.focus(); rt.move_cursor(row=2); await pilot.pause()
        await pilot.press("enter")
        await poll(lambda: fake.played)
        got = fake.played[-1] if fake.played else {}
        check("Enter plays from the row with the rest of the radio after it",
              got.get("uris") == ["x:track:r3", "x:track:r4", "x:track:r1", "x:track:r2"], str(got))

        # Ctrl+R reloads it.
        n = len(fake.radio_calls)
        app.action_refresh()
        await poll(lambda: len(fake.radio_calls) > n)
        check("Ctrl+R reloads the radio", len(fake.radio_calls) == n + 1)
        await poll(lambda: radio_table(app) is not None and radio_table(app) is not rt)
        await pilot.pause()
        app._right_view = None      # the next tables stand for a newer view

        # Artist row.
        t = app._render_search_table("Results", rows, check_saved=False)
        await pilot.pause(); await pilot.pause()
        t.focus(); t.move_cursor(row=1); await pilot.pause()
        await pilot.press("R")
        await poll(lambda: fake.radio_calls[-1] == ("artist", "ar1"))
        check("R on an artist row: artist radio", fake.radio_calls[-1] == ("artist", "ar1"), f"{fake.radio_calls} focused={app.focused!r} cur={app._get_cursor_row(t)}")

        # Album row: refused, no call.
        t = app._render_search_table("Results", rows, check_saved=False)
        await pilot.pause(); await pilot.pause()
        t.focus(); t.move_cursor(row=2); await pilot.pause()
        n = len(fake.radio_calls)
        await pilot.press("R"); await pilot.pause()
        check("R on an album: explained, no call",
              len(fake.radio_calls) == n and "Radio starts from" in status(app), status(app))

        # Sidebar playlist.
        await poll(lambda: len(app.pl_list.children) > 0)
        app.pl_list.focus(); app.pl_list.index = 0; await pilot.pause()
        await pilot.press("R")
        await poll(lambda: fake.radio_calls[-1] == ("playlist", "pl1"))
        check("R on a sidebar playlist: playlist radio", fake.radio_calls[-1] == ("playlist", "pl1"))

        # Nothing focused: the playing track.
        app._bar_last_track = {"id": "now1", "name": "Now", "type": "track"}
        app.set_focus(None); await pilot.pause()
        app.action_start_radio()
        await poll(lambda: fake.radio_calls[-1] == ("track", "now1"))
        check("nothing focused: radio from the playing track", fake.radio_calls[-1] == ("track", "now1"))


async def test_ui_unsupported():
    config.LOCAL_CFG = {}
    fake = UiFake(caps=Capabilities(can_radio=False))
    fake.unsupported_message = lambda cap: "Radio is not available with Fake."
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._bar_last_track = {"id": "now1", "name": "Now", "type": "track"}
        app.action_start_radio(); await pilot.pause()
        check("provider without radio: notice, no call",
              fake.radio_calls == [] and "Radio is not available" in status(app), status(app))

    class Failing(UiFake):
        def radio(self, *a, **k):
            raise ProviderUnsupported(RETIRED_ENDPOINTS["recommendations"])
    fake = Failing()
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._bar_last_track = {"id": "now1", "name": "Now", "type": "track"}
        app.action_start_radio()
        ok = await poll(lambda: "development-mode" in status(app))
        check("retired endpoint: explained on the status line", ok, status(app))
        check("…and in the panel, no table", radio_table(app) is None)


if __name__ == "__main__":
    test_base()
    test_ytmusic()
    test_subsonic()
    test_spotify()
    asyncio.run(test_ui())
    asyncio.run(test_ui_unsupported())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
