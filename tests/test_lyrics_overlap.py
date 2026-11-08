"""Opening the lyrics view in the same event-loop turn as another view (or twice)
must not crash with DuplicateIds('lyrics_box'). Offline: fake client.

Run standalone:  python tests/test_lyrics_overlap.py
"""
import os, sys, asyncio, tempfile, itertools, traceback
os.environ.setdefault("SPT_SPICY_LYRICS", "off")   # never reach the real API from a suite

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_lov_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spt_tui import config
import test_queue_repeat as T

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

PL = {"type": "playlist", "id": "pl1", "uri": "spotify:playlist:pl1", "name": "PL",
      "owner": {"display_name": "me"}, "tracks": {"total": 0}}

OTHERS = {
    "queue": lambda a: a.action_open_queue(),
    "devices": lambda a: a.action_manage_devices(),
    "playlist": lambda a: a._open_playlist_table(dict(PL)),
    "settings": lambda a: a.action_prompt_settings(),
}

async def run(label, act):
    config.LOCAL_CFG = {}
    app = T.TApp(T.Fake()); err = None
    try:
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause(); T.pause_intervals(app)
            act(app)
            for _ in range(8): await pilot.pause()
            await asyncio.sleep(0.3)
    except Exception:
        err = traceback.format_exc().splitlines()[-1][:160]
    if app.errors and not err: err = app.errors[0][:160]
    check(label, err is None, err or "")

async def main():
    def lyrics_twice(a):
        a._open_lyrics_view(); a._open_lyrics_view()
    await run("lyrics opened twice in one turn", lyrics_twice)
    for name, fn in OTHERS.items():
        def lo(a, fn=fn):
            for _ in range(3):
                a._open_lyrics_view(); fn(a)
        def ol(a, fn=fn):
            for _ in range(3):
                fn(a); a._open_lyrics_view()
        await run(f"lyrics -> {name} x3", lo)
        await run(f"{name} -> lyrics x3", ol)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
