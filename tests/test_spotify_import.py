"""Import from Spotify into YouTube Music's local library — offline, with a
simulated Spotify account (read-only) and simulated ytmusicapi answers: good,
ambiguous and missing candidates, remasters and feat., network errors in the
middle, cancellation and resuming from the match cache. Plus the UI entry,
the picker entry, the no-token guidance and a full run from the view.

Run standalone:  python tests/test_spotify_import.py
"""
import os, sys, json, asyncio, tempfile, shutil, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui import spotify_import as si
from spt_tui.providers.ytmusic import YTMusicProvider

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


# --------------------------------------------------------------------------- #
# Simulated Spotify (spotipy-shaped answers)
# --------------------------------------------------------------------------- #
def sp_track(i, name, artists, album, dur):
    return {"id": f"sp{i}", "type": "track", "name": name, "duration_ms": dur,
            "artists": [{"name": a} for a in artists], "album": {"name": album}}

PL_TRACKS = [
    sp_track(1, "Bohemian Rhapsody - Remastered 2011", ["Queen"], "A Night At The Opera", 354000),
    sp_track(2, "Get Lucky (feat. Pharrell Williams)", ["Daft Punk", "Pharrell Williams"], "Random Access Memories", 369000),
    sp_track(3, "Intro", ["The xx"], "xx", 127000),          # ambiguous -> doubtful
    sp_track(4, "Zzqx Nonexistent", ["Nobody 4815"], "", 200000),  # not found anywhere
    sp_track(5, "Rare Upload", ["Bedroom Artist"], "Demo", 180000),  # only as a video (lax)
]
LIKED = [sp_track(10, "Blinding Lights", ["The Weeknd"], "After Hours", 200000),
         sp_track(11, "Glimpse of Us", ["Joji"], "SMITHEREENS", 233000)]


class FakeSpotify:
    def __init__(self):
        self.writes = []
    def current_user_playlists(self, limit=50, offset=0):
        items = [{"id": "PL1", "name": "Mi playlist", "items": {"total": len(PL_TRACKS)}}]
        return {"items": items[offset:offset + limit], "next": None, "total": 1}
    def playlist_items(self, pid, limit=100, offset=0, fields=None):
        items = [{"item": t} for t in PL_TRACKS] + [{"item": {"type": "episode", "id": "ep"}}]
        return {"items": items[offset:offset + limit], "next": None, "total": len(items)}
    def current_user_saved_tracks(self, limit=50, offset=0):
        items = [{"track": t, "added_at": "x"} for t in LIKED]
        return {"items": items[offset:offset + limit], "next": None, "total": len(items)}
    def current_user_saved_albums(self, limit=50, offset=0):
        items = [{"album": {"id": "al1", "name": "Nectar", "artists": [{"name": "Joji"}]}}]
        return {"items": items[offset:offset + limit], "next": None, "total": 1}
    def current_user_followed_artists(self, limit=50, after=None):
        return {"artists": {"items": [{"id": "ar1", "name": "Joji"}], "next": None,
                            "cursors": {"after": None}, "total": 1}}
    # Write endpoints: the import must never reach these.
    def playlist_add_items(self, *a, **k): self.writes.append("playlist_add_items")
    def current_user_saved_tracks_add(self, *a, **k): self.writes.append("saved_add")
    def user_playlist_create(self, *a, **k): self.writes.append("create")


# --------------------------------------------------------------------------- #
# Simulated ytmusicapi
# --------------------------------------------------------------------------- #
def song(vid, title, artists, album, secs, rtype="song", vtype="MUSIC_VIDEO_TYPE_ATV"):
    return {"resultType": rtype, "videoId": vid, "title": title, "videoType": vtype,
            "artists": [{"name": a, "id": None} for a in artists],
            "album": {"name": album, "id": None} if album else None, "duration_seconds": secs}

STRICT = {
    "bohemian rhapsody queen": [
        song("vLive", "Bohemian Rhapsody (Live Aid)", ["Queen"], "Live Aid", 360),
        song("vBR", "Bohemian Rhapsody", ["Queen"], "A Night At The Opera", 355)],
    "get lucky daft punk": [
        song("vGL", "Get Lucky (feat. Pharrell Williams and Nile Rodgers)", ["Daft Punk", "Pharrell Williams"],
             "Random Access Memories", 369)],
    "intro the xx": [song("vIntro", "Intro (Remix)", ["Someone Else"], "Other", 150)],
    "blinding lights the weeknd": [song("vBL", "Blinding Lights", ["The Weeknd"], "After Hours", 200)],
    "glimpse of us joji": [song("vGOU", "Glimpse of Us", ["Joji"], "SMITHEREENS", 234)],
}
LAX = {
    "rare upload bedroom artist": [
        song("vRare", "Bedroom Artist - Rare Upload", ["some channel"], None, 181, "video", "MUSIC_VIDEO_TYPE_UGC")],
}

def qkey(q):
    return si.normalize_title(q)


class FakeYT:
    def __init__(self):
        self.calls = []
        self.fail_queries = {}      # normalized query -> remaining failures
        self.fail_all = False
    def search(self, query, filter=None, limit=20):
        k = qkey(query)
        self.calls.append((k, filter))
        if self.fail_all or self.fail_queries.get(k, 0) > 0:
            if k in self.fail_queries:
                self.fail_queries[k] -= 1
            raise ConnectionError("network down")
        if filter == "songs":
            return list(STRICT.get(k, []))
        if filter == "albums":
            return [{"resultType": "album", "browseId": "MPREb_N", "title": "Nectar",
                     "artists": [{"name": "Joji"}], "year": "2020"}] if "nectar" in k else []
        if filter == "artists":
            return [{"resultType": "artist", "browseId": "UCjoji", "artist": "Joji"}] if "joji" in k else []
        if filter is None:
            return list(LAX.get(k, [])) + list(STRICT.get(k, []))
        return []


class Player:
    def unavailable_reason(self): return None
    def is_running(self): return False
    def state(self): return {"running": False, "idle": True}
    def close(self): pass


class env:
    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="spt_import_")
        self.old = (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG, config.CACHE_PATH,
                    config.CLIENT_ID, config.CLIENT_SECRET, config.REDIRECT_URI)
        config.CACHE_DIR = self.d
        config.CONFIG_PATH = os.path.join(self.d, "spt_config.json")
        config.CACHE_PATH = os.path.join(self.d, ".cache_spotify_token")
        config.LOCAL_CFG = {}
        config.CLIENT_ID, config.CLIENT_SECRET, config.REDIRECT_URI = "cid", "csec", "http://127.0.0.1:9/cb"
        return self
    def __exit__(self, *a):
        (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG, config.CACHE_PATH,
         config.CLIENT_ID, config.CLIENT_SECRET, config.REDIRECT_URI) = self.old
        shutil.rmtree(self.d, ignore_errors=True)


def make(yt=None):
    yt = yt or FakeYT()
    prov = YTMusicProvider(client=yt, player=Player(), which=lambda n: "/usr/bin/" + n)
    return prov, yt


def matcher(prov, **kw):
    return si.TrackMatcher(prov, min_interval=0, sleep=lambda s: None, **kw)


# --------------------------------------------------------------------------- #
def test_normalisation_and_scoring():
    n = si.normalize_title
    check("remaster suffix removed", n("Bohemian Rhapsody - Remastered 2011") == "bohemian rhapsody")
    check("(feat. …) removed", n("Get Lucky (feat. Pharrell Williams)") == "get lucky")
    check("soundtrack suffix removed", n('Lose Yourself - From "8 Mile" Soundtrack') == "lose yourself")
    check("accents folded", n("Mediterráneo") == "mediterraneo")
    check("remix kept (a remix is not the original)", "remix" in n("Despacito - Remix"))
    src = {"name": "Blinding Lights", "artists": ["The Weeknd"], "album": "After Hours", "duration_ms": 200000}
    good = {"name": "Blinding Lights", "artists": [{"name": "The Weeknd"}], "album": {"name": "After Hours"}, "duration_ms": 201000}
    wrong_artist = dict(good, artists=[{"name": "Cover Band"}])
    long_ver = dict(good, duration_ms=260000)
    s_good, s_wa, s_long = (si.score_candidate(src, c) for c in (good, wrong_artist, long_ver))
    check("exact candidate is accepted", s_good >= si.ACCEPT, f"{s_good}")
    check("wrong artist scores lower", s_wa < s_good and s_wa < si.ACCEPT, f"{s_wa}")
    check("duration ±3 s counts fully, 60 s off does not", s_long < s_good, f"{s_long} vs {s_good}")


def test_matching_cases():
    with env():
        prov, yt = make()
        m = matcher(prov)
        r = m.match_track({"id": "sp1", **{k: v for k, v in {"name": "Bohemian Rhapsody - Remastered 2011",
                           "artists": ["Queen"], "album": "A Night At The Opera", "duration_ms": 354000}.items()}})
        check("remaster: the studio song wins over the live one", r["status"] == "matched"
              and r["track"]["id"] == "vBR", repr(r))
        r = m.match_track({"id": "sp2", "name": "Get Lucky (feat. Pharrell Williams)",
                           "artists": ["Daft Punk", "Pharrell Williams"], "album": "Random Access Memories",
                           "duration_ms": 369000})
        check("feat. in either title: matched", r["status"] == "matched" and r["track"]["id"] == "vGL", repr(r))
        r = m.match_track({"id": "sp3", "name": "Intro", "artists": ["The xx"], "album": "xx", "duration_ms": 127000})
        check("ambiguous candidate: doubtful, not matched", r["status"] in ("doubtful", "not_found"), repr(r))
        n_before = len(yt.calls)
        r = m.match_track({"id": "sp5", "name": "Rare Upload", "artists": ["Bedroom Artist"], "album": "Demo",
                           "duration_ms": 180000})
        check("nothing with filter=songs -> second, lax search (no filter)",
              [c[1] for c in yt.calls[n_before:]] == ["songs", None], repr(yt.calls[n_before:]))
        check("…finds the channel upload", r["status"] in ("matched", "doubtful") and r["track"]["id"] == "vRare", repr(r))
        r = m.match_track({"id": "sp4", "name": "Zzqx Nonexistent", "artists": ["Nobody 4815"], "album": "", "duration_ms": 200000})
        check("no candidate anywhere: not found", r["status"] == "not_found" and r["track"] is None)


def test_cache_and_retries():
    with env():
        prov, yt = make()
        m = matcher(prov)
        src = {"id": "sp10", "name": "Blinding Lights", "artists": ["The Weeknd"], "album": "After Hours", "duration_ms": 200000}
        m.match_track(src); m.save()
        n = len(yt.calls)
        m.match_track(src)
        check("cached: no second search", len(yt.calls) == n)
        m2 = matcher(prov)
        m2.match_track(src)
        check("cache persisted and reused by a new matcher (resume)", len(yt.calls) == n
              and os.path.exists(m.cache_path))
        nf = {"id": "sp4", "name": "Zzqx Nonexistent", "artists": ["Nobody 4815"], "album": "", "duration_ms": 1}
        m2.match_track(nf); n2 = len(yt.calls)
        m2.match_track(nf)
        check("not found is cached too", len(yt.calls) == n2)
        m2.match_track(nf, retry_not_found=True)
        check("…but can be retried on demand", len(yt.calls) > n2)

        yt.fail_queries[qkey("glimpse of us joji")] = 2
        r = m2.match_track({"id": "sp11", "name": "Glimpse of Us", "artists": ["Joji"], "album": "SMITHEREENS", "duration_ms": 233000})
        check("transient network error retried, then matched", r["status"] == "matched")
        yt.fail_all = True
        try:
            m2.match_track({"id": "spX", "name": "Other", "artists": ["X"], "album": "", "duration_ms": 1}); raised = False
        except ConnectionError:
            raised = True
        check("persistent network error raises ConnectionError", raised)
        check("…and is NOT cached (retried next time)", "spX" not in m2.cache["tracks"])

        clock = [0.0]; slept = []
        m3 = si.TrackMatcher(prov, min_interval=0.5, now=lambda: clock[0],
                             sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
        yt.fail_all = False
        m3._search("a", "track", False); m3._search("b", "track", False)
        check("rate limit: pauses between searches", slept and abs(slept[-1] - 0.5) < 1e-6, repr(slept))


def test_readonly_spotify():
    fake = FakeSpotify()
    ro = si.ReadOnlySpotify(fake)
    blocked = 0
    for name in ("playlist_add_items", "current_user_saved_tracks_add", "user_playlist_create",
                 "current_user_follow_artists", "start_playback"):
        try:
            getattr(ro, name)
        except PermissionError:
            blocked += 1
    check("every write / playback method is refused", blocked == 5)
    check("reads are allowed", ro.current_user_playlists(limit=50)["items"][0]["name"] == "Mi playlist")


def run_import(prov, yt, selection, *, cancel=None, progress=None):
    fake = FakeSpotify()
    src = si.SpotifySource(si.ReadOnlySpotify(fake))
    imp = si.SpotifyImporter(src, prov, matcher=matcher(prov), cancel=cancel,
                             progress=progress or (lambda m: None))
    return imp.run(selection), fake, imp


def test_full_import():
    with env():
        prov, yt = make()
        msgs = []
        res, fake, imp = run_import(prov, yt, [{"kind": "playlist", "id": "PL1", "name": "Mi playlist"},
                                               {"kind": "liked"}, {"kind": "albums"}, {"kind": "artists"}],
                                    progress=msgs.append)
        s = res["summary"]
        check("nothing written to Spotify", fake.writes == [])
        check("summary counts matched / doubtful / not found",
              s["matched"] >= 5 and s["not_found"] >= 1 and s["matched"] + s["doubtful"] + s["not_found"] == 9, repr(s))
        check("progress reported per track", any("Importing 'Mi playlist' 3/5" in m for m in msgs), repr(msgs[:6]))
        pls = [p for p in prov.user_playlists()["items"] if p.get("_local")]
        check("Spotify playlist -> local playlist with its original name", [p["name"] for p in pls] == ["Mi playlist"])
        items = [e["item"] for e in prov.playlist_items(pls[0]["id"])["items"]]
        ids = [t["id"] for t in items]
        check("matched tracks in Spotify order (episode skipped)", ids[:2] == ["vBR", "vGL"] and "vRare" in ids, repr(ids))
        doubtful = [t for t in items if "(? check match)" in t["name"]]
        check("doubtful matches imported and marked", doubtful or s["doubtful"] == 0, repr([t["name"] for t in items]))
        liked = [e["track"]["id"] for e in prov.saved_tracks()["items"]]
        check("Liked Songs imported, Spotify order kept (newest first)", liked == ["vBL", "vGOU"], repr(liked))
        check("saved album matched into Saved Albums", prov.saved_albums()["items"][0]["album"]["id"] == "MPREb_N")
        check("followed artist into Saved Artists", prov.followed_artists()["items"][0]["id"] == "UCjoji")
        rep = json.load(open(res["report"], encoding="utf-8"))
        nf = [x["name"] for it in rep["items"] for x in it["not_found"]]
        check("report lists the not-found track", "Zzqx Nonexistent" in nf, repr(nf))
        lk = [it for it in rep["items"] if it["kind"] == "liked"][0]
        check("report: liked songs say how many were really new", lk["added_new"] == len(liked)
              and lk["merged_or_already_there"] == lk["imported"] - lk["added_new"] == 0, repr(lk))
        check("report next to the config, with the summary", os.path.dirname(res["report"]) == config.CACHE_DIR
              and rep["summary"] == s)
        n_calls = len(yt.calls)
        res2, _, _ = run_import(prov, yt, [{"kind": "playlist", "id": "PL1", "name": "Mi playlist"}])
        pls2 = [p for p in prov.user_playlists()["items"] if p.get("_local")]
        check("re-import reuses the cache (no new searches)", len(yt.calls) == n_calls)
        check("…and refreshes the same local playlist (no duplicate)", len(pls2) == 1
              and len(prov.playlist_items(pls2[0]["id"])["items"]) == len(items))


def test_liked_duplicates_are_reported():
    """Two Spotify likes that match the same YouTube video: 2 matched, 1 added, 1 reported as merged."""
    with env():
        prov, yt = make()
        _, _, imp = run_import(prov, yt, [{"kind": "liked"}])
        t = dict(prov.saved_tracks()["items"][0]["track"])
        imp.library.remove("tracks", [t["id"]])
        class Src:
            def liked_tracks(self_):   # the same song twice, as two Spotify releases of it
                return [{"id": "s1", "name": t["name"], "artists": [a["name"] for a in t["artists"]],
                         "album": "A", "duration_ms": t["duration_ms"]},
                        {"id": "s2", "name": t["name"], "artists": [a["name"] for a in t["artists"]],
                         "album": "B", "duration_ms": t["duration_ms"]}]
        imp.source = Src()
        imp.summary = {"matched": 0, "doubtful": 0, "not_found": 0, "errors": 0}
        imp.items = []
        imp._import_liked()
        e = imp.items[-1]
        check("2 songs matched the same video", e["imported"] == 2, repr(e))
        check("only 1 was new", e["added_new"] == 1 and e["merged_or_already_there"] == 1, repr(e))


def test_network_drop_and_resume():
    with env():
        prov, yt = make()
        yt.fail_queries[qkey("get lucky daft punk")] = 99   # this one keeps failing
        res, _, imp = run_import(prov, yt, [{"kind": "playlist", "id": "PL1", "name": "Mi playlist"}])
        check("a failing track counts as an error, the import goes on", res["summary"]["errors"] == 1
              and not res["aborted"], repr(res["summary"]))
        yt.fail_queries.clear()
        res2, _, _ = run_import(prov, yt, [{"kind": "playlist", "id": "PL1", "name": "Mi playlist"}])
        pl = [p for p in prov.user_playlists()["items"] if p.get("_local")][0]
        ids = [e["item"]["id"] for e in prov.playlist_items(pl["id"])["items"]]
        check("re-run retries the failed one and fills the playlist", "vGL" in ids and res2["summary"]["errors"] == 0, repr(ids))

        os.remove(os.path.join(config.CACHE_DIR, si.CACHE_FILE))   # nothing cached: must search
        prov2, yt2 = make()
        yt2.fail_all = True
        res3, _, _ = run_import(prov2, yt2, [{"kind": "liked"}, {"kind": "playlist", "id": "PL1", "name": "Mi playlist"}])
        check("network down: aborts after repeated failures with a clear message",
              res3["aborted"] and "network" in res3["aborted"], repr(res3["aborted"]))


def test_cancel_and_resume():
    with env():
        prov, yt = make()
        cancel = threading.Event()
        seen = []
        def progress(msg):
            seen.append(msg)
            if "2/5" in msg:
                cancel.set()
        res, _, _ = run_import(prov, yt, [{"kind": "playlist", "id": "PL1", "name": "Mi playlist"},
                                          {"kind": "liked"}], cancel=cancel, progress=progress)
        check("cancel stops the job", res["cancelled"] and not any("Liked" in m for m in seen), repr(seen[-2:]))
        check("…report still written", os.path.exists(res["report"]))
        n = len(yt.calls)
        res2, _, _ = run_import(prov, yt, [{"kind": "playlist", "id": "PL1", "name": "Mi playlist"}, {"kind": "liked"}])
        check("resume: the already-matched track is not searched again",
              not any(c[0] == qkey("bohemian rhapsody queen") for c in yt.calls[n:]))
        check("resume completes", not res2["cancelled"] and len(prov.saved_tracks()["items"]) == 2)


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
async def test_ui():
    from spt_tui.app import SptPy
    from spt_tui.app.provider_switch import ProviderPickerScreen
    from spt_tui.app.spotify_import_view import IMPORT_ENTRY, PICKER_IMPORT_ID
    from textual.widgets import SelectionList, OptionList

    class TApp(SptPy):
        def __init__(self, prov):
            super().__init__(); self.spotify = prov

    def lib_names(app):
        return [(getattr(li, "data", {}) or {}).get("name") for li in app.lib_list.children]

    def status(app):
        try: return str(app.status_line.render())
        except Exception: return ""

    async def poll(fn, timeout=6.0):
        loop = asyncio.get_event_loop(); end = loop.time() + timeout
        while loop.time() < end:
            try:
                if fn(): return True
            except Exception:
                pass
            await asyncio.sleep(0.03)
        return False

    with env():
        class SpotifyLike:
            def has_cached_token(self): return True
            def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
            def get_playback(self): return {}
            def recently_played(self, limit=50): return {"items": []}
            def ensure(self): return self
            def fmt_duration(self, ms): return "0:00"
        app = TApp(SpotifyLike())
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            check("Spotify itself: no 'Import from Spotify' entry", IMPORT_ENTRY not in lib_names(app))

        prov, yt = make()
        app = TApp(prov)
        app._spotify_reader_factory = lambda: si.ReadOnlySpotify(FakeSpotify())
        app._import_matcher_factory = lambda p: matcher(p)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            check("ytmusic + Spotify credentials: entry shown (last)", lib_names(app)[-1] == IMPORT_ENTRY, repr(lib_names(app)))
            await pilot.press("P")
            await poll(lambda: isinstance(app.screen, ProviderPickerScreen))
            ol = app.screen.query_one("#provider_options", OptionList)
            ids = [ol.get_option_at_index(i).id for i in range(ol.option_count)]
            check("the provider picker offers 'Import from Spotify…'", PICKER_IMPORT_ID in ids, repr(ids))
            await pilot.press("escape"); await pilot.pause()

            app.action_import_from_spotify(); await pilot.pause()
            check("no Spotify token: explains how to sign in, nothing breaks",
                  "sign in" in str(app.right_panel.render()).lower() and not getattr(app, "_import_running", False))

            open(config.CACHE_PATH, "w").write("{}")      # a cached token exists now
            app.action_import_from_spotify()
            ok = await poll(lambda: isinstance(getattr(app, "import_selection", None), SelectionList)
                            and app.import_selection.option_count == 4)
            check("selection list: Liked / Albums / Artists / each playlist", ok)
            sel = app.import_selection
            sel.select("liked"); sel.select("pl:PL1")
            app.on_button_pressed(type("E", (), {"button": app._import_start_button, "stop": lambda self: None})())
            ok = await poll(lambda: not app._import_running and "Import finished" in status(app), timeout=10)
            check("import runs in the background and reports a summary", ok, status(app))
            check("…counts shown", "matched" in status(app) and "not found" in status(app), status(app))
            ok = await poll(lambda: "Mi playlist" in [(getattr(li, "data", {}) or {}).get("name") for li in app.pl_list.children])
            check("the imported playlist appears in the sidebar", ok)

            # Cancel with Esc while running (slow the matcher down).
            slow = threading.Event()
            class SlowMatcher(si.TrackMatcher):
                def match_track(self, src, **kw):
                    slow.wait(0.2)
                    return super().match_track(src, **kw)
            app._import_matcher_factory = lambda p: SlowMatcher(p, min_interval=0, sleep=lambda s: None,
                                                                cache_path=os.path.join(config.CACHE_DIR, "c2.json"))
            app.action_import_from_spotify()
            await poll(lambda: isinstance(getattr(app, "import_selection", None), SelectionList)
                       and app.import_selection.option_count == 4)
            app.import_selection.select("pl:PL1")
            app.on_button_pressed(type("E", (), {"button": app._import_start_button, "stop": lambda self: None})())
            await poll(lambda: app._import_running)
            app.switch_provider("spotify")
            await pilot.pause()
            check("provider switch refused while importing",
                  app.spotify is prov and not getattr(app, "_switching_provider", False))
            await pilot.press("escape")
            ok = await poll(lambda: not app._import_running and "cancelled" in status(app).lower(), timeout=10)
            check("Esc cancels the running import", ok, status(app))


def test_spotify_page_retries():
    """One slow page used to abort the whole import (spotipy cuts at 5 s)."""
    import requests
    pauses = []
    n = {"c": 0}
    def flaky(off):
        n["c"] += 1
        if n["c"] <= 2:
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=5)")
        return {"items": [1], "next": None}
    check("a page that times out twice then works is returned",
          si._fetch_page(flaky, 0, sleep=pauses.append) == {"items": [1], "next": None} and n["c"] == 3)
    check("it waits a growing pause between tries", pauses == [1.0, 2.0], str(pauses))
    n["c"] = -100
    try:
        si._fetch_page(flaky, 0, sleep=lambda s: None); ok = False
    except requests.exceptions.ReadTimeout:
        ok = n["c"] == -100 + si.PAGE_RETRIES + 1
    check("a page that never answers raises after the retries", ok, str(n))
    calls = []
    def bad(off):
        calls.append(off); raise ValueError("bug")
    try:
        si._fetch_page(bad, 0, sleep=lambda s: None); ok = False
    except ValueError:
        ok = len(calls) == 1
    check("other errors are not retried", ok)
    check("the timeout used for Spotify reads is above spotipy's 5 s", si.READ_TIMEOUT > 5)


ALL = [test_spotify_page_retries, test_liked_duplicates_are_reported, test_normalisation_and_scoring, test_matching_cases, test_cache_and_retries, test_readonly_spotify,
       test_full_import, test_network_drop_and_resume, test_cancel_and_resume]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    try:
        asyncio.run(test_ui())
    except Exception:
        check("test_ui (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (spotify_import) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name, "::", (detail or "").strip().splitlines()[-1] if detail else "")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
