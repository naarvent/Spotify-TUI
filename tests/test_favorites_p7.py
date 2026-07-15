"""P7: serialize favorite read-modify-write per item id.

Reproduces the race where two quick presses on the SAME item both read the
pre-mutation state and issue duplicate operations (e.g. two saves instead of a
save then a remove). The fix serializes per id, while leaving DIFFERENT ids free
to run concurrently.

Event/poll based synchronisation (threading.Event gate + bounded polling); no
fixed sleep as the sole mechanism.

Run standalone:  python tests/test_favorites_p7.py
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
        self.liked = {}
        self.ops = []            # ('save'|'remove', id) in commit order
        self.reads = []          # (id, was_liked) as each worker read it
        self.check_entered = 0
        self.check_gate = None   # Event: block inside check_saved_tracks
        self.lock = threading.Lock()

    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, any_id): return any_id

    def check_saved_tracks(self, ids):
        tid = ids[0]
        with self.lock:
            self.check_entered += 1
        if self.check_gate is not None:
            self.check_gate.wait(10)
        with self.lock:
            val = self.liked.get(tid, False)
            self.reads.append((tid, val))
        return [val]

    def save_tracks(self, ids):
        tid = ids[0]
        with self.lock:
            self.liked[tid] = True; self.ops.append(("save", tid))
        return True

    def remove_tracks(self, ids):
        tid = ids[0]
        with self.lock:
            self.liked[tid] = False; self.ops.append(("remove", tid))
        return True


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


async def test_same_id_serialized():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        fake.check_gate = threading.Event()          # hold the first read open

        # Press 1: enters check and blocks on the gate.
        app._toggle_track_favorite("X", None, None)
        await poll(lambda: fake.check_entered >= 1, timeout=4.0)
        # Press 2 on the SAME id while press 1 is mid-read.
        app._toggle_track_favorite("X", None, None)

        # With per-id serialization, press 2 must not start its own read yet.
        raced = await poll(lambda: fake.check_entered > 1, timeout=0.6)
        check("same-ID: 2nd press does not read concurrently (RMW serialized)",
              not raced, f"check_entered={fake.check_entered}")

        # Release; both presses now complete in order.
        fake.check_gate.set()
        await poll(lambda: len(fake.ops) >= 2, timeout=4.0)
        check("same-ID: exactly one save then one remove (no duplicate op)",
              fake.ops == [("save", "X"), ("remove", "X")], f"ops={fake.ops}")
        check("same-ID: 2nd press read the post-mutation state",
              fake.reads == [("X", False), ("X", True)], f"reads={fake.reads}")
        check("same-ID: final state is unliked (two toggles net out)",
              fake.liked.get("X") is False, f"liked={fake.liked}")


async def test_different_ids_not_blocked():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        fake.check_gate = threading.Event()

        app._toggle_track_favorite("A", None, None)
        await poll(lambda: fake.check_entered >= 1, timeout=4.0)
        app._toggle_track_favorite("B", None, None)
        # Different ids must be free to read concurrently (no cross-id blocking).
        both = await poll(lambda: fake.check_entered >= 2, timeout=2.0)
        check("different IDs read concurrently (not serialized)", bool(both),
              f"check_entered={fake.check_entered}")

        fake.check_gate.set()
        await poll(lambda: len(fake.ops) >= 2, timeout=4.0)
        check("different IDs: both mutations applied",
              sorted(fake.ops) == [("save", "A"), ("save", "B")], f"ops={fake.ops}")


ALL = [test_same_id_serialized, test_different_ids_not_blocked]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (favorites P7) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
