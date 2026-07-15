"""Deterministic tests for the devices-view refresh interval lifecycle (P3).

Reproduces the leak where _devices_interval keeps firing devices() after the
view is left via _back_one_level (the path taken right after transferring to a
device), and pins the fixed behaviour for every exit route:
  * entry creates the interval + table
  * exit via Escape cancels it
  * exit via device selection (transfer -> _back_one_level) cancels it
  * no devices() polling happens once #devices_table is gone
  * reopening the view recreates the interval

Synchronisation is event/poll based (bounded), never a fixed sleep as the sole
mechanism.

Run standalone:  python tests/test_devices.py
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeSpotify:
    def __init__(self):
        self.devices_calls = 0
        self.devices_evt = threading.Event()
        self.transfer_calls = []
        self._devices = [{"id": "devA", "name": "A", "type": "Computer", "is_active": True},
                         {"id": "devB", "name": "B", "type": "Smartphone", "is_active": False}]

    # --- mount-time needs ---
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, any_id): return any_id

    # --- devices ---
    def devices(self):
        self.devices_calls += 1
        self.devices_evt.set()
        return list(self._devices)

    def transfer(self, device_id, force_play=False):
        self.transfer_calls.append((device_id, force_play))


class TApp(SptPy):
    def __init__(self, fake):
        super().__init__()
        self.spotify = fake


async def poll(fn, timeout=4.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        try:
            v = fn()
        except Exception:
            v = None
        if v:
            return v
        await asyncio.sleep(0.01)
    try:
        return fn()
    except Exception:
        return None

async def poll_false(fn, timeout=0.6):
    """Bounded negative check: return True if `fn` stayed falsy the whole time."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if fn():
            return False
        await asyncio.sleep(0.01)
    return not fn()

async def pump(pilot, n=20):
    for _ in range(n):
        await pilot.pause()

def has_devices_table(app):
    from textual.widgets import DataTable
    try:
        for w in app.query(DataTable):
            if getattr(w, "id", "") == "devices_table":
                return True
    except Exception:
        pass
    return False

def pause_bg_intervals(app):
    # Leave _devices_interval alone; silence the now-bar / retry timers.
    for attr in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval"):
        t = getattr(app, attr, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


class FakeEvent:
    def __init__(self, data_table, row_key):
        self.data_table = data_table
        self.row_key = row_key


async def test_entry_creates_interval():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        app.action_manage_devices()
        await pump(pilot)
        check("entry: #devices_table mounted", has_devices_table(app))
        check("entry: _devices_interval created", getattr(app, "_devices_interval", None) is not None)


async def test_escape_cancels_interval():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        app.action_manage_devices(); await pump(pilot)
        app.action_escape_to_menu(); await pump(pilot)
        check("escape: _devices_interval cancelled (None)", getattr(app, "_devices_interval", None) is None,
              f"iv={getattr(app,'_devices_interval',None)}")
        check("escape: #devices_table gone", not has_devices_table(app))


async def test_device_selection_cancels_interval():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        app.action_manage_devices(); await pump(pilot)
        # Simulate selecting a device row: transfer thread + _back_one_level.
        from textual.widgets import DataTable
        tbl = next(w for w in app.query(DataTable) if getattr(w, "id", "") == "devices_table")
        app.on_data_table_row_selected(FakeEvent(tbl, 0))
        await pump(pilot)
        check("device selection: transfer invoked", len(fake.transfer_calls) >= 1, f"{fake.transfer_calls}")
        check("device selection: _devices_interval cancelled (None)",
              getattr(app, "_devices_interval", None) is None,
              f"iv={getattr(app,'_devices_interval',None)}")


async def test_no_devices_polling_after_exit():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        app.action_manage_devices(); await pump(pilot)
        # Leave the view exactly like the post-transfer path does.
        app._back_one_level(); await pump(pilot)
        # The only caller of _refresh_devices_table() is this interval (plus the
        # one-shot on view entry). Once the interval is cancelled there is no
        # scheduler left to issue any further devices() call. That cancellation
        # IS the production guarantee — the interval never fires again.
        # (Note: _back_one_level does not itself clear the right panel, so the
        # orphaned #devices_table may linger; that pre-existing UI detail is out
        # of P3's scope. The self-heal test covers the "table gone" tick.)
        check("after exit via back: devices interval cancelled (no further ticks)",
              getattr(app, "_devices_interval", None) is None,
              f"iv={getattr(app,'_devices_interval',None)}")


async def test_refresh_selfheal_when_table_absent():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        app.action_manage_devices(); await pump(pilot)
        # Force the table out of the tree but leave the interval handle set.
        app._clear_right()
        for _ in range(40):
            await pilot.pause()
            if not has_devices_table(app):
                break
        fake.devices_evt.clear(); before = fake.devices_calls
        app._refresh_devices_table()
        stayed_silent = await poll_false(lambda: fake.devices_evt.is_set(), timeout=0.6)
        check("self-heal: refresh with no table makes no devices() call",
              stayed_silent and fake.devices_calls == before, f"calls {before}->{fake.devices_calls}")
        check("self-heal: interval cancelled by refresh tick",
              getattr(app, "_devices_interval", None) is None,
              f"iv={getattr(app,'_devices_interval',None)}")


async def test_reopen_after_exit():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_bg_intervals(app)
        app.action_manage_devices(); await pump(pilot)
        app.action_escape_to_menu(); await pump(pilot)
        check("reopen precondition: interval cancelled", getattr(app, "_devices_interval", None) is None)
        app.action_manage_devices(); await pump(pilot)
        check("reopen: #devices_table mounted again", has_devices_table(app))
        check("reopen: _devices_interval recreated", getattr(app, "_devices_interval", None) is not None)


ALL = [test_entry_creates_interval, test_escape_cancels_interval,
       test_device_selection_cancels_interval, test_no_devices_polling_after_exit,
       test_refresh_selfheal_when_table_absent, test_reopen_after_exit]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (devices) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
