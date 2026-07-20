"""P6: saved-state revalidation must be chained AFTER a successful mutation.

Reproduces two defects:
  * the saved-column revalidation runs concurrently with (and can finish before)
    the mutation, so it reads pre-mutation state;
  * when the mutation fails, a confirmed state ("Added to Liked Songs") is shown
    anyway (SpotifyClient.save_tracks used to swallow the error).

Fixed behaviour:
  * revalidation is triggered only after the mutation reports success;
  * a failed mutation shows an error, never a confirmed state, and does not
    revalidate as if saved.

Event/poll based synchronisation (threading.Event gate + bounded polling); no
fixed sleep as the sole mechanism.

Run standalone:  python tests/test_favorites_p6.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeInner:
    def __init__(self, owner):
        self.owner = owner
    def current_user_saved_tracks_contains(self, ids):
        self.owner.reval_count += 1
        self.owner.reval_read.set()
        return [False] * len(ids)
    # unused for track-only rows, present for safety
    def current_user_saved_albums_contains(self, ids): return [False] * len(ids)
    def current_user_saved_shows_contains(self, ids): return [False] * len(ids)
    def current_user_saved_episodes_contains(self, ids): return [False] * len(ids)
    def current_user_followed_artists(self, limit=50): return {"artists": {"items": []}}


class FakeSpotify:
    def __init__(self):
        self.inner = FakeInner(self)
        self.reval_count = 0
        self.reval_read = threading.Event()
        self.save_entered = threading.Event()
        self.save_gate = None          # if set (Event), save_tracks blocks on it
        self.save_should_fail = False
        self.mut = []
        self.mut_done = threading.Event()

    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def ensure(self): return self.inner
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, any_id):
        if not any_id: return None
        s = str(any_id); return s.split(":")[-1] if ":" in s else s

    def check_saved_tracks(self, ids): return [False] * len(ids)
    def save_tracks(self, ids):
        self.save_entered.set()
        if self.save_gate is not None:
            self.save_gate.wait(10)
        if self.save_should_fail:
            return False
        self.mut.append(("save", tuple(ids))); self.mut_done.set()
        return True
    def remove_tracks(self, ids):
        self.mut.append(("remove", tuple(ids))); self.mut_done.set(); return True


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

async def stays_false(fn, timeout=0.6):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if fn():
            return False
        await asyncio.sleep(0.01)
    return not fn()

def static_text(w):
    return str(getattr(w, "_Static__content", "") or "")

def feedback(app):
    """Transient feedback now goes to the status line, not the content panel:
    written into #right it sat behind any mounted table and was never seen."""
    try:
        return static_text(app.query_one("#status_line"))
    except Exception:
        return ""

def pause_bg_intervals(app):
    for attr in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
                 "_devices_interval", "_queue_interval"):
        t = getattr(app, attr, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def track_row():
    return {"type": "track", "id": "T1", "uri": "spotify:track:T1",
            "title": "song", "artist": "art", "album": "alb", "dur": "0:00", "raw": {}}


async def test_reval_chained_after_mutation():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        # Render a one-row search table and let its initial saved-column check run.
        table = app._render_search_table("[b]Results[/b]", [track_row()])
        await poll(lambda: fake.reval_count >= 1, timeout=4.0)   # initial one-shot
        for _ in range(10):
            await pilot.pause()
        fake.reval_read.clear(); base = fake.reval_count
        # Gate the mutation so we can observe ordering deterministically.
        fake.save_gate = threading.Event()
        from textual.widgets import DataTable
        assert isinstance(app.focused, DataTable), "search table should be focused"

        app.action_toggle_favorite()
        await poll(lambda: fake.save_entered.is_set(), timeout=4.0)
        # While the mutation is still pending, revalidation must NOT have read state.
        no_early = await stays_false(lambda: fake.reval_count > base, timeout=0.6)
        check("revalidation does not run before the mutation completes", no_early,
              f"reval_count={fake.reval_count} base={base}")
        # Complete the mutation; revalidation must now run.
        fake.save_gate.set()
        await poll(lambda: fake.mut_done.is_set(), timeout=4.0)
        ran = await poll(lambda: fake.reval_count > base, timeout=4.0)
        check("revalidation runs after a successful mutation", bool(ran),
              f"reval_count={fake.reval_count} base={base}")


async def test_failed_mutation_no_false_confirm():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        # Use the cached now-playing path (table=None) so this isolates the
        # confirm/error behaviour from search revalidation.
        app._bar_last_track = {"type": "track", "id": "PB", "uri": "spotify:track:PB"}
        try: app.set_focus(None)
        except Exception: pass
        fake.save_should_fail = True
        base = fake.reval_count

        app.action_toggle_favorite()
        await poll(lambda: fake.save_entered.is_set(), timeout=4.0)
        # Give the worker time to (not) paint a confirmation.
        shown_added = await poll(lambda: "Added to Liked Songs" in feedback(app), timeout=0.6)
        check("failed mutation does NOT show a confirmed state",
              not shown_added, f"status={feedback(app)!r}")
        err = await poll(lambda: "Could not update" in feedback(app), timeout=2.0)
        check("failed mutation surfaces an error", bool(err),
              f"status={feedback(app)!r}")
        no_reval = await stays_false(lambda: fake.reval_count > base, timeout=0.4)
        check("failed mutation does not revalidate as saved", no_reval,
              f"reval_count={fake.reval_count} base={base}")
        check("failed mutation recorded no successful save", fake.mut == [], f"mut={fake.mut}")


ALL = [test_reval_chained_after_mutation, test_failed_mutation_no_false_confirm]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (favorites P6) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
