"""Deterministic tests for _sync_playback worker overlap (P4).

Reproduces the bug where each 1.5s tick spawns a new worker with no guard, so a
slow OLD sync (A) can finish after a newer sync (B) and overwrite B's freshly
applied state. Pins the fixed behaviour:
  * no worker accumulation when a fetch takes >1.5s (only one in flight)
  * an older/slower worker never overwrites a newer worker's published state
  * state re-syncs on the next tick after a slow OR failed request
    (the in-flight guard must be released even on exception)

Synchronisation is event/poll based (threading.Event gates + bounded polling),
never a fixed sleep as the sole mechanism.

Run standalone:  python tests/test_playback_sync.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeSpotify:
    """Only what on_mount / the now-bar touch; playback fetch is patched per test."""
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def get_playback(self): return {}
    def devices(self): return []
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, any_id): return any_id


class TApp(SptPy):
    def __init__(self, fake):
        super().__init__()
        self.spotify = fake


class Fetch:
    """Programmable stand-in for _get_current_or_last_track().

    plan[i] describes the i-th call: {'id', 'gate' (Event to block on),
    'entered' (Event set on entry), 'raise' (bool)}. Tracks concurrency so a
    test can prove no accumulation.
    """
    def __init__(self, plan):
        self.plan = plan
        self.calls = 0
        self.inflight = 0
        self.max_inflight = 0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.calls += 1
            idx = self.calls
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            spec = self.plan[idx - 1] if idx - 1 < len(self.plan) else self.plan[-1]
            ev = spec.get("entered")
            if ev: ev.set()
            gate = spec.get("gate")
            if gate: gate.wait(10)
            if spec.get("raise"):
                raise RuntimeError("slow fetch failed")
            tid = spec.get("id")
            return ({"id": tid, "duration_ms": 1000}, 100, 1000, True)
        finally:
            with self.lock:
                self.inflight -= 1


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

async def poll_stable(fn, timeout=0.6):
    """Bounded observation window; returns the last sampled value."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    v = fn()
    while loop.time() < end:
        v = fn()
        await asyncio.sleep(0.01)
    return v

async def pump(pilot, n=20):
    for _ in range(n):
        await pilot.pause()

def pause_bg_intervals(app):
    for attr in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
                 "_devices_interval", "_queue_interval"):
        t = getattr(app, attr, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def cur_id(app):
    return getattr(app, "_now_internal_track_id", None)


async def test_no_overlap_and_no_stale_overwrite():
    """A (older, slow) must not accumulate a second worker nor overwrite B."""
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)

        entA = threading.Event(); gateA = threading.Event()
        fetch = Fetch(plan=[
            {"id": "A", "entered": entA, "gate": gateA},  # call 1: old + slow
            {"id": "B"},                                   # call 2: newer + fast
            {"id": "B"},                                   # call 3+: newest
        ])
        app._get_current_or_last_track = fetch

        observed = []
        def record():
            v = cur_id(app)
            if v and (not observed or observed[-1] != v):
                observed.append(v)

        # Tick 1: worker A starts and blocks inside the fetch.
        app._sync_playback()
        await poll(lambda: entA.is_set(), timeout=4.0)
        record()

        # Ticks 2..4 fire while A is still in flight (Spotify slow > 1.5s).
        app._sync_playback(); app._sync_playback(); app._sync_playback()
        # Bounded window: with the fix nothing else runs; without it, B runs.
        await poll_stable(lambda: fetch.calls, timeout=0.5)
        record()
        no_accum = fetch.max_inflight <= 1
        check("no worker accumulation while a fetch is slow (max in-flight == 1)",
              no_accum, f"max_inflight={fetch.max_inflight}, calls={fetch.calls}")
        check("newer worker did not publish out of order while A in flight",
              cur_id(app) in (None,) , f"cur={cur_id(app)}")

        # Let A finish; it publishes 'A'.
        gateA.set()
        await poll(lambda: cur_id(app) == "A", timeout=4.0)
        record()

        # Next tick (no worker in flight now) re-syncs to the freshest value.
        app._sync_playback()
        await poll(lambda: cur_id(app) == "B", timeout=4.0)
        record()

        order = {"A": 0, "B": 1}
        idxs = [order[x] for x in observed if x in order]
        check("published state never regresses (older never overwrites newer)",
              idxs == sorted(idxs), f"observed={observed}")


async def test_resync_after_slow_request():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        entA = threading.Event(); gateA = threading.Event()
        fetch = Fetch(plan=[
            {"id": "OLD", "entered": entA, "gate": gateA},
            {"id": "NEW"},
        ])
        app._get_current_or_last_track = fetch
        app._sync_playback()
        await poll(lambda: entA.is_set(), timeout=4.0)
        gateA.set()
        await poll(lambda: cur_id(app) == "OLD", timeout=4.0)
        # A slow request completed; the next tick must run and refresh.
        app._sync_playback()
        got = await poll(lambda: cur_id(app) == "NEW", timeout=4.0)
        check("state re-syncs on next tick after a slow request", cur_id(app) == "NEW",
              f"cur={cur_id(app)}")


async def test_resync_after_failed_request():
    """A failed fetch must release the in-flight guard so ticks resume."""
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        entA = threading.Event()
        fetch = Fetch(plan=[
            {"id": None, "entered": entA, "raise": True},   # call 1 fails
            {"id": "AFTER"},                                 # call 2 succeeds
        ])
        app._get_current_or_last_track = fetch
        app._sync_playback()
        await poll(lambda: entA.is_set(), timeout=4.0)
        await poll_stable(lambda: fetch.calls, timeout=0.3)   # let worker unwind
        # Next tick must not be permanently blocked by a leaked in-flight guard.
        app._sync_playback()
        got = await poll(lambda: cur_id(app) == "AFTER", timeout=4.0)
        check("state re-syncs on next tick after a failed request", cur_id(app) == "AFTER",
              f"cur={cur_id(app)}, calls={fetch.calls}")


ALL = [test_no_overlap_and_no_stale_overwrite, test_resync_after_slow_request,
       test_resync_after_failed_request]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (playback sync) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
