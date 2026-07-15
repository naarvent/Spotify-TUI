"""P5: action_toggle_favorite must return control immediately.

Reproduces the bug where, with no DataTable focused, the action resolves the
currently-playing item with a blocking get_playback() on the UI thread. The fix
resolves from cached now-playing state when available, otherwise moves the
get_playback() lookup to a worker; either way the keypress returns at once.

Synchronisation is event/poll based (threading.Event gate + bounded polling),
never a fixed sleep as the sole mechanism.

Run standalone:  python tests/test_favorites_p5.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeSpotify:
    def __init__(self):
        self.gp_calls = 0
        self.gp_entered = threading.Event()
        self.gp_gate = threading.Event()   # closed: get_playback blocks
        self.saved = []
        self.save_evt = threading.Event()
        self.playback_item = {"type": "track", "id": "PB", "uri": "spotify:track:PB"}

    # mount-time
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, any_id):
        if not any_id: return None
        s = str(any_id)
        return s.split(":")[-1] if ":" in s else s

    def get_playback(self):
        self.gp_calls += 1
        self.gp_entered.set()
        self.gp_gate.wait(10)
        return {"item": dict(self.playback_item)}

    # track mutation
    def check_saved_tracks(self, ids): return [False] * len(ids)
    def save_tracks(self, ids):
        self.saved.append(("save", tuple(ids))); self.save_evt.set(); return True
    def remove_tracks(self, ids):
        self.saved.append(("remove", tuple(ids))); self.save_evt.set(); return True


class TApp(SptPy):
    def __init__(self, fake):
        super().__init__()
        self.spotify = fake


async def poll(fn, timeout=4.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        try:
            v = fn()
        except Exception:
            v = None
        if v:
            return v
        await asyncio.sleep(0.01)
    try:
        return fn()
    except Exception:
        return None

def pause_bg_intervals(app):
    for attr in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
                 "_devices_interval", "_queue_interval"):
        t = getattr(app, attr, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def test_action_returns_immediately_via_get_playback():
    """No focused table, empty now-playing cache -> action must not block on
    get_playback(); it returns immediately and resolves on a worker."""
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        app._bar_last_track = None          # force the get_playback() path
        try:
            app.set_focus(None)
        except Exception:
            pass

        returned = threading.Event()
        def call():
            try:
                app.action_toggle_favorite()
            finally:
                returned.set()
        threading.Thread(target=call, daemon=True).start()

        # With the gate still closed, the action must have returned already.
        immediate = await poll(lambda: returned.is_set(), timeout=0.8)
        check("action_toggle_favorite returns immediately (not blocked on get_playback)",
              immediate, f"returned={returned.is_set()} gp_calls={fake.gp_calls}")

        # Release the blocked lookup; the mutation then completes on a worker.
        fake.gp_gate.set()
        await poll(lambda: fake.save_evt.is_set(), timeout=4.0)
        check("mutation completes after slow get_playback resolves",
              fake.saved == [("save", ("PB",))], f"saved={fake.saved}")


async def test_cached_now_playing_avoids_get_playback():
    """When the now-playing cache is populated, no get_playback() is needed."""
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        app._bar_last_track = {"type": "track", "id": "CACHED", "uri": "spotify:track:CACHED"}
        try:
            app.set_focus(None)
        except Exception:
            pass
        app.action_toggle_favorite()
        await poll(lambda: fake.save_evt.is_set(), timeout=4.0)
        check("cached now-playing used (no get_playback network call)", fake.gp_calls == 0,
              f"gp_calls={fake.gp_calls}")
        check("cached item mutated", fake.saved == [("save", ("CACHED",))], f"saved={fake.saved}")


ALL = [test_action_returns_immediately_via_get_playback, test_cached_now_playing_avoids_get_playback]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (favorites P5) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
