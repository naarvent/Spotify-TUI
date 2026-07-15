"""Deterministic end-to-end TUI tests via Textual's pilot.

Covers the two audited flows and the P1/P2 fixes:
  * action_toggle_favorite: track/album/artist/show/episode, save + remove.
  * _do_search + _render_search_results: every forced type, combined, empty,
    error, and the token-protection / off-UI-thread behaviour:
      - B finishes before A -> A must NOT overwrite B.
      - A finishes before B -> B stays visible (older A suppressed).
      - check_saved_tracks never runs on the main/UI thread.
      - the table may be unmounted before the worker finishes without error.
      - a check_saved_tracks failure does not wipe already-rendered results.

Synchronisation is event/poll based (the test coroutine yields to the event
loop until a worker sets an Event or a widget appears/updates). No fixed sleep
is used as the sole sync mechanism.

Run standalone:  python tests/test_tui_flows.py
Exit code is 0 only if every check passes.
"""
import os, sys, asyncio, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeInner:
    """Stand-in for spotipy.Spotify (what ensure() returns)."""
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.contains = False
        self.gate_enabled = False
        self.entered = {}       # q -> Event (search() reached)
        self.release = {}       # q -> Event (allow search() to return)
        self.mut = None
        self.mut_evt = threading.Event()

    def _rec(self, *a):
        with self.lock:
            self.calls.append(a)

    def _set_mut(self, *a):
        self.mut = a; self.mut_evt.set()

    def _entered(self, q):
        return self.entered.setdefault(q, threading.Event())

    def _release(self, q):
        return self.release.setdefault(q, threading.Event())

    def search(self, q, type=None, limit=None):
        self._rec("search", q, type)
        if self.gate_enabled and type == "track":
            self._entered(q).set()
            self._release(q).wait(10)
        return self._results_for(q, type)

    def _results_for(self, q, type):
        if type == "track":
            return {"tracks": {"items": [{"id": f"t_{q}", "uri": f"spotify:track:t_{q}",
                    "name": f"song::{q}", "type": "track",
                    "artists": [{"name": "artistX", "id": "a1"}],
                    "album": {"name": "albumX"}, "duration_ms": 123000}]}}
        if type == "artist":
            return {"artists": {"items": [{"id": f"ar_{q}", "uri": f"spotify:artist:ar_{q}",
                    "name": f"artist::{q}"}]}}
        if type == "album":
            return {"albums": {"items": [{"id": f"al_{q}", "uri": f"spotify:album:al_{q}",
                    "name": f"album::{q}", "album_type": "album",
                    "artists": [{"name": "artistX", "id": "a1"}]}]}}
        if type == "playlist":
            return {"playlists": {"items": [{"id": f"pl_{q}", "uri": f"spotify:playlist:pl_{q}",
                    "name": f"pl::{q}", "owner": {"display_name": "own"}}]}}
        if type == "episode":
            return {"episodes": {"items": [{"id": f"ep_{q}", "uri": f"spotify:episode:ep_{q}",
                    "name": f"ep::{q}", "duration_ms": 60000, "show": {"name": "showX"}}]}}
        if type == "show":
            return {"shows": {"items": [{"id": f"sh_{q}", "uri": f"spotify:show:sh_{q}",
                    "name": f"show::{q}", "publisher": "pubX"}]}}
        return {}

    def current_user_saved_tracks_contains(self, ids):
        self._rec("tracks_contains", tuple(ids)); return [self.contains] * len(ids)
    def current_user_saved_albums_contains(self, ids):
        self._rec("albums_contains", tuple(ids)); return [self.contains] * len(ids)
    def current_user_saved_shows_contains(self, ids):
        self._rec("shows_contains", tuple(ids)); return [self.contains] * len(ids)
    def current_user_saved_episodes_contains(self, ids):
        self._rec("episodes_contains", tuple(ids)); return [self.contains] * len(ids)
    def current_user_followed_artists(self, limit=50):
        self._rec("followed_artists")
        return {"artists": {"items": ([{"id": "ar_x"}] if self.contains else [])}}

    def current_user_saved_albums_add(self, ids): self._set_mut("album_add", tuple(ids))
    def current_user_saved_albums_delete(self, ids): self._set_mut("album_del", tuple(ids))
    def current_user_saved_shows_add(self, ids): self._set_mut("show_add", tuple(ids))
    def current_user_saved_shows_delete(self, ids): self._set_mut("show_del", tuple(ids))
    def current_user_saved_episodes_add(self, ids): self._set_mut("ep_add", tuple(ids))
    def current_user_saved_episodes_delete(self, ids): self._set_mut("ep_del", tuple(ids))
    def current_user_follow_artists(self, ids): self._set_mut("artist_follow", tuple(ids))
    def current_user_unfollow_artists(self, ids): self._set_mut("artist_unfollow", tuple(ids))


class FakeSpotify:
    def __init__(self):
        self.inner = FakeInner()
        self.playback = {}
        self.mut = None
        self.mut_evt = threading.Event()
        # check_saved_tracks instrumentation
        self.check_saved_thread = None
        self.cst_entered = None     # Event set when check_saved_tracks is entered
        self.cst_gate = None        # Event to block inside check_saved_tracks
        self.cst_raise = False      # raise inside check_saved_tracks

    def ensure(self): return self.inner
    def ensure_app(self): return self.inner
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return dict(self.playback)
    def fmt_duration(self, ms):
        s = int(round((ms or 0) / 1000)); m, s = divmod(s, 60); h, m = divmod(m, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"
    def _normalize_track_id(self, any_id):
        if not any_id: return None
        s = str(any_id).strip()
        if ":" in s: s = s.split(":")[-1]
        if "/" in s: s = s.rsplit("/", 1)[-1]
        return s or None
    def check_saved_tracks(self, ids):
        self.check_saved_thread = threading.get_ident()
        if self.cst_entered is not None: self.cst_entered.set()
        if self.cst_gate is not None: self.cst_gate.wait(10)
        if self.cst_raise: raise RuntimeError("saved lookup boom")
        return [self.inner.contains] * len(ids)
    def save_tracks(self, ids): self.mut = ("save_tracks", tuple(ids)); self.mut_evt.set(); return True
    def remove_tracks(self, ids): self.mut = ("remove_tracks", tuple(ids)); self.mut_evt.set(); return True


class TApp(SptPy):
    def __init__(self, fake):
        super().__init__()
        self.spotify = fake


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
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

async def pump(pilot, n=30):
    for _ in range(n):
        await pilot.pause()

def dispatch_search(app, q, raw, force=None):
    t = threading.Thread(target=app._do_search, args=(q, raw, force), daemon=True)
    t.start()
    return t

def find_search_table(app):
    from textual.widgets import DataTable
    try:
        for w in app.query(DataTable):
            if getattr(w, "id", "") == "search_table":
                return w
    except Exception:
        pass
    return None

def rendered_query(app):
    tbl = find_search_table(app)
    if tbl is None:
        return None
    rows = getattr(tbl, "_model_rows", None)
    if not rows:
        return None
    title = rows[0].get("title", "")
    return title.split("::", 1)[1] if "::" in title else None

def static_text(w):
    return str(getattr(w, "_Static__content", "") or "")

def table_refs_q(app, q):
    tbl = find_search_table(app)
    if tbl is None:
        return None
    rows = getattr(tbl, "_model_rows", None) or []
    if not rows:
        return None
    if any(q in str(r.get("id") or "") or q in str(r.get("uri") or "") for r in rows):
        return tbl
    return None

async def clear_and_settle(app, pilot):
    app._clear_right()
    for _ in range(50):
        await pilot.pause()
        if find_search_table(app) is None:
            return True
    return find_search_table(app) is None

def pause_intervals(app):
    for attr in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
                 "_devices_interval", "_queue_interval"):
        t = getattr(app, attr, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def test_toggle_favorites():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)
        cases = [
            ("track save",    {"type": "track",   "id": "tid",  "uri": "spotify:track:tid"},   False, ("save_tracks", ("tid",))),
            ("track remove",  {"type": "track",   "id": "tid",  "uri": "spotify:track:tid"},   True,  ("remove_tracks", ("tid",))),
            ("album save",    {"type": "album",   "id": "alid", "uri": "spotify:album:alid"},  False, ("album_add", ("alid",))),
            ("album remove",  {"type": "album",   "id": "alid", "uri": "spotify:album:alid"},  True,  ("album_del", ("alid",))),
            ("artist save",   {"type": "artist",  "id": "ar_x", "uri": "spotify:artist:ar_x"}, False, ("artist_follow", ("ar_x",))),
            ("artist remove", {"type": "artist",  "id": "ar_x", "uri": "spotify:artist:ar_x"}, True,  ("artist_unfollow", ("ar_x",))),
            ("show save",     {"type": "show",    "id": "shid", "uri": "spotify:show:shid"},   False, ("show_add", ("shid",))),
            ("show remove",   {"type": "show",    "id": "shid", "uri": "spotify:show:shid"},   True,  ("show_del", ("shid",))),
            ("episode save",  {"type": "episode", "id": "epid", "uri": "spotify:episode:epid"},False, ("ep_add", ("epid",))),
            ("episode remove",{"type": "episode", "id": "epid", "uri": "spotify:episode:epid"},True,  ("ep_del", ("epid",))),
        ]
        for label, item, contains, expected in cases:
            fake.inner.contains = contains
            fake.inner.mut = None; fake.inner.mut_evt.clear()
            fake.mut = None; fake.mut_evt.clear()
            fake.playback = {"item": item}
            app.action_toggle_favorite()
            got = await poll(lambda: fake.mut or fake.inner.mut, timeout=4.0)
            check(f"toggle {label} -> {expected[0]}", got == expected, f"got={got}")


async def test_search_types_and_edges():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)

        type_expect = {"track": "track", "artist": "artist", "album": "album",
                       "playlist": "playlist", "episode": "episode", "podcast": "podcast"}
        for ftype, rowtype in type_expect.items():
            fake.inner.contains = False
            await clear_and_settle(app, pilot)
            q = f"q{ftype}"
            dispatch_search(app, q, q, ftype)
            tbl = await poll(lambda: table_refs_q(app, q), timeout=4.0)
            ok = tbl is not None and any(r.get("type") == rowtype for r in (getattr(tbl, "_model_rows", []) or []))
            check(f"search force_type={ftype} renders {rowtype} rows", ok,
                  f"rows={[r.get('type') for r in (getattr(tbl,'_model_rows',[]) or [])]}")

        # combined
        fake.inner.contains = False
        await clear_and_settle(app, pilot)
        dispatch_search(app, "combo", "combo", None)
        tbl = await poll(lambda: table_refs_q(app, "combo"), timeout=4.0)
        types = set(r.get("type") for r in (getattr(tbl, "_model_rows", []) or [])) if tbl else set()
        check("combined search renders >=4 distinct types", len(types) >= 4, f"types={sorted(types)}")

        # empty
        await clear_and_settle(app, pilot)
        orig = fake.inner._results_for
        fake.inner._results_for = lambda q, type: {"tracks": {"items": []}} if type == "track" else {}
        dispatch_search(app, "none", "none", "track")
        tbl = await poll(lambda: find_search_table(app), timeout=4.0)
        fake.inner._results_for = orig
        rows = getattr(tbl, "_model_rows", None) if tbl else None
        check("empty search renders 0-row table (no crash)", tbl is not None and rows == [], f"rows={rows}")

        # error
        await clear_and_settle(app, pilot)
        good = fake.inner.search
        fake.inner.search = lambda q, type=None, limit=None: (_ for _ in ()).throw(RuntimeError("network down"))
        dispatch_search(app, "err", "err", "track")
        shown = await poll(lambda: "Error searching" in static_text(app.right_panel), timeout=4.0)
        fake.inner.search = good
        check("search error surfaces 'Error searching' (no crash)", bool(shown),
              f"panel={static_text(app.right_panel)!r}")


async def test_check_saved_off_ui_thread():
    """P2: check_saved_tracks must run on a worker, not the UI/event-loop thread."""
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)
        main_ident = threading.get_ident()
        fake.check_saved_thread = None
        await clear_and_settle(app, pilot)
        dispatch_search(app, "onui", "onui", "track")
        await poll(lambda: fake.check_saved_thread is not None, timeout=4.0)
        check("check_saved_tracks runs OFF the UI thread",
              fake.check_saved_thread is not None and fake.check_saved_thread != main_ident,
              f"cst={fake.check_saved_thread} main={main_ident}")


async def _run_order(app, pilot, fake, first):
    """Dispatch A(older) then B(newer) both blocked in search(); release `first`
    first, then the other. Returns the finally-rendered query."""
    fake.inner.contains = False
    fake.inner.gate_enabled = True
    fake.inner.entered.clear(); fake.inner.release.clear()
    await clear_and_settle(app, pilot)

    dispatch_search(app, "qA", "qA", "track")
    await poll(lambda: fake.inner.entered.get("qA") and fake.inner.entered["qA"].is_set(), timeout=4.0)
    dispatch_search(app, "qB", "qB", "track")
    await poll(lambda: fake.inner.entered.get("qB") and fake.inner.entered["qB"].is_set(), timeout=4.0)

    fake.inner._release(first).set()
    if first == "qB":
        # newer released first: it must render, then older completes and must NOT clobber it
        await poll(lambda: rendered_query(app) == "qB", timeout=4.0)
        fake.inner._release("qA").set()
        await pump(pilot, 40)
    else:
        # older released first: it must be suppressed (never paints qA)
        await pump(pilot, 40)
        suppressed = rendered_query(app) != "qA"
        fake.inner._release("qB").set()
        await poll(lambda: rendered_query(app) == "qB", timeout=4.0)
        await pump(pilot, 10)
        check("A(older) released first is suppressed (never paints qA)", suppressed,
              f"rendered_before_B={rendered_query(app)}")
    fake.inner.gate_enabled = False
    return rendered_query(app)


async def test_out_of_order_B_then_A():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)
        final = await _run_order(app, pilot, fake, first="qB")
        check("B before A: stale A does NOT overwrite B", final == "qB", f"final={final}")


async def test_out_of_order_A_then_B():
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)
        final = await _run_order(app, pilot, fake, first="qA")
        check("A before B: newer B stays visible", final == "qB", f"final={final}")


async def test_unmount_before_worker_finishes():
    """The saved-state worker may complete after the table is unmounted; this
    must not raise and must not touch the old table."""
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)
        await clear_and_settle(app, pilot)
        fake.inner.contains = False
        fake.cst_entered = threading.Event()
        fake.cst_gate = threading.Event()          # block check_saved_tracks
        dispatch_search(app, "unmnt", "unmnt", "track")
        tbl = await poll(lambda: table_refs_q(app, "unmnt"), timeout=4.0)
        check("unmount test: table rendered before worker finishes", tbl is not None)
        # Wait until the worker is inside check_saved_tracks, then unmount the table.
        await poll(lambda: fake.cst_entered.is_set(), timeout=4.0)
        await clear_and_settle(app, pilot)         # unmount the search table
        # Release the worker; its guarded apply() must early-return, no exception.
        fake.cst_gate.set()
        await pump(pilot, 40)
        applied = getattr(tbl, "_liked_map", "UNSET")
        check("unmount before worker finish: no liked map applied to stale table",
              applied == "UNSET", f"applied={applied}")
        # App still responsive: a fresh search renders.
        fake.cst_entered = None; fake.cst_gate = None
        dispatch_search(app, "after", "after", "track")
        ok = await poll(lambda: table_refs_q(app, "after"), timeout=4.0)
        check("unmount before worker finish: app still renders afterwards", ok is not None)


async def test_saved_error_does_not_wipe_results():
    """A check_saved_tracks failure must leave the already-rendered rows intact."""
    fake = FakeSpotify(); app = TApp(fake)
    async with app.run_test() as pilot:
        await pilot.pause(); pause_intervals(app)
        await clear_and_settle(app, pilot)
        fake.inner.contains = False
        fake.cst_entered = threading.Event()
        fake.cst_raise = True                       # check_saved_tracks raises
        dispatch_search(app, "keep", "keep", "track")
        tbl = await poll(lambda: table_refs_q(app, "keep"), timeout=4.0)
        check("error-preserve: rows rendered first", tbl is not None and len(getattr(tbl, "_model_rows", []) or []) == 1)
        await poll(lambda: fake.cst_entered.is_set(), timeout=4.0)  # worker attempted
        await pump(pilot, 40)                       # let the failing worker return
        rows_after = getattr(find_search_table(app), "_model_rows", None) if find_search_table(app) else None
        check("check_saved_tracks failure does NOT wipe rendered results",
              rows_after is not None and len(rows_after) == 1 and rendered_query(app) == "keep",
              f"rows_after={rows_after}")
        fake.cst_raise = False; fake.cst_entered = None


ALL = [test_toggle_favorites, test_search_types_and_edges, test_check_saved_off_ui_thread,
       test_out_of_order_B_then_A, test_out_of_order_A_then_B,
       test_unmount_before_worker_finishes, test_saved_error_does_not_wipe_results]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (tui flows) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
