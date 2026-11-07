"""Settings > Appearance > colour: one accent colour re-colours frames, titles and selection. A preset
or a hex, applied at once, saved, re-applied at start; invalid input is refused. The default stays
the original teal. Offline: a fake Provider, Textual Pilot.

Run standalone:  python tests/test_accent.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spt_tui import config
from spt_tui.theming import normalize_color, build_theme, PRESETS
import test_queue_view as Q

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


async def settle(pilot):
    for _ in range(3):
        await pilot.pause()
    await asyncio.sleep(0.3)
    await pilot.pause()


def frame_color(app, sel):
    c = app.query_one(sel).styles.border.top[1]
    return "#%02x%02x%02x" % (c.r, c.g, c.b)


async def body():
    check("normalize: hex, no #, short, name, junk",
          [normalize_color(x) for x in ("#FF7EB6", "ff7eb6", "#f7b", "blue", "nope", "")]
          == ["#ff7eb6", "#ff7eb6", "#ff77bb", PRESETS["blue"], None, None])
    t = build_theme("x")
    check("default theme keeps the original teal", (t.primary, t.secondary, t.accent, t.background)
          == ("#27e1c1", "#93e1e1", "#6ef7d6", "#0b1e24") and t.variables["spt-dim"] == "#1a3a40")
    for n, h in PRESETS.items():
        th = build_theme("x", h)
        ok = all((v.startswith("#") and len(v) == 7) or v == "transparent" for v in
                 [th.primary, th.secondary, th.accent, th.background, th.surface, th.panel, *th.variables.values()])
        if not ok:
            check(f"preset {n} builds a valid palette", False, str(th)); break
    else:
        check("every preset builds a valid palette", True)

    saved = {}
    config.save_local_config = lambda cfg: saved.update(cfg)
    config.LOCAL_CFG = {}
    prov = Q.FakeProv(); app = Q.TApp(prov)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); Q.pause_intervals(app)
        check("starts teal", frame_color(app, "#right") == "#27e1c1", frame_color(app, "#right"))
        app.action_prompt_settings(); await settle(pilot)
        names = list(PRESETS)
        app.accent_select.highlighted = names.index("pink")
        app.accent_select.action_select(); await settle(pilot)
        check("a preset recolours the frames at once", frame_color(app, "#right") == PRESETS["pink"], frame_color(app, "#right"))
        check("…and is saved", saved.get("accent_color") == PRESETS["pink"], str(saved))
        check("…and Python-built markup follows", app._accent_hex() == PRESETS["pink"], app._accent_hex())

        app.accent_input.focus(); app.accent_input.value = "#3a86ff"
        await pilot.press("enter"); await settle(pilot)
        check("a custom hex applies", frame_color(app, "#right") == "#3a86ff", frame_color(app, "#right"))
        check("…and is saved", saved.get("accent_color") == "#3a86ff", str(saved))

        app.accent_input.value = "banana"
        await pilot.press("enter"); await settle(pilot)
        check("an invalid colour is refused and nothing changes",
              frame_color(app, "#right") == "#3a86ff" and saved.get("accent_color") == "#3a86ff")

        app.accent_select.highlighted = names.index("teal")
        app.accent_select.action_select(); await settle(pilot)
        check("teal goes back to the original", frame_color(app, "#right") == "#27e1c1")

    config.LOCAL_CFG = {"accent_color": "#ff7eb6"}
    app2 = Q.TApp(Q.FakeProv())
    async with app2.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); Q.pause_intervals(app2)
        check("a saved colour is applied at start", frame_color(app2, "#right") == "#ff7eb6", frame_color(app2, "#right"))


def main():
    try:
        asyncio.run(body())
    except Exception:
        check("accent test (crashed)", False, traceback.format_exc(limit=8))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    for n, c, dd in RESULTS:
        if not c: print("  FAIL:", n, "::", dd)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
