"""The real TUI driving a non-Spotify provider end to end (Subsonic with a fake
HTTP session and a fake mpv), plus the provider fields of the Settings screen
(provider choice persisted, Subsonic password sent to the keyring and never
written to spt_config.json). No network, no audio.

Run standalone:  python tests/test_provider_ui.py
"""
import os, sys, asyncio, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.app import SptPy
from spt_tui.providers.subsonic import SubsonicProvider
import spt_tui.secret_store as secret_store

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_subsonic_provider import FakeSession, FakePlayer  # noqa: E402

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class TApp(SptPy):
    def __init__(self, prov):
        super().__init__()
        self.spotify = prov


class FakeEvent:
    def __init__(self, inp, value):
        self.input, self.value = inp, value


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


async def test_subsonic_in_the_ui():
    player = FakePlayer()
    prov = SubsonicProvider(url="https://music.example.org", user="alice", password="pw",
                            session=FakeSession(), player=player)
    app = TApp(prov)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._load_playlists(force=True)
        ok = await poll(lambda: len(app.pl_list.children) == 2)
        check("Subsonic playlists listed in the sidebar", ok, f"n={len(app.pl_list.children)}")
        names = [getattr(li, "data", {}).get("name") for li in app.pl_list.children]
        check("playlist names", names == ["Road Trip", "Chill"], repr(names))

        app._open_playlist_table({"id": "p1", "name": "Road Trip", "uri": "subsonic:playlist:p1"})
        from textual.widgets import DataTable
        def tracks_table():
            return next((t for t in app.query(DataTable) if t.id == "tracks_table"), None)
        ok = await poll(lambda: tracks_table() is not None and tracks_table().row_count == 2)
        check("playlist table rendered from getPlaylist", ok)
        tbl = tracks_table()
        check("hearts come from getStarred2 (s1 starred)",
              await poll(lambda: bool((getattr(tbl, "_liked_map", {}) or {}).get(0))), repr(getattr(tbl, "_liked_map", None)))
        app._play_row(1, tbl)
        ok = await poll(lambda: player.loaded is not None)
        check("Enter on a row plays through mpv", ok)
        if ok:
            urls, start = player.loaded
            check("…the playlist context at the chosen row", len(urls) == 2 and start == 1, repr(player.loaded))

        app._dispatch_search("band")
        ok = await poll(lambda: len(app.query("#search_grid")) > 0)
        check("combined search grid rendered from search3", ok)

        app._clear_status_line()
        app.action_manage_devices()
        await pilot.pause()
        check("devices view allowed (mpv device)", "not available" not in status_text(app))
        app.action_escape_to_menu()

        app._clear_status_line()
        app._open_saved_podcasts()
        await pilot.pause()
        check("podcasts gated with a notice", "not available" in status_text(app), status_text(app))
        check("sub_title shows the provider", app.sub_title == "Subsonic", repr(app.sub_title))
    check("teardown closes the provider (mpv)", player.closed)


async def test_settings_provider_fields():
    tmp = tempfile.mkdtemp(prefix="spt_prov_ui_")
    old = (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG, os.environ.pop("SPT_PROVIDER", None))
    config.CACHE_DIR = tmp
    config.CONFIG_PATH = os.path.join(tmp, "spt_config.json")
    config.LOCAL_CFG = {}

    class KR:
        def __init__(self): self.d = {}
        def get_password(self, s, a): return self.d.get((s, a))
        def set_password(self, s, a, p): self.d[(s, a)] = p
    kr = KR()
    old_store = secret_store._default
    secret_store._default = secret_store.SecretStore(keyring_module=kr)

    class Fake:
        def has_cached_token(self): return True
        def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
        def get_playback(self): return {}
        def recently_played(self, limit=50): return {"items": []}
        def ensure(self): return self
        def fmt_duration(self, ms): return "0:00"
    app = TApp(Fake())
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_prompt_settings()
            await pilot.pause()
            for a in ("provider_select", "subsonic_url_input", "subsonic_user_input", "subsonic_password_input"):
                check(f"{a} mounted", getattr(app, a, None) is not None)
            check("password field is masked", app.subsonic_password_input.password is True)
            from textual.widgets import OptionList
            sel = app.provider_select
            check("provider is a selectable list (OptionList), not a text field", isinstance(sel, OptionList))
            ids = [sel.get_option_at_index(i).id for i in range(sel.option_count)]
            check("list offers the four providers", ids == ["spotify", "spotify_mpris", "subsonic", "ytmusic"], repr(ids))
            app.on_input_submitted(FakeEvent(app.subsonic_url_input, "https://nd.local/"))
            app.on_input_submitted(FakeEvent(app.subsonic_user_input, "alice"))
            check("URL and user saved", config.LOCAL_CFG.get("subsonic_url") == "https://nd.local"
                  and config.LOCAL_CFG.get("subsonic_user") == "alice")
            app.on_input_submitted(FakeEvent(app.subsonic_password_input, "top-secret"))
            await pilot.pause()
            check("password stored in the keyring", kr.d.get(("spt-tui", "subsonic:alice@https://nd.local")) == "top-secret", repr(kr.d))
            raw = open(config.CONFIG_PATH, encoding="utf-8").read()
            check("password NOT in spt_config.json", "top-secret" not in raw)
            check("wizard finished and tore down its inputs", getattr(app, "subsonic_password_input", None) is None)

            # No keyring at all: refuse, do not store anywhere.
            secret_store._default = secret_store.SecretStore(use_keyring=False, which=lambda n: None)
            app.action_prompt_settings()
            await pilot.pause()
            app.on_input_submitted(FakeEvent(app.subsonic_password_input, "other-secret"))
            await pilot.pause()
            check("no keyring: user told to use SPT_SUBSONIC_PASSWORD", "SPT_SUBSONIC_PASSWORD" in status_text(app), status_text(app))
            check("no keyring: still not in the JSON", "other-secret" not in open(config.CONFIG_PATH, encoding="utf-8").read())
    finally:
        secret_store._default = old_store
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG, env = old
        if env is not None:
            os.environ["SPT_PROVIDER"] = env
        shutil.rmtree(tmp, ignore_errors=True)


ASYNC = [test_subsonic_in_the_ui, test_settings_provider_fields]

def main():
    for fn in ASYNC:
        try:
            asyncio.run(fn())
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (provider_ui) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
