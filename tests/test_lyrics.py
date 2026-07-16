"""Deterministic tests for lyrics fetching (lrclib), with the network mocked.

Verifies the reliability fixes:
  * titles are cleaned of feat./remaster noise;
  * the exact /get endpoint (track+artist+album+duration) is tried first with the
    PRIMARY artist and cleaned title (the old /get?isrc= always 400'd);
  * on a /get miss it falls back to /search and picks the closest-duration,
    synced result instead of blindly taking the first.

Run standalone:  python tests/test_lyrics.py
"""
import os, sys, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spt_tui.app.lyrics_view as lv
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeResp:
    def __init__(self, status, payload): self.status_code = status; self._p = payload
    def json(self): return self._p

import requests as _real_requests

class FakeRequests:
    exceptions = _real_requests.exceptions   # code catches requests.exceptions.*
    def __init__(self, handler): self.calls = []; self.handler = handler
    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        return self.handler(url, dict(params or {}))


def make_app():
    # Construct without mounting; _fetch_synced_lyrics is pure when requests != None.
    return SptPy.__new__(SptPy)  # bypass __init__ (we only call lyric helpers)


def with_requests(handler):
    fake = FakeRequests(handler)
    lv.requests = fake
    return fake


def test_clean_title():
    app = make_app()
    cases = {
        "Save Your Tears (feat. Ariana Grande)": "Save Your Tears",
        "Blinding Lights - 2020 Remaster": "Blinding Lights",
        "Song - Live": "Song",
        "Plain Title": "Plain Title",
        "Track (with Someone)": "Track",
    }
    ok = all(app._clean_track_title(k) == v for k, v in cases.items())
    check("titles cleaned of feat/remaster/live", ok,
          str({k: app._clean_track_title(k) for k in cases}))


def test_exact_get_first_with_primary_artist():
    app = make_app()
    def handler(url, params):
        if url.endswith("/get"):
            return FakeResp(200, {"syncedLyrics": "[00:01.00]hello", "plainLyrics": "hello"})
        return FakeResp(200, [])
    fake = with_requests(handler)
    lines, status = app._fetch_synced_lyrics(title="Save Your Tears (feat. Ariana Grande)",
                                             artist="The Weeknd", album="After Hours", duration_ms=215000)
    get_calls = [c for c in fake.calls if c[0].endswith("/get")]
    p = get_calls[0][1] if get_calls else {}
    check("exact /get tried first", len(get_calls) >= 1)
    check("query uses cleaned title", p.get("track_name") == "Save Your Tears", f"p={p}")
    check("query uses primary artist + album + duration",
          p.get("artist_name") == "The Weeknd" and p.get("album_name") == "After Hours" and p.get("duration") == 215,
          f"p={p}")
    check("exact /get synced lyrics parsed", lines == [(1000, "hello")] and status == "found", f"lines={lines} status={status}")
    check("User-Agent header sent", "User-Agent" in (get_calls[0][2] if get_calls else {}))


def test_search_fallback_picks_closest_duration():
    app = make_app()
    def handler(url, params):
        if url.endswith("/get"):
            return FakeResp(404, {})
        # /search: first result is a wrong-length version, second matches duration
        return FakeResp(200, [
            {"duration": 300, "syncedLyrics": "[00:01.00]wrong"},
            {"duration": 191, "syncedLyrics": "[00:02.00]right"},
        ])
    fake = with_requests(handler)
    lines, status = app._fetch_synced_lyrics(title="Save Your Tears", artist="The Weeknd", duration_ms=191000)
    search_calls = [c for c in fake.calls if c[0].endswith("/search")]
    check("falls back to /search after /get miss", len(search_calls) >= 1)
    check("picks closest-duration synced result (not the first)",
          lines == [(2000, "right")] and status == "found", f"lines={lines} status={status}")


def test_notfound_status_when_lrclib_answers_empty():
    app = make_app()
    fake = with_requests(lambda url, params: FakeResp(404, {}) if url.endswith("/get") else FakeResp(200, []))
    lines, status = app._fetch_synced_lyrics(title="Nonexistent", artist="Nobody", duration_ms=100000)
    check("LRCLIB answered but empty -> status 'notfound'", lines == [] and status == "notfound",
          f"lines={lines} status={status}")


def test_error_status_on_network_failure():
    app = make_app()
    def boom(url, params):
        raise _real_requests.exceptions.Timeout("slow")
    fake = with_requests(boom)
    lines, status = app._fetch_synced_lyrics(title="Song", artist="Artist", duration_ms=100000)
    check("timeout/connection failure -> status 'error' (never cached as absence)",
          lines == [] and status == "error", f"lines={lines} status={status}")


ALL = [test_clean_title, test_exact_get_first_with_primary_artist,
       test_search_fallback_picks_closest_duration, test_notfound_status_when_lrclib_answers_empty,
       test_error_status_on_network_failure]

def main():
    orig = lv.requests
    try:
        for fn in ALL:
            try:
                fn()
            except Exception:
                check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    finally:
        lv.requests = orig
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (lyrics) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
