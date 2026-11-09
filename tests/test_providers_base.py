"""Provider layer: registry / selection, Capabilities, pages and the
Spotify-shaped adapter. No network, no Textual.

Run standalone:  python tests/test_providers_base.py
"""
import os, sys, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui import providers as P
from spt_tui.providers.base import (Capabilities, Provider, SpotifyShapedAdapter,
                                    make_page, ProviderUnsupported)

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class isolated_config:
    """Point the config file at a temp dir and clear SPT_PROVIDER for the block."""
    def __init__(self, cfg=None, env=None):
        self.cfg, self.env = dict(cfg or {}), env
    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="spt_prov_")
        self.old = (config.CONFIG_PATH, config.LOCAL_CFG, os.environ.get("SPT_PROVIDER"))
        config.CONFIG_PATH = os.path.join(self.tmp, "spt_config.json")
        config.LOCAL_CFG = self.cfg
        os.environ.pop("SPT_PROVIDER", None)
        if self.env is not None:
            os.environ["SPT_PROVIDER"] = self.env
        return self
    def __exit__(self, *a):
        config.CONFIG_PATH, config.LOCAL_CFG, env = self.old
        if env is None:
            os.environ.pop("SPT_PROVIDER", None)
        else:
            os.environ["SPT_PROVIDER"] = env
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_resolution_order():
    with isolated_config():
        check("default provider is spotify", P.resolve_provider_name() == "spotify")
    with isolated_config({"provider": "subsonic"}):
        check("config 'provider' is honoured", P.resolve_provider_name() == "subsonic")
    with isolated_config({"provider": "subsonic"}, env="ytmusic"):
        check("SPT_PROVIDER overrides the config", P.resolve_provider_name() == "ytmusic")
    with isolated_config(env="Navidrome"):
        check("aliases are accepted (navidrome -> subsonic)", P.resolve_provider_name() == "subsonic")
    with isolated_config(env="winamp"):
        check("unknown env value falls back to spotify", P.resolve_provider_name() == "spotify")
    with isolated_config({"provider": 42}):
        check("garbage config value falls back to spotify", P.resolve_provider_name() == "spotify")


def test_save_choice():
    with isolated_config():
        ok = P.save_provider_choice("Navidrome")
        check("save_provider_choice normalises and persists", ok and config.LOCAL_CFG.get("provider") == "subsonic",
              repr(config.LOCAL_CFG))
        check("saved file holds the choice", '"subsonic"' in open(config.CONFIG_PATH).read())
        check("unknown choice rejected", P.save_provider_choice("nope") is False)


def test_create_fallback():
    class Broken(Provider):
        name = "x"
        def unavailable_reason(self): return "missing thing."
    orig = P.provider_class
    def fake_class(name):
        return Broken if name == "subsonic" else orig(name)
    P.provider_class = fake_class
    try:
        with isolated_config():
            prov, notice = P.create_provider("subsonic")
        check("unavailable provider falls back to Spotify", type(prov).__name__ == "SpotifyProvider",
              type(prov).__name__)
        check("fallback carries a clear notice", notice and "subsonic" in notice and "missing thing" in notice, repr(notice))
    finally:
        P.provider_class = orig

    def boom(name):
        if name == "ytmusic":
            raise ImportError("no module named x")
        return orig(name)
    P.provider_class = boom
    try:
        with isolated_config():
            prov, notice = P.create_provider("ytmusic")
        check("a provider that crashes on import falls back too", type(prov).__name__ == "SpotifyProvider")
        check("crash notice explains it", notice and "no module named x" in notice, repr(notice))
    finally:
        P.provider_class = orig

    with isolated_config():
        prov, notice = P.create_provider()
    check("default creation: Spotify, no notice", type(prov).__name__ == "SpotifyProvider" and notice is None)


def test_capabilities():
    c = Capabilities()
    check("capabilities default to fully capable", all(v for k, v in c.as_dict().items()
                                                       if k not in ("supports_local_player", "needs_spotify_auth", "local_library",
                                                                     "can_import_from_spotify",
                                                                     # opt-in: only providers that implement them
                                                                     "can_artist_top_tracks", "can_related_artists", "can_radio",
                                                                     "can_download")))
    d = c.replace(can_play=False)
    check("replace() returns a modified copy", d.can_play is False and c.can_play is True)


def test_make_page():
    items = list(range(10))
    p = make_page(items, offset=0, limit=4)
    check("page slices", p["items"] == [0, 1, 2, 3] and p["total"] == 10 and p["next"])
    p = make_page(items, offset=8, limit=4)
    check("last page has no next", p["items"] == [8, 9] and not p["next"])
    p = make_page([], offset=0, limit=50)
    check("empty page: total 0, no next", p["total"] == 0 and p["items"] == [] and not p["next"])


def test_adapter():
    class Prov(Provider):
        name = "fake"
        def __init__(self):
            super().__init__(); self.calls = []
        def search(self, query, kind="track", limit=10, offset=0):
            self.calls.append((query, kind, limit, offset))
            return make_page([{"id": kind}])
        @property
        def capabilities(self):
            return Capabilities(can_podcasts=False)
    p = Prov()
    sp = p.ensure()
    check("ensure() returns an adapter", isinstance(sp, SpotifyShapedAdapter))
    check("ensure() is cached", p.ensure() is sp)
    res = sp.search("abba", type="track", limit=5)
    check("adapter.search wraps the page under the plural key", res["tracks"]["items"] == [{"id": "track"}], repr(res))
    check("adapter.search passes query/limit", p.calls[-1] == ("abba", "track", 5, 0), repr(p.calls))
    res = sp.search("x", type="show")
    check("podcast search on a provider without podcasts is empty",
          res["shows"]["items"] == [] and len(p.calls) == 1)
    check("unknown spotipy methods are AttributeError (hasattr False)",
          not hasattr(sp, "playlist_change_details"))
    check("audited saved-state methods exist with safe defaults",
          sp.current_user_saved_albums_contains(["a", "b"]) == [False, False]
          and sp.current_user_saved_shows_contains(["s"]) == [False]
          and sp.current_user_saved_episodes_contains(["e"]) == [False]
          and sp.current_user_follows_playlists_contains(["p"]) == [False])
    check("followed artists: empty page shape", sp.current_user_followed_artists(limit=50)["artists"]["items"] == [])
    try:
        sp.current_user_unfollow_playlist("p1")
        raised = False
    except ProviderUnsupported:
        raised = True
    check("delete playlist unsupported -> ProviderUnsupported", raised)
    try:
        p.start_playback(uris=["x"])
        raised = False
    except ProviderUnsupported:
        raised = True
    check("base start_playback raises ProviderUnsupported", raised)
    check("base check_saved_tracks is aligned and False", p.check_saved_tracks(["a", "b"]) == [False, False])


def test_compat_shim():
    from spt_tui.spotify_client import SpotifyClient, RateLimiter
    from spt_tui.providers.spotify import SpotifyProvider
    check("spotify_client.SpotifyClient is SpotifyProvider", SpotifyClient is SpotifyProvider)
    check("SpotifyProvider is a Provider", issubclass(SpotifyProvider, Provider))
    check("RateLimiter still importable", RateLimiter is not None)
    check("historic static helpers kept", SpotifyClient.fmt_duration(61000) == "1:01")


ALL = [test_resolution_order, test_save_choice, test_create_fallback, test_capabilities,
       test_make_page, test_adapter, test_compat_shim]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (providers_base) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
