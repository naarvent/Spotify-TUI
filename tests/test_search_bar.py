"""Search bar feedback, narrower Help, no header icon, key hints bar (toggle in Settings).

Run standalone:  python tests/test_search_bar.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from textual.widgets import Footer, Header
from spt_tui import config
import test_queue_view as Q

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


async def settle(pilot, n=3):
    for _ in range(n): await pilot.pause()
    await asyncio.sleep(0.25); await pilot.pause()


async def body():
    saved = {}
    config.save_local_config = lambda cfg: saved.update(cfg)
    config.LOCAL_CFG = {}
    app = Q.TApp(Q.FakeProv())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); Q.pause_intervals(app)
        wrap = app.query_one("#search_wrap")
        check("Help box is narrow", app.query_one("#help_wrap").size.width <= 12, str(app.query_one("#help_wrap").size))
        icon = app.query_one("HeaderIcon")
        check("the header icon is gone", not icon.display or icon.size.width == 0, str(icon.size))
        check("the key hints bar has no command-palette key", app.query_one(Footer).show_command_palette is False)

        app.action_focus_search(); await settle(pilot)
        check("/ focuses the search input", app.focused is app.search_input, str(app.focused))
        check("focused: the frame is heavy and shows a hint", wrap.border_subtitle == app._SEARCH_HINT, repr(wrap.border_subtitle))
        app._stop_search_pulse()

        # Enter: spinner until the results for that token are up, then a green tick.
        app._searching_token = 7        # as _begin_search_feedback leaves it while the search runs
        app._last_search_worker = 7; app._search_rendered_token = None
        app._start_search_spinner(7, "joji")
        await settle(pilot)
        check("submitted: the frame turns busy and the hint spins", wrap.has_class("-searching")
              and "searching" in (wrap.border_subtitle or ""), repr(wrap.border_subtitle))
        a = wrap.border_subtitle
        await asyncio.sleep(0.4); await pilot.pause()
        check("…and it animates", wrap.border_subtitle != a, repr(wrap.border_subtitle))
        app._searching_token = 7; app._search_rendered_token = 7
        await asyncio.sleep(0.4); await pilot.pause()
        check("results up: green tick, spinner stopped", wrap.has_class("-done") and not wrap.has_class("-searching")
              and "✓" in (wrap.border_subtitle or ""), repr(wrap.border_subtitle))
        await asyncio.sleep(1.5); await pilot.pause()
        check("…then back to the focus hint", not wrap.has_class("-done") and wrap.border_subtitle == app._SEARCH_HINT,
              repr(wrap.border_subtitle))

        # A newer search supersedes the spinner of an older one.
        app._searching_token = 8; app._last_search_worker = 8; app._search_rendered_token = None
        app._start_search_spinner(8, "x"); app._last_search_worker = 9
        await asyncio.sleep(0.4); await pilot.pause()
        check("superseded search: its spinner stops", getattr(app, "_spin_timer", None) is None
              and not wrap.has_class("-searching"))

        app.search_input.blur(); await settle(pilot)
        check("leaving the bar clears the hint", not (wrap.border_subtitle or ""), repr(wrap.border_subtitle))

        # Settings > key hints bar.
        app.action_prompt_settings(); await settle(pilot)
        app.hints_select.highlighted = 1
        app.hints_select.action_select(); await settle(pilot)
        check("Hide: the bar disappears and it is saved", app.query_one(Footer).display is False
              and saved.get("show_key_hints") is False, str(saved))
        app.hints_select.highlighted = 0
        app.hints_select.action_select(); await settle(pilot)
        check("Show: it is back", app.query_one(Footer).display is True and saved.get("show_key_hints") is True)

    config.LOCAL_CFG = {"show_key_hints": False}
    app2 = Q.TApp(Q.FakeProv())
    async with app2.run_test(size=(160, 40)) as pilot:
        await pilot.pause(); Q.pause_intervals(app2)
        check("a saved 'hide' is applied at start", app2.query_one(Footer).display is False)


def main():
    try:
        asyncio.run(body())
    except Exception:
        check("search bar test (crashed)", False, traceback.format_exc(limit=8))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    for n, c, dd in RESULTS:
        if not c: print("  FAIL:", n, "::", dd)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
