"""Add-to-playlist works on every row, not just plain tracks.

A track/episode row adds that one item. A container row selected from a listing
(album, single, playlist, artist, podcast) gathers every track/episode it holds
and adds them all; a big container (> _BULK_ADD_CONFIRM_MIN) asks for
confirmation first. This is what "Ctrl+Shift+P works everywhere" means.

Run standalone:  python tests/test_add_to_playlist.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from textual.widgets import DataTable, ListView

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def __init__(self):
        self.added = []
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0):
        return {"items": [{"id": "P", "name": "MyPlaylist", "uri": "spotify:playlist:P"}], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def _normalize_track_id(self, x): return (x or "").split(":")[-1]
    def fmt_duration(self, ms): return "0:00"

    # container gathering
    def album_tracks(self, album_id, limit=50, offset=0):
        if offset: return {"items": [], "next": None}
        n = {"alb-small": 5}.get(album_id, 20)   # discography albums = 20 each
        return {"items": [{"uri": f"spotify:track:{album_id}-t{i}"} for i in range(n)], "next": None}
    def artist_albums(self, artist_id, album_type="album", limit=50, offset=0):
        if offset: return {"items": [], "next": None}
        if album_type == "album":
            return {"items": [{"id": "dA"}, {"id": "dB"}], "next": None}   # 2 * 20 = 40
        return {"items": [], "next": None}
    def show_episodes(self, show_id, limit=50, offset=0):
        if offset: return {"items": [], "next": None}
        return {"items": [{"uri": f"spotify:episode:{show_id}-e{i}"} for i in range(8)], "next": None}
    def playlist_items(self, pid, limit=100, offset=0, fields=None):
        if offset: return {"items": [], "next": None}
        n = 40 if pid == "pl-big" else 5
        return {"items": [{"track": {"uri": f"spotify:track:{pid}-t{i}"}} for i in range(n)], "next": None}
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)

    def add_items_to_playlist(self, playlist_id, uris):
        self.added.append((playlist_id, list(uris)))
        return True


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake; self.playlists_cache = None


async def poll(fn, timeout=6.0):
    loop = asyncio.get_event_loop(); end = loop.time() + timeout
    while loop.time() < end:
        try: v = fn()
        except Exception: v = None
        if v: return v
        await asyncio.sleep(0.02)
    try: return fn()
    except Exception: return None


def find(app, cls, tid=None):
    for w in app.query(cls):
        if tid is None or getattr(w, "id", "") == tid:
            return w
    return None


async def open_row(app, pilot, rtype, rid, uri, tracks_table=False):
    """Render a one-row table (search-style, or a plain tracks_table) with the
    cursor on it and fire add-to-playlist."""
    app._pending_add_uri = None
    app._pending_multi_add_uris = None
    app._pending_container_add = None
    row = {"type": rtype, "id": rid, "uri": uri, "title": "Item",
           "artist": "a", "album": "al", "dur": "0:00", "added": "x", "raw": {"id": rid}}
    if tracks_table:
        app._render_tracks_table("[b]T[/b]", [row], [False], context_uris=[uri])
        tid = "tracks_table"
    else:
        app._render_search_table("[b]S[/b]", [row], check_saved=False)
        tid = "search_table"
    t = await poll(lambda: find(app, DataTable, tid))
    t.focus()
    try: t.move_cursor(row=0)
    except Exception: pass
    await pilot.pause()
    app.action_add_to_playlist()
    await poll(lambda: app._pending_add_uri or app._pending_multi_add_uris
               or (app._right_view and app._right_view[0] == "confirm_bulk_add"), timeout=5.0)
    await pilot.pause()


async def test_track_row_adds_the_single_track():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await open_row(app, pilot, "track", "tk1", "spotify:track:tk1")
        check("track -> single pending uri", app._pending_add_uri == "spotify:track:tk1",
              f"{app._pending_add_uri}")
        check("track -> no bulk", not app._pending_multi_add_uris)
        check("track -> playlist picker shown", find(app, ListView, "add_pl_list") is not None)


async def test_tracks_table_row_has_no_type_but_still_adds():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await open_row(app, pilot, "track", "tk9", "spotify:track:tk9", tracks_table=True)
        check("tracks_table row (no row_to_type) -> single add",
              app._pending_add_uri == "spotify:track:tk9", f"{app._pending_add_uri}")


async def test_episode_row_adds_the_episode():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await open_row(app, pilot, "episode", "ep1", "spotify:episode:ep1")
        check("episode -> single pending uri", app._pending_add_uri == "spotify:episode:ep1",
              f"{app._pending_add_uri}")


async def test_small_album_gathers_its_tracks_without_confirm():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await open_row(app, pilot, "album", "alb-small", "spotify:album:alb-small")
        multi = app._pending_multi_add_uris or []
        check("small album -> all 5 tracks queued", len(multi) == 5, f"n={len(multi)}")
        check("small album -> no confirm screen",
              not (app._right_view and app._right_view[0] == "confirm_bulk_add"))
        check("small album -> picker shown", find(app, ListView, "add_pl_list") is not None)
        check("small album -> real track uris",
              all(u.startswith("spotify:track:alb-small-") for u in multi), f"{multi[:2]}")


async def test_podcast_gathers_episodes():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await open_row(app, pilot, "podcast", "sh1", "spotify:show:sh1")
        multi = app._pending_multi_add_uris or []
        check("podcast -> 8 episode uris", len(multi) == 8 and
              all(u.startswith("spotify:episode:") for u in multi), f"n={len(multi)}")


async def test_artist_gathers_full_discography():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await open_row(app, pilot, "artist", "art1", "spotify:artist:art1")
        check("artist -> confirm screen (40 > 25)",
              app._right_view and app._right_view[0] == "confirm_bulk_add",
              f"rv={app._right_view}")
        pend = app._pending_container_add or {}
        uris = pend.get("uris", [])
        check("artist -> 40 tracks across album+single, deduped",
              len(uris) == 40 and len(set(uris)) == 40, f"n={len(uris)}")


async def test_big_container_confirms_then_enter_opens_picker_and_adds():
    fake = Fake(); app = TApp(fake)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await open_row(app, pilot, "playlist", "pl-big", "spotify:playlist:pl-big")
        check("big playlist -> confirm screen",
              app._right_view and app._right_view[0] == "confirm_bulk_add", f"rv={app._right_view}")
        check("big playlist -> nothing queued before confirming", not app._pending_multi_add_uris)
        # Enter on the confirm screen goes through the real on_key handler.
        await pilot.press("enter")
        picker = await poll(lambda: find(app, ListView, "add_pl_list"))
        check("confirm + Enter -> picker shown", picker is not None)
        check("confirm + Enter -> 40 tracks queued",
              len(app._pending_multi_add_uris or []) == 40, f"n={len(app._pending_multi_add_uris or [])}")
        check("confirm cleared", app._pending_container_add is None)
        # Pick the playlist and add for real.
        picker.index = 0
        await pilot.press("enter")
        await poll(lambda: fake.added, timeout=5.0)
        check("the bulk add reached the API", bool(fake.added), f"added={len(fake.added)}")
        if fake.added:
            pid, uris = fake.added[0]
            check("added to the chosen playlist with 40 uris",
                  pid == "P" and len(uris) == 40, f"pid={pid} n={len(uris)}")


async def test_escape_abandons_the_confirm():
    app = TApp(Fake())
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await open_row(app, pilot, "playlist", "pl-big", "spotify:playlist:pl-big")
        check("confirm is armed", app._pending_container_add is not None)
        app.action_escape_to_menu()
        await pilot.pause()
        check("escape clears the pending confirm", app._pending_container_add is None)


ALL = [test_track_row_adds_the_single_track, test_tracks_table_row_has_no_type_but_still_adds,
       test_episode_row_adds_the_episode, test_small_album_gathers_its_tracks_without_confirm,
       test_podcast_gathers_episodes, test_artist_gathers_full_discography,
       test_big_container_confirms_then_enter_opens_picker_and_adds,
       test_escape_abandons_the_confirm]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (add to playlist) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
