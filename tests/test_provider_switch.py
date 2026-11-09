"""Provider picker and live switching: readiness probe, the `P` picker and the
Settings list, the hot switch (close old, reset state, reload the view,
persist), failure before the switch, failure after it with rollback, and
SPT_PROVIDER precedence (session-only, not saved). Fake providers only.

Run standalone:  python tests/test_provider_switch.py
"""
import os, sys, asyncio, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui import providers as P
from spt_tui.providers.base import Provider, make_page
from spt_tui.app import SptPy
from spt_tui.app.provider_switch import ProviderPickerScreen

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
INSTANCES = []

class FakeProv(Provider):
    name = "spotify"
    display_name = "Spotify"
    playlists = [{"id": "sp1", "name": "Spotify Mix", "uri": "spotify:playlist:sp1"}]
    reason = None
    def __init__(self):
        super().__init__()
        self.closed = False; self.searches = []; self.saved_calls = 0
        INSTANCES.append(self)
    def unavailable_reason(self): return type(self).reason
    def close(self): self.closed = True
    def user_playlists(self, limit=50, offset=0):
        return make_page(list(self.playlists), offset=offset, limit=limit)
    def search(self, query, kind="track", limit=10, offset=0):
        self.searches.append((query, kind)); return make_page([])
    def saved_tracks(self, limit=50, offset=0):
        self.saved_calls += 1; return make_page([])

class FakeSub(FakeProv):
    name = "subsonic"; display_name = "Subsonic"
    playlists = [{"id": "p1", "name": "Road Trip", "uri": "subsonic:playlist:p1"},
                 {"id": "p2", "name": "Chill", "uri": "subsonic:playlist:p2"}]

class FakeYT(FakeProv):
    name = "ytmusic"; display_name = "YouTube Music"
    playlists = []
    reason = "the optional 'ytmusicapi' package is not installed."

FAKES = {"spotify": FakeProv, "subsonic": FakeSub, "ytmusic": FakeYT}


class FakeLP:
    def __init__(self): self.stopped = False
    def stop(self): self.stopped = True
    def is_available(self): return False
    def is_running(self): return False


class TApp(SptPy):
    def __init__(self):
        super().__init__()
        self.spotify = FakeProv()
        self.local_player = FakeLP()


class env:
    """Isolated config + fake registry (+ optional SPT_PROVIDER)."""
    def __init__(self, spt_provider=None):
        self.spt_provider = spt_provider
    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="spt_switch_")
        self.old = (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG,
                    os.environ.get("SPT_PROVIDER"), P.provider_class)
        config.CACHE_DIR = self.tmp
        config.CONFIG_PATH = os.path.join(self.tmp, "spt_config.json")
        config.LOCAL_CFG = {}
        os.environ.pop("SPT_PROVIDER", None)
        if self.spt_provider:
            os.environ["SPT_PROVIDER"] = self.spt_provider
        P.provider_class = lambda n: FAKES[n]
        FakeYT.reason = "the optional 'ytmusicapi' package is not installed."
        INSTANCES.clear()
        return self
    def __exit__(self, *a):
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG, e, P.provider_class = self.old
        if e is None:
            os.environ.pop("SPT_PROVIDER", None)
        else:
            os.environ["SPT_PROVIDER"] = e
        shutil.rmtree(self.tmp, ignore_errors=True)


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


def pl_names(app):
    return [(getattr(li, "data", {}) or {}).get("name") for li in app.pl_list.children]


# --------------------------------------------------------------------------- #
# Readiness probe (no app)
# --------------------------------------------------------------------------- #
def test_status_probe():
    with env():
        P.provider_class = P.__dict__["provider_class"]  # unused by the probe
        none = lambda n: None
        allbin = lambda n: "/usr/bin/" + n
        st = P.provider_status("ytmusic", which=allbin, find_spec=lambda m: None)
        check("ytmusic without ytmusicapi: unusable", not st["usable"] and "missing ytmusicapi" in st["issues"], repr(st))
        st = P.provider_status("ytmusic", which=lambda n: None if n == "mpv" else "/bin/x", find_spec=lambda m: object())
        check("ytmusic without mpv: usable, playback issue listed", st["usable"] and any("mpv" in i for i in st["issues"]), repr(st))
        st = P.provider_status("subsonic", which=allbin)
        check("subsonic without config: unusable", not st["usable"] and any("config" in i for i in st["issues"]), repr(st))
        config.LOCAL_CFG = {"subsonic_url": "https://h", "subsonic_user": "u"}
        os.environ["SPT_SUBSONIC_PASSWORD"] = "x"
        try:
            st = P.provider_status("subsonic", which=allbin)
            check("subsonic configured + env password: available", st["usable"] and not st["issues"], repr(st))
        finally:
            os.environ.pop("SPT_SUBSONIC_PASSWORD", None)
        from spt_tui.secret_store import SecretStore
        st = P.provider_status("subsonic", which=allbin, secret_store=SecretStore(use_keyring=False, which=none))
        check("subsonic configured, no password anywhere: unusable", not st["usable"]
              and any("password" in i for i in st["issues"]), repr(st))
        from spt_tui.app.provider_switch import status_label
        check("label 'available' when nothing is missing", status_label({"usable": True, "issues": []}) == "available")
        check("label marks unusable providers", status_label({"usable": False, "issues": ["missing x"]}).startswith("unavailable"))


# --------------------------------------------------------------------------- #
# Picker + live switch
# --------------------------------------------------------------------------- #
async def test_picker_opens_with_P():
    with env():
        app = TApp()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await pilot.press("P")
            ok = await poll(lambda: isinstance(app.screen, ProviderPickerScreen))
            check("P opens the provider picker (modal)", ok, type(app.screen).__name__)
            if not ok:
                return
            from textual.widgets import OptionList
            ol = app.screen.query_one("#provider_options", OptionList)
            ids = [ol.get_option_at_index(i).id for i in range(ol.option_count)]
            check("picker lists the four providers", ids == list(P.PROVIDER_NAMES), repr(ids))
            prompts = " ".join(str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count))
            check("picker shows each provider's status", "missing" in prompts or "available" in prompts, prompts[:200])
            check("active provider marked (● and ACTIVE badge)", "ACTIVE" in prompts and "●" in prompts)
            check("no SPT_PROVIDER warning when unset", len(app.screen.query("#provider_picker_env")) == 0)
            await pilot.press("escape")
            ok = await poll(lambda: not isinstance(app.screen, ProviderPickerScreen))
            check("Esc closes the picker without switching", ok and isinstance(app.spotify, FakeProv)
                  and type(app.spotify) is FakeProv)


async def test_hot_switch():
    with env():
        app = TApp()
        old = app.spotify; lp = app.local_player
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app._load_playlists(force=True)
            await poll(lambda: pl_names(app) == ["Spotify Mix"])
            # State that must not survive the switch.
            app._local_queue = [{"uri": "spotify:track:x"}]
            app._bar_last_track = {"id": "x", "name": "old"}
            app._now_internal_is_playing = True
            app._playlist_cache = {"sp1": ([], [])}
            app._right_view = ("search", "abba", 1)

            await pilot.press("P")
            await poll(lambda: isinstance(app.screen, ProviderPickerScreen))
            from textual.widgets import OptionList
            ol = app.screen.query_one("#provider_options", OptionList)
            ol.highlighted = list(P.PROVIDER_NAMES).index("subsonic")
            await pilot.press("enter")
            ok = await poll(lambda: isinstance(app.spotify, FakeSub))
            check("Enter in the picker switches live (no restart)", ok, type(app.spotify).__name__)
            new = app.spotify
            check("old provider closed (mpv/sockets)", old.closed)
            check("librespot stopped", lp.stopped and app.local_player is None)
            check("queue reset", app._local_queue == [])
            check("now-playing state reset", app._bar_last_track is None and not app._now_internal_is_playing)
            check("playlist track cache reset", app._playlist_cache == {})
            ok = await poll(lambda: pl_names(app) == ["Road Trip", "Chill"])
            check("sidebar reloaded from the new provider", ok, repr(pl_names(app)))
            ok = await poll(lambda: ("abba", "track") in new.searches)
            check("active search view re-run on the new provider", ok, repr(new.searches))
            check("choice persisted to spt_config.json", config.LOCAL_CFG.get("provider") == "subsonic"
                  and '"subsonic"' in open(config.CONFIG_PATH).read())
            ok = await poll(lambda: "saved" in status_text(app))
            check("status line confirms the switch", ok, status_text(app))
            check("sub_title shows the provider", app.sub_title == "Subsonic")
            check("switch flag cleared", not getattr(app, "_switching_provider", False))

            # Library view reload: Liked Songs on the new provider.
            app._right_view = ("liked", "", 2)
            app.switch_provider("subsonic")   # picking the active one reloads it
            ok = await poll(lambda: isinstance(app.spotify, FakeSub) and app.spotify is not new
                            and app.spotify.saved_calls >= 1)
            check("re-picking the active provider reloads it and its view (Liked Songs)", ok)
            check("…closing the previous instance", new.closed)


async def test_failure_keeps_current():
    with env():
        app = TApp(); old = app.spotify
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.switch_provider("ytmusic")
            ok = await poll(lambda: "Could not switch" in status_text(app))
            check("unavailable provider: clear notice", ok, status_text(app))
            check("…mentions the reason", "ytmusicapi" in status_text(app))
            check("…current provider kept and NOT closed", app.spotify is old and not old.closed)
            check("…nothing persisted", "provider" not in config.LOCAL_CFG)
            check("…switch flag cleared", await poll(lambda: not getattr(app, "_switching_provider", False)))
            app.switch_provider("winamp")
            await pilot.pause()
            check("unknown name rejected", "Unknown provider" in status_text(app) and app.spotify is old)


async def test_failure_after_close_rolls_back():
    with env():
        app = TApp(); old = app.spotify
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            orig = app._reload_after_switch
            def boom(rv):
                if isinstance(app.spotify, FakeSub):
                    raise RuntimeError("reload exploded")
                return orig(rv)
            app._reload_after_switch = boom
            app.switch_provider("subsonic")
            ok = await poll(lambda: "Restored" in status_text(app))
            check("failure after closing: rollback notice", ok, status_text(app))
            check("…previous provider restored (fresh instance of its class)",
                  type(app.spotify) is FakeProv and app.spotify is not old, type(app.spotify).__name__)
            check("…old instance had been closed", old.closed)
            subs = [i for i in INSTANCES if isinstance(i, FakeSub)]
            check("…the failed new provider is closed", subs and all(s.closed for s in subs))
            check("…nothing persisted", "provider" not in config.LOCAL_CFG)
            ok = await poll(lambda: pl_names(app) == ["Spotify Mix"])
            check("…UI reloaded from the restored provider", ok, repr(pl_names(app)))


async def test_env_precedence():
    with env(spt_provider="spotify"):
        check("startup: SPT_PROVIDER wins over the saved choice",
              (config.LOCAL_CFG.update(provider="subsonic") or P.resolve_provider_name()) == "spotify")
        config.LOCAL_CFG = {}
        app = TApp()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await pilot.press("P")
            await poll(lambda: isinstance(app.screen, ProviderPickerScreen))
            warn = app.screen.query("#provider_picker_env")
            check("picker warns that SPT_PROVIDER is set", len(warn) == 1
                  and "session only" in str(warn.first().render()))
            await pilot.press("escape")
            await poll(lambda: not isinstance(app.screen, ProviderPickerScreen))
            app.switch_provider("subsonic")
            ok = await poll(lambda: isinstance(app.spotify, FakeSub))
            check("switch still allowed for this session", ok)
            ok = await poll(lambda: "session only" in status_text(app))
            check("notice: session only, not saved", ok, status_text(app))
            check("choice NOT persisted while SPT_PROVIDER is set", "provider" not in config.LOCAL_CFG
                  and not os.path.exists(config.CONFIG_PATH))


async def test_settings_list_switches():
    with env():
        app = TApp()
        async with app.run_test(size=(130, 50)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_prompt_settings()
            await pilot.pause()
            sel = app.provider_select
            sel.focus(); await pilot.pause()
            sel.highlighted = list(P.PROVIDER_NAMES).index("subsonic")
            await pilot.press("enter")
            ok = await poll(lambda: isinstance(app.spotify, FakeSub))
            check("Enter on the Settings provider list switches live", ok, type(app.spotify).__name__)


async def test_busy_guard():
    with env():
        app = TApp()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app._switching_provider = True
            app.switch_provider("subsonic")
            await pilot.pause()
            check("a second switch while one runs is refused", "already in progress" in status_text(app)
                  and type(app.spotify) is FakeProv)


ALL = [test_status_probe]
ASYNC = [test_picker_opens_with_P, test_hot_switch, test_failure_keeps_current,
         test_failure_after_close_rolls_back, test_env_precedence, test_settings_list_switches,
         test_busy_guard]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    for fn in ASYNC:
        try:
            asyncio.run(fn())
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (provider_switch) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name, "::", (detail or "").strip().splitlines()[-1] if detail else "")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
