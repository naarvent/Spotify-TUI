"""The TUI with an account-less provider (YouTube Music over recorded
responses): Library menu per provider, hearts + `f` through the local library
(songs, playlists, sidebar), no AttributeError in the saved-state checks,
renaming a local playlist, the outgoing provider paused on a switch, the Now
Playing bar reset, provider notices, and the Now Playing layout (indicators
flush right, titles cut with "…") at several widths.

Run standalone:  python tests/test_local_library_ui.py
"""
import os, sys, asyncio, logging, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.cells import cell_len
from spt_tui import config
from spt_tui import providers as P
from spt_tui.app import SptPy
from spt_tui.providers.base import Provider, Capabilities, make_page
from spt_tui.providers.ytmusic import YTMusicProvider
from test_ytmusic_library import Replay, Player  # noqa: E402

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class ErrorCounter(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR); self.records = []
    def emit(self, record):
        txt = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            txt += " " + repr(record.exc_info[1])
        self.records.append(txt)


class env:
    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="spt_libui_")
        self.old = (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG,
                    os.environ.pop("SPT_PROVIDER", None), P.provider_class)
        config.CACHE_DIR = self.d
        config.CONFIG_PATH = os.path.join(self.d, "spt_config.json")
        config.LOCAL_CFG = {}
        self.errors = ErrorCounter(); logging.getLogger().addHandler(self.errors)
        return self
    def __exit__(self, *a):
        logging.getLogger().removeHandler(self.errors)
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG, e, P.provider_class = self.old
        if e is not None:
            os.environ["SPT_PROVIDER"] = e
        shutil.rmtree(self.d, ignore_errors=True)


def yt_provider():
    return YTMusicProvider(client=Replay(), player=Player(), which=lambda n: "/usr/bin/" + n)


class TApp(SptPy):
    def __init__(self, prov):
        super().__init__()
        self.spotify = prov


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def poll(fn, timeout=4.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.02)
    return False


def status_text(app):
    try:
        return str(app.status_line.render())
    except Exception:
        return ""


def lib_names(app):
    return [(getattr(li, "data", {}) or {}).get("name") for li in app.lib_list.children]


def pl_names(app):
    return [(getattr(li, "data", {}) or {}).get("name") for li in app.pl_list.children]


# --------------------------------------------------------------------------- #
async def test_library_menu_per_provider():
    with env():
        class SpotifyLike:
            def has_cached_token(self): return True
            def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
            def get_playback(self): return {}
            def recently_played(self, limit=50): return {"items": []}
            def ensure(self): return self
            def fmt_duration(self, ms): return "0:00"
        app = TApp(SpotifyLike())
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            check("Spotify (no caps): every Library entry", "Saved Podcasts" in lib_names(app)
                  and "Saved Episodes" in lib_names(app) and "Import Playlists" in lib_names(app))
        app = TApp(yt_provider())
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            names = lib_names(app)
            check("ytmusic: no Saved Podcasts / Saved Episodes", "Saved Podcasts" not in names
                  and "Saved Episodes" not in names, repr(names))
            check("ytmusic: Liked Songs / Saved Albums / Saved Artists / Recently Played / Import kept",
                  all(n in names for n in ("Liked Songs", "Saved Albums", "Saved Artists",
                                           "Recently Played", "Import Playlists", "Create Playlist")))

            class Sub(Provider):
                name = "subsonic"; display_name = "Subsonic"
                @property
                def capabilities(self): return Capabilities(can_podcasts=False, can_import_playlists=False)
            P.provider_class = lambda n: Sub
            app.switch_provider("subsonic")
            ok = await poll(lambda: isinstance(app.spotify, Sub) and "Import Playlists" not in lib_names(app))
            check("live switch updates the menu (subsonic: no Import Playlists)", ok, repr(lib_names(app)))


async def test_hearts_and_f_with_ytmusic():
    with env() as e:
        prov = yt_provider()
        app = TApp(prov)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app._dispatch_search("joji")
            ok = await poll(lambda: len(app.query("#search_grid")) > 0)
            check("search grid with ytmusic", ok)
            from textual.widgets import DataTable
            await asyncio.sleep(0.3)
            tables = [t for t in app.query(DataTable)]
            for t in tables:
                app._revalidate_saved_column(t, max_rows=50)
            await asyncio.sleep(0.2)
            attr = [r for r in e.errors.records if "AttributeError" in r]
            check("saved-state checks raise no AttributeError (log clean)", not attr, repr(attr[:2]))

            songs = next((t for t in tables if "track" in (getattr(t, "row_to_type", {}) or {}).values()), None)
            check("songs panel present", songs is not None)
            if songs is not None:
                songs.focus(); songs.move_cursor(row=0); await pilot.pause()
                app.action_toggle_favorite()
                ok = await poll(lambda: prov.check_saved_tracks(["20zVdUxZdwg"]) == [True])
                check("f on a song saves it in the local library", ok)
                ok = await poll(lambda: "Added to Liked Songs" in status_text(app))
                check("…with the usual confirmation", ok, status_text(app))

            plt = next((t for t in tables if "playlist" in (getattr(t, "row_to_type", {}) or {}).values()), None)
            check("playlists panel present", plt is not None)
            if plt is not None:
                rows = [k for k, v in plt.row_to_type.items() if v == "playlist"]
                plt.focus(); plt.move_cursor(row=rows[0]); await pilot.pause()
                app.action_toggle_favorite()
                ok = await poll(lambda: "Joji Mix" in pl_names(app))
                check("f on a playlist result adds it to the sidebar Playlists", ok, repr(pl_names(app)))
                ok = await poll(lambda: bool((getattr(plt, "_model_rows", [])[rows[0]] or {}).get("saved")))
                check("…and its heart fills", ok, repr(plt._model_rows[rows[0]]))
                app.pl_list.focus(); app.pl_list.index = pl_names(app).index("Joji Mix"); await pilot.pause()
                app.action_toggle_favorite()
                ok = await poll(lambda: "Joji Mix" not in pl_names(app))
                check("f on it in the sidebar removes it", ok, repr(pl_names(app)))

            app._open_liked_table()
            ok = await poll(lambda: any(t.id == "tracks_table" and t.row_count >= 1 for t in app.query(DataTable)))
            check("Liked Songs view lists the local like", ok)


async def test_rename_local_playlist():
    with env():
        prov = yt_provider()
        pl = prov.create_playlist("Old Name")
        app = TApp(prov)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app._load_playlists(force=True)
            await poll(lambda: "Old Name" in pl_names(app))
            app.pl_list.focus(); app.pl_list.index = pl_names(app).index("Old Name"); await pilot.pause()
            app.action_rename_playlist()
            await pilot.pause()
            inp = getattr(app, "rename_playlist_input", None)
            check("Ctrl+E opens the rename field, prefilled", inp is not None and inp.value == "Old Name")
            class Ev:
                def __init__(self, i, v): self.input, self.value = i, v
            app.on_input_submitted(Ev(inp, "New Name"))
            ok = await poll(lambda: "New Name" in pl_names(app))
            check("renamed in the sidebar", ok, repr(pl_names(app)))
            check("…and in the store", prov.library.playlist(pl["id"])["name"] == "New Name")


class Outgoing(Provider):
    name = "spotify"; display_name = "Spotify"
    can_pause = True
    def __init__(self):
        super().__init__(); self.paused = 0; self.closed = False
    @property
    def capabilities(self):
        return Capabilities(can_control_playback=type(self).can_pause)
    def get_playback(self):
        return {"is_playing": True, "progress_ms": 1000,
                "item": {"id": "x", "name": "Remote Song", "duration_ms": 200000}}
    def pause(self):
        self.paused += 1
        return True
    def close(self): self.closed = True
    def user_playlists(self, limit=50, offset=0): return make_page([])


class Incoming(Provider):
    name = "ytmusic"; display_name = "YouTube Music"
    def user_playlists(self, limit=50, offset=0): return make_page([])


async def test_switch_pauses_and_resets_bar():
    with env():
        P.provider_class = lambda n: {"ytmusic": Incoming, "spotify": Outgoing}[n]
        old = Outgoing()
        app = TApp(old)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app._bar_last_track = {"id": "x", "name": "Remote Song", "artists": [{"name": "A"}]}
            app._now_internal_dur_ms = 200000; app._now_internal_is_playing = True
            app._update_now_bar()
            await poll(lambda: "Remote Song" in str(app.np_title.render()))
            app.switch_provider("ytmusic")
            ok = await poll(lambda: isinstance(app.spotify, Incoming))
            check("switched", ok)
            check("the outgoing provider was paused (no song left playing remotely)", old.paused == 1)
            ok = await poll(lambda: "Now Playing: —" in str(app.np_title.render()))
            check("Now Playing bar reset", ok, str(app.np_title.render()))
            check("cached playback state cleared", not app._now_internal_is_playing and app._bar_last_track is None)

        Outgoing.can_pause = False   # Spotify Free: cannot pause from here
        try:
            old = Outgoing()
            app = TApp(old)
            async with app.run_test(size=(130, 40)) as pilot:
                await pilot.pause(); pause_intervals(app)
                app.switch_provider("ytmusic")
                ok = await poll(lambda: "could not pause" in status_text(app))
                check("cannot pause (Free/no device): warned on the status line", ok, status_text(app))
                check("…and nothing was sent", old.paused == 0)
        finally:
            Outgoing.can_pause = True


async def test_provider_notices_shown():
    with env():
        class Noisy(Provider):
            name = "ytmusic"; display_name = "YouTube Music"
            def __init__(self):
                super().__init__(); self.n = ["Skipped unavailable track: Deleted Video"]
            def take_notices(self):
                out, self.n = self.n, []
                return out
            def user_playlists(self, limit=50, offset=0): return make_page([])
        app = TApp(Noisy())
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app._sync_playback()
            ok = await poll(lambda: "Deleted Video" in status_text(app))
            check("a skipped unavailable track is announced on the status line", ok, status_text(app))


async def test_now_playing_layout():
    with env():
        items = [
            ("spotify short", {"id": "a", "type": "track", "name": "Ew", "artists": [{"name": "Joji"}]}),
            ("ytmusic long", {"id": "b", "type": "track",
                              "name": "I Don't Feel Like Killing Myself Today (IDFLKMST) Extended Version",
                              "artists": [{"name": "JOJI"}, {"name": "Someone Else"}]}),
            ("wide glyphs", {"id": "c", "type": "track", "name": "夜に駆ける 夜に駆ける 夜に駆ける 夜に駆ける",
                             "artists": [{"name": "YOASOBI"}]}),
        ]
        for width in (80, 120, 200):
            class Plain:
                def has_cached_token(self): return True
                def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
                def get_playback(self): return {}
                def recently_played(self, limit=50): return {"items": []}
                def ensure(self): return self
                def fmt_duration(self, ms): return "0:00"
            app = TApp(Plain())
            async with app.run_test(size=(width, 30)) as pilot:
                await pilot.pause(); pause_intervals(app)
                for label, item in items:
                    app._bar_last_track = item
                    app._now_internal_dur_ms = 240000; app._now_internal_pos_ms = 55000
                    app._update_now_bar()
                    await poll(lambda: str(item["name"][:2]) in str(app.np_title.render()))
                    await pilot.pause()
                    title = str(app.np_title.render()).splitlines()[0]
                    bar = str(app.np_bar_full.render()).splitlines()[0]
                    tw = app.query_one("#np_title").content_size.width
                    ok = (cell_len(title) == cell_len(bar) == tw and title.endswith("Shuffle: ↪")
                          and bar.endswith("]"))
                    check(f"w={width} {label}: indicators flush with the bar's right edge",
                          ok, f"title={cell_len(title)} bar={cell_len(bar)} widget={tw} {title[-30:]!r}")
                    if cell_len(f"Now Playing: {item['name']}") > tw - 25:
                        check(f"w={width} {label}: long title cut with …", "…" in title, title[-40:])


async def test_follow_artist_with_ytmusic():
    """Regression: f on an artist with YouTube Music used to answer "Podcasts
    and followed artists is not available" (Saved Artists was gated on
    can_podcasts). It must follow through the local library."""
    with env():
        prov = yt_provider()
        app = TApp(prov)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(); pause_intervals(app)
            check("ytmusic can follow artists (separate from podcasts)",
                  app._cap("can_follow_artists") and not app._cap("can_podcasts"))
            check("Saved Artists stays in the Library menu", "Saved Artists" in lib_names(app))
            app._dispatch_search("joji")
            await poll(lambda: len(app.query("#search_grid")) > 0)
            await asyncio.sleep(0.3)
            from textual.widgets import DataTable
            arts = next((t for t in app.query(DataTable)
                         if "artist" in (getattr(t, "row_to_type", {}) or {}).values()), None)
            check("artists panel present", arts is not None)
            if arts is None:
                return
            row = next(k for k, v in arts.row_to_type.items() if v == "artist")
            arts.focus(); arts.move_cursor(row=row); await pilot.pause()
            app._clear_status_line()
            app.action_toggle_favorite()
            ok = await poll(lambda: prov.ensure().current_user_followed_artists(limit=50)["artists"]["items"])
            check("f on an artist follows it in the local library", ok)
            ok = await poll(lambda: "Followed Artist" in status_text(app))
            check("…confirmed as 'Followed Artist'", ok, status_text(app))
            check("…never the 'not available' notice", "not available" not in status_text(app), status_text(app))
            ok = await poll(lambda: bool((arts._model_rows[row] or {}).get("saved")))
            check("artist heart filled right after f", ok, repr(arts._model_rows[row]))

            app._clear_status_line()
            app._open_saved_artists()
            ok = await poll(lambda: any(t.id in ("tracks_table", "search_table") and t.row_count >= 1
                                        for t in app.query(DataTable)))
            check("Saved Artists lists the followed artist", ok)
            check("opening Saved Artists gives no 'not available' notice", "not available" not in status_text(app),
                  status_text(app))
            # Unfollow from inside the Saved Artists view (it then refreshes itself).
            tbl = next(t for t in app.query(DataTable) if t.id in ("tracks_table", "search_table"))
            tbl.focus(); tbl.move_cursor(row=0); await pilot.pause()
            app.action_toggle_favorite()
            ok = await poll(lambda: not prov.ensure().current_user_followed_artists(limit=50)["artists"]["items"])
            check("f again unfollows it", ok)
            await asyncio.sleep(0.3)
            check("…no 'not available' notice when the view refreshes",
                  "not available" not in status_text(app), status_text(app))
            from spt_tui.providers.base import CAPABILITY_LABELS
            check("podcast notice never mentions followed artists",
                  "artist" not in CAPABILITY_LABELS["can_podcasts"].lower())


ASYNC = [test_follow_artist_with_ytmusic, test_library_menu_per_provider, test_hearts_and_f_with_ytmusic, test_rename_local_playlist,
         test_switch_pauses_and_resets_bar, test_provider_notices_shown, test_now_playing_layout]

def main():
    for fn in ASYNC:
        try:
            asyncio.run(fn())
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (local_library_ui) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name, "::", (detail or "").strip().splitlines()[-1] if detail else "")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
