"""YouTube Music against *recorded* ytmusicapi responses
(tests/fixtures/ytmusic_recorded.json, captured anonymously from the real
API) plus simulated failures: mapping of the real shapes, the local library
(likes, saved albums/artists, followed and local playlists, history) driven
through the same spotipy-style calls the UI makes, and what happens when
things vanish (video unavailable, playlist 404, network down).

Run standalone:  python tests/test_ytmusic_library.py
"""
import os, sys, json, copy, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.providers.ytmusic import YTMusicProvider, clean_artist_name, split_channel_upload
from spt_tui.providers.base import ProviderError
from spt_tui.local_library import LocalLibrary

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

FIX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "fixtures", "ytmusic_recorded.json"), encoding="utf-8"))


class Replay:
    """ytmusicapi.YTMusic stand-in that answers with the recorded responses.
    `fail` maps a method name (or '*') to an exception to raise."""
    def __init__(self):
        self.fail = {}
        self.calls = []
    def _go(self, name, value):
        self.calls.append(name)
        exc = self.fail.get(name) or self.fail.get("*")
        if exc is not None:
            raise exc
        return copy.deepcopy(value)
    def search(self, query, filter=None, limit=20):
        key = {"songs": "search_songs", "albums": "search_albums", "artists": "search_artists",
               "community_playlists": "search_playlists"}[filter]
        return self._go("search", FIX[key])
    def get_artist(self, cid): return self._go("get_artist", FIX["get_artist"])
    def get_album(self, bid):
        return self._go("get_album", FIX["get_single"] if bid == "MPREb_xFKzVdd04EL" else FIX["get_album"])
    def get_playlist(self, pid, limit=None): return self._go("get_playlist", FIX["get_playlist"])
    def get_song(self, vid):
        if vid == "GONE":
            # What ytmusicapi returns for a removed / private video.
            return self._go("get_song", {"playabilityStatus": {"status": "ERROR", "reason": "Video unavailable"}})
        song = copy.deepcopy(FIX["get_song"])
        song["videoDetails"]["videoId"] = vid
        if vid != "20zVdUxZdwg":
            song["videoDetails"]["title"] = f"Song {vid}"
        return self._go("get_song", song)


class Player:
    def __init__(self):
        self.loaded = None; self.path = None; self.errors = []; self.entries = []
        self.st = {"running": False, "idle": True, "paused": True, "position": 0.0, "duration": 0.0, "index": -1, "volume": 70}
    def unavailable_reason(self): return None
    def load(self, urls, start_index=0):
        self.loaded = (list(urls), start_index); self.path = urls[start_index]
        self.entries = [{"filename": u, "id": i + 1} for i, u in enumerate(urls)]
        self.st.update(running=True, idle=False, paused=False)
    def append(self, url): pass
    def is_running(self): return self.st["running"]
    def get(self, prop, default=None): return self.path if prop == "path" else default
    def state(self): return dict(self.st)
    def take_errors(self):
        e, self.errors = self.errors, []
        return e
    def playlist_entries(self): return list(self.entries)
    def close(self): pass


class env:
    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="spt_ytlib_")
        self.old = (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG)
        config.CACHE_DIR = self.d
        config.CONFIG_PATH = os.path.join(self.d, "spt_config.json")
        config.LOCAL_CFG = {}
        return self
    def __exit__(self, *a):
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG = self.old
        shutil.rmtree(self.d, ignore_errors=True)


def make():
    yt, pl = Replay(), Player()
    p = YTMusicProvider(client=yt, player=pl, which=lambda n: "/usr/bin/" + n)
    return p, yt, pl


# --------------------------------------------------------------------------- #
def test_recorded_mapping():
    with env():
        p, yt, _ = make()
        songs = p.search("joji glimpse of us", "track", 10)["items"]
        s = songs[0]
        check("song from the real search shape", s["id"] == "20zVdUxZdwg" and s["name"] == "Glimpse of Us"
              and s["artists"][0]["name"] == "Joji" and s["album"]["name"] == "SMITHEREENS"
              and s["duration_ms"] == 234000, repr(s))
        albums = p.search("nectar", "album", 5)["items"]
        check("album from search", albums[0]["id"] == "MPREb_GyHKAaehWoW" and albums[0]["artists"][0]["name"] == "Joji")
        arts = p.search("joji", "artist", 5)["items"]
        check("artist from search", arts[0]["id"] == "UCXi-Rme73-wjVK5IE78a3SQ" and arts[0]["name"] == "Joji")
        pls = p.search("joji mix", "playlist", 5)["items"]
        check("playlist from search (VL stripped, author)", pls[0]["id"] == "PLnyBUpoNe_27GHOKI2eMGU7E_YB65APh1"
              and pls[0]["owner"]["display_name"] == "SegaGamerSonic")
        a_albums = p.artist_albums("UCXi-Rme73-wjVK5IE78a3SQ", album_type="album")["items"]
        check("artist page albums carry the artist (real response has artists: [])",
              a_albums and all(a["artists"] and a["artists"][0]["name"] == "Joji" for a in a_albums),
              repr([a["artists"] for a in a_albums]))
        singles = p.artist_albums("UCXi-Rme73-wjVK5IE78a3SQ", album_type="single")["items"]
        check("artist page singles carry the artist", singles and singles[0]["artists"][0]["name"] == "Joji"
              and singles[0]["id"] == "MPREb_xFKzVdd04EL")
        tracks = p.album_tracks("MPREb_GyHKAaehWoW")["items"]
        check("album tracks: album given as a plain string is mapped", tracks[0]["album"]["name"] == "Nectar"
              and tracks[0]["name"] == "Ew" and tracks[0]["duration_ms"] == 208000, repr(tracks[0]))
        single = p.album_tracks("MPREb_xFKzVdd04EL")["items"]
        check("single opens with its track(s)", single and single[0]["name"] == "Beautiful"
              and [a["name"] for a in single[0]["artists"]] == ["Anyma", "Joji"])
        items = p.playlist_items("PLnyBUpoNe_27GHOKI2eMGU7E_YB65APh1")["items"]
        check("public playlist tracks", len(items) == 5 and items[0]["item"]["name"] == "SLOW DANCING IN THE DARK")
        meta = p.playlist("https://music.youtube.com/playlist?list=PLnyBUpoNe_27GHOKI2eMGU7E_YB65APh1")
        check("playlist by URL", meta["name"] == "Joji Mix" and meta["tracks"]["total"] == 19)
        t = p.track("20zVdUxZdwg")
        check("get_song -> track", t["name"] == "Glimpse of Us" and t["duration_ms"] == 233000)


def test_channel_uploads_and_topic():
    ugc = FIX["search_videos_ugc"]
    p = YTMusicProvider(client=Replay(), player=Player(), which=lambda n: "/x",
                        library=LocalLibrary("yt-test", path=os.path.join(tempfile.mkdtemp(), "l.json")))
    a = p._map_song(ugc[0])
    check("real channel upload: artist taken from the title, channel dropped",
          a["name"] == "Unsaved Info Extended Version" and [x["name"] for x in a["artists"]] == ["Joji"], repr(a))
    b = p._map_song(ugc[1])
    check("…brackets around the artist removed", [x["name"] for x in b["artists"]] == ["Joji"]
          and b["name"].startswith("Feeling Like The End"), repr(b))
    title, arts = split_channel_upload("JOJI - I Don't Feel Like Killing Myself Today (IDFLKMST)",
                                       [{"name": "2Restarted"}], "MUSIC_VIDEO_TYPE_UGC")
    check("the case from the screenshot", title.startswith("I Don't Feel Like") and arts[0]["name"] == "JOJI")
    title2, arts2 = split_channel_upload("Glimpse of Us", [{"name": "Joji"}], "MUSIC_VIDEO_TYPE_ATV")
    check("songs are left alone", title2 == "Glimpse of Us" and arts2[0]["name"] == "Joji")
    check("'- Topic' channels cleaned", clean_artist_name("Joji - Topic") == "Joji")


def test_local_library_through_the_adapter():
    with env():
        p, yt, _ = make()
        sp = p.ensure()
        p.search("joji", "track", 10)
        check("f on a song: saved locally", p.save_tracks(["20zVdUxZdwg"])
              and sp.current_user_saved_tracks_contains(["20zVdUxZdwg", "nope"]) == [True, False])
        liked = p.saved_tracks()["items"]
        check("Liked Songs lists it with its metadata", liked[0]["track"]["name"] == "Glimpse of Us")
        p.search("nectar", "album", 5)
        sp.current_user_saved_albums_add(["MPREb_GyHKAaehWoW"])
        check("f on an album: Saved Albums", sp.current_user_saved_albums_contains(["MPREb_GyHKAaehWoW"]) == [True]
              and sp.current_user_saved_albums()["items"][0]["album"]["name"] == "Nectar")
        sp.current_user_saved_albums_delete(["MPREb_GyHKAaehWoW"])
        check("f again removes it", sp.current_user_saved_albums_contains(["MPREb_GyHKAaehWoW"]) == [False])
        p.search("joji", "artist", 5)
        sp.current_user_follow_artists(["UCXi-Rme73-wjVK5IE78a3SQ"])
        page = sp.current_user_followed_artists(limit=50)
        check("f on an artist: Saved Artists", page["artists"]["items"][0]["name"] == "Joji")
        sp.current_user_unfollow_artists(["UCXi-Rme73-wjVK5IE78a3SQ"])
        check("unfollow", sp.current_user_followed_artists(limit=50)["artists"]["items"] == [])
        pid = "PLnyBUpoNe_27GHOKI2eMGU7E_YB65APh1"
        sp.local_follow_playlists([pid])
        check("f on a playlist: in the sidebar list (persisted in ytmusic_playlists)",
              sp.current_user_follows_playlists_contains([pid]) == [True]
              and config.LOCAL_CFG["ytmusic_playlists"][0]["id"] == pid
              and any(x["id"] == pid for x in p.user_playlists()["items"]))
        sp.local_unfollow_playlists([pid])
        check("f again removes it", sp.current_user_follows_playlists_contains([pid]) == [False])
        check("no AttributeError for shows/episodes checks", sp.current_user_saved_shows_contains(["x"]) == [False]
              and sp.current_user_saved_episodes_contains(["y"]) == [False])


def test_local_playlists():
    with env():
        p, yt, pl = make()
        sp = p.ensure()
        p.search("joji", "track", 10)
        new = p.create_playlist("Road", public=False)
        pid = new["id"]
        check("Create Playlist -> local playlist", pid.startswith("local:") and new["owner"]["display_name"] == "local library")
        check("listed in the sidebar", any(x["id"] == pid for x in p.user_playlists()["items"]))
        ok = p.add_items_to_playlist(pid, ["ytmusic:track:20zVdUxZdwg", "ytmusic:track:UGB_Bsm5Unk"])
        items = p.playlist_items(pid)["items"]
        check("Add to Playlist", ok and [e["item"]["id"] for e in items] == ["20zVdUxZdwg", "UGB_Bsm5Unk"])
        check("adding to someone else's public playlist is refused",
              not p.add_items_to_playlist("PLnyBUpoNe_27GHOKI2eMGU7E_YB65APh1", ["ytmusic:track:x"]))
        p.start_playback(context_uri=f"ytmusic:playlist:{pid}", offset={"position": 1})
        check("plays the local playlist from the chosen track",
              pl.loaded and pl.loaded[1] == 1 and pl.loaded[0][1].endswith("UGB_Bsm5Unk"))
        sp.playlist_remove_specific_occurrences_of_items(pid, [{"uri": "ytmusic:track:20zVdUxZdwg", "positions": [0]}])
        check("Ctrl+D removes one entry", [e["item"]["id"] for e in p.playlist_items(pid)["items"]] == ["UGB_Bsm5Unk"])
        check("rename", p.can_rename_playlist(pid) and p.rename_playlist(pid, "Trip")
              and p.playlist(pid)["name"] == "Trip")
        check("public playlists cannot be renamed", not p.can_rename_playlist("PLnyBUpoNe_27GHOKI2eMGU7E_YB65APh1"))
        sp.current_user_unfollow_playlist(pid)
        check("delete", p.library.playlist(pid) is None)


def test_unavailable_and_offline():
    with env():
        p, yt, pl = make()
        sp = p.ensure()
        pid = "PLnyBUpoNe_27GHOKI2eMGU7E_YB65APh1"
        p.follow_playlist(pid)
        p.search("joji", "track", 10)
        p.save_tracks(["20zVdUxZdwg"])
        p.search("nectar", "album", 5)
        sp.current_user_saved_albums_add(["MPREb_GyHKAaehWoW"])
        local = p.create_playlist("Offline")["id"]
        p.add_items_to_playlist(local, ["ytmusic:track:20zVdUxZdwg"])

        # Playlist made private / deleted upstream.
        yt.fail["get_playlist"] = RuntimeError("HTTP Error 404: Not Found")
        try:
            p.playlist_items(pid); err = None
        except ProviderError as exc:
            err = exc
        check("playlist 404 -> clear ProviderError", err is not None and "unavailable" in str(err))
        sidebar = {x["id"]: x for x in p.user_playlists()["items"]}
        check("…the followed playlist is NOT removed", pid in sidebar and config.LOCAL_CFG["ytmusic_playlists"])
        check("…it is shown as unavailable", "(unavailable)" in sidebar[pid]["name"], sidebar[pid]["name"])
        del yt.fail["get_playlist"]
        p.playlist_items(pid)
        check("…and the mark clears once it loads again",
              "(unavailable)" not in {x["id"]: x for x in p.user_playlists()["items"]}[pid]["name"])

        # Network down: everything stored keeps working.
        yt.fail["*"] = ConnectionError("network unreachable")
        try:
            p.search("x", "track"); s_err = None
        except ProviderError as exc:
            s_err = exc
        check("network down: search fails with a clear error", s_err is not None)
        check("network down: Liked Songs still listed", p.saved_tracks()["items"][0]["track"]["id"] == "20zVdUxZdwg")
        check("network down: Saved Albums still listed", p.saved_albums()["items"][0]["album"]["id"] == "MPREb_GyHKAaehWoW")
        check("network down: local playlist still opens", p.playlist_items(local)["items"][0]["item"]["name"] == "Glimpse of Us")
        p2 = YTMusicProvider(client=yt, player=Player(), which=lambda n: "/usr/bin/" + n)
        check("network down: a new instance (provider switch) still sees the library",
              p2.check_saved_tracks(["20zVdUxZdwg"]) == [True])
        check("network down: liked track resolves from the library to play it",
              (p2._lookup("ytmusic:track:20zVdUxZdwg") or {}).get("name") == "Glimpse of Us")
        p.start_playback(context_uri=f"ytmusic:playlist:{local}", offset={"position": 0})
        check("network down: local playlist playback still queues mpv (audio needs the network)",
              pl.loaded and pl.loaded[0][0].endswith("20zVdUxZdwg"))
        check("network down: removing works (f / Ctrl+D)", p.remove_tracks(["20zVdUxZdwg"])
              and p.check_saved_tracks(["20zVdUxZdwg"]) == [False])
        yt.fail.clear()


def test_failure_streak_does_not_mark_everything():
    """53 tracks marked in 14 s (the network or yt-dlp failing) must not hide the whole library."""
    with env():
        p, yt, pl = make()
        tr = [{"id": f"id{i:09d}", "uri": f"ytmusic:track:id{i:09d}", "name": f"T{i}", "artists": [],
               "duration_ms": 1000} for i in range(8)]
        p.library.add("tracks", tr)
        p.on_track_error(tr[0], "loading failed"); p.on_track_error(tr[1], "loading failed")
        check("the first two failures are marked (they may be really dead)",
              p.library.unavailable_reason(tr[0]["uri"]) and p.library.unavailable_reason(tr[1]["uri"]))
        p.on_track_error(tr[2], "loading failed")
        check("the third in a row undoes the streak's marks",
              all(p.library.unavailable_reason(t["uri"]) is None for t in tr[:3]))
        notes = p.take_notices()
        check("…and says why, once", sum("in a row" in n for n in notes) == 1, repr(notes))
        for t in tr[3:]:
            p.on_track_error(t, "loading failed")
        check("while it keeps failing nothing is marked", not any(p.library.unavailable_reason(t["uri"]) for t in tr))
        check("a long streak does not repeat the notice", not any("in a row" in n for n in p.take_notices()))
        p.on_track_started(tr[0])
        p.on_track_error(tr[5], "loading failed")
        check("after a track plays, a single failure is marked again", p.library.unavailable_reason(tr[5]["uri"]) == "loading failed")
        p.on_track_error(tr[5], "loading failed")
        check("the same track failing twice is not a streak", p.library.unavailable_reason(tr[5]["uri"]) == "loading failed")


def test_stale_mark_not_shown_in_lists():
    with env():
        p, yt, pl = make()
        t = {"id": "stale000001", "uri": "ytmusic:track:stale000001", "name": "Old", "artists": [], "duration_ms": 1000}
        p.library.add("tracks", [t])
        p.library.mark_unavailable(t["uri"], "loading failed")
        check("fresh mark is shown", "(unavailable)" in p.saved_tracks()["items"][0]["track"]["name"])
        p.library._load()["unavailable_at"][t["uri"]] = 0
        check("stale mark is not shown", "(unavailable)" not in p.saved_tracks()["items"][0]["track"]["name"])


def test_network_error_does_not_mark_unavailable():
    import requests
    with env():
        p, yt, pl = make()
        def boom(exc):
            def fn(): raise exc
            return fn
        for exc in (requests.exceptions.ReadTimeout("slow"), requests.exceptions.ConnectionError("down"),
                    TimeoutError()):
            try:
                p._remote("album", "ytmusic:album:X", boom(exc)); ok = False
            except ProviderError:
                ok = True
            check(f"{type(exc).__name__}: raises ProviderError", ok)
            check(f"{type(exc).__name__}: NOT marked unavailable", p.library.unavailable_reason("ytmusic:album:X") is None)
        try:
            p._remote("album", "ytmusic:album:X", boom(ValueError("deleted")))
        except ProviderError:
            pass
        check("a real failure (not network) still marks it", p.library.unavailable_reason("ytmusic:album:X") == "ValueError")
        p._remote("album", "ytmusic:album:X", lambda: {})
        check("success clears the mark", p.library.unavailable_reason("ytmusic:album:X") is None)


def test_video_unavailable_at_play_time():
    with env():
        p, yt, pl = make()
        gone = {"id": "GONE", "uri": "ytmusic:track:GONE", "name": "Deleted Video", "artists": [], "duration_ms": 1000}
        ok_t = {"id": "20zVdUxZdwg", "uri": "ytmusic:track:20zVdUxZdwg", "name": "Glimpse of Us", "artists": [], "duration_ms": 234000}
        p.library.add("tracks", [gone, ok_t])
        p.start_playback(uris=["ytmusic:track:GONE", "ytmusic:track:20zVdUxZdwg"])
        check("both queued the first time", len(pl.loaded[0]) == 2)
        # mpv: the first entry failed to load and it moved on to the next.
        pl.errors.append({"event": "end-file", "reason": "error", "playlist_entry_id": 1,
                          "file_error": "loading failed"})
        pl.path = pl.loaded[0][1]
        pl.st.update(position=1.0, duration=234.0)
        pb = p.get_playback()
        notes = p.take_notices()
        check("playback continues with the next track", pb["item"]["id"] == "20zVdUxZdwg")
        check("notice: skipped unavailable track", any("Deleted Video" in n for n in notes), repr(notes))
        check("track marked unavailable, still liked", p.library.unavailable_reason("ytmusic:track:GONE")
              and p.check_saved_tracks(["GONE"]) == [True])
        liked = {e["track"]["id"]: e["track"] for e in p.saved_tracks()["items"]}
        check("shown as unavailable in Liked Songs", "(unavailable)" in liked["GONE"]["name"])
        p.start_playback(uris=["ytmusic:track:20zVdUxZdwg", "ytmusic:track:GONE"])
        check("next time it is skipped up front", len(pl.loaded[0]) == 1 and pl.loaded[0][0].endswith("20zVdUxZdwg"))
        check("…with a notice", any("Skipped 1" in n for n in p.take_notices()))
        # a mark that is no longer fresh (or from an older version) must not hide a track that may work now
        p.library._load()["unavailable_at"]["ytmusic:track:GONE"] = 0
        p.take_notices()
        p.start_playback(uris=["ytmusic:track:20zVdUxZdwg", "ytmusic:track:GONE"])
        check("a stale mark no longer hides the track", len(pl.loaded[0]) == 2 and not p.take_notices())
        p.library.mark_unavailable("ytmusic:track:GONE", "loading failed")
        p._notice("same"); p._notice("same"); p._notice("other"); p._notice("same")
        check("a notice repeated back to back is kept once", p.take_notices() == ["same", "other", "same"])
        p.start_playback(uris=["ytmusic:track:GONE"])
        check("playing it explicitly still retries it", pl.loaded[0][0].endswith("GONE"))
        check("unplayable get_song -> no metadata, no crash", p.track("GONE") == {})
        check("f on the unavailable track still removes it", p.remove_tracks(["GONE"])
              and p.check_saved_tracks(["GONE"]) == [False])
        pl.path = pl.loaded[0][0]
        p.library.add("tracks", [ok_t])


def test_history():
    with env():
        p, yt, pl = make()
        p.search("joji", "track", 10)
        p.start_playback(uris=["ytmusic:track:20zVdUxZdwg"])
        pl.st.update(position=0.0, duration=0.0)
        p.get_playback()
        check("still loading (no duration): not recorded, reported paused",
              p.recently_played()["items"] == [] and p.get_playback()["is_playing"] is False)
        pl.st.update(position=2.0, duration=234.0)
        p.get_playback()
        rec = p.recently_played()["items"]
        check("Recently Played: local history once it really plays", rec and rec[0]["track"]["id"] == "20zVdUxZdwg")


ALL = [test_recorded_mapping, test_channel_uploads_and_topic, test_local_library_through_the_adapter,
       test_local_playlists, test_unavailable_and_offline, test_network_error_does_not_mark_unavailable,
       test_failure_streak_does_not_mark_everything, test_stale_mark_not_shown_in_lists,
       test_video_unavailable_at_play_time, test_history]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (ytmusic_library) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name, "::", (detail or "").strip().splitlines()[-1] if detail else "")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
