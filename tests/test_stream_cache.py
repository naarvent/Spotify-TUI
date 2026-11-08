"""StreamResolver (spt_tui/stream_cache.py): prefetch window, cache expiry, failures, limits.
Run standalone:  python tests/test_stream_cache.py
"""
import os, sys, time, threading, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spt_tui import stream_cache as sc
from spt_tui.stream_cache import StreamResolver

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

def wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred(): return True
        time.sleep(0.01)
    return pred()

def fake(calls, delay=0.0, fail=(), gate=None):
    def ext(url):
        calls.append(url)
        if gate is not None: gate.wait(5)
        if delay: time.sleep(delay)
        if url in fail: raise RuntimeError("gone")
        return f"https://rr.googlevideo.com/x?id={url[-3:]}&expire={int(time.time()) + 21600}"
    return ext


def test_basic_prefetch_and_get():
    calls = []
    r = StreamResolver(fake(calls))
    check("nothing cached at first", r.get("w:aaa") is None)
    r.prefetch(["w:aaa", "w:bbb", "w:aaa"])
    check("both resolve (duplicates once)", wait_for(lambda: r.get("w:aaa") and r.get("w:bbb")))
    check("each resolved exactly once", sorted(calls) == ["w:aaa", "w:bbb"], str(calls))
    r.prefetch(["w:aaa", "w:bbb"])
    time.sleep(0.1)
    check("already cached -> not resolved again", len(calls) == 2)
    r.close()


def test_wish_list_replaced():
    """Changing the queue must drop the work that is no longer wanted."""
    calls = []; gate = threading.Event()
    r = StreamResolver(fake(calls, gate=gate), workers=1)
    r.prefetch(["w:a01", "w:a02", "w:a03", "w:a04"])
    wait_for(lambda: len(calls) == 1)              # the single worker is busy with a01
    r.prefetch(["w:b01", "w:b02"])                  # user changed playlist
    gate.set()
    wait_for(lambda: r.get("w:b02") is not None)
    check("old pending URLs were dropped", "w:a03" not in calls and "w:a04" not in calls, str(calls))
    check("the in-flight one finished and is cached", r.get("w:a01") is not None)
    check("the new list was resolved, in order", calls[1:] == ["w:b01", "w:b02"], str(calls))
    r.cancel_pending(); r.prefetch([]); r.close()


def test_failure_not_retried_in_a_loop():
    calls = []; t = [1000.0]
    r = StreamResolver(fake(calls, fail=("w:bad",)), now=lambda: t[0])
    r.prefetch(["w:bad"])
    wait_for(lambda: r.failures == 1)
    check("failed -> no direct URL", r.get("w:bad") is None)
    r.prefetch(["w:bad"]); time.sleep(0.1)
    check("not retried within the negative TTL", calls == ["w:bad"], str(calls))
    t[0] += sc.NEGATIVE_TTL + 1
    r.prefetch(["w:bad"])
    check("retried after the TTL", wait_for(lambda: len(calls) == 2), str(calls))
    r.close()


def test_expiry_and_invalidate():
    t = [1000.0]; calls = []
    def ext(url):
        calls.append(url); return f"https://x/y?expire={int(t[0]) + 3600}"
    r = StreamResolver(ext, now=lambda: t[0])
    r.prefetch(["w:aaa"]); wait_for(lambda: r.get("w:aaa"))
    check("fresh URL served", r.get("w:aaa") is not None)
    t[0] += 3600 - sc.EXPIRY_MARGIN + 1
    check("dropped before its own expiry (margin)", r.get("w:aaa") is None)
    r.prefetch(["w:aaa"]); wait_for(lambda: r.get("w:aaa"))
    r.invalidate("w:aaa")
    check("invalidate forgets it", r.get("w:aaa") is None)
    r.invalidate("w:aaa", failed=True)
    r.prefetch(["w:aaa"]); time.sleep(0.1)
    check("invalidate(failed=True) blocks re-resolving for a while", r.get("w:aaa") is None and len(calls) == 2)
    r.close()


def test_max_age_without_expire_param():
    t = [0.0]
    r = StreamResolver(lambda u: "https://no-expire-param/x", now=lambda: t[0])
    r.prefetch(["w:aaa"]); wait_for(lambda: r.get("w:aaa"))
    t[0] = sc.MAX_AGE - 1
    ok1 = r.get("w:aaa") is not None
    t[0] = sc.MAX_AGE + 1
    check("kept up to MAX_AGE, not beyond", ok1 and r.get("w:aaa") is None)
    r.close()


def test_limits():
    calls = []; cur = {"n": 0, "max": 0}; lock = threading.Lock()
    def ext(url):
        with lock:
            cur["n"] += 1; cur["max"] = max(cur["max"], cur["n"])
        time.sleep(0.05)
        with lock: cur["n"] -= 1
        return f"https://x/{url}"
    r = StreamResolver(ext, workers=2, capacity=3)
    r.prefetch([f"w:{i:03d}" for i in range(8)])
    wait_for(lambda: r.resolved == 8, 5)
    check("never more than 2 resolutions at once", cur["max"] <= 2, str(cur))
    check("cache keeps at most `capacity` URLs", len(r._cache) == 3, str(len(r._cache)))
    check("…the most recent ones", r.get("w:007") is not None and r.get("w:000") is None)
    r.close()


def test_never_raises_and_close():
    r = StreamResolver(lambda u: (_ for _ in ()).throw(ValueError("boom")))
    r.prefetch(["w:aaa", None, "", "w:bbb"])
    wait_for(lambda: r.failures == 2)
    check("extractor errors are swallowed", r.failures == 2 and r.get("w:aaa") is None)
    check("resolve_now returns None on failure", r.resolve_now("w:zzz") is None)
    r.close()
    r.prefetch(["w:ccc"]); time.sleep(0.05)
    check("after close, prefetch does nothing", r.failures == 3)
    r2 = StreamResolver(lambda u: "https://x/" + u)
    check("resolve_now blocks and caches", r2.resolve_now("w:aaa") == "https://x/w:aaa" and r2.get("w:aaa"))
    r2.close()


def test_expire_regex():
    m = sc._EXPIRE_RE.search("https://r.googlevideo.com/videoplayback?expire=1700000000&ip=1.2.3.4")
    m2 = sc._EXPIRE_RE.search("https://r.googlevideo.com/videoplayback/expire/1700000001/ei/x")
    check("expire= parsed (query and path forms)", m and m.group(1) == "1700000000" and m2 and m2.group(1) == "1700000001")


ALL = [test_basic_prefetch_and_get, test_wish_list_replaced, test_failure_not_retried_in_a_loop,
       test_expiry_and_invalidate, test_max_age_without_expire_param, test_limits,
       test_never_raises_and_close, test_expire_regex]

if __name__ == "__main__":
    for fn in ALL:
        try: fn()
        except Exception: check(fn.__name__ + " (crashed)", False, traceback.format_exc(limit=4))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    sys.exit(0 if ok == len(RESULTS) else 1)
