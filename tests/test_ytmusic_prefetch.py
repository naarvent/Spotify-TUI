"""YouTube Music: direct stream URLs resolved ahead (StreamResolver) and swapped into mpv's queue.
Fake mpv playlist (ids, current, insert-at, playlist-remove, shuffle/unshuffle with mpv's real
quirk: entries inserted after a shuffle sort first on unshuffle) and a fake extractor. No network.

Run standalone:  python tests/test_ytmusic_prefetch.py
"""
import os, sys, time, random, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.providers.ytmusic import YTMusicProvider, WATCH_URL
from spt_tui.stream_cache import StreamResolver
from spt_tui.local_library import LocalLibrary

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

W = lambda vid: WATCH_URL.format(vid)
D = lambda vid: f"https://rr.googlevideo.com/videoplayback?id={vid}&expire={int(time.time()) + 21600}"
def vid_of(url):
    return url.split("v=")[1] if "watch?v=" in url else url.split("id=")[1].split("&")[0]


class FakeMpv:
    """Playlist entries {"filename", "id", "orig"}; pos = current index."""
    def __init__(self):
        self.pl = []; self.pos = -1; self.nid = 0; self.paused = False
        self.loop_pl = False; self.loop_file = False; self.errors = []; self.running = False
        self.log = []; self.fail_ipc = False
    def _new(self, url):
        self.nid += 1
        return {"filename": url, "id": self.nid, "orig": -1}
    def _ipc(self):
        if self.fail_ipc: raise RuntimeError("ipc broken")
    # MpvPlayer API ---------------------------------------------------- #
    def unavailable_reason(self): return None
    def is_running(self): return self.running
    def load(self, urls, start_index=0):
        self.running = True
        self.pl = [self._new(u) for u in urls]
        for i, e in enumerate(self.pl): e["orig"] = i
        self.pos = start_index; self.paused = False
    def append(self, url):
        e = self._new(url); e["orig"] = len(self.pl); self.pl.append(e)
        if self.pos < 0: self.pos = len(self.pl) - 1
    def get(self, prop, default=None):
        self._ipc()
        if prop == "path": return self.pl[self.pos]["filename"] if self.pos >= 0 else None
        return default
    def state(self):
        if not self.running: return {"running": False, "idle": True, "paused": True, "index": -1}
        return {"running": True, "idle": self.pos < 0, "paused": self.paused, "position": 1.0,
                "duration": 100.0, "index": self.pos, "volume": 70}
    def playlist_entries(self):
        self._ipc(); self.log.append("entries")
        return [{"filename": e["filename"], "id": e["id"], **({"current": True} if i == self.pos else {})}
                for i, e in enumerate(self.pl)]
    def playlist_urls(self): return [e["filename"] for e in self.pl]
    def replace_entry(self, index, url, expect):
        self._ipc()
        if not (0 <= index < len(self.pl)) or self.pl[index]["filename"] != expect or index == self.pos:
            return False
        self.log.append(("replace", index))
        e = self._new(url); e["orig"] = -1          # mpv: inserted entries have no original index
        self.pl.insert(index, e)
        if index <= self.pos: self.pos += 1
        self.pl.pop(index + 1)
        if index + 1 <= self.pos: self.pos -= 1
        return True
    def reorder(self, ids):
        cur = self.pl[self.pos] if self.pos >= 0 else None
        by = {e["id"]: e for e in self.pl}
        self.pl = [by[i] for i in ids]
        if cur is not None: self.pos = self.pl.index(cur)
        return True
    def play_index(self, i): self.pos = i; self.paused = False
    def next(self):
        if self.loop_file: return
        if self.pos + 1 < len(self.pl): self.pos += 1
        elif self.loop_pl: self.pos = 0
        else: self.pos = -1
    def prev(self): self.pos = max(0, self.pos - 1)
    def set_loop(self, state):
        self.loop_file = state == "track"; self.loop_pl = state == "context"
    def command(self, *args):
        cur = self.pl[self.pos] if self.pos >= 0 else None
        if args[0] == "playlist-shuffle":
            for i, e in enumerate(self.pl): e["orig"] = i
            rest = [e for e in self.pl if e is not cur]
            random.Random(7).shuffle(rest)
            self.pl = ([cur] if cur else []) + rest
        elif args[0] == "playlist-unshuffle":
            self.pl.sort(key=lambda e: e["orig"])
        if cur is not None: self.pos = self.pl.index(cur)
    def pause(self): self.paused = True
    def resume(self): self.paused = False
    def take_errors(self):
        e, self.errors = self.errors, []
        return e
    def fail_current(self, reason="loading failed"):
        """mpv could not open the current entry: end-file error and it moves on."""
        e = self.pl[self.pos]
        self.errors.append({"event": "end-file", "reason": "error", "playlist_entry_id": e["id"],
                            "file_error": reason})
        self.next()
    def close(self): self.running = False
    # helpers --------------------------------------------------------- #
    def vids(self): return [vid_of(e["filename"]) for e in self.pl]
    def direct_vids(self): return {vid_of(e["filename"]) for e in self.pl if "googlevideo" in e["filename"]}


class env:
    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="spt_ytpf_")
        self.old = (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG)
        config.CACHE_DIR = self.d
        config.CONFIG_PATH = os.path.join(self.d, "spt_config.json")
        config.LOCAL_CFG = {}
        return self
    def __exit__(self, *a):
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG = self.old
        shutil.rmtree(self.d, ignore_errors=True)


def make(n=6, extractor=None, fail=()):
    calls = []
    def ext(url):
        calls.append(url)
        if url in fail or vid_of(url) in fail: raise RuntimeError("gone")
        return D(vid_of(url))
    r = StreamResolver(extractor or ext)
    mpv = FakeMpv()
    p = YTMusicProvider(client=object(), player=mpv, which=lambda n: "/usr/bin/" + n,
                        library=LocalLibrary("ytmusic"), stream_resolver=r)
    tracks = [{"id": f"v{i}", "uri": f"ytmusic:track:v{i}", "name": f"T{i}", "duration_ms": 1000}
              for i in range(n)]
    for t in tracks: p._remember(t)
    return p, mpv, r, calls, tracks

def settle(p, mpv, want, timeout=3.0):
    """Poll get_playback (like the UI) until `want` vids are direct in mpv's playlist."""
    return wait_for(lambda: (p.get_playback(), set(want) <= mpv.direct_vids())[1], timeout)

def play(p, tracks, start=0):
    p.start_playback(uris=[t["uri"] for t in tracks], offset={"position": start})


# --------------------------------------------------------------------------- #
def test_window_and_substitution():
    with env():
        p, mpv, r, calls, tracks = make()
        play(p, tracks)
        ok = settle(p, mpv, {"v1", "v2", "v3"})
        check("next 3 entries swapped to direct URLs", ok, str(mpv.vids()) + str(mpv.direct_vids()))
        check("current entry never touched", "googlevideo" not in mpv.pl[0]["filename"])
        check("v4/v5 outside the window", not ({"v4", "v5"} & mpv.direct_vids()))
        check("queue listing still maps direct URLs to tracks",
              [t["id"] for t in p.queue()["queue"]] == ["v1", "v2", "v3", "v4", "v5"], str(p.queue()))
        n_entries = mpv.log.count("entries")
        for _ in range(20): p.get_playback()
        check("polls with an unchanged window cause no extra IPC", mpv.log.count("entries") == n_entries,
              f"{n_entries} -> {mpv.log.count('entries')}")
        p.next()
        pb = p.get_playback()
        check("current track resolved from a direct path", pb.get("item", {}).get("id") == "v1", str(pb)[:200])
        check("window moved with next()", settle(p, mpv, {"v2", "v3", "v4"}))
        p.close()


def test_shuffle():
    with env():
        p, mpv, r, calls, tracks = make(8)
        play(p, tracks)
        settle(p, mpv, {"v1", "v2", "v3"})
        p.shuffle(True)
        check("swapped entries travel with their track (all 8 still there once)",
              sorted(mpv.vids()) == sorted(t["id"] for t in tracks), str(mpv.vids()))
        nxt = mpv.vids()[1:4]
        check("window follows the shuffled order", settle(p, mpv, set(nxt)), f"{nxt} {mpv.direct_vids()}")
        before = list(r._wanted)
        check("pending list only has the new window", all(vid_of(u) in nxt for u in before), str(before))
        p.shuffle(False)
        check("unshuffle restores the original order despite swapped entries",
              mpv.vids() == [t["id"] for t in tracks], str(mpv.vids()))
        check("current still playing v0", mpv.vids()[mpv.pos] == "v0")
        check("window recalculated after unshuffle", settle(p, mpv, {"v1", "v2", "v3"}))
        p.close()


def test_start_playback_new_queue():
    with env():
        p, mpv, r, calls, tracks = make(8)
        play(p, tracks[:4])
        settle(p, mpv, {"v1", "v2", "v3"})
        cancelled = []
        orig = r.cancel_pending
        r.cancel_pending = lambda: (cancelled.append(1), orig())[1]
        play(p, tracks[4:])
        check("start_playback cancels pending", cancelled == [1])
        check("old queue's direct maps cleared", all("v1" not in d and "v2" not in d for d in p._direct_to_watch),
              str(p._direct_to_watch))
        check("new window prefetched", settle(p, mpv, {"v5", "v6", "v7"}))
        # Replaying the first queue: cached direct URLs are reused straight away (no re-resolve).
        n = len(calls)
        play(p, tracks[:4])
        check("cached direct URLs used directly in the new queue",
              mpv.direct_vids() == {"v1", "v2", "v3"} and len(calls) == n, f"{mpv.direct_vids()} {calls[n:]}")
        check("current track still known", p.get_playback().get("item", {}).get("id") == "v0")
        p.close()


def test_add_next_prev_repeat_fast_skips():
    with env():
        p, mpv, r, calls, tracks = make(8)
        play(p, tracks[:2])
        settle(p, mpv, {"v1"})
        p.add_to_queue(tracks[2]["uri"])
        check("add_to_queue enters a short window", settle(p, mpv, {"v2"}), str(mpv.vids()))
        p.prev()
        check("prev() at start keeps window", settle(p, mpv, {"v1", "v2"}))
        # repeat track: nothing ahead
        p.repeat("track")
        p.get_playback()
        check("repeat=track: empty window", r._wanted == [] and p._window_pending == [])
        p.repeat("context")
        p.next(); p.next()       # now at v2 (last)
        check("repeat=context: wraps to the first", settle(p, mpv, {"v0"}) or "v0" in mpv.direct_vids()
              or mpv.pos == 0, f"pos={mpv.pos} {mpv.vids()}")
        p.repeat("off")
        # fast skips on a longer queue with a slow extractor
        p2, mpv2, r2, calls2, tr2 = make(12, extractor=lambda u: (time.sleep(0.15), D(vid_of(u)))[1])
        play(p2, tr2)
        for _ in range(6):
            p2.next(); p2.get_playback()
        time.sleep(0.05)
        wanted = [vid_of(u) for u in r2._wanted]
        check("fast skips: pending only near the current position (v7..v9)",
              all(w in ("v7", "v8", "v9") for w in wanted), f"pos={mpv2.pos} wanted={wanted}")
        check("fast skips: window reached after settling", settle(p2, mpv2, {"v7", "v8", "v9"}))
        p.close(); p2.close()


def test_next_swaps_cached_entry():
    with env():
        p, mpv, r, calls, tracks = make(4)
        r._resolve(W("v1"))            # already cached, but the window has not run yet
        mpv.load([W(t["id"]) for t in tracks], 0)
        for t in tracks: p._url_for(t)
        p.next()
        check("next() swaps the cached direct URL in before jumping",
              "googlevideo" in mpv.pl[1]["filename"] and mpv.pos == 1, str(mpv.pl))
        check("and the track is known through it", p.get_playback().get("item", {}).get("id") == "v1")
        p.close()


def test_direct_failure_is_not_a_track_failure():
    with env():
        p, mpv, r, calls, tracks = make(5)
        play(p, tracks)
        settle(p, mpv, {"v1", "v2", "v3"})
        marks = []; p.on_track_error = lambda t, why: marks.append(t.get("id"))
        p.next()                       # now on v1 (direct)
        mpv.fail_current("403")        # the direct URL expired -> mpv moved to v2
        pb = p.get_playback()
        check("no on_track_error / unavailable mark", marks == [] and not p.library.unavailable_recent("ytmusic:track:v1"))
        check("no fail streak, no 'Skipped' notice", p._fail_streak == [] and
              not any("Skipped" in n for n in p.take_notices()))
        check("watch URL back in the same place", mpv.pl[1]["filename"] == W("v1"), str(mpv.pl))
        check("and v1 plays again", mpv.pos == 1 and pb.get("item", {}).get("id") in ("v1", None), f"pos={mpv.pos}")
        check("direct URL invalidated (failed -> not re-resolved now)", r.get(W("v1")) is None
              and r._failed.get(W("v1"), 0) > time.time())
        # A failure on a watch URL still counts as before.
        mpv.fail_current("Video unavailable")
        p.get_playback()
        check("failure on a watch URL still reaches on_track_error", marks == ["v1"], str(marks))
        p.close()


def test_unavailable_not_prefetched():
    with env():
        p, mpv, r, calls, tracks = make(5)
        p.library.mark_unavailable("ytmusic:track:v2", "gone")
        play(p, tracks[:1] + tracks[1:])   # v2 is filtered by start_playback
        check("start_playback filtered the marked track", "v2" not in mpv.vids(), str(mpv.vids()))
        settle(p, mpv, {"v1", "v3", "v4"})
        check("marked track never resolved", W("v2") not in calls, str(calls))
        # a single explicit marked track is played (kept) but not prefetched when queued
        p.add_to_queue("ytmusic:track:v2")
        p.get_playback(); time.sleep(0.1)
        check("queued marked track not prefetched", W("v2") not in calls)
        p.close()


def test_edges_and_close():
    with env():
        p, mpv, r, calls, tracks = make(3)
        p.get_playback()
        check("mpv not running: no work", calls == [] and mpv.log == [])
        play(p, tracks[:1])
        p.get_playback(); time.sleep(0.1)
        check("queue of 1: nothing to prefetch", calls == [])
        p.next(); pb = p.get_playback()
        check("end of queue: idle, empty playback", pb == {} and calls == [])
        play(p, tracks)
        mpv.paused = True; mpv.log.clear()
        p._window_key = None
        p.get_playback()
        check("paused: no new prefetch", mpv.log == [])
        mpv.paused = False
        p.close()
        check("close() closes the resolver", r._closed and p._resolver is None)
        p.get_playback()          # after close: must not raise
        check("after close nothing breaks", True)
        # hot provider switch: provider_switch calls close() on the old provider
        q = YTMusicProvider(client=object(), player=FakeMpv(), which=lambda n: "/x/" + n)
        check("default with a fake player: no resolver (exactly as before)", q._resolver is None)
        q.close()


def test_errors_are_swallowed():
    with env():
        p, mpv, r, calls, tracks = make(5)
        play(p, tracks)
        def boom(*a, **k): raise RuntimeError("resolver down")
        r.prefetch = boom; r.get = boom
        p._window_key = None
        pb = p.get_playback()
        check("resolver errors do not reach the UI", pb.get("item", {}).get("id") == "v0")
        p.next()
        check("next() still works with a broken resolver", mpv.pos == 1)
        play(p, tracks)
        check("start_playback works with a broken resolver (watch URLs)",
              all("watch?v=" in e["filename"] for e in mpv.pl))
        mpv.fail_ipc = True
        p._window_key = None
        try:
            p._prefetch_tick(); ok = True
        except Exception:
            ok = False
        check("IPC errors during prefetch are swallowed", ok)
        mpv.fail_ipc = False
        p.close()


ALL = [test_window_and_substitution, test_shuffle, test_start_playback_new_queue,
       test_add_next_prev_repeat_fast_skips, test_next_swaps_cached_entry,
       test_direct_failure_is_not_a_track_failure, test_unavailable_not_prefetched,
       test_edges_and_close, test_errors_are_swallowed]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (ytmusic_prefetch) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name, "::", (detail or "").strip().splitlines()[-1] if detail else "")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
