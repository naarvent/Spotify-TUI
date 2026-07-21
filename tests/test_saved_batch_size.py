"""The liked-songs lookup must not oversize its batches.

`check_saved_tracks` sent 50 ids per call, but the endpoint spotipy uses
(`me/library/contains`) rejects anything over 40 with
`400 Too many uris requested`. So *every* batch failed and fell through to the
per-id retry: 51 requests instead of 1. On a 596-track playlist that was ~612
requests and 78 seconds of hearts trickling in, plus a logged traceback (with
the full URL) per batch, which is what grew the log file to megabytes.

The per-id fallback stays — one genuinely bad id (a local file, an episode)
must not zero a whole batch — but it should be the exception, not the norm.

Run standalone:  python tests/test_saved_batch_size.py
"""
import os, sys, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.spotify_client import SpotifyClient

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


# Measured against the live API: 40 ids succeed, 41 return 400.
API_MAX = 40


class Inner:
    """Rejects an oversized batch exactly like the real endpoint."""
    def __init__(self, limit=API_MAX, bad_ids=()):
        self.calls = []
        self.limit = limit
        self.bad_ids = set(bad_ids)
    def current_user_saved_tracks_contains(self, ids):
        self.calls.append(list(ids))
        if len(ids) > self.limit:
            raise RuntimeError("http status: 400 - Too many uris requested")
        if any(i in self.bad_ids for i in ids):
            raise RuntimeError("http status: 400 - invalid id")
        return [True] * len(ids)


def client(inner):
    c = SpotifyClient.__new__(SpotifyClient)
    c.ensure = lambda: inner
    return c


def test_batches_stay_within_the_api_limit():
    inner = Inner()
    ids = [f"t{i}" for i in range(596)]          # a real playlist's size
    out = client(inner).check_saved_tracks(ids)
    biggest = max(len(c) for c in inner.calls)
    check("no call exceeds the API limit", biggest <= API_MAX, f"biggest={biggest}")
    check("one value per id", len(out) == 596 and all(out), f"len={len(out)}")
    check("no batch was rejected, so no per-id retry",
          len(inner.calls) == 15, f"calls={len(inner.calls)} (expected 15)")


def test_the_old_size_would_have_failed_every_batch():
    """Pins why this matters: at 50 the endpoint refuses every single call."""
    inner = Inner()
    ids = [f"t{i}" for i in range(100)]
    rejected = 0
    for i in range(0, len(ids), 50):
        try:
            inner.current_user_saved_tracks_contains(ids[i:i + 50])
        except Exception:
            rejected += 1
    check("batches of 50 are rejected outright", rejected == 2, f"rejected={rejected}")


def test_a_bad_id_still_falls_back_per_id():
    inner = Inner(bad_ids={"t7"})
    ids = [f"t{i}" for i in range(40)]
    out = client(inner).check_saved_tracks(ids)
    check("the result is still aligned to the ids", len(out) == 40, f"len={len(out)}")
    check("the bad id reports False, not an exception", out[7] is False, f"out[7]={out[7]}")
    check("the good ids still report True", out[0] and out[39])
    check("it retried one id at a time", len(inner.calls) == 41, f"calls={len(inner.calls)}")


def test_on_batch_offsets_follow_the_new_size():
    inner = Inner()
    seen = []
    ids = [f"t{i}" for i in range(90)]
    client(inner).check_saved_tracks(ids, on_batch=lambda s, v: seen.append((s, len(v))))
    check("batch offsets line up with the ids", seen == [(0, 40), (40, 40), (80, 10)],
          f"seen={seen}")


def test_empty_input_makes_no_calls():
    inner = Inner()
    out = client(inner).check_saved_tracks([])
    check("no ids, no requests", out == [] and inner.calls == [], f"calls={inner.calls}")


ALL = [test_batches_stay_within_the_api_limit, test_the_old_size_would_have_failed_every_batch,
       test_a_bad_id_still_falls_back_per_id, test_on_batch_offsets_follow_the_new_size,
       test_empty_input_makes_no_calls]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (saved batch size) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
