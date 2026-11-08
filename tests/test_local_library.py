"""LocalLibrary (the on-disk library for account-less providers): likes, saved
albums/artists, local playlists, history, availability marks, atomic and
concurrent writes, corrupt-file handling and per-provider separation.
Also the config / playlist-cache writers that used to share one `.tmp`.

Run standalone:  python tests/test_local_library.py
"""
import os, sys, json, tempfile, shutil, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config
from spt_tui.local_library import LocalLibrary, atomic_write_json, RECENT_MAX

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class tmpdir:
    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="spt_lib_")
        self.old = (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG)
        config.CACHE_DIR = self.d
        config.CONFIG_PATH = os.path.join(self.d, "spt_config.json")
        config.LOCAL_CFG = {}
        return self.d
    def __exit__(self, *a):
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG = self.old
        shutil.rmtree(self.d, ignore_errors=True)


T1 = {"id": "v1", "uri": "ytmusic:track:v1", "name": "Tune", "artists": [{"name": "A", "id": "UC1"}],
      "album": {"name": "Rec", "id": "MP1"}, "duration_ms": 1000, "junk": "x" * 100}
T2 = {"id": "v2", "uri": "ytmusic:track:v2", "name": "Two", "artists": [], "album": {"name": ""}, "duration_ms": 2000}


def test_likes_and_saved():
    with tmpdir() as d:
        lib = LocalLibrary("ytmusic")
        check("file lives next to spt_config.json, per provider",
              lib.path == os.path.join(d, "local_library_ytmusic.json"))
        lib.add("tracks", [T1, T2])
        check("contains is aligned", lib.contains("tracks", ["v2", "zz", "v1"]) == [True, False, True])
        items = lib.items("tracks")
        check("newest first", [t["id"] for t in items] == ["v2", "v1"])
        check("only a minimal metadata copy is stored", "junk" not in items[1]
              and items[1]["artists"][0]["name"] == "A" and items[1]["duration_ms"] == 1000)
        lib.add("tracks", [T1])
        check("re-adding is a no-op", len(lib.items("tracks")) == 2)
        lib.remove("tracks", ["v1"])
        check("remove deletes only that entry", [t["id"] for t in lib.items("tracks")] == ["v2"])
        lib.add("albums", [{"id": "MP1", "uri": "ytmusic:album:MP1", "name": "Rec", "artists": [{"name": "A"}]}])
        lib.add("artists", [{"id": "UC1", "uri": "ytmusic:artist:UC1", "name": "A"}])
        again = LocalLibrary("ytmusic")
        check("persisted and re-read by a new instance (provider switch and back)",
              again.contains("albums", ["MP1"]) == [True] and again.contains("artists", ["UC1"]) == [True]
              and again.contains("tracks", ["v2"]) == [True])
        other = LocalLibrary("subsonic")
        check("stores are separate per provider", other.contains("tracks", ["v2"]) == [False]
              and other.path != lib.path)


def test_playlists():
    with tmpdir():
        lib = LocalLibrary("ytmusic")
        pl = lib.create_playlist("Mine", "desc")
        check("local playlist id is local:<uuid>", pl["id"].startswith("local:") and len(pl["id"]) > 20)
        lib.add_tracks(pl["id"], [T1, T2, T1])
        check("add tracks (duplicates allowed, in order)",
              [t["id"] for t in lib.playlist(pl["id"])["tracks"]] == ["v1", "v2", "v1"])
        lib.remove_positions(pl["id"], [2])
        check("remove one position only", [t["id"] for t in lib.playlist(pl["id"])["tracks"]] == ["v1", "v2"])
        check("rename", lib.rename_playlist(pl["id"], "Renamed") and lib.playlist(pl["id"])["name"] == "Renamed")
        check("rename refuses an empty name", not lib.rename_playlist(pl["id"], ""))
        check("find_track resolves playlist tracks offline", lib.find_track("v2")["name"] == "Two")
        check("delete", lib.delete_playlist(pl["id"]) and lib.playlist(pl["id"]) is None)
        check("deleting twice is False", not lib.delete_playlist(pl["id"]))


def test_recent():
    with tmpdir():
        lib = LocalLibrary("ytmusic")
        for i in range(RECENT_MAX + 10):
            lib.record_played({"id": f"t{i}", "uri": f"ytmusic:track:t{i}", "name": f"n{i}"})
        rec = lib.recent(100)
        check(f"history bounded to {RECENT_MAX}", len(rec) == RECENT_MAX)
        check("newest first", rec[0]["track"]["id"] == f"t{RECENT_MAX + 9}")
        lib.record_played({"id": f"t{RECENT_MAX + 9}", "name": "same"})
        check("consecutive repeat not duplicated", len(lib.recent(100)) == RECENT_MAX
              and lib.recent(2)[1]["track"]["id"] != f"t{RECENT_MAX + 9}")


def test_unavailable_never_deletes():
    with tmpdir():
        lib = LocalLibrary("ytmusic")
        lib.add("tracks", [T1])
        lib.mark_unavailable(T1["uri"], "Video unavailable")
        check("marked unavailable", lib.unavailable_reason(T1["uri"]) == "Video unavailable")
        check("…but still saved", lib.contains("tracks", ["v1"]) == [True])
        lib.clear_unavailable(T1["uri"])
        check("mark cleared when it works again", lib.unavailable_reason(T1["uri"]) is None)


def test_unavailable_marks_expire():
    """A mark hides a track only while it is fresh; one from a hiccup must not hide it forever."""
    with tmpdir():
        t = [1_000_000.0]
        lib = LocalLibrary("ytmusic", now=lambda: t[0])
        lib.mark_unavailable("u1", "boom")
        check("fresh mark counts", lib.unavailable_recent("u1") == "boom")
        t[0] += 23 * 3600
        check("still fresh after 23 h", lib.unavailable_recent("u1") == "boom")
        t[0] += 2 * 3600
        check("stale after 25 h: no longer hides the track", lib.unavailable_recent("u1") is None)
        check("…but the reason is still recorded (shown in the list)", lib.unavailable_reason("u1") == "boom")
        lib.mark_unavailable("u1", "boom")
        check("marking again refreshes the date", lib.unavailable_recent("u1") == "boom")
        lib.clear_unavailable("u1")
        check("clear removes mark and date", lib.unavailable_reason("u1") is None
              and "u1" not in lib._load()["unavailable_at"])
        # file written by an older version: reasons without dates
        with open(lib.path, "w") as f:
            json.dump({"version": 1, "tracks": [], "albums": [], "artists": [], "playlists": [],
                       "recent": [], "unavailable": {"old": "Video unavailable"}}, f)
        lib2 = LocalLibrary("ytmusic", now=lambda: t[0])
        check("old file: mark kept, but undated marks never skip", lib2.unavailable_reason("old") == "Video unavailable"
              and lib2.unavailable_recent("old") is None)


def test_corrupt_file_kept_aside():
    with tmpdir() as d:
        p = os.path.join(d, "local_library_ytmusic.json")
        open(p, "w").write("{not json")
        lib = LocalLibrary("ytmusic")
        check("corrupt file -> empty library, no crash", lib.items("tracks") == [])
        kept = [f for f in os.listdir(d) if ".corrupt-" in f]
        check("corrupt file kept aside, not deleted", len(kept) == 1, repr(os.listdir(d)))
        lib.add("tracks", [T1])
        check("new file written", json.load(open(p))["tracks"][0]["id"] == "v1")


def test_concurrent_writes():
    with tmpdir() as d:
        lib = LocalLibrary("ytmusic")
        errs = []
        def w(i):
            try:
                for j in range(20):
                    lib.add("tracks", [{"id": f"t{i}-{j}", "uri": f"u{i}-{j}", "name": "x"}])
            except Exception as exc:
                errs.append(exc)
        ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        check("8 threads x 20 saves: no error", not errs, repr(errs[:2]))
        check("all saves present", len(LocalLibrary("ytmusic").items("tracks")) == 160)
        check("no stray temp files", not [f for f in os.listdir(d) if f.endswith(".tmp")], repr(os.listdir(d)))

        # The bug from the log: two writers sharing "<path>.tmp".
        path = os.path.join(d, "playlists_cache_ytmusic.json")
        errs2 = []
        def wc(i):
            try:
                for _ in range(30):
                    atomic_write_json(path, [{"i": i}])
            except Exception as exc:
                errs2.append(exc)
        ts = [threading.Thread(target=wc, args=(i,)) for i in range(6)]
        for t in ts: t.start()
        for t in ts: t.join()
        check("concurrent playlist-cache writes: no FileNotFoundError", not errs2, repr(errs2[:2]))
        check("result is valid JSON", isinstance(json.load(open(path)), list))

        errs3 = []
        def cfgw(i):
            try:
                for _ in range(20):
                    config.save_local_config({"k": i})
            except Exception as exc:
                errs3.append(exc)
        ts = [threading.Thread(target=cfgw, args=(i,)) for i in range(6)]
        for t in ts: t.start()
        for t in ts: t.join()
        check("concurrent config saves: valid file, no leftovers", not errs3
              and "k" in json.load(open(config.CONFIG_PATH))
              and not [f for f in os.listdir(d) if f.endswith(".tmp")])


ALL = [test_likes_and_saved, test_playlists, test_recent, test_unavailable_never_deletes, test_unavailable_marks_expire,
       test_corrupt_file_kept_aside, test_concurrent_writes]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (local_library) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
