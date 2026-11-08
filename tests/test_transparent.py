"""Settings > Appearance: "Transparent" lets the terminal's own background through (ansi_color +
`ansi_default`), leaving painted only the frames, the text and the selection. Opaque is unchanged,
the choice is saved, applied at once and re-applied at start. Offline: a fake Provider, Textual Pilot.

Run standalone:  python tests/test_transparent.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from textual.widgets import OptionList
from spt_tui import config
import test_queue_view as Q

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

# Colours that may stay painted in transparent mode: the selection tints and the scrollbar.
ALLOWED = {(14, 45, 52), (18, 71, 79), (23, 96, 106), (0, 13, 20), (0, 0, 0)}


def painted(app):
    """{rgb: cells} of every cell whose background is not the terminal's default."""
    out = {}
    for strip in app.screen._compositor.render_strips():
        for seg in strip:
            bg = seg.style.bgcolor if seg.style else None
            if bg is not None and bg.type.name != "DEFAULT":
                t = bg.get_truecolor()
                out[(t.red, t.green, t.blue)] = out.get((t.red, t.green, t.blue), 0) + len(seg.text)
    return out


async def settle(pilot):
    for _ in range(3):
        await pilot.pause()
    await asyncio.sleep(0.3)
    await pilot.pause()


async def body():
    saved = {}
    config.save_local_config = lambda cfg: saved.update(cfg)       # never touch the real config
    config.LOCAL_CFG = {}
    prov = Q.FakeProv(); app = Q.TApp(prov)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); Q.pause_intervals(app)
        check("opaque by default", not app.has_class("-transparent") and len(painted(app)) > 3, str(painted(app)))

        app.action_prompt_settings(); await settle(pilot)
        sel = app.appearance_select
        check("Settings has an Appearance list, opaque highlighted", sel.highlighted == 0, str(sel.highlighted))
        sel.highlighted = 1
        sel.action_select()
        await settle(pilot)
        check("choosing Transparent applies it at once", app.has_class("-transparent") and app.ansi_color is True)
        check("…and saves it", saved.get("transparent_background") is True, str(saved))
        bad = {k: v for k, v in painted(app).items() if k not in ALLOWED}
        check("settings view: nothing painted but frames/selection", not bad, str(bad))

        app._now_internal_track_id = prov.current["id"]
        app.action_open_queue(); await settle(pilot)
        bad = {k: v for k, v in painted(app).items() if k not in ALLOWED}
        check("queue view: nothing painted but frames/selection", not bad, str(bad))
        app.action_help(); await settle(pilot)
        bad = {k: v for k, v in painted(app).items() if k not in ALLOWED}
        check("help view: nothing painted but frames/selection", not bad, str(bad))
        table = app._render_tracks_table("[b]Mix[/b]", [
            {"id": "idB", "uri": "ytmusic:track:idB", "title": "B", "artist": "Joji", "album": "", "dur": "", "added": ""}],
            [False], context_uri="ytmusic:playlist:pl1", context_uris=["ytmusic:track:idB"])
        await settle(pilot)
        bad = {k: v for k, v in painted(app).items() if k not in ALLOWED}
        check("tracks table: nothing painted but frames/selection", not bad, str(bad))

        app.action_prompt_settings(); await settle(pilot)
        app.appearance_select.highlighted = 0
        app.appearance_select.action_select(); await settle(pilot)
        check("choosing Opaque goes back", not app.has_class("-transparent") and app.ansi_color is None
              and saved.get("transparent_background") is False, str(saved))
        check("…and the theme colours are painted again", len(painted(app)) > 3, str(painted(app)))

    # Saved choice is re-applied when the app starts.
    config.LOCAL_CFG = {"transparent_background": True}
    app2 = Q.TApp(Q.FakeProv())
    async with app2.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); Q.pause_intervals(app2)
        check("a saved transparent choice is applied at start", app2.has_class("-transparent") and app2.ansi_color is True)


def main():
    try:
        asyncio.run(body())
    except Exception:
        check("transparent test (crashed)", False, traceback.format_exc(limit=8))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    for n, c, dd in RESULTS:
        if not c: print("  FAIL:", n, "::", dd)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
