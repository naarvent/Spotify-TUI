"""Three usability gaps found by reading the UI code.

* The sidebar takes 38 fixed columns and could never be hidden: the CSS for a
  collapsed left column existed and `_welcome_dims` read the class, but nothing
  ever set it.
* On a narrow terminal the column fitter drops Added, then Album, then Artist
  with no indication, so a column you rely on just vanishes.
* Removing a track from a playlist fired immediately, while deleting a whole
  playlist makes you type its name — the small destructive action had less of a
  safety net than the big one.

Run standalone:  python tests/test_ui_affordances.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def __init__(self): self.removed = []
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return (x or "").split(":")[-1]
    def playlist_remove_all_occurrences_of_items(self, pid, uris):
        self.removed.append((pid, list(uris))); return True

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
    return [{"id": f"i{i}", "uri": f"spotify:track:i{i}", "title": f"track {i}",
             "artist": "artist", "album": "album", "dur": "3:21", "added": "2024-01-01"}
            for i in range(n)]

def labels(t):
    return [str(getattr(c, "label", "")) for c in t.ordered_columns]

def panel_title(app):
    return str(getattr(app.right_panel, "border_title", "") or "")

async def poll(fn, timeout=5.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.01)
    try: return fn()
    except Exception: return None


# --- sidebar collapse ------------------------------------------------------ #

async def test_sidebar_collapses_and_gives_its_width_to_the_panel():
    app = TApp(Fake())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        before = app.right_panel.content_size.width
        app.action_toggle_sidebar()
        for _ in range(5):
            await pilot.pause()
        grid = app.query_one("#grid")
        check("the grid is marked collapsed", grid.has_class("left-collapsed"))
        after = app.right_panel.content_size.width
        check("the content panel gets the freed width", after > before,
              f"{before} -> {after}")
        check("the sidebar is hidden", not app.query_one("#left_col").display)

        app.action_toggle_sidebar()
        for _ in range(5):
            await pilot.pause()
        check("toggling again brings the sidebar back",
              not grid.has_class("left-collapsed") and app.query_one("#left_col").display)
        check("and the panel goes back to its original width",
              app.right_panel.content_size.width == before,
              f"{before} -> {app.right_panel.content_size.width}")


async def test_table_refits_when_the_sidebar_collapses():
    """The freed columns must reach the table, not just the panel."""
    app = TApp(Fake())
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]P[/b]", rows_n(20), [False] * 20, profile="playlist")
        for _ in range(4):
            await pilot.pause()
        before = sum(int(getattr(c, "width", 0) or 0) for c in t.ordered_columns)
        app.action_toggle_sidebar()
        for _ in range(6):
            await pilot.pause()
        after = sum(int(getattr(c, "width", 0) or 0) for c in t.ordered_columns)
        check("the table columns widen with the panel", after > before, f"{before} -> {after}")


# --- dropped-column hint --------------------------------------------------- #

async def test_dropped_columns_are_named_in_the_title():
    app = TApp(Fake())
    async with app.run_test(size=(110, 25)) as pilot:   # panel ~70: Added drops
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("[b]My List[/b]", rows_n(10), [False] * 10, profile="playlist")
        for _ in range(4):
            await pilot.pause()
        check("Added really was dropped", "Added" not in labels(t), f"{labels(t)}")
        title = panel_title(app)
        check("the title still names the view", "My List" in title, f"title={title!r}")
        check("the title says what is hidden", "Added" in title, f"title={title!r}")


async def test_no_hint_when_everything_fits():
    app = TApp(Fake())
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._render_tracks_table("[b]My List[/b]", rows_n(10), [False] * 10, profile="playlist")
        for _ in range(4):
            await pilot.pause()
        title = panel_title(app)
        check("a wide terminal gets a clean title", "hidden" not in title.lower(),
              f"title={title!r}")


async def test_hint_disappears_when_the_terminal_widens():
    app = TApp(Fake())
    async with app.run_test(size=(110, 25)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._render_tracks_table("[b]My List[/b]", rows_n(10), [False] * 10, profile="playlist")
        for _ in range(4):
            await pilot.pause()
        check("the hint is there while narrow", "Added" in panel_title(app),
              f"title={panel_title(app)!r}")
        await pilot.resize_terminal(200, 40)
        for _ in range(8):
            await pilot.pause()
        check("the hint goes once everything fits again",
              "hidden" not in panel_title(app).lower(), f"title={panel_title(app)!r}")


# --- track removal confirmation -------------------------------------------- #

async def test_removing_a_track_asks_first():
    fake = Fake(); app = TApp(fake)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        pdata = {"id": "pl1", "name": "L", "uri": "spotify:playlist:pl1"}
        app._confirm_remove_track("pl1", "spotify:track:i3", "track 3", pdata)
        for _ in range(3):
            await pilot.pause()
        check("nothing is removed before confirming", fake.removed == [], f"{fake.removed}")
        check("a pending removal is recorded",
              (getattr(app, "_pending_remove_track", None) or {}).get("uri") == "spotify:track:i3",
              f"{getattr(app, '_pending_remove_track', None)}")
        check("the track name is in the prompt",
              "track 3" in str(getattr(app, "_pending_remove_track", {}).get("title", "")))


async def test_confirming_removes_and_cancelling_does_not():
    fake = Fake(); app = TApp(fake)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        pdata = {"id": "pl1", "name": "L", "uri": "spotify:playlist:pl1"}

        app._confirm_remove_track("pl1", "spotify:track:i3", "track 3", pdata)
        await pilot.pause()
        app._cancel_remove_track()
        await pilot.pause()
        check("cancelling removes nothing", fake.removed == [], f"{fake.removed}")
        check("and clears the pending removal",
              getattr(app, "_pending_remove_track", None) is None)

        app._confirm_remove_track("pl1", "spotify:track:i3", "track 3", pdata)
        await pilot.pause()
        app._apply_pending_remove_track()
        await poll(lambda: bool(fake.removed), timeout=5.0)
        check("confirming does remove it", fake.removed and fake.removed[0][0] == "pl1",
              f"{fake.removed}")
        check("and clears the pending removal",
              getattr(app, "_pending_remove_track", None) is None)


async def test_applying_without_a_pending_removal_is_harmless():
    fake = Fake(); app = TApp(fake)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._apply_pending_remove_track()
        app._cancel_remove_track()
        await pilot.pause()
        check("no stray API call", fake.removed == [], f"{fake.removed}")


async def test_changing_view_cancels_an_armed_track_removal():
    fake = Fake(); app = TApp(fake)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(); pause_intervals(app)
        pdata = {"id": "pl1", "name": "L", "uri": "spotify:playlist:pl1"}
        app._new_view_token("playlist", "pl1")
        app._confirm_remove_track("pl1", "spotify:track:i3", "track 3", pdata)
        app._new_view_token("search", "other query")
        app._apply_pending_remove_track()
        await pilot.pause()
        check("view change clears the pending removal",
              getattr(app, "_pending_remove_track", None) is None)
        check("stale confirmation cannot remove a track", fake.removed == [], f"{fake.removed}")


ALL = [test_sidebar_collapses_and_gives_its_width_to_the_panel,
       test_table_refits_when_the_sidebar_collapses,
       test_dropped_columns_are_named_in_the_title, test_no_hint_when_everything_fits,
       test_hint_disappears_when_the_terminal_widens,
       test_removing_a_track_asks_first, test_confirming_removes_and_cancelling_does_not,
       test_applying_without_a_pending_removal_is_harmless,
       test_changing_view_cancels_an_armed_track_removal]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (ui affordances) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
