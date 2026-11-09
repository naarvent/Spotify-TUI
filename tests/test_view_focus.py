"""Focus never gets stuck between the sidebar / Search / Help and an open
right-hand view: Settings (form with Inputs + provider list), Import
Playlists, Devices, and the modal provider picker (P).

The bug: with Settings open, moving focus to Playlists / Library / Search /
Help (mouse click or Tab) left no way back — Right did nothing in an open view
and Tab never offered the form as a stop.

Run standalone:  python tests/test_view_focus.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import DataTable, Input, OptionList
from spt_tui.app import SptPy
from spt_tui.app.provider_switch import ProviderPickerScreen

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0):
        return {"items": [{"id": "p1", "name": "Road Trip", "uri": "spotify:playlist:p1"}], "next": None, "total": 1}
    def get_playback(self): return {}
    def recently_played(self, limit=50): return {"items": []}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def devices(self): return [{"id": "d1", "name": "Desk", "type": "Computer", "is_active": True}]


class TApp(SptPy):
    def __init__(self):
        super().__init__()
        self.spotify = Fake()


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval", "_devices_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def settle(pilot, n=4):
    for _ in range(n):
        await pilot.pause()


def where(app):
    w = app.focused
    return f"{type(w).__name__}#{getattr(w, 'id', None)}"


async def test_settings_focus_round_trip():
    app = TApp()
    async with app.run_test(size=(150, 60)) as pilot:
        await settle(pilot); pause_intervals(app)
        app._load_playlists(force=True); await asyncio.sleep(0.2)
        await pilot.press("<"); await settle(pilot)
        check("Settings opens with the first field focused", app.focused is app.seek_vol_down_input, where(app))
        await pilot.press("down"); await settle(pilot)
        field = app.focused
        for target in ("#pl_list", "#lib_list", "help"):
            if target == "help":
                app._focus_help_box()   # Help takes focus from the keyboard (Tab)
            else:
                await pilot.click(target)
            await settle(pilot)
            moved = not app._in_right_panel(app.focused)
            await pilot.press("right"); await settle(pilot)
            check(f"after clicking {target}, Right returns to the same Settings field",
                  moved and app.focused is field, where(app))
        await pilot.click("#pl_list"); await settle(pilot)
        reached = False
        for _ in range(6):
            await pilot.press("tab"); await settle(pilot, 2)
            if app._in_right_panel(app.focused):
                reached = True
                break
        check("Tab from the sidebar reaches the open Settings form", reached, where(app))

        fields = app._right_form_fields()
        check("the form exposes all 19 fields (6 inputs (incl. visualizer FPS), Spicy Lyrics list + API key, visualizer mode list, yt-dlp list, appearance + colour lists, custom colour, key hints + title bar lists, provider list, 3 Subsonic)", len(fields) == 19,
              repr([type(f).__name__ for f in fields]))
        fields[0].focus(); await settle(pilot)
        order = []
        for _ in range(len(fields)):
            await pilot.press("tab"); await settle(pilot, 2)
            order.append(app.focused)
        check("Tab walks every field in order, then leaves the form (never trapped)",
              order[:-1] == fields[1:] and not app._in_right_panel(order[-1]),
              repr([type(o).__name__ for o in order]))
        fields[0].focus(); await settle(pilot)
        await pilot.press("shift+tab"); await settle(pilot)
        check("Shift+Tab on the first field leaves backwards", not app._in_right_panel(app.focused), where(app))
        app.lyrics_cache_input.focus(); await settle(pilot)
        await pilot.press("down"); await settle(pilot)
        check("Down from the lyrics cache field reaches the Spicy Lyrics list", app.focused is app.spicy_select,
              where(app))
        for _ in range(4):                                  # option 2 of the list, then out of it
            if app.focused is not app.spicy_select:
                break
            await pilot.press("down"); await settle(pilot)
        check("Down from the Spicy Lyrics list reaches the API key field", app.focused is app.spicy_key_input,
              where(app))
        await pilot.press("down"); await settle(pilot)
        check("Down from the API key field reaches the Visualizer FPS field", app.focused is app.visualizer_fps_input,
              where(app))
        await pilot.press("down"); await settle(pilot)
        check("Down from the Visualizer FPS field reaches the visualizer mode list",
              app.focused is app.visualizer_mode_select, where(app))
        for _ in range(4):
            if app.focused is not app.visualizer_mode_select:
                break
            await pilot.press("down"); await settle(pilot)
        check("Down from the visualizer mode list reaches the yt-dlp list", app.focused is app.ytdlp_select,
              where(app))
        for _ in range(4):
            if app.focused is not app.ytdlp_select:
                break
            await pilot.press("down"); await settle(pilot)
        check("Down from the yt-dlp list reaches the appearance list", app.focused is app.appearance_select,
              where(app))
        app.subsonic_url_input.focus(); await settle(pilot)
        await pilot.press("down"); await settle(pilot)
        check("Down walks the Subsonic fields too", app.focused is app.subsonic_user_input, where(app))
        await pilot.press("escape"); await settle(pilot)
        check("Esc still closes Settings", getattr(app, "_right_view", None) is None)


async def test_import_and_devices_round_trip():
    app = TApp()
    async with app.run_test(size=(150, 50)) as pilot:
        await settle(pilot); pause_intervals(app)
        app._open_import_playlists(); await settle(pilot)
        inp = app.import_input
        check("Import Playlists focuses its input", app.focused is inp, where(app))
        await pilot.click("#lib_list"); await settle(pilot)
        await pilot.press("right"); await settle(pilot)
        check("Import Playlists: Right brings focus back to its input", app.focused is inp, where(app))
        app.action_escape_to_menu(); await settle(pilot)

        app.action_manage_devices(); await settle(pilot, 8)
        pause_intervals(app)
        await pilot.click("#pl_list"); await settle(pilot)
        await pilot.press("right"); await settle(pilot)
        check("Devices: Right brings focus back to the devices table",
              isinstance(app.focused, DataTable) and app.focused.id == "devices_table", where(app))


async def test_settings_provider_list_arrows_leave_at_edges():
    """Down on the LAST provider must go on to the next field (Server URL), not wrap to the first
    provider; Up on the FIRST provider goes back to the field above."""
    app = TApp()
    async with app.run_test(size=(150, 60)) as pilot:
        await settle(pilot); pause_intervals(app)
        await pilot.press("<"); await settle(pilot)
        sel = app.provider_select
        fields = app._right_form_fields()
        i = [k for k, f in enumerate(fields) if f is sel][0]
        n = sel.option_count
        check("the provider list has several options", n >= 2, str(n))
        sel.focus(); sel.highlighted = 0; await settle(pilot)
        await pilot.press("down"); await settle(pilot)
        check("Down inside the list moves to the next provider", app.focused is sel and sel.highlighted == 1,
              f"{where(app)} hl={sel.highlighted}")
        sel.highlighted = n - 1; await settle(pilot)
        await pilot.press("down"); await settle(pilot)
        check("Down on the LAST provider goes to the next field", app.focused is fields[i + 1],
              f"{where(app)} hl={sel.highlighted}")
        check("…and the highlight did NOT wrap to the first provider", sel.highlighted == n - 1, str(sel.highlighted))
        sel.focus(); sel.highlighted = 0; await settle(pilot)
        await pilot.press("up"); await settle(pilot)
        check("Up on the FIRST provider goes to the field above", app.focused is fields[i - 1],
              f"{where(app)} hl={sel.highlighted}")
        sel.focus(); sel.highlighted = n - 1; await settle(pilot)
        await pilot.press("up"); await settle(pilot)
        check("Up inside the list moves to the previous provider", app.focused is sel and sel.highlighted == n - 2,
              f"{where(app)} hl={sel.highlighted}")
        fields[-1].focus(); await settle(pilot)
        await pilot.press("down"); await settle(pilot)
        check("Down on the very last field stays put (no wrap)", app.focused is fields[-1], where(app))
        await pilot.press("escape"); await settle(pilot)


async def test_import_selection_tab_reaches_button():
    """Import from Spotify: Tab from the list goes to «Import selected», Enter starts it,
    Shift+Tab goes back to the list, and Tab from the button leaves the form."""
    app = TApp()
    started = []
    app._start_spotify_import = lambda chosen: started.append(chosen)
    async with app.run_test(size=(150, 50)) as pilot:
        await settle(pilot); pause_intervals(app)
        app._right_view = ("spotify_import",)
        app._show_import_selection(object(), {"liked": 3, "albums": 1, "artists": 2,
                                              "playlists": [{"id": "p1", "name": "Road Trip", "total": 5}]})
        await settle(pilot)
        check("Import selection: starts on the list", app.focused is app.import_selection, where(app))
        await pilot.press("tab"); await settle(pilot)
        check("Import selection: Tab goes to the button", app.focused is app._import_start_button, where(app))
        await pilot.press("shift+tab"); await settle(pilot)
        check("Import selection: Shift+Tab goes back to the list", app.focused is app.import_selection, where(app))
        await pilot.press("tab"); await settle(pilot)
        await pilot.press("enter"); await settle(pilot)
        check("Import selection: Enter on the button with nothing marked starts nothing", started == [])
        app.import_selection.select_all(); await settle(pilot)
        app._import_start_button.focus(); await settle(pilot)
        await pilot.press("enter"); await settle(pilot)
        check("Import selection: Enter on the button starts the import", len(started) == 1 and len(started[0]) == 4,
              str(started))
        n = app.import_selection.option_count
        app.import_selection.focus(); app.import_selection.highlighted = n - 1; await settle(pilot)
        await pilot.press("down"); await settle(pilot)
        check("Import selection: Down on the last item reaches the button (no wrap to the first)",
              app._import_start_button is app.focused and app.import_selection.highlighted == n - 1, where(app))
        await pilot.press("up"); await settle(pilot)
        check("Import selection: Up from the button goes back to the list", app.focused is app.import_selection, where(app))
        await pilot.press("tab"); await settle(pilot)
        app._import_start_button.focus(); await settle(pilot)
        await pilot.press("tab"); await settle(pilot)
        check("Import selection: Tab from the button leaves the form (not trapped)",
              not app._in_right_panel(app.focused), where(app))


async def test_picker_is_modal():
    app = TApp()
    async with app.run_test(size=(150, 50)) as pilot:
        await settle(pilot); pause_intervals(app)
        await pilot.press("P")
        for _ in range(50):
            if isinstance(app.screen, ProviderPickerScreen):
                break
            await asyncio.sleep(0.05)
        await settle(pilot)
        check("P opens the modal picker focused on its list",
              isinstance(app.screen, ProviderPickerScreen) and isinstance(app.focused, OptionList), where(app))
        for k in ("tab", "right", "left", "shift+tab"):
            await pilot.press(k); await settle(pilot, 2)
        check("keys cannot move focus behind the modal", isinstance(app.screen, ProviderPickerScreen)
              and app.focused is not None and app.focused.screen is app.screen, where(app))
        await pilot.press("escape"); await settle(pilot)
        check("Esc closes it and focus is back on the main screen",
              not isinstance(app.screen, ProviderPickerScreen) and app.focused is not None
              and app.focused.screen is app.screen, where(app))


ASYNC = [test_settings_focus_round_trip, test_import_and_devices_round_trip,
         test_import_selection_tab_reaches_button, test_settings_provider_list_arrows_leave_at_edges,
         test_picker_is_modal]

def main():
    for fn in ASYNC:
        try:
            asyncio.run(fn())
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (view_focus) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name, "::", (detail or "").strip().splitlines()[-1] if detail else "")
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
