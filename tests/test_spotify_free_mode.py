"""Spotify Free degraded mode: tier detection, capabilities, and the UI hiding
playback (status-line notice instead of failing API calls) while search,
likes and playlists keep working. No network.

Run standalone:  python tests/test_spotify_free_mode.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.providers.spotify import SpotifyProvider, FREE_NOTICE
from spt_tui.providers.base import ProviderError, Capabilities
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Inner:
    def __init__(self, product="free", raise_user=False):
        self.product, self.raise_user = product, raise_user
        self.calls = []
    def current_user(self):
        if self.raise_user:
            raise RuntimeError("network down")
        return {"id": "u", "product": self.product}
    def devices(self):
        return {"devices": [{"id": "d1", "is_active": True}]}
    def start_playback(self, **kw):
        self.calls.append(("start_playback", kw))
        err = RuntimeError("http status: 403, code:-1 - Player command failed: Premium required, reason: PREMIUM_REQUIRED")
        err.reason = "PREMIUM_REQUIRED"
        raise err


def provider(inner):
    p = SpotifyProvider()
    p.sp = inner
    return p


def test_detection():
    p = provider(Inner("free"))
    check("before detection: full capabilities", p.capabilities.can_play and p.status_notice() is None)
    p.refresh_account()
    caps = p.capabilities
    check("free: product read", p.product == "free")
    check("free: playback disabled", not caps.can_play and not caps.can_control_playback
          and not caps.can_seek and not caps.can_set_volume and not caps.can_control_queue)
    check("free: search/likes/playlists stay enabled",
          caps.can_search and caps.can_like and caps.can_edit_playlists and caps.can_browse_library)
    check("free: librespot disabled (needs Premium)", not caps.supports_local_player)
    check("free: status notice explains the limit", p.status_notice() == FREE_NOTICE and "Premium" in FREE_NOTICE)

    p = provider(Inner("premium")); p.refresh_account()
    check("premium: full capabilities, no notice", p.capabilities.can_play and p.status_notice() is None)
    check("premium: local player supported", p.capabilities.supports_local_player)

    p = provider(Inner(raise_user=True)); p.refresh_account()
    check("detection failure never raises and keeps full caps", p.capabilities.can_play and p.product is None)


def test_premium_required_on_play():
    p = provider(Inner("premium"))
    p._product = None
    try:
        p.start_playback(uris=["spotify:track:x"])
        raised = None
    except ProviderError as exc:
        raised = exc
    check("PREMIUM_REQUIRED becomes a clear ProviderError", raised is not None and "Premium" in str(raised), repr(raised))
    check("…and switches to Free mode", p.is_free_account() and not p.capabilities.can_play)


# --------------------------------------------------------------------------- #
# UI: a Free provider must not fire playback calls, and must say why.
# --------------------------------------------------------------------------- #
class FreeFake:
    display_name = "Spotify"
    def __init__(self):
        self.calls = []
        self.capabilities = Capabilities(can_play=False, can_control_playback=False, can_seek=False,
                                         can_set_volume=False, can_control_queue=False,
                                         can_shuffle_repeat=False, can_list_devices=False)
        self.refreshed = threading.Event()
    def unsupported_message(self, cap): return "Spotify Premium is required for playback control (Free account)."
    def status_notice(self): return FREE_NOTICE
    def refresh_account(self): self.refreshed.set()
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
    def get_playback(self): return {}
    def recently_played(self, limit=50): return {"items": []}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)
    def save_tracks(self, ids): self.calls.append(("save_tracks", ids)); return True
    def remove_tracks(self, ids): return True
    def __getattr__(self, name):
        # Any playback method reaching the provider is a failure of the gate.
        if name in ("pause", "resume", "next", "prev", "seek_ms", "set_volume", "start_playback",
                    "add_to_queue", "shuffle", "repeat", "devices"):
            def rec(*a, **k):
                self.calls.append((name, a, k))
            return rec
        raise AttributeError(name)


class TApp(SptPy):
    def __init__(self, fake):
        super().__init__()
        self.spotify = fake


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def poll(fn, timeout=3.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        if fn():
            return True
        await asyncio.sleep(0.02)
    return bool(fn())


def status_text(app):
    try:
        return str(app.status_line.render())
    except Exception:
        return ""


async def test_ui_gates():
    fake = FreeFake(); app = TApp(fake)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(); pause_intervals(app)
        ok = await poll(lambda: fake.refreshed.is_set())
        check("startup runs refresh_account on the provider", ok)
        shown = await poll(lambda: "Premium" in status_text(app))
        check("startup shows the Free notice on the status line", shown, status_text(app))
        check("the notice is a warning", app.status_line.has_class("-warn"))

        for action in ("action_play_pause", "action_next", "action_prev_logic", "action_seek_fwd",
                       "action_seek_back", "action_volume_up", "action_volume_down",
                       "action_toggle_mute", "action_toggle_shuffle", "action_toggle_repeat"):
            app._clear_status_line()
            getattr(app, action)()
            await pilot.pause()
            check(f"{action}: notice shown", "Premium" in status_text(app), status_text(app))
        await asyncio.sleep(0.3)
        played = [c for c in fake.calls if c[0] != "save_tracks"]
        check("no playback call reached the provider", not played, repr(played))
        check("play_pause did not flip the cached playing state", not app._now_internal_is_playing)

        app._clear_status_line()
        app._play_row(0, app.right_panel)
        await pilot.pause()
        check("Enter on a row (_play_row) is gated with a notice", "Premium" in status_text(app))
        app._clear_status_line()
        app.action_manage_devices()
        await pilot.pause()
        check("devices view gated", "Premium" in status_text(app))

        check("_cap reports likes as still available", app._cap("can_like"))
        check("_cap reports playlists as still available", app._cap("can_edit_playlists"))
        check("provider property is the active provider", app.provider is fake)


async def test_ui_full_caps_unchanged():
    """A provider without `capabilities` (all the older fakes) is fully capable."""
    class Plain:
        def __init__(self): self.calls = []
        def has_cached_token(self): return True
        def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None, "total": 0}
        def get_playback(self): return {}
        def recently_played(self, limit=50): return {"items": []}
        def ensure(self): return self
        def fmt_duration(self, ms): return "0:00"
        def next(self): self.calls.append("next")
    fake = Plain(); app = TApp(fake)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(); pause_intervals(app)
        check("legacy provider: every capability allowed", app._cap("can_play") and app._cap("can_control_playback"))
        app.action_next()
        ok = await poll(lambda: "next" in fake.calls)
        check("legacy provider: action_next still calls the provider", ok, repr(fake.calls))


ALL = [test_detection, test_premium_required_on_play]
ASYNC = [test_ui_gates, test_ui_full_caps_unchanged]

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
    print("\n==== SUMMARY (spotify_free_mode) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
