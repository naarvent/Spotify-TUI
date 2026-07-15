"""Deterministic unit tests for spotify_client.py primitives.

No Textual, no network. Uses a fake inner spotipy client with recorded calls,
and a monkeypatched clock where time matters (no fixed sleep is used as the
synchronisation mechanism).

Run standalone:  python tests/test_spotify_client.py
Exit code is 0 only if every check passes.
"""
import os, sys, time, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.spotify_client import SpotifyClient, RateLimiter

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeInner:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.batch_should_raise = False
        self.saved_map = {}
        self.raise_ids = set()
        self._devices = {"devices": []}

    def _rec(self, name, *a):
        with self.lock:
            self.calls.append((name, a))

    def current_user_saved_tracks_contains(self, ids):
        self._rec("contains", tuple(ids))
        if len(ids) > 1 and self.batch_should_raise:
            raise RuntimeError("batch boom")
        out = []
        for i in ids:
            if i in self.raise_ids:
                raise RuntimeError("bad id " + str(i))
            out.append(self.saved_map.get(i, False))
        return out

    def devices(self):
        self._rec("devices"); return self._devices

    def transfer_playback(self, device_id, force_play=False):
        self._rec("transfer_playback", device_id, force_play)

    def current_playback(self):
        self._rec("current_playback"); return {"is_playing": True}

    def start_playback(self, *a, **k):
        self._rec("start_playback")


def make_client(inner):
    c = SpotifyClient()
    c.sp = inner
    return c


def test_check_saved_happy():
    inner = FakeInner(); inner.saved_map = {"a": True, "b": False, "c": True}
    out = make_client(inner).check_saved_tracks(["a", "b", "c"])
    check("check_saved happy alignment", out == [True, False, True], repr(out))

def test_check_saved_batching():
    inner = FakeInner()
    ids = [f"id{i}" for i in range(120)]
    for i in range(0, 120, 2):
        inner.saved_map[f"id{i}"] = True
    out = make_client(inner).check_saved_tracks(ids)
    contains_calls = [x for x in inner.calls if x[0] == "contains"]
    expected = [(i % 2 == 0) for i in range(120)]
    check("check_saved batches by 50 (3 calls)", len(contains_calls) == 3, f"{len(contains_calls)}")
    check("check_saved batched alignment", out == expected)
    check("check_saved one bool per id", len(out) == 120, str(len(out)))

def test_check_saved_fallback():
    inner = FakeInner()
    inner.batch_should_raise = True
    inner.saved_map = {"x": True, "z": True}
    inner.raise_ids = {"y"}
    out = make_client(inner).check_saved_tracks(["x", "y", "z"])
    check("check_saved fallback length preserved", len(out) == 3, repr(out))
    check("check_saved fallback (bad id -> False)", out == [True, False, True], repr(out))

def test_active_device_cache():
    inner = FakeInner()
    inner._devices = {"devices": [{"id": "devA", "is_active": True},
                                  {"id": "devB", "is_active": False}]}
    c = make_client(inner)
    d1 = c._active_device_id(); c._active_device_id()
    dev_calls = [x for x in inner.calls if x[0] == "devices"]
    check("active_device returns active id", d1 == "devA", repr(d1))
    check("active_device cached (1 devices() for 2 reads)", len(dev_calls) == 1, f"{len(dev_calls)}")
    c.transfer("devB", force_play=False)
    c._active_device_id()
    dev_calls2 = [x for x in inner.calls if x[0] == "devices"]
    check("transfer invalidates device cache", len(dev_calls2) == 2, f"{len(dev_calls2)}")

def test_active_device_cache_expiry():
    import spt_tui.spotify_client as scmod
    inner = FakeInner(); inner._devices = {"devices": [{"id": "devA", "is_active": True}]}
    c = make_client(inner)
    fake_now = [1000.0]; orig = scmod.time.time
    scmod.time.time = lambda: fake_now[0]
    try:
        c._active_device_id()
        fake_now[0] = 1004.9; c._active_device_id()
        n_cached = len([x for x in inner.calls if x[0] == "devices"])
        fake_now[0] = 1006.0; c._active_device_id()
        n_expired = len([x for x in inner.calls if x[0] == "devices"])
    finally:
        scmod.time.time = orig
    check("device cache holds <5s", n_cached == 1, f"{n_cached}")
    check("device cache expires >5s", n_expired == 2, f"{n_expired}")

def test_rate_limiter():
    rl = RateLimiter(max_calls=5, period=0.2)
    acquired = []; lock = threading.Lock()
    def worker():
        rl.acquire()
        with lock: acquired.append(time.monotonic())
    threads = [threading.Thread(target=worker) for _ in range(12)]
    t0 = time.monotonic()
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)
    fast = [a for a in acquired if (a - t0) < 0.15]
    check("rate limiter admits all eventually", len(acquired) == 12, str(len(acquired)))
    check("rate limiter throttles beyond capacity", len(fast) <= 5, f"{len(fast)} fast")

def test_transfer_order():
    inner = FakeInner(); inner._devices = {"devices": [{"id": "devA", "is_active": True}]}
    c = make_client(inner)
    c._active_device_id()
    check("cache populated pre-transfer", c._dev_cache is not None)
    seen = {}; orig_tp = inner.transfer_playback
    def spy(device_id, force_play=False):
        seen["cache_at_call"] = c._dev_cache
        return orig_tp(device_id, force_play=force_play)
    inner.transfer_playback = spy
    c.transfer("devB")
    check("transfer nulls cache before transfer_playback", seen.get("cache_at_call") is None,
          repr(seen.get("cache_at_call")))


ALL = [test_check_saved_happy, test_check_saved_batching, test_check_saved_fallback,
       test_active_device_cache, test_active_device_cache_expiry,
       test_rate_limiter, test_transfer_order]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (spotify_client) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
