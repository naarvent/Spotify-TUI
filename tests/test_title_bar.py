"""Settings > Appearance: hide / show the title bar (Textual Header).

* `show_header: false` in spt_config.json hides it at start; the grid takes
  the freed row;
* the Settings option applies at once and is saved;
* while hidden, sub_title keeps updating (provider switch) and shows when the
  bar comes back;
* the karaoke view still fills its (now taller) box exactly.

Run standalone:  python tests/test_title_bar.py
"""
import os, sys, asyncio, tempfile
os.environ.setdefault("SPT_SPICY_LYRICS", "off")   # never reach the real API from a suite

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_titlebar_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import Header
from spt_tui import config
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


class TApp(SptPy):
    def __init__(self):
        super().__init__(); self.spotify = Fake()
    def _fetch_synced_lyrics(self, **kw):
        return [(i * 3000, f"line {i}") for i in range(20)], "found"


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def poll(fn, timeout=5.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.02)
    try: return fn()
    except Exception: return None


async def test_title_bar():
    config.LOCAL_CFG = {}
    app = TApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(); pause_intervals(app)
        hdr = app.query_one(Header)
        grid = app.query_one("#grid")
        y_shown, h_shown = grid.region.y, grid.region.height
        check("shown by default", hdr.display and y_shown >= 1)

        app.action_prompt_settings(); await pilot.pause(); await pilot.pause()
        sel = getattr(app, "header_select", None)
        check("Settings has the title-bar option", sel is not None and sel.highlighted == 0)
        sel.focus(); sel.highlighted = 1; await pilot.pause()
        await pilot.press("enter"); await pilot.pause(); await pilot.pause()
        check("choosing 'Hide it' hides it now", not hdr.display)
        check("…and saves show_header=false", config.load_local_config().get("show_header") is False)
        check("the grid takes the freed row", grid.region.y == y_shown - 1 and grid.region.height == h_shown + 1,
              f"{grid.region} vs y={y_shown} h={h_shown}")

        app.sub_title = "YouTube Music"          # what a provider switch does
        await pilot.pause()
        app._apply_title_bar(True); await pilot.pause()
        check("shown again", hdr.display and grid.region.y == y_shown)
        check("sub_title kept updating while hidden", hdr.screen_sub_title == "YouTube Music",
              repr(hdr.screen_sub_title))

    # Start hidden from the config; the karaoke view fills the taller box.
    config.LOCAL_CFG = {"show_header": False, "lyrics_mode": "karaoke"}
    app = TApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(); pause_intervals(app)
        check("show_header=false hides it at start", not app.query_one(Header).display)
        app._bar_last_track = {"id": "k1", "name": "Song", "artists": [{"name": "Band"}], "duration_ms": 60000}
        app._now_internal_track_id = "k1"; app._now_internal_pos_ms = 4000; app._now_internal_dur_ms = 60000
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        app._lyrics_interval.pause()
        await pilot.pause(); await pilot.pause()
        app._render_karaoke_view(force=True); await pilot.pause()
        h_hidden = app.lyrics_box.content_size.height
        check("karaoke frame = box height with the bar hidden", len(app._karaoke_rows) == h_hidden,
              f"{len(app._karaoke_rows)} vs {h_hidden}")
        app._apply_title_bar(True)
        await pilot.pause(); await pilot.pause()
        app._update_lyrics_highlight(); await pilot.pause()
        h_shown = app.lyrics_box.content_size.height
        check("…and after showing it again (one row less)",
              h_shown == h_hidden - 1 and len(app._karaoke_rows) == h_shown, f"{len(app._karaoke_rows)} vs {h_shown}")


if __name__ == "__main__":
    asyncio.run(test_title_bar())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
