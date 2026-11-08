"""Settings > Visualizer FPS (`visualizer_fps`, 10-60, default 30): the field exists with a help
line, bad values are rejected, good ones are saved and applied to a running visualizer at once
(cava restarted with the new framerate, repaint timer re-armed).

Run standalone:  python tests/test_settings_visualizer_fps.py
"""
import os, sys, asyncio, tempfile, time

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_visfps_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_visualizer as tv          # fake cava + Fake/TApp/poll helpers
from spt_tui import config, visualizer as viz
from textual.widgets import Input

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def seen_fps():
    cfg = open(os.path.join(tv.TMP, "seen_config")).read()
    for line in cfg.splitlines():
        if line.startswith("framerate"):
            return int(line.split("=")[1])


async def main():
    orig_find, orig_save = viz.find_cava, config.save_local_config
    viz.find_cava = lambda: tv.FAKE
    saved = []
    config.save_local_config = lambda cfg: saved.append(dict(cfg))
    try:
        check("range is 10-60, default 30", (viz.MIN_FPS, viz.MAX_FPS, viz.DEFAULT_FPS) == (10, 60, 30))
        config.LOCAL_CFG = {}
        app = tv.TApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(); tv.pause_intervals(app)
            app.action_prompt_settings(); await pilot.pause(); await pilot.pause()
            f = getattr(app, "visualizer_fps_input", None)
            check("Settings has the Visualizer FPS field", isinstance(f, Input) and f.is_attached)
            check("placeholder shows the current value (30)", f.placeholder == "30", str(f.placeholder))
            texts = " ".join(str(getattr(w, "_Static__content", "")) for w in app.query("Static"))
            check("one-line help with range and default", "10-60" in texts and "default 30" in texts)
            check("field is reachable by Tab/arrows (part of the form)", f in app._right_form_fields())
            # Bad values.
            for bad in ("9", "61", "abc", "-5", "3.5"):
                saved.clear(); config.LOCAL_CFG = {}
                ok = app._save_visualizer_fps_setting(bad)
                check(f"rejects {bad!r}", ok is False and not saved and "visualizer_fps" not in config.LOCAL_CFG)
            check("empty keeps the value", app._save_visualizer_fps_setting("  ") is True and not saved)
            # Through the real Enter on the field.
            config.LOCAL_CFG = {}; saved.clear()
            f.focus(); await pilot.pause()
            f.value = "20"
            await pilot.press("enter"); await pilot.pause()
            check("Enter saves visualizer_fps=20", config.LOCAL_CFG.get("visualizer_fps") == 20 and saved,
                  str(config.LOCAL_CFG))
            check("Enter keeps Settings open", getattr(app, "_right_view", None)[0] == "settings")
            for edge in (10, 60):
                check(f"accepts the edge {edge}", app._save_visualizer_fps_setting(str(edge)) is True
                      and config.LOCAL_CFG["visualizer_fps"] == edge)
            # Closing Settings clears the attribute with the others.
            app._finish_settings(); await pilot.pause()
            check("_finish_settings forgets the field", not hasattr(app, "visualizer_fps_input"))

        # Hot apply to a running visualizer.
        config.LOCAL_CFG = {}
        app = tv.TApp()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(); tv.pause_intervals(app)
            app._now_internal_is_playing = True
            await pilot.press("v")
            await tv.poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None and app._vis_paints > 2)
            check("opens at the default 30 FPS", seen_fps() == 30 and abs(app._vis_period - 1 / 30) < 1e-9,
                  f"{seen_fps()} {app._vis_period}")
            old = app._vis_proc
            old_pid = old.proc.pid
            app._save_visualizer_fps_setting("45")
            await tv.poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None and app._vis_proc is not old)
            await tv.poll(pilot, lambda: app._vis_paints > 0)
            check("running visualizer: new cava with framerate 45 (no restart of the app)",
                  app._vis_proc is not old and seen_fps() == 45, f"{seen_fps()}")
            check("…repaint timer re-armed at 1/45", abs(app._vis_period - 1 / 45) < 1e-9, str(app._vis_period))
            time.sleep(0.1)
            check("…and the old cava is dead", not tv.alive(old_pid))
            check("still in the Visualizer view", app._right_view[0] == "visualizer" and app._vis_on)
            await pilot.press("v"); await pilot.pause(); await pilot.pause()
            # Reopen: reads the saved value.
            await pilot.press("v")
            await tv.poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None)
            check("next open uses the saved value", abs(app._vis_period - 1 / 45) < 1e-9 and seen_fps() == 45)
            await pilot.press("v"); await pilot.pause()
    finally:
        viz.find_cava, config.save_local_config = orig_find, orig_save
        config.LOCAL_CFG = {}


if __name__ == "__main__":
    asyncio.run(main())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
