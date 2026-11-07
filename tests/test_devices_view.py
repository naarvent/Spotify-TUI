"""Devices view (`d`) in the same frame as a playlist / the Queue.

* the table borrows #right's frame (title "Devices · N"), no inner box;
* active device marked; Enter transfers to the highlighted device, also on a
  "/" filtered row; the cursor survives the 5 s refresh; opening it again
  never leaves two tables.

Run standalone:  python tests/test_devices_view.py
"""
import os, sys, asyncio, tempfile, time

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_devices_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import DataTable, Static
from spt_tui import config
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def __init__(self):
        self.transfers = []
        self.devs = [{"id": "PC", "name": "Desk PC", "type": "Computer", "is_active": True},
                     {"id": "PH", "name": "Phone", "type": "Smartphone", "is_active": False},
                     {"id": "TV", "name": "Living TV", "type": "TV", "is_active": False}]
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def get_playback(self): return {}
    def ensure(self): return self
    def devices(self): return [dict(d) for d in self.devs]
    def transfer(self, dev_id, force_play=False): self.transfers.append(dev_id)


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake
    local_player = None


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval", "_devices_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def poll(pilot, fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        await pilot.pause()
        try:
            if fn(): return True
        except Exception:
            pass
        await asyncio.sleep(0.05)
    return False


def dtable(app):
    try: return app.query_one("#devices_table", DataTable)
    except Exception: return None


async def test_devices_view():
    config.LOCAL_CFG = {}
    fake = Fake()
    app = TApp(fake)
    app.local_player = None
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app.action_manage_devices()
        ok = await poll(pilot, lambda: dtable(app) is not None and dtable(app).row_count == 3)
        check("three devices listed", ok)
        pause_intervals(app)
        t = dtable(app)
        check("borrows #right's frame", app.right_panel.has_class("table-view"))
        check("title on the frame with the count", "Devices · 3" in str(app.right_panel.border_title),
              str(app.right_panel.border_title))
        check("no inner 'Devices' box", not [w for w in app.right_panel.children if isinstance(w, Static)])
        check("active device marked", str(t.get_row_at(0)[0]) != str(t.get_row_at(1)[0]))
        t.focus(); t.move_cursor(row=2); await pilot.pause()
        app._refresh_devices_table()
        await poll(pilot, lambda: True, timeout=0.5)
        await pilot.pause()
        check("refresh keeps the cursor", t.cursor_row == 2, str(t.cursor_row))
        # Filter then Enter on the filtered row.
        await pilot.press("slash"); await pilot.pause()
        for ch in "phone":
            await pilot.press(ch)
        await poll(pilot, lambda: t.row_count == 1, timeout=2.0)
        await pilot.press("down"); await pilot.pause()
        await pilot.press("enter")
        ok = await poll(pilot, lambda: fake.transfers)
        check("Enter on the filtered row transfers to it", fake.transfers[:1] == ["PH"], str(fake.transfers))
        # Open twice quickly: never two tables.
        app._open_devices_view(); app._open_devices_view()
        await poll(pilot, lambda: dtable(app) is not None and dtable(app).row_count == 3)
        check("opening twice leaves one table", len(app.query("#devices_table")) == 1)
        pause_intervals(app)


if __name__ == "__main__":
    asyncio.run(test_devices_view())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
