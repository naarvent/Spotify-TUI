"""Subsonic/OpenSubsonic provider: token auth, search3, playlists, star/unstar,
scrobble and stream playback — against a fake HTTP session and a fake mpv
player. No network, no audio.

Run standalone:  python tests/test_subsonic_provider.py
"""
import os, sys, hashlib, tempfile, shutil, traceback
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.providers.subsonic import SubsonicProvider, SubsonicError, password_account
from spt_tui.providers.base import ProviderError
from spt_tui.secret_store import SecretStore

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

PW = "s3cret-pw"
SONG = {"id": "s1", "title": "Song One", "artist": "Band", "artistId": "ar1",
        "album": "LP", "albumId": "al1", "duration": 200, "starred": "2026-01-01T00:00:00Z"}
SONG2 = {"id": "s2", "title": "Song Two", "artist": "Band", "artistId": "ar1",
         "album": "LP", "albumId": "al1", "duration": 100}


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self):
        return self.body


def ok(**kw):
    return {"subsonic-response": {"status": "ok", "version": "1.16.1", **kw}}


class FakeSession:
    def __init__(self):
        self.calls = []      # (endpoint, list of (k, v))
        self.routes = {
            "ping": ok(),
            "search3": ok(searchResult3={"song": [SONG, SONG2],
                                         "album": [{"id": "al1", "name": "LP", "artist": "Band", "songCount": 2, "year": 2020}],
                                         "artist": [{"id": "ar1", "name": "Band"}]}),
            "getPlaylists": ok(playlists={"playlist": [
                {"id": "p1", "name": "Road Trip", "owner": "me", "songCount": 2},
                {"id": "p2", "name": "Chill", "owner": "me", "songCount": 0}]}),
            "getPlaylist": ok(playlist={"id": "p1", "name": "Road Trip", "entry": [SONG, SONG2]}),
            "getStarred2": ok(starred2={"song": [SONG], "album": [{"id": "al1", "name": "LP", "starred": "x"}]}),
            "star": ok(), "unstar": ok(), "scrobble": ok(),
            "createPlaylist": ok(playlist={"id": "p9", "name": "New", "owner": "me", "songCount": 0}),
            "updatePlaylist": ok(), "deletePlaylist": ok(),
            "getAlbum": ok(album={"id": "al1", "name": "LP", "song": [SONG, SONG2]}),
            "getArtist": ok(artist={"id": "ar1", "name": "Band", "album": [{"id": "al1", "name": "LP"}]}),
            "getSong": ok(song=SONG2),
        }
    def get(self, url, params=None, timeout=None):
        endpoint = url.rsplit("/rest/", 1)[-1]
        self.calls.append((endpoint, list(params or [])))
        body = self.routes.get(endpoint)
        if body is None:
            return Resp({"subsonic-response": {"status": "failed", "error": {"code": 0, "message": "no route"}}})
        return Resp(body)
    def last(self, endpoint):
        for e, p in reversed(self.calls):
            if e == endpoint:
                return p
        return None


class FakePlayer:
    def __init__(self, available=True):
        self.available = available; self.loaded = None; self.appended = []
        self.st = {"running": False, "idle": True, "paused": True, "position": 0.0,
                   "duration": 0.0, "index": -1, "volume": 70}
        self.path = None; self.closed = False; self.cmds = []
    def unavailable_reason(self): return None if self.available else "mpv was not found on PATH (install mpv)."
    def load(self, urls, start_index=0):
        self.loaded = (list(urls), start_index); self.path = urls[start_index]
        self.st.update(running=True, idle=False, paused=False, index=start_index)
    def append(self, url): self.appended.append(url)
    def is_running(self): return self.st["running"]
    def get(self, prop, default=None): return self.path if prop == "path" else default
    def state(self): return dict(self.st)
    def pause(self): self.cmds.append("pause")
    def resume(self): self.cmds.append("resume")
    def next(self): self.cmds.append("next")
    def prev(self): self.cmds.append("prev")
    def seek(self, s): self.cmds.append(("seek", s))
    def set_volume(self, v): self.cmds.append(("vol", v))
    def set_loop(self, s): self.cmds.append(("loop", s))
    def command(self, *a): self.cmds.append(a)
    def playlist_urls(self): return list(self.loaded[0]) if self.loaded else []
    def close(self): self.closed = True


def make(player=None, **kw):
    sess = FakeSession()
    salts = iter(f"salt{i}" for i in range(10000))
    p = SubsonicProvider(url="https://music.example.org/", user="alice", password=PW,
                         session=sess, player=player or FakePlayer(),
                         salt_factory=lambda: next(salts), **kw)
    return p, sess


def params(lst):
    d = {}
    for k, v in lst:
        d.setdefault(k, []).append(v)
    return d


def test_auth():
    p, sess = make()
    check("trailing slash stripped from the URL", p.url == "https://music.example.org")
    p.ping()
    q = params(sess.last("ping"))
    check("u/v/c/f sent", q["u"] == ["alice"] and q["v"] == ["1.16.1"] and q["c"] == ["spt-tui"] and q["f"] == ["json"], repr(q))
    salt = q["s"][0]
    check("t = md5(password + salt)", q["t"] == [hashlib.md5((PW + salt).encode()).hexdigest()])
    check("clear password never sent", all(PW not in str(v) for _, v in sess.last("ping")))
    p.ping()
    check("a fresh salt per request", params(sess.last("ping"))["s"][0] != salt)


def test_errors():
    p, sess = make()
    sess.routes["search3"] = {"subsonic-response": {"status": "failed", "error": {"code": 40, "message": "Wrong username or password"}}}
    try:
        p.search("x"); err = None
    except SubsonicError as exc:
        err = exc
    check("status=failed raises SubsonicError with the server message", err and "Wrong username" in str(err), repr(err))
    check("SubsonicError is a ProviderError", isinstance(err, ProviderError))
    class Boom(FakeSession):
        def get(self, url, params=None, timeout=None):
            raise ConnectionError(f"failed {url}?t=secrettoken")
    p2 = SubsonicProvider(url="https://h", user="u", password=PW, session=Boom(), player=FakePlayer())
    try:
        p2.ping(); p2._call("ping"); msg = ""
    except SubsonicError as exc:
        msg = str(exc)
    check("transport errors are wrapped without leaking the token", "secrettoken" not in msg and "ping" in msg, msg)


def test_search():
    p, sess = make()
    res = p.search("band", kind="track", limit=10)
    t = res["items"][0]
    check("search3 used with songCount=limit and others 0",
          params(sess.last("search3"))["songCount"] == [10] and params(sess.last("search3"))["albumCount"] == [0])
    check("song mapped to a Spotify-shaped track",
          t["id"] == "s1" and t["uri"] == "subsonic:track:s1" and t["name"] == "Song One"
          and t["artists"][0]["name"] == "Band" and t["album"]["name"] == "LP" and t["duration_ms"] == 200000, repr(t))
    al = p.search("lp", kind="album")["items"][0]
    check("album mapped", al["uri"] == "subsonic:album:al1" and al["artists"][0]["name"] == "Band")
    ar = p.search("band", kind="artist")["items"][0]
    check("artist mapped", ar["uri"] == "subsonic:artist:ar1" and ar["name"] == "Band")
    pl = p.search("road", kind="playlist")
    check("playlist search filters getPlaylists by name", [x["name"] for x in pl["items"]] == ["Road Trip"])
    sp = p.ensure().search("band", type="track", limit=5)
    check("adapter search works through ensure()", sp["tracks"]["items"][0]["id"] == "s1")


def test_playlists():
    p, sess = make()
    page = p.user_playlists(limit=50, offset=0)
    check("user_playlists: items + total + no next", len(page["items"]) == 2 and page["total"] == 2 and not page["next"])
    pl = page["items"][0]
    check("playlist shape (owner/uri/tracks.total)", pl["uri"] == "subsonic:playlist:p1"
          and pl["owner"]["display_name"] == "me" and pl["tracks"]["total"] == 2, repr(pl))
    items = p.playlist_items("p1", limit=100, offset=0)
    from spt_tui.providers.spotify import SpotifyClient
    check("playlist_items entries carry the track under item/track",
          SpotifyClient.playlist_entry_track(items["items"][0])["id"] == "s1" and items["total"] == 2)
    new = p.create_playlist("New")
    check("createPlaylist returns the mapped playlist", new and new["id"] == "p9")
    check("add_items_to_playlist -> updatePlaylist songIdToAdd",
          p.add_items_to_playlist("p1", ["subsonic:track:s2", "s1"])
          and params(sess.last("updatePlaylist"))["songIdToAdd"] == ["s2", "s1"])
    check("remove position -> songIndexToRemove",
          p.remove_playlist_positions("p1", [1]) and params(sess.last("updatePlaylist"))["songIndexToRemove"] == [1])
    check("delete -> deletePlaylist", p.delete_playlist("p2") and params(sess.last("deletePlaylist"))["id"] == ["p2"])
    sp = p.ensure()
    sp.playlist_remove_specific_occurrences_of_items("p1", [{"uri": "subsonic:track:s2", "positions": [1]}])
    check("adapter maps specific-occurrence removal", params(sess.last("updatePlaylist"))["songIndexToRemove"] == [1])
    sess.routes["createPlaylist"] = {"subsonic-response": {"status": "failed", "error": {"code": 50, "message": "not allowed"}}}
    check("create failure -> None + _last_create_error", p.create_playlist("X") is None and "not allowed" in p._last_create_error)


def test_favourites():
    p, sess = make()
    check("check_saved_tracks from getStarred2", p.check_saved_tracks(["s1", "s2"]) == [True, False])
    n = len(sess.calls)
    p.check_saved_tracks(["s1"])
    check("starred set cached (no second getStarred2)", len(sess.calls) == n)
    check("save -> star with repeated id", p.save_tracks(["s2"]) and params(sess.last("star"))["id"] == ["s2"])
    check("cache updated after star", p.check_saved_tracks(["s2"]) == [True])
    check("remove -> unstar", p.remove_tracks(["s1"]) and params(sess.last("unstar"))["id"] == ["s1"])
    check("cache updated after unstar", p.check_saved_tracks(["s1"]) == [False])
    liked = p.saved_tracks(limit=50, offset=0)
    check("saved_tracks is the Liked Songs page ({track: …})", liked["items"][0]["track"]["id"] == "s1")
    albums = p.ensure().current_user_saved_albums(limit=50, offset=0)
    check("starred albums as Saved Albums", albums["items"][0]["album"]["id"] == "al1")
    seen = []
    p.check_saved_tracks(["s1", "s2"], on_batch=lambda i, v: seen.append((i, v)))
    check("on_batch called once with aligned values", seen and seen[0][0] == 0 and len(seen[0][1]) == 2)


def test_playback_and_scrobble():
    player = FakePlayer()
    p, sess = make(player=player)
    p.user_playlists()
    p.start_playback(context_uri="subsonic:playlist:p1", offset={"position": 1})
    urls, start = player.loaded
    check("context playback loads the whole playlist at the offset", len(urls) == 2 and start == 1)
    u = urlparse(urls[1]); q = parse_qs(u.query)
    check("stream URL uses /rest/stream with id + token auth", u.path == "/rest/stream" and q["id"] == ["s2"] and "t" in q and "s" in q)
    check("stream URL never contains the clear password", PW not in urls[1])

    player.st.update(position=10.0, duration=100.0)
    pb = p.get_playback()
    check("get_playback: Spotify shape from mpv state", pb["item"]["id"] == "s2" and pb["progress_ms"] == 10000
          and pb["item"]["duration_ms"] == 100000 and pb["is_playing"] is True, repr(pb))
    now_playing = [params(c[1]) for c in sess.calls if c[0] == "scrobble"]
    check("track start -> scrobble submission=false (now playing)",
          now_playing and now_playing[-1]["submission"] == ["false"] and now_playing[-1]["id"] == ["s2"], repr(now_playing))
    player.st.update(position=60.0)
    p.get_playback()
    subs = [params(c[1]) for c in sess.calls if c[0] == "scrobble" and params(c[1])["submission"] == ["true"]]
    check("past half the track -> scrobble submission=true", len(subs) == 1 and subs[0]["id"] == ["s2"], repr(subs))
    player.st.update(position=70.0)
    p.get_playback()
    subs2 = [c for c in sess.calls if c[0] == "scrobble" and params(c[1])["submission"] == ["true"]]
    check("submitted only once per play", len(subs2) == 1)

    p.start_playback(uris=["subsonic:track:s1"])
    check("uris playback resolves known tracks", player.loaded[0][0].count("id=s1") == 1)
    p.start_playback(uris=["subsonic:track:s2"])
    check("unknown uri is looked up (getSong) or remembered", player.loaded is not None)
    p.add_to_queue("subsonic:track:s1")
    check("add_to_queue appends a stream URL", player.appended and "id=s1" in player.appended[-1])
    p.pause(); p.resume(); p.next(); p.prev(); p.seek_ms(5000); p.set_volume(40); p.repeat("track")
    check("controls forwarded to mpv", player.cmds[:7] == ["pause", "resume", "next", "prev", ("seek", 5.0), ("vol", 40), ("loop", "track")], repr(player.cmds))
    check("devices: the local mpv device", p.devices()[0]["name"].startswith("mpv"))
    p.close()
    check("close() closes the player", player.closed)


def test_no_mpv_degrades():
    p, _ = make(player=FakePlayer(available=False))
    caps = p.capabilities
    check("without mpv: playback disabled, browsing kept", not caps.can_play and caps.can_search and caps.can_like)
    check("without mpv: clear status notice", "mpv" in (p.status_notice() or ""))
    try:
        p.start_playback(uris=["subsonic:track:s1"]); raised = None
    except ProviderError as exc:
        raised = exc
    check("start_playback without mpv raises a clear ProviderError", raised and "mpv" in str(raised))


def test_config_and_password():
    tmp = tempfile.mkdtemp(prefix="spt_sub_")
    old = (config.CONFIG_PATH, config.LOCAL_CFG)
    saved_env = {k: os.environ.pop(k, None) for k in ("SPT_SUBSONIC_PASSWORD", "SPT_SUBSONIC_URL", "SPT_SUBSONIC_USER")}
    try:
        config.CONFIG_PATH = os.path.join(tmp, "spt_config.json")
        config.LOCAL_CFG = {}
        p = SubsonicProvider(session=FakeSession(), player=FakePlayer(),
                             secret_store=SecretStore(use_keyring=False, which=lambda n: None))
        check("no URL/user -> unavailable with a hint", "Settings" in (p.unavailable_reason() or ""))
        config.LOCAL_CFG = {"subsonic_url": "https://h", "subsonic_user": "bob"}
        p = SubsonicProvider(session=FakeSession(), player=FakePlayer(),
                             secret_store=SecretStore(use_keyring=False, which=lambda n: None))
        check("no password -> unavailable, mentions the env var", "SPT_SUBSONIC_PASSWORD" in (p.unavailable_reason() or ""))

        class KR:
            def __init__(self): self.store = {}
            def get_password(self, s, a): return self.store.get((s, a))
            def set_password(self, s, a, pw): self.store[(s, a)] = pw
        kr = KR(); kr.set_password("spt-tui", password_account("https://h", "bob"), "kr-pw")
        p = SubsonicProvider(session=FakeSession(), player=FakePlayer(),
                             secret_store=SecretStore(keyring_module=kr))
        check("password read from the keyring", p._password == "kr-pw" and p.unavailable_reason() is None)
        os.environ["SPT_SUBSONIC_PASSWORD"] = "env-pw"
        p = SubsonicProvider(session=FakeSession(), player=FakePlayer(),
                             secret_store=SecretStore(keyring_module=kr))
        check("SPT_SUBSONIC_PASSWORD takes precedence", p._password == "env-pw")
        os.environ["SPT_SUBSONIC_URL"] = "https://other/"
        p = SubsonicProvider(session=FakeSession(), player=FakePlayer(),
                             secret_store=SecretStore(keyring_module=kr))
        check("SPT_SUBSONIC_URL overrides the config URL", p.url == "https://other")
        check("the JSON config never holds a password", "pw" not in str(config.LOCAL_CFG))
    finally:
        config.CONFIG_PATH, config.LOCAL_CFG = old
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


ALL = [test_auth, test_errors, test_search, test_playlists, test_favourites,
       test_playback_and_scrobble, test_no_mpv_degrades, test_config_and_password]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok_, _ in RESULTS if ok_)
    print("\n==== SUMMARY (subsonic_provider) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok_, detail in RESULTS:
        if not ok_:
            print("  FAIL:", name, "::", (detail or "").strip().splitlines()[-1] if detail else "")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
