"""Mutating a playlist drops its cached tracks.

The session cache (see test_playlist_cache) keeps a playlist's rows so reopening
it paints at once. Adding or removing a track makes that copy wrong, and the
remove path reopens the playlist right after, so the deleted track would flash
back on screen until the refresh landed. Every mutation drops the entry.

Run standalone:  python tests/test_playlist_cache_invalidation.py
"""
import os, sys, asyncio, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Fake:
    def __init__(self):
        self.added = []
        self.removed = []
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return (x or "").split(":")[-1]
    def add_items_to_playlist(self, playlist_id, uris):
        self.added.append((playlist_id, list(uris)))
        return True
    def playlist_remove_all_occurrences_of_items(self, playlist_id, uris):
        self.removed.append((playlist_id, list(uris)))
        return True


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake


def seed_cache(app, *pl_ids):
    """Pretend those playlists have been loaded already."""
    for pid in pl_ids:
        app._cache_playlist(pid, [{"id": f"{pid}-t0", "uri": f"spotify:track:{pid}-t0",
                                   "title": "song", "artist": "a", "album": "al",
                                   "dur": "0:00", "added": "x"}], [False])

def cache_keys(app):
    return sorted((getattr(app, "_playlist_cache", {}) or {}).keys())


async def test_helper_drops_one_entry():
    app = TApp(Fake())
    async with app.run_test(size=(120, 25)) as pilot:
        await pilot.pause()
        seed_cache(app, "pl1", "pl2")
        check("both playlists start cached", cache_keys(app) == ["pl1", "pl2"], f"{cache_keys(app)}")
        app._invalidate_playlist_cache("pl1")
        check("the mutated playlist is dropped", cache_keys(app) == ["pl2"], f"{cache_keys(app)}")
        check("the others are untouched", "pl2" in cache_keys(app))
        app._invalidate_playlist_cache("nope")     # unknown id must not raise
        app._invalidate_playlist_cache("")         # nor an empty one
        check("invalidating an unknown id is harmless", cache_keys(app) == ["pl2"],
              f"{cache_keys(app)}")


async def test_adding_a_track_invalidates():
    fake = Fake(); app = TApp(fake)
    async with app.run_test(size=(120, 25)) as pilot:
        await pilot.pause()
        seed_cache(app, "pl1", "pl2")
        app._pending_add_uri = "spotify:track:new1"
        app._pending_multi_add_uris = None
        ok = app.spotify.add_items_to_playlist("pl1", ["spotify:track:new1"])
        app._invalidate_playlist_cache("pl1")
        check("the add went through", ok and fake.added, f"added={fake.added}")
        check("the target playlist leaves the cache", cache_keys(app) == ["pl2"],
              f"{cache_keys(app)}")


async def test_removing_a_track_invalidates_before_the_reopen():
    """The remove path reopens the playlist, so the entry must already be gone
    or the reopen paints the deleted track straight back."""
    fake = Fake(); app = TApp(fake)
    async with app.run_test(size=(120, 25)) as pilot:
        await pilot.pause()
        seed_cache(app, "pl1")
        order = []
        real_invalidate = app._invalidate_playlist_cache
        real_open = app._open_playlist_table
        app._invalidate_playlist_cache = lambda pid: (order.append(("invalidate", pid)),
                                                      real_invalidate(pid))[1]
        app._open_playlist_table = lambda pdata: order.append(("open", pdata.get("id")))

        app._remove_track_from_playlist("pl1", "spotify:track:pl1-t0",
                                        {"id": "pl1", "name": "L", "uri": "spotify:playlist:pl1"})
        loop = asyncio.get_event_loop(); end = loop.time() + 5
        while loop.time() < end and not fake.removed:
            await asyncio.sleep(0.01)
        end = loop.time() + 3
        while loop.time() < end and len(order) < 2:
            await asyncio.sleep(0.01)

        check("the remove reached the API", bool(fake.removed), f"removed={fake.removed}")
        check("the cache entry is dropped", "pl1" not in cache_keys(app), f"{cache_keys(app)}")
        kinds = [k for k, _ in order]
        check("invalidation happens before the reopen",
              "invalidate" in kinds and "open" in kinds and
              kinds.index("invalidate") < kinds.index("open"), f"order={order}")
        app._invalidate_playlist_cache = real_invalidate
        app._open_playlist_table = real_open


ALL = [test_helper_drops_one_entry, test_adding_a_track_invalidates,
       test_removing_a_track_invalidates_before_the_reopen]

async def main():
    for fn in ALL:
        try:
            await fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (playlist cache invalidation) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
