"""P9: application teardown must be safe.

Reproduces the failure where closing the app while a search worker is in flight
lets an unhandled RuntimeError escape the worker thread (the final
call_from_thread in _do_search was unwrapped) and emits a
"coroutine ... was never awaited" RuntimeWarning.

Fixed behaviour:
  * a `_closing` flag is set on teardown and all intervals are stopped
    idempotently (on_unmount may run more than once without error);
  * _do_search drops its UI updates once the app is closing;
  * no thread exception escapes and no "never awaited" warning is emitted.

Event/poll based synchronisation (threading.Event gate + thread join); no fixed
sleep as the sole mechanism.

Run standalone:  python tests/test_teardown.py
"""
import os, sys, io, gc, asyncio, threading, contextlib, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Inner:
    def __init__(self, entered, gate):
        self.entered = entered; self.gate = gate
    def search(self, q, type=None, limit=None):
        self.entered.set(); self.gate.wait(10)
        return {"tracks": {"items": [{"id": "t", "uri": "spotify:track:t", "name": "n",
                "type": "track", "artists": [{"name": "a", "id": "1"}],
                "album": {"name": "al"}, "duration_ms": 1000}]}}
    def current_user_saved_tracks_contains(self, ids): return [False] * len(ids)


class Fake:
    def __init__(self, entered, gate):
        self.inner = Inner(entered, gate)
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self.inner
    def fmt_duration(self, ms): return "0:00"
    def check_saved_tracks(self, ids): return [False] * len(ids)
    def _normalize_track_id(self, x): return x


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def _drive_close_during_search(entered, gate, holder):
    app = TApp(Fake(entered, gate))
    holder["app"] = app
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)
        th = threading.Thread(target=app._do_search, args=("q", "q", "track"), daemon=True)
        holder["thread"] = th
        th.start()
        for _ in range(300):
            await pilot.pause()
            if entered.is_set():
                break
    # app has now exited (on_unmount fired) while the search worker is blocked.


def test_close_during_search():
    entered = threading.Event(); gate = threading.Event()
    holder = {}
    escapes = []
    prev_hook = threading.excepthook
    threading.excepthook = lambda args: escapes.append((args.exc_type.__name__, args.thread.name))
    stderr_buf = io.StringIO()
    try:
        asyncio.run(_drive_close_during_search(entered, gate, holder))
        # Release the blocked worker AFTER shutdown; capture anything it emits.
        with contextlib.redirect_stderr(stderr_buf):
            gate.set()
            th = holder.get("thread")
            if th is not None:
                th.join(timeout=5)
            gc.collect()          # force finalize any dropped coroutine
    finally:
        threading.excepthook = prev_hook

    app = holder["app"]
    err_text = stderr_buf.getvalue()
    check("no unhandled exception escapes the search worker on close",
          escapes == [], f"escapes={escapes}")
    check("no 'coroutine was never awaited' warning on close",
          "never awaited" not in err_text, f"stderr~={err_text[:160]!r}")
    check("_closing flag is set after teardown", getattr(app, "_closing", False) is True,
          f"_closing={getattr(app, '_closing', None)}")


def test_teardown_idempotent():
    entered = threading.Event(); gate = threading.Event()
    holder = {}
    asyncio.run(_drive_close_during_search(entered, gate, holder))
    gate.set()
    th = holder.get("thread")
    if th is not None:
        th.join(timeout=5)
    app = holder["app"]
    # on_unmount already ran once (on exit); running it again must be safe.
    ok = True
    try:
        app.on_unmount()
        app.on_unmount()
    except Exception:
        ok = False
        traceback.print_exc()
    check("teardown can run twice without error (idempotent)", ok)
    check("all intervals stopped after teardown",
          all(getattr(app, a, None) is None for a in
              ("_now_sync_interval", "_now_tick_interval", "_now_interval",
               "_devices_interval", "_queue_interval", "_lyrics_interval")),
          "some interval handle still set")


ALL = [test_close_during_search, test_teardown_idempotent]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (teardown) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
