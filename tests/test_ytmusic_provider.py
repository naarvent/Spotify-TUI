"""YouTube Music provider: optional-dependency handling, result mapping,
imported public playlists and mpv/yt-dlp playback — with a fake ytmusicapi
client and a fake player. No network.

Run standalone:  python tests/test_ytmusic_provider.py
"""
import os, sys, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui import providers as P
from spt_tui.providers.ytmusic import YTMusicProvider, parse_playlist_id
from spt_tui.providers.base import ProviderError

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeYT:
    def __init__(self):
        self.calls = []
    def search(self, query, filter=None, limit=20):
        self.calls.append(("search", query, filter, limit))
        if filter == "songs":
            return [{"videoId": "v1", "title": "Tune", "artists": [{"name": "Artist", "id": "UC1"}],
                     "album": {"name": "Record", "id": "MPRE1"}, "duration_seconds": 185},
                    {"videoId": None, "title": "broken"},
                    {"videoId": "v2", "title": "Other", "artists": [], "duration": "3:05"}]
        if filter == "albums":
            return [{"browseId": "MPRE1", "title": "Record", "artists": [{"name": "Artist"}], "year": "2021"}]
        if filter == "artists":
            return [{"browseId": "UC1", "artist": "Artist"}]
        if filter == "community_playlists":
            return [{"browseId": "VLPL123", "title": "Mix", "author": "someone", "itemCount": "42"}]
        return []
    def get_playlist(self, pid, limit=None):
        self.calls.append(("get_playlist", pid))
        return {"id": pid, "title": "Public Mix", "author": {"name": "dj"}, "trackCount": 1,
                "tracks": [{"videoId": "v3", "title": "In List", "artists": [{"name": "X"}], "duration_seconds": 60}]}
    def get_album(self, bid):
        return {"title": "Record", "tracks": [{"videoId": "v1", "title": "Tune", "artists": [{"name": "Artist"}],
                                                "duration_seconds": 185}]}
    def get_artist(self, cid):
        return {"albums": {"results": [{"browseId": "MPRE1", "title": "Record"}]},
                "singles": {"results": [{"browseId": "MPRE2", "title": "Single"}]}}
    def get_song(self, vid):
        return {"videoDetails": {"videoId": vid, "title": "Looked Up", "author": "A", "lengthSeconds": "90"}}


class FakePlayer:
    def __init__(self, available=True):
        self.available = available; self.loaded = None; self.path = None; self.closed = False
        self.st = {"running": False, "idle": True, "paused": True, "position": 0.0, "duration": 0.0, "index": -1, "volume": 70}
    def unavailable_reason(self): return None if self.available else "mpv was not found on PATH (install mpv)."
    def load(self, urls, start_index=0):
        self.loaded = (urls, start_index); self.path = urls[start_index]
        self.st.update(running=True, idle=False, paused=False)
    def is_running(self): return self.st["running"]
    def get(self, prop, default=None): return self.path if prop == "path" else default
    def state(self): return dict(self.st)
    def close(self): self.closed = True


def make(ytdlp=True, player=None):
    yt = FakeYT()
    p = YTMusicProvider(client=yt, player=player or FakePlayer(),
                        which=lambda n: "/usr/bin/" + n if (n != "yt-dlp" or ytdlp) else None)
    return p, yt


class isolated_config:
    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="spt_yt_")
        self.old = (config.CONFIG_PATH, config.LOCAL_CFG)
        config.CONFIG_PATH = os.path.join(self.tmp, "spt_config.json"); config.LOCAL_CFG = {}
        return self
    def __exit__(self, *a):
        config.CONFIG_PATH, config.LOCAL_CFG = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_missing_dependency():
    p = YTMusicProvider(module_loader=lambda: None, player=FakePlayer())
    reason = p.unavailable_reason() or ""
    check("no ytmusicapi -> unavailable with an install hint", "ytmusicapi" in reason and "[ytmusic]" in reason, reason)
    orig = P.provider_class
    P.provider_class = lambda n: (lambda: YTMusicProvider(module_loader=lambda: None, player=FakePlayer())) \
        if n == "ytmusic" else orig(n)
    try:
        prov, notice = P.create_provider("ytmusic")
    finally:
        P.provider_class = orig
    check("create_provider falls back to Spotify (no crash)", type(prov).__name__ == "SpotifyProvider")
    check("…with the missing-package notice", notice and "ytmusicapi" in notice)
    try:
        p.search("x"); raised = False
    except ProviderError:
        raised = True
    check("using it anyway raises a ProviderError, not ImportError", raised)

    class Mod:
        created = 0
        class YTMusic:
            def __init__(self):
                Mod.created += 1
    p = YTMusicProvider(module_loader=lambda: Mod, player=FakePlayer())
    check("with the module: available and lazy (no client yet)", p.unavailable_reason() is None and Mod.created == 0)
    p._yt(); p._yt()
    check("anonymous YTMusic() created once on first use", Mod.created == 1)


def test_search_mapping():
    p, yt = make()
    tr = p.search("tune", kind="track", limit=10)
    items = tr["items"]
    check("songs filter used", yt.calls[-1][2] == "songs")
    check("entries without videoId dropped", [t["id"] for t in items] == ["v1", "v2"])
    t = items[0]
    check("song mapped Spotify-style", t["uri"] == "ytmusic:track:v1" and t["artists"][0]["name"] == "Artist"
          and t["album"]["name"] == "Record" and t["duration_ms"] == 185000, repr(t))
    check("'m:ss' durations parsed", items[1]["duration_ms"] == 185000)
    al = p.search("r", kind="album")["items"][0]
    check("album mapped", al["uri"] == "ytmusic:album:MPRE1" and al["release_date"] == "2021")
    ar = p.search("a", kind="artist")["items"][0]
    check("artist mapped", ar["uri"] == "ytmusic:artist:UC1" and ar["name"] == "Artist")
    pl = p.search("m", kind="playlist")["items"][0]
    check("playlist mapped (VL prefix stripped, count parsed)", pl["id"] == "PL123" and pl["tracks"]["total"] == 42
          and pl["owner"]["display_name"] == "someone", repr(pl))
    res = p.ensure().search("x", type="show")
    check("podcasts not offered", res["shows"]["items"] == [])
    check("album_tracks", p.album_tracks("MPRE1")["items"][0]["id"] == "v1")
    check("artist singles vs albums", p.artist_albums("UC1", album_type="single")["items"][0]["id"] == "MPRE2"
          and p.artist_albums("UC1", album_type="album")["items"][0]["id"] == "MPRE1")


def test_capabilities():
    p, _ = make()
    c = p.capabilities
    check("no account: likes and playlists through the local library",
          c.can_like and c.can_edit_playlists and c.local_library)
    check("search and playback enabled with mpv + yt-dlp", c.can_search and c.can_play and c.can_seek)
    check("podcasts not offered", not c.can_podcasts)
    p, _ = make(ytdlp=False)
    check("no yt-dlp: playback disabled with notice", not p.capabilities.can_play and "yt-dlp" in (p.status_notice() or ""))
    p, _ = make(player=FakePlayer(available=False))
    check("no mpv: playback disabled with notice", not p.capabilities.can_play and "mpv" in (p.status_notice() or ""))


def test_playlist_ids():
    check("music.youtube.com URL", parse_playlist_id("https://music.youtube.com/playlist?list=PLabc&si=x") == "PLabc")
    check("youtube.com URL", parse_playlist_id("https://www.youtube.com/playlist?list=PLdef") == "PLdef")
    check("ytmusic: URI", parse_playlist_id("ytmusic:playlist:PLx") == "PLx")
    check("bare id", parse_playlist_id(" PLraw ") == "PLraw")
    check("empty -> None", parse_playlist_id("") is None)


def test_imported_playlists():
    with isolated_config():
        p, yt = make()
        check("no imported playlists -> empty list", p.user_playlists()["items"] == [])
        sp = p.ensure()
        meta = sp.playlist("https://music.youtube.com/playlist?list=PLabc")
        check("Import: playlist() resolves a URL", meta["id"] == "PLabc" and meta["name"] == "Public Mix", repr(meta))
        sp.current_user_follow_playlist("PLabc")
        saved = config.LOCAL_CFG.get("ytmusic_playlists")
        check("Import: follow stores the playlist in the config", saved == [{"id": "PLabc", "name": "Public Mix"}], repr(saved))
        sp.current_user_follow_playlist("PLabc")
        check("importing twice does not duplicate", len(config.LOCAL_CFG["ytmusic_playlists"]) == 1)
        page = p.user_playlists()
        check("imported playlist listed", page["items"][0]["uri"] == "ytmusic:playlist:PLabc")
        items = p.playlist_items("PLabc")
        check("playlist_items from get_playlist", items["items"][0]["item"]["id"] == "v3")
        check("delete forgets an imported playlist", p.delete_playlist("PLabc") and config.LOCAL_CFG["ytmusic_playlists"] == [])


def test_playback():
    player = FakePlayer()
    p, yt = make(player=player)
    p.search("tune", kind="track")
    p.start_playback(uris=["ytmusic:track:v1", "ytmusic:track:v2"])
    urls, start = player.loaded
    check("audio URL is music.youtube.com/watch (mpv + yt-dlp)", urls == ["https://music.youtube.com/watch?v=v1",
                                                                         "https://music.youtube.com/watch?v=v2"], repr(urls))
    player.st.update(position=3.0, duration=185.0)
    pb = p.get_playback()
    check("now playing from mpv", pb["item"]["name"] == "Tune" and pb["progress_ms"] == 3000)
    p.start_playback(uris=["ytmusic:track:zzz"])
    check("unknown video looked up with get_song", player.loaded[0] == ["https://music.youtube.com/watch?v=zzz"])
    p.start_playback(context_uri="ytmusic:playlist:PLabc", offset={"position": 0})
    check("context playlist playback", player.loaded[0] == ["https://music.youtube.com/watch?v=v3"])
    check("mpv args request bestaudio via ytdl", "--ytdl-format=bestaudio/best" in YTMusicProvider.MPV_ARGS)


ALL = [test_missing_dependency, test_search_mapping, test_capabilities, test_playlist_ids,
       test_imported_playlists, test_playback]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (ytmusic_provider) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
