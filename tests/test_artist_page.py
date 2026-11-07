"""Artist page: top tracks, discography (albums / singles) and related artists.

* base: build_artist_overview never raises; an unsupported or failing section
  comes back empty with a note; duplicates between albums and singles dropped;
* Spotify: top tracks / related artists through spotipy; a 403/404 (endpoints
  Spotify retired for development-mode apps) degrades with a clear note and is
  not retried in the same session;
* YouTube Music: one get_artist call (songs, albums, singles, related; full
  discography via get_artist_albums when available);
* Subsonic: getArtist + getTopSongs (by name) + getArtistInfo2, OpenSubsonic
  releaseTypes split albums from singles; an old server degrades;
* UI: the page lists sections in order, Enter on a top track plays the top
  tracks from there, Enter on an album / related artist opens it, notes go to
  the status line.

All offline (fakes for spotipy, ytmusicapi and the Subsonic HTTP session).
Run standalone:  python tests/test_artist_page.py
"""
import os, sys, asyncio, tempfile
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_artist_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.providers.base import (Provider, ProviderUnsupported, build_artist_overview,
                                    make_page, Capabilities)
from spt_tui.providers.spotify import SpotifyProvider, RETIRED_ENDPOINTS
from spt_tui.providers.ytmusic import YTMusicProvider
from spt_tui.providers.subsonic import SubsonicProvider
from spt_tui.local_library import LocalLibrary
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def T(i, name=None):
    return {"id": f"t{i}", "uri": f"x:track:t{i}", "type": "track", "name": name or f"Song {i}",
            "artists": [{"name": "Band"}], "album": {"name": "LP"}, "duration_ms": 1000 * (60 + i)}

def A(i, kind="album"):
    return {"id": f"a{i}", "uri": f"x:album:a{i}", "type": "album", "name": f"{kind} {i}",
            "artists": [{"name": "Band"}], "release_date": f"20{10 + i}-01-01"}


# --------------------------------------------------------------------------- #
# base
# --------------------------------------------------------------------------- #
class BareProvider(Provider):
    display_name = "Bare"
    def __init__(self, albums=None, singles=None, fail_albums=False):
        super().__init__()
        self._albums, self._singles, self._fail = albums or [], singles or [], fail_albums
    def artist_albums(self, artist_id, album_type=None, limit=50, offset=0):
        if self._fail:
            raise RuntimeError("boom")
        return make_page(self._singles if album_type == "single" else self._albums)


def test_base_overview():
    p = BareProvider(albums=[A(1), A(2)], singles=[A(2, "single"), A(3, "single")])
    ov = p.artist_overview("ar1", "Band")
    check("albums from artist_albums", [a["id"] for a in ov["albums"]] == ["a1", "a2"])
    check("a release in both lists is kept once, as an album", [a["id"] for a in ov["singles"]] == ["a3"])
    check("default: no top tracks, no related", ov["top_tracks"] == [] and ov["related"] == [])
    check("notes say what is missing",
          any("Artist top tracks" in n for n in ov["notes"]) and any("Related artists" in n for n in ov["notes"]),
          str(ov["notes"]))
    check("defaults are off in Capabilities",
          not Capabilities().can_artist_top_tracks and not Capabilities().can_related_artists)
    ov = BareProvider(fail_albums=True).artist_overview("ar1")
    check("a failing section never raises; it leaves a note",
          ov["albums"] == [] and any("could not be loaded" in n for n in ov["notes"]), str(ov["notes"]))

    class OnlyEnsure:
        display_name = "Fake"
        def ensure(self): return self
        def artist_albums(self, *a, **k): return {"items": [A(9)]}
    ov = build_artist_overview(OnlyEnsure(), "x")
    check("works with an object that only has ensure()", [a["id"] for a in ov["albums"]] == ["a9"])


# --------------------------------------------------------------------------- #
# Spotify
# --------------------------------------------------------------------------- #
class HttpErr(Exception):
    def __init__(self, status):
        super().__init__(f"http status: {status}")
        self.http_status = status


class SpInner:
    def __init__(self, top_status=None, rel_status=None):
        self.top_status, self.rel_status = top_status, rel_status
        self.calls = []
    def artist_top_tracks(self, artist_id, country="US"):
        self.calls.append(("top", artist_id, country))
        if self.top_status:
            raise HttpErr(self.top_status)
        return {"tracks": [T(1), T(2)]}
    def artist_related_artists(self, artist_id):
        self.calls.append(("related", artist_id))
        if self.rel_status:
            raise HttpErr(self.rel_status)
        return {"artists": [{"id": "r1", "name": "Other", "uri": "spotify:artist:r1"}]}
    def artist_albums(self, artist_id, album_type=None, limit=50, **k):
        self.calls.append(("albums", album_type, limit))
        return {"items": [A(1)] if album_type == "album" else [A(5, "single")]}


def sp_provider(inner):
    p = SpotifyProvider(); p.sp = inner
    return p


def test_spotify():
    inner = SpInner()
    p = sp_provider(inner)
    check("Spotify advertises top tracks / related (tried, may be retired)",
          p.capabilities.can_artist_top_tracks and p.capabilities.can_related_artists)
    ov = p.artist_overview("ar1", "Band")
    check("top tracks", [t["id"] for t in ov["top_tracks"]] == ["t1", "t2"])
    check("market from the user's token", ("top", "ar1", "from_token") in inner.calls)
    check("related artists", [a["id"] for a in ov["related"]] == ["r1"])
    check("albums and singles", [a["id"] for a in ov["albums"]] == ["a1"] and [a["id"] for a in ov["singles"]] == ["a5"])
    check("no notes when everything works", ov["notes"] == [], str(ov["notes"]))

    inner = SpInner(top_status=403, rel_status=404)
    p = sp_provider(inner)
    ov = p.artist_overview("ar1", "Band")
    check("403 on top tracks: empty section + retirement note",
          ov["top_tracks"] == [] and RETIRED_ENDPOINTS["top_tracks"] in ov["notes"], str(ov["notes"]))
    check("404 on related: empty section + retirement note",
          ov["related"] == [] and RETIRED_ENDPOINTS["related"] in ov["notes"])
    check("the discography still loads", ov["albums"] and ov["singles"])
    n_before = len([c for c in inner.calls if c[0] in ("top", "related")])
    p.artist_overview("ar2", "Band")
    n_after = len([c for c in inner.calls if c[0] in ("top", "related")])
    check("a retired endpoint is not called again in the session", n_after == n_before, f"{n_before}->{n_after}")

    inner = SpInner(top_status=500)
    ov = sp_provider(inner).artist_overview("ar1")
    check("other errors are a 'could not be loaded' note, not 'retired'",
          any("could not be loaded" in n for n in ov["notes"]) and RETIRED_ENDPOINTS["top_tracks"] not in ov["notes"])


# --------------------------------------------------------------------------- #
# YouTube Music
# --------------------------------------------------------------------------- #
class YT:
    def __init__(self, with_full=True, related=True):
        self.calls = []
        self.with_full, self.related = with_full, related
    def get_artist(self, cid):
        self.calls.append(("get_artist", cid))
        out = {"name": "Band - Topic",
               "songs": {"browseId": "VLx", "results": [
                   {"videoId": "v1", "title": "Hit", "artists": [{"name": "Band", "id": cid}],
                    "album": {"name": "LP", "id": "MPRE1"}, "duration": "3:05"},
                   {"title": "no id"}]},
               "albums": {"browseId": cid, "params": "P1" if self.with_full else None,
                          "results": [{"browseId": "MPRE1", "title": "LP", "year": "2020"}]},
               "singles": {"results": [{"browseId": "MPRE2", "title": "Single", "year": "2021"}]}}
        if self.related:
            out["related"] = {"results": [{"browseId": "UC2", "title": "Friend", "subscribers": "1.2M"},
                                          {"title": "no id"}]}
        return out
    def get_artist_albums(self, cid, params, limit=None):
        self.calls.append(("get_artist_albums", cid, params))
        return [{"browseId": "MPRE1", "title": "LP", "year": "2020"},
                {"browseId": "MPRE3", "title": "Old LP", "year": "2010"}]


class FakePlayer:
    def __getattr__(self, name):
        return lambda *a, **k: None


def yt_provider(yt):
    tmp = tempfile.mkdtemp(prefix="spt_artist_yt_")
    return YTMusicProvider(client=yt, player=FakePlayer(), stream_resolver=None,
                           library=LocalLibrary("ytmusic", path=os.path.join(tmp, "lib.json")))


def test_ytmusic():
    yt = YT()
    p = yt_provider(yt)
    check("ytmusic advertises top tracks and related",
          p.capabilities.can_artist_top_tracks and p.capabilities.can_related_artists)
    ov = p.artist_overview("UC1", "Band")
    check("one get_artist call for the whole page",
          [c for c in yt.calls if c[0] == "get_artist"] == [("get_artist", "UC1")], str(yt.calls))
    check("top songs mapped (entries without videoId dropped)",
          [t["id"] for t in ov["top_tracks"]] == ["v1"] and ov["top_tracks"][0]["duration_ms"] == 185000)
    check("full discography via get_artist_albums", [a["id"] for a in ov["albums"]] == ["MPRE1", "MPRE3"])
    check("albums carry the artist (get_artist lists omit it)", ov["albums"][0]["artists"][0]["name"] == "Band")
    check("singles", [a["id"] for a in ov["singles"]] == ["MPRE2"])
    check("related artists with subscriber info",
          [a["id"] for a in ov["related"]] == ["UC2"] and ov["related"][0]["_info"] == "1.2M subscribers")
    check("no notes when all sections are there", ov["notes"] == [], str(ov["notes"]))

    ov = yt_provider(YT(with_full=False, related=False)).artist_overview("UC1")
    check("without params: the short album list", [a["id"] for a in ov["albums"]] == ["MPRE1"])
    check("no related: a note says so", ov["related"] == [] and any("related" in n for n in ov["notes"]))

    class Broken(YT):
        def get_artist(self, cid):
            raise ConnectionError("offline")
    ov = yt_provider(Broken()).artist_overview("UC1")
    check("no network: empty page with a note, no exception",
          not any(ov[k] for k in ("top_tracks", "albums", "singles", "related")) and ov["notes"])
    check("per-section methods also work",
          [t["id"] for t in yt_provider(YT()).artist_top_tracks("UC1")] == ["v1"]
          and [a["id"] for a in yt_provider(YT()).related_artists("UC1")] == ["UC2"])


# --------------------------------------------------------------------------- #
# Subsonic
# --------------------------------------------------------------------------- #
class Resp:
    def __init__(self, body): self.body, self.status_code = body, 200
    def raise_for_status(self): pass
    def json(self): return self.body

def ok(**kw):
    return {"subsonic-response": {"status": "ok", "version": "1.16.1", **kw}}

def err(code, msg):
    return {"subsonic-response": {"status": "failed", "error": {"code": code, "message": msg}}}


class SubSession:
    def __init__(self, old_server=False, top=True):
        self.calls = []
        self.old, self.top = old_server, top
    def get(self, url, params=None, timeout=None):
        ep = urlparse(url).path.rsplit("/", 1)[-1]
        q = dict(params or [])
        self.calls.append((ep, q))
        if ep == "getArtist":
            return Resp(ok(artist={"id": "ar1", "name": "Band", "album": [
                {"id": "al1", "name": "LP", "year": 2020, "releaseTypes": ["Album"]},
                {"id": "al2", "name": "Single A", "releaseTypes": ["Single"]},
                {"id": "al3", "name": "EP B", "releaseTypes": ["EP"]},
                {"id": "al4", "name": "Untyped"}]}))
        if ep == "getTopSongs":
            if self.old:
                return Resp(err(0, "Unknown method"))
            songs = [{"id": "s1", "title": "Hit", "artist": "Band", "duration": 100}] if self.top else []
            return Resp(ok(topSongs={"song": songs}))
        if ep == "getArtistInfo2":
            if self.old:
                return Resp(err(0, "Unknown method"))
            return Resp(ok(artistInfo2={"similarArtist": [{"id": "ar2", "name": "Friend"},
                                                          {"name": "Not in library"}]}))
        return Resp(ok())


def sub_provider(sess):
    return SubsonicProvider(url="https://music.example.org", user="me", password="pw",
                            session=sess, player=FakePlayer())


def test_subsonic():
    sess = SubSession()
    p = sub_provider(sess)
    check("subsonic advertises top tracks and similar artists",
          p.capabilities.can_artist_top_tracks and p.capabilities.can_related_artists)
    ov = p.artist_overview("ar1")
    check("releaseTypes: albums (and untyped ones)", [a["id"] for a in ov["albums"]] == ["al1", "al4"])
    check("releaseTypes: singles and EPs", [a["id"] for a in ov["singles"]] == ["al2", "al3"])
    top_q = [q for ep, q in sess.calls if ep == "getTopSongs"]
    check("getTopSongs by artist NAME from getArtist", top_q and top_q[0].get("artist") == "Band", str(top_q))
    check("top songs mapped", [t["id"] for t in ov["top_tracks"]] == ["s1"])
    check("similar artists: only those with an id (in the library)", [a["id"] for a in ov["related"]] == ["ar2"])
    check("artist_albums(single) now returns singles",
          [a["id"] for a in p.artist_albums("ar1", album_type="single")["items"]] == ["al2", "al3"])
    check("artist_albums(album) returns albums", [a["id"] for a in p.artist_albums("ar1", album_type="album")["items"]] == ["al1", "al4"])
    check("no secret in the notes", "pw" not in " ".join(ov["notes"]))

    ov = sub_provider(SubSession(old_server=True)).artist_overview("ar1")
    check("old server: discography still shown", [a["id"] for a in ov["albums"]] == ["al1", "al4"])
    check("old server: both missing sections explained",
          any("Top songs" in n for n in ov["notes"]) and any("Similar artists" in n for n in ov["notes"]), str(ov["notes"]))
    ov = sub_provider(SubSession(top=False)).artist_overview("ar1")
    check("empty top songs: Last.fm hint", any("Last.fm" in n for n in ov["notes"]))


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
class UiFake:
    display_name = "Fake"
    def __init__(self, notes=None):
        self.played = []
        self.notes = notes or []
        self.opened = []
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def search(self, *a, **k): return {}
    def fmt_duration(self, ms):
        s = int((ms or 0) / 1000); return f"{s // 60}:{s % 60:02d}"
    def shuffle(self, *a, **k): pass
    def start_playback(self, **kw): self.played.append(kw)
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)
    def current_user_saved_tracks_contains(self, ids): return [False] * len(ids)
    def current_user_saved_albums_contains(self, ids): return [False] * len(ids)
    def current_user_followed_artists(self, limit=50): return {"artists": {"items": []}}
    def artist_overview(self, artist_id, artist_name=None):
        return {"top_tracks": [T(1), T(2), T(3)], "albums": [A(1)], "singles": [A(2, "single")],
                "related": [{"id": "r1", "name": "Friend", "uri": "x:artist:r1", "type": "artist"}],
                "notes": list(self.notes)}


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


def search_table(app):
    try:
        return app.query_one("#search_table")
    except Exception:
        return None


async def test_ui():
    config.LOCAL_CFG = {}
    fake = UiFake(notes=["Related artists: test note."])
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        opened = []
        app._open_album_table = lambda obj, **k: opened.append(("album", obj.get("id")))
        app._open_artist_table({"id": "ar1", "name": "Band"})
        t = await poll(lambda: (lambda x: x if x is not None and len(getattr(x, "_model_rows", []) or []) == 6 else None)(search_table(app)))
        check("artist page rendered", t is not None)
        if t is None:
            return
        await pilot.pause()
        types = [r["type_label"] for r in t._model_rows]
        check("sections in order: top tracks, albums, singles, related",
              types == ["TOP", "TOP", "TOP", "ALB", "SNG", "REL"], str(types))
        check("title says what is there",
              "3 top tracks" in str(app.right_panel.border_title) and "1 related" in str(app.right_panel.border_title),
              str(app.right_panel.border_title))
        check("album column shows the year for releases", t._model_rows[3]["album"] == "2011")
        check("provider notes reach the status line", "test note" in str(app.status_line.render()))
        t.focus(); await pilot.pause()
        t.move_cursor(row=1); await pilot.pause()
        await pilot.press("enter")
        await poll(lambda: fake.played)
        got = fake.played[-1] if fake.played else {}
        check("Enter on a top track plays the top tracks from there",
              got.get("uris") == ["x:track:t2", "x:track:t3", "x:track:t1"], str(got))
        t.move_cursor(row=3); await pilot.pause()
        await pilot.press("enter"); await pilot.pause()
        check("Enter on an album opens it", opened and opened[-1] == ("album", "a1"), str(opened))
        t.move_cursor(row=4); await pilot.pause()
        await pilot.press("enter"); await pilot.pause()
        check("Enter on a single opens it as an album", opened[-1] == ("album", "a2"), str(opened))
        # The range guard: a non-track row past the context list never plays a top track.
        fake.played.clear(); app._last_played = None
        app._play_row(5, t)
        await poll(lambda: fake.played, timeout=1.0)
        check("a row outside the top-track list does not play the wrong track",
              all(p.get("uris") != ["x:track:t1", "x:track:t2", "x:track:t3"] for p in fake.played), str(fake.played))
        # Filter composes: "/" on the artist page.
        await pilot.press("slash"); await pilot.pause()
        for ch in "song 3":
            await pilot.press("space" if ch == " " else ch)
        await poll(lambda: t.row_count == 1, timeout=2.0)
        await pilot.press("down"); await pilot.pause()
        fake.played.clear(); app._last_played = None
        await pilot.press("enter")
        await poll(lambda: fake.played)
        got = fake.played[-1] if fake.played else {}
        check("filtered top track plays from its real place in the top list",
              got.get("uris") == ["x:track:t3", "x:track:t1", "x:track:t2"], str(got))
        await pilot.press("escape"); await pilot.pause()

        # A new search result in the reused table drops the artist context.
        t2 = app._render_search_table("Results", [{"type": "track", "id": "z", "uri": "x:track:z",
                                                   "title": "Z", "artist": "", "album": ""}], check_saved=False)
        check("reused search table forgets the artist play context", getattr(t2, "_context_uris", None) is None)


async def test_ui_generic_fallback():
    """A provider object without artist_overview (adapter-only) still gets a page."""
    class NoOverview(UiFake):
        artist_overview = None
        def artist_albums(self, artist_id, album_type=None, limit=50, **k):
            return {"items": [A(7)] if album_type == "album" else []}
    config.LOCAL_CFG = {}
    app = TApp(NoOverview())
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_artist_table({"id": "ar1", "name": "Band"})
        t = await poll(lambda: (lambda x: x if x is not None and getattr(x, "_model_rows", None) else None)(search_table(app)))
        check("generic build shows the albums", t is not None and [r["id"] for r in t._model_rows] == ["a7"])
        check("and says top tracks / related are missing",
              "not available" in str(app.status_line.render()), str(app.status_line.render()))


if __name__ == "__main__":
    test_base_overview()
    test_spotify()
    test_ytmusic()
    test_subsonic()
    asyncio.run(test_ui())
    asyncio.run(test_ui_generic_fallback())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
