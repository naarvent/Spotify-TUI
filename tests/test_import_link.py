"""Library > Import Playlists with a link: Spotify links are copied (anonymously, via the scraper) into
a local playlist when the provider is YouTube Music; the help text says what is accepted per provider.
No network: the scraper client and ytmusicapi are simulated.

Run standalone:  python tests/test_import_link.py
"""
import os, sys, json, asyncio, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from spt_tui import config
from spt_tui import spotify_import as si
from spt_tui import scraper
from spt_tui.scraper_source import ScraperSource
from spt_tui.app import SptPy
from spt_tui.providers.ytmusic import YTMusicProvider
import test_spotify_import as base          # reuse its simulated ytmusicapi / env / provider builder

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

PID = "37i9dQZF1DXcBWIGoYBM5M"


def api_track(i, name, artists, album, ms):
    return {"track": {"id": f"{i:022d}", "uri": f"spotify:track:{i:022d}", "type": "track", "name": name,
                      "artists": [{"name": a} for a in artists], "album": {"name": album},
                      "duration_ms": ms}, "added_at": "2026-11-07T10:00:00Z"}


class FakeScraper:
    """What ScraperClient.playlist returns (Web API shape)."""
    def __init__(self, items=None, total=None, partial=False, error=None):
        self.items = items if items is not None else [
            api_track(1, "Bohemian Rhapsody - Remastered 2011", ["Queen"], "A Night At The Opera", 354000),
            api_track(2, "Get Lucky (feat. Pharrell Williams)", ["Daft Punk", "Pharrell Williams"], "Random Access Memories", 369000),
            api_track(4, "Zzqx Nonexistent", ["Nobody 4815"], "", 200000)]
        self.total = total if total is not None else len(self.items)
        self.partial, self.error, self.calls, self.closed = partial, error, [], False
    def playlist(self, value, max_tracks=100):
        self.calls.append((value, max_tracks))
        if self.error: raise self.error
        tr = {"total": self.total, "items": list(self.items)}
        if self.partial: tr["partial"] = True
        return {"id": PID, "name": "Mix de prueba", "tracks": tr}
    def close(self): self.closed = True


def test_classify():
    c = si.classify_import_input
    check("Spotify URL -> playlist", c(f"https://open.spotify.com/playlist/{PID}?si=abc") == ("spotify_playlist", PID))
    check("Spotify URL with intl segment", c(f"open.spotify.com/intl-es/playlist/{PID}") == ("spotify_playlist", PID))
    check("spotify: URI", c(f"spotify:playlist:{PID}") == ("spotify_playlist", PID))
    check("bare 22-char id is a Spotify playlist", c(PID) == ("spotify_playlist", PID))
    check("Spotify album link is recognised as not a playlist", c(f"https://open.spotify.com/album/{PID}") == ("spotify_other", "album"))
    check("YouTube Music link", c("https://music.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI")[0] == "youtube")
    check("youtube.com / youtu.be / ytmusic: are YouTube", all(c(x)[0] == "youtube" for x in (
        "https://www.youtube.com/playlist?list=PLx", "youtu.be/abc", "ytmusic:playlist:PLx")))
    check("a YouTube playlist id is NOT taken for Spotify", c("PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI") == ("other", None))
    check("empty / junk -> other", c("") == ("other", None) and c("hola que tal") == ("other", None))
    check("a lookalike host is not Spotify", c(f"https://open.spotify.com.evil.example/playlist/{PID}") == ("other", None))


def test_texts():
    t_yt, h_yt = si.import_playlists_text("ytmusic", True)
    check("ytmusic text: says Spotify link is copied and YouTube link is kept",
          "Spotify link" in t_yt and "copied" in t_yt and "YouTube Music link" in t_yt and "stays on YouTube" in t_yt, t_yt)
    check("ytmusic placeholder mentions both", "Spotify" in h_yt and "YouTube" in h_yt)
    t_sp, h_sp = si.import_playlists_text("spotify", False)
    check("spotify text: followed on your account, nothing copied", "followed" in t_sp and "nothing is copied" in t_sp, t_sp)
    check("spotify placeholder says Spotify", "Spotify" in h_sp)
    t_o, _ = si.import_playlists_text("subsonic", False)
    check("other providers: generic text", "playlist link or ID" in t_o)
    check("credentials help explains the Premium requirement and the link alternative",
          "Premium" in si.NO_CREDENTIALS_HELP and "Import Playlists" in si.NO_CREDENTIALS_HELP)


def test_describe_errors():
    d = si.describe_link_error
    check("NotFound -> private/missing, public only", "not public" in d(scraper.NotFoundError("x")))
    check("RateLimited -> try again", "limiting" in d(scraper.RateLimitedError("x")))
    check("Parsing/Token -> needs an update", all("update" in d(e("x")) for e in (scraper.ParsingError, scraper.TokenError)))
    check("Network -> network problem", "network" in d(scraper.NetworkError("x")))
    check("URLError keeps its message", d(scraper.URLError("bad link")) == "bad link")
    check("unknown error names its type", "ValueError" in d(ValueError("x")))


def test_source():
    fake = FakeScraper(items=[api_track(1, "A", ["X"], "Al", 1000), {"track": {"type": "episode", "id": "e"}},
                              api_track(2, "B", ["Y", "Z"], "Bl", 2000)], total=5)
    src = ScraperSource(fake)
    info = src.load(f"https://open.spotify.com/playlist/{PID}")
    check("source: info (name, total, read, skipped)", info["name"] == "Mix de prueba" and info["total"] == 5
          and info["read"] == 2 and info["skipped"] == 3 and not info["partial"], repr(info))
    tr = src.playlist_tracks(PID)
    check("source: tracks in the importer's shape", tr[1] == {"id": f"{2:022d}", "name": "B", "artists": ["Y", "Z"],
                                                              "album": "Bl", "duration_ms": 2000}, repr(tr))
    check("source: asked for ALL tracks", fake.calls == [(f"https://open.spotify.com/playlist/{PID}", None)], repr(fake.calls))
    check("source: nothing for liked/albums/artists", src.liked_tracks() == [] and src.saved_albums() == []
          and src.followed_artists() == [])
    check("source: partial is passed through", ScraperSource(FakeScraper(partial=True)).load(PID)["partial"])
    try:
        ScraperSource(FakeScraper(error=scraper.NotFoundError("private"))).load(PID); ok = False
    except scraper.NotFoundError:
        ok = True
    check("source: NotFoundError propagates", ok)


async def test_ui():
    from textual.widgets import Input
    class TApp(SptPy):
        def __init__(self, prov):
            super().__init__(); self.spotify = prov
    async def poll(fn, timeout=8.0):
        loop = asyncio.get_event_loop(); end = loop.time() + timeout
        while loop.time() < end:
            try:
                if fn(): return True
            except Exception: pass
            await asyncio.sleep(0.03)
        return False
    def status(app):
        try: return str(app.status_line.render())
        except Exception: return ""
    def right_text(app):
        from textual.widgets import Static
        out = []
        for w in app.right_panel.query(Static):
            c = getattr(w, "content", None)
            out.append(str(c if c is not None else getattr(w, "renderable", "")))
        return "\n".join(out)

    with base.env():
        # --- YouTube Music: a Spotify link is copied -------------------------------------------
        prov, yt = base.make()
        scr = FakeScraper()
        app = TApp(prov)
        app._scraper_client_factory = lambda: scr
        app._import_matcher_factory = lambda p: base.matcher(p)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app._open_import_playlists(); await pilot.pause()
            check("ytmusic: the Import Playlists view explains both kinds of link",
                  "Spotify link" in right_text(app) and "YouTube Music link" in right_text(app), right_text(app)[:200])
            check("ytmusic: the placeholder mentions Spotify and YouTube",
                  "Spotify" in app.import_input.placeholder and "YouTube" in app.import_input.placeholder)
            app.import_input.value = f"https://open.spotify.com/playlist/{PID}?si=zz"
            await pilot.press("enter")
            ok = await poll(lambda: not getattr(app, "_import_running", False) and "Import finished" in status(app))
            check("ytmusic: a Spotify link imports without any Spotify account", ok, status(app))
            pls = [p for p in prov.user_playlists()["items"] if p.get("_local")]
            check("…as a local playlist with the Spotify name", [p["name"] for p in pls] == ["Mix de prueba"], repr(pls))
            ids = [e["item"]["id"] for e in prov.playlist_items(pls[0]["id"])["items"]] if pls else []
            check("…with the songs matched on YouTube Music, in order", ids == ["vBR", "vGL"], repr(ids))
            check("…the scraper client was closed", scr.closed)
            check("…and the original follow flow did not run (no remote playlist call)", not yt.calls == [] and
                  all(c[1] in ("songs", None, "albums", "artists") for c in yt.calls))

            # a second paste refreshes the same local playlist (no duplicate)
            app._open_import_playlists(); await pilot.pause()
            app.import_input.value = PID
            await pilot.press("enter")
            await poll(lambda: not getattr(app, "_import_running", False) and not getattr(app, "_import_link_busy", False)
                       and len([p for p in prov.user_playlists()["items"] if p.get("_local")]) >= 1)
            await asyncio.sleep(0.5)
            pls2 = [p for p in prov.user_playlists()["items"] if p.get("_local")]
            check("a bare id works too, and re-importing does not duplicate the playlist", len(pls2) == 1, repr(pls2))

            # an album link is explained, nothing runs
            app._open_import_playlists(); await pilot.pause()
            before = len(scr.calls)
            app.import_input.value = f"https://open.spotify.com/album/{PID}"
            await pilot.press("enter"); await pilot.pause()
            check("a Spotify ALBUM link is explained and not read", "album" in status(app).lower() and len(scr.calls) == before, status(app))

        # --- errors are shown in plain words ----------------------------------------------------
        for exc, word in ((scraper.NotFoundError("gone"), "not public"), (scraper.NetworkError("down"), "network")):
            prov, yt = base.make()
            app = TApp(prov)
            app._scraper_client_factory = lambda e=exc: FakeScraper(error=e)
            n_before = len([p for p in prov.user_playlists()["items"] if p.get("_local")])
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.pause()
                app._open_import_playlists(); await pilot.pause()
                app.import_input.value = f"spotify:playlist:{PID}"
                await pilot.press("enter")
                ok = await poll(lambda: word in status(app).lower())
                check(f"{type(exc).__name__}: plain-language message, no import started", ok
                      and not getattr(app, "_import_running", False) and not getattr(app, "_import_link_busy", False), status(app))
                check(f"{type(exc).__name__}: nothing new was created",
                      len([p for p in prov.user_playlists()["items"] if p.get("_local")]) == n_before)

        # --- Spotify as provider: its own text; a YouTube link is explained ---------------------
        class SpotifyLike:
            name = "spotify"
            def has_cached_token(self): return True
            def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
            def get_playback(self): return {}
            def recently_played(self, limit=50): return {"items": []}
            def ensure(self): return self
            def fmt_duration(self, ms): return "0:00"
        app = TApp(SpotifyLike())
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app._open_import_playlists(); await pilot.pause()
            check("spotify: the view says playlists are followed, not copied",
                  "followed" in right_text(app) and "Spotify playlist link" in right_text(app), right_text(app)[:200])
            handled = app._import_link_dispatch("https://music.youtube.com/playlist?list=PLabc")
            check("spotify: a YouTube link is explained and not sent to Spotify", handled and "YouTube" in status(app), status(app))
            check("spotify: a Spotify link is left to the normal follow flow", app._import_link_dispatch(f"spotify:playlist:{PID}") is False)


ALL = [test_classify, test_texts, test_describe_errors, test_source]

def main():
    for fn in ALL:
        try: fn()
        except Exception: check(fn.__name__ + " (crashed)", False, traceback.format_exc(limit=4))
    try: asyncio.run(test_ui())
    except Exception: check("test_ui (crashed)", False, traceback.format_exc(limit=6))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    for n, c, d in RESULTS:
        if not c: print("  FAIL:", n, "::", d)
    sys.exit(0 if ok == len(RESULTS) else 1)

if __name__ == "__main__":
    main()
