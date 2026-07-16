"""P8-E: background workers must never paint widgets directly from the worker
thread when call_from_thread fails.

Reproduces the unsafe fallback `try: call_from_thread(cb) except Exception: cb()`:
when call_from_thread raises, the old code ran `cb()` on the worker thread,
mutating widgets cross-thread. The fix drops the update (logging instead) and
never touches widgets from the worker.

Deterministic: call_from_thread is replaced by a stub that always raises, so the
fallback path is taken every time (no timing).

Run standalone:  python tests/test_worker_error_handling.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x
    def check_saved_tracks(self, ids): return [False] * len(ids)
    def save_tracks(self, ids): return True
    def remove_tracks(self, ids): return True

class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def static_text(w):
    return str(getattr(w, "_Static__content", "") or "")

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


def test_favorite_worker_does_not_paint_cross_thread():
    escapes = []
    prev = threading.excepthook
    threading.excepthook = lambda a: escapes.append((a.exc_type.__name__, a.thread.name))

    async def body():
        app = TApp(Fake())
        async with app.run_test() as pilot:
            await pilot.pause(); pause_intervals(app)
            app.right_panel.update("SENTINEL")
            # Force every call_from_thread to fail so the fallback path runs.
            def boom(*a, **k):
                raise RuntimeError("call_from_thread forced failure")
            app.call_from_thread = boom
            app._closing = False               # normal-operation branch

            save_seen = {"v": False}
            orig_save = app.spotify.save_tracks
            def save_spy(ids):
                save_seen["v"] = True
                return orig_save(ids)
            app.spotify.save_tracks = save_spy

            app._toggle_track_favorite("X", None, None)
            # Wait until the worker has run its mutation (save) and hit the fallback.
            await poll(lambda: save_seen["v"], timeout=4.0)
            for _ in range(20):
                await pilot.pause()
            return static_text(app.right_panel)

    try:
        panel = asyncio.run(body())
    finally:
        threading.excepthook = prev

    check("favorite worker does NOT paint the panel from the worker thread "
          "(no 'Added to Liked Songs' when call_from_thread fails)",
          "Added to Liked Songs" not in panel, f"panel={panel!r}")
    check("favorite worker keeps the pre-existing panel content (update dropped)",
          "SENTINEL" in panel, f"panel={panel!r}")
    check("no unhandled exception escapes the favorite worker",
          escapes == [], f"escapes={escapes}")


ALL = [test_favorite_worker_does_not_paint_cross_thread]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (worker error handling) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
