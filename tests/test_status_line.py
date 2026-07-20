"""Transient feedback reaches the user.

Messages used to go out as `right_panel.update(...)`. Whenever a table is on
screen — which is exactly when most of them fire — the table stays mounted as
the panel's child and covers the panel's own content, so the message was never
seen: pressing a key with nothing selected looked like the key was broken.

They now go to a docked status line on an overlay layer, which sits above the
table and clears itself after a few seconds.

Run standalone:  python tests/test_status_line.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from textual.widgets import Static

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

class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def rows_n(n):
    return [{"id": f"i{i}", "uri": f"spotify:track:i{i}", "title": f"track number {i}",
             "artist": "artist", "album": "album", "dur": "3:21", "added": "2024-01-01"}
            for i in range(n)]

def screen_text(app):
    """Everything actually composited, overlays included."""
    return "\n".join("".join(seg.text for seg in strip)
                     for strip in app.screen._compositor.render_strips(app.screen.size))

def status(app):
    try:
        return app.query_one("#status_line", Static)
    except Exception:
        return None

async def settle(seconds=0.3):
    loop = asyncio.get_event_loop(); end = loop.time() + seconds
    while loop.time() < end:
        await asyncio.sleep(0.02)


async def test_message_is_visible_over_a_table():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._render_tracks_table("[b]P[/b]", rows_n(30), [False] * 30, profile="playlist")
        for _ in range(4):
            await pilot.pause()
        check("the table is on screen", "track number 0" in screen_text(app))
        app._notify("ZZZ_UNIQUE_MESSAGE_ZZZ")
        for _ in range(4):
            await pilot.pause()
        text = screen_text(app)
        check("the message is visible with a table mounted",
              "ZZZ_UNIQUE_MESSAGE_ZZZ" in text)
        check("the table is still visible underneath", "track number 0" in text)


async def test_message_clears_itself():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._notify("TEMPORARY_MSG", seconds=0.3)
        for _ in range(4):
            await pilot.pause()
        check("the message shows", "TEMPORARY_MSG" in screen_text(app))
        await settle(0.7)
        for _ in range(4):
            await pilot.pause()
        check("the message is gone once its time is up",
              "TEMPORARY_MSG" not in screen_text(app), "still on screen")
        s = status(app)
        check("the status line hides itself again", s is not None and not s.display,
              f"display={getattr(s, 'display', None)}")


async def test_a_newer_message_replaces_the_previous_one():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._notify("FIRST_MSG", seconds=5)
        for _ in range(3):
            await pilot.pause()
        app._notify("SECOND_MSG", seconds=5)
        for _ in range(3):
            await pilot.pause()
        text = screen_text(app)
        check("only the newer message is shown",
              "SECOND_MSG" in text and "FIRST_MSG" not in text, "both on screen")


async def test_the_first_message_timer_does_not_clear_the_second():
    """A stale timer must not wipe a message posted after it."""
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._notify("SHORT_MSG", seconds=0.3)
        for _ in range(2):
            await pilot.pause()
        app._notify("LONG_MSG", seconds=5)
        await settle(0.8)                      # the first timer would fire here
        for _ in range(3):
            await pilot.pause()
        check("the newer message survives the older timer",
              "LONG_MSG" in screen_text(app), "the stale timer cleared it")


async def test_timer_is_dropped_on_teardown():
    """`_stop_all_intervals` is what on_unmount calls; calling on_unmount itself
    mid-session wedges the test harness."""
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._notify("BYE", seconds=30)
        for _ in range(3):
            await pilot.pause()
        check("a pending timer is tracked", getattr(app, "_status_timer", None) is not None)
        app._stop_all_intervals()
        check("teardown drops the status timer", getattr(app, "_status_timer", None) is None,
              "timer still set after teardown")
        app._stop_all_intervals()          # documented as idempotent
        check("dropping it twice is safe", getattr(app, "_status_timer", None) is None)


async def test_notify_is_a_no_op_while_closing():
    """Workers finishing after teardown must not paint into a dying app.

    Checked without running the app: flipping `_closing` on a live one wedges
    Textual's test harness on shutdown.
    """
    class Recorder:
        def __init__(self): self.calls = []
        def update(self, text): self.calls.append(text)
        def add_class(self, *a): self.calls.append(("add_class",) + a)
        def remove_class(self, *a): self.calls.append(("remove_class",) + a)
        def set_class(self, *a): self.calls.append(("set_class",) + a)

    app = TApp(Fake())
    line = Recorder()
    app.status_line = line
    app._closing = True
    app._notify("SHOULD_NOT_APPEAR")
    check("the status line is not touched once the app is closing", line.calls == [],
          f"calls={line.calls}")
    check("and no timer is left behind", getattr(app, "_status_timer", None) is None)


async def test_notify_is_safe_before_the_widget_exists():
    """Called from a worker during startup or teardown it must not raise."""
    app = TApp(Fake())
    try:
        app._notify("too early")
        check("notifying before mount does not raise", True)
    except Exception as e:
        check("notifying before mount does not raise", False, repr(e))


ALL = [test_message_is_visible_over_a_table, test_message_clears_itself,
       test_a_newer_message_replaces_the_previous_one,
       test_the_first_message_timer_does_not_clear_the_second,
       test_timer_is_dropped_on_teardown, test_notify_is_a_no_op_while_closing,
       test_notify_is_safe_before_the_widget_exists]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (status line) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
