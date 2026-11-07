"""Offline downloads (YouTube Music, Subsonic).

* OfflineStore: files land in <root>, index survives a reload, LRU pruning to
  the cap (the newest copy is kept), a file bigger than the cap is refused, a
  copy deleted by hand is forgotten, ids become safe file names;
* providers: download_track writes through .partial/ and cleans it on failure;
  playing a downloaded track hands mpv the LOCAL FILE and makes no network
  call (no ytmusicapi, no resolver, no HTTP session), also after a "restart"
  (metadata from the store); Subsonic falls back from `download` to `stream`
  and keeps no secret in paths; capabilities and folder per server+user;
* UI: `o` downloads / deletes, the ↓ mark, `O` downloads the visible (filtered)
  rows only, Library > Downloaded (only with can_download), Settings cap.

No network, no audio, no real yt-dlp.  Run standalone:  python tests/test_offline.py
"""
import os, sys, asyncio, tempfile, shutil
from urllib.parse import urlparse

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_offline_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config, offline
from spt_tui.offline import OfflineStore, OfflineError, safe_name
from spt_tui.providers.base import ProviderError, Capabilities
from spt_tui.providers.ytmusic import YTMusicProvider
from spt_tui.providers.subsonic import SubsonicProvider
from spt_tui.local_library import LocalLibrary
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

MB = 1024 * 1024


def write(path, size):
    with open(path, "wb") as fh:
        fh.write(b"\0" * size)
    return path


def T(i):
    return {"id": f"v{i}", "uri": f"ytmusic:track:v{i}", "type": "track", "name": f"Song {i}",
            "artists": [{"name": "A"}], "album": {"name": "L"}, "duration_ms": 1000}


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self):
        self.t += 1; return self.t


def test_store():
    root = tempfile.mkdtemp(prefix="spt_off_store_")
    clock = Clock()
    st = OfflineStore("x", root=root, max_bytes=60 * MB, now=clock)
    tmp = tempfile.mkdtemp()
    for i in range(3):
        st.add(T(i), write(os.path.join(tmp, f"f{i}.webm"), 20 * MB))
    check("three 20 MB copies fit in 60 MB", sorted(st.ids()) == ["v0", "v1", "v2"] and st.total_bytes() == 60 * MB)
    check("file kept with its extension", st.path_for("v0").endswith("v0.webm") and os.path.isfile(st.path_for("v0")))
    st.touch("v0")                       # v0 used recently: v1 is now the oldest
    st.add(T(3), write(os.path.join(tmp, "f3.webm"), 20 * MB))
    check("over the cap: least recently used deleted first", sorted(st.ids()) == ["v0", "v2", "v3"], str(st.ids()))
    check("its file is gone too", not os.path.exists(os.path.join(root, "v1.webm")))
    raised = False
    try:
        st.add(T(9), write(os.path.join(tmp, "big.webm"), 61 * MB))
    except OfflineError:
        raised = True
    check("a file bigger than the cap is refused (and deleted)", raised and not os.path.exists(os.path.join(tmp, "big.webm")))
    st2 = OfflineStore("x", root=root, max_bytes=60 * MB)
    check("index survives a reload", sorted(st2.ids()) == ["v0", "v2", "v3"])
    check("stored metadata", st2.track("v2")["name"] == "Song 2" and st2.track("v2")["uri"] == "ytmusic:track:v2")
    os.remove(st2.path_for("v2"))
    check("a copy deleted by hand is forgotten", st2.path_for("v2") is None and not st2.has("v2"))
    check("remove()", st2.remove("v0") and not st2.has("v0") and not st2.remove("v0"))
    removed = st2.prune(limit=1)
    check("prune(limit) for a smaller cap", removed == ["v3"] and st2.ids() == [], str(removed))
    check("safe file names", safe_name("abc_D-9") == "abc_D-9" and "/" not in safe_name("a/b:c")
          and safe_name("a/b") != safe_name("a_b"))
    config.LOCAL_CFG = {"offline_max_bytes": 1}
    check("configured cap is clamped (floor 50 MB)", offline.configured_limit() == offline.OFFLINE_MIN_BYTES)
    config.LOCAL_CFG = {}
    check("default cap 1 GB", offline.configured_limit() == 1024 * MB)
    check("default folder under CACHE_DIR/offline/<provider>",
          offline.offline_root("ytmusic") == os.path.join(config.CACHE_DIR, "offline", "ytmusic"))


class FakePlayer:
    def __init__(self):
        self.loaded = None; self.path = None
        self.st = {"running": False, "idle": True, "paused": True, "position": 0.0, "duration": 0.0,
                   "index": -1, "volume": 70}
    def unavailable_reason(self): return None
    def load(self, urls, start_index=0):
        self.loaded = (list(urls), start_index); self.path = urls[start_index]
        self.st.update(running=True, idle=False, paused=False, index=start_index)
    def is_running(self): return self.st["running"]
    def get(self, prop, default=None): return self.path if prop == "path" else default
    def state(self): return dict(self.st)
    def playlist_entries(self):
        urls, cur = self.loaded or ([], 0)
        return [{"filename": u, "current": i == cur, "id": i + 1} for i, u in enumerate(urls)]
    def close(self): pass


class NoNetwork:
    """Any attribute access means the code reached for the network."""
    def __init__(self): self.touched = []
    def __getattr__(self, name):
        self.touched.append(name)
        raise AssertionError(f"network used: {name}")


class Resolver:
    """Records what the stream resolver was asked to resolve (that would be network)."""
    def __init__(self): self.touched = []
    def get(self, url): self.touched.append(("get", url)); return None
    def prefetch(self, urls):
        urls = list(urls or [])
        if urls:
            self.touched.append(("prefetch", urls))
    def cancel_pending(self): pass
    def resolve_now(self, url): self.touched.append(("resolve_now", url)); return None
    def close(self): pass


def yt(tmpdir, client=None, downloader=None, resolver=None):
    lib = LocalLibrary("ytmusic", path=os.path.join(tmpdir, "lib.json"))
    store = OfflineStore("ytmusic", root=os.path.join(tmpdir, "off"), max_bytes=100 * MB)
    p = YTMusicProvider(client=client or NoNetwork(), player=FakePlayer(), library=lib,
                        stream_resolver=resolver, offline=store, downloader=downloader,
                        which=lambda n: "/usr/bin/" + n)
    return p


def test_ytmusic():
    tmpdir = tempfile.mkdtemp(prefix="spt_off_yt_")
    calls = []
    def downloader(url, base):
        calls.append(url)
        return write(base + ".webm", 3 * MB)
    p = yt(tmpdir, downloader=downloader)
    check("ytmusic with a store: can_download", p.capabilities.can_download)
    p._remember(T(1))
    path = p.download_track(T(1))
    check("download through yt-dlp's watch URL", calls == ["https://music.youtube.com/watch?v=v1"], str(calls))
    check("stored in the provider folder", path.startswith(os.path.join(tmpdir, "off")) and os.path.isfile(path))
    check(".partial is empty afterwards", not os.listdir(os.path.join(tmpdir, "off", ".partial")))
    check("is_downloaded", p.is_downloaded("v1") and not p.is_downloaded("v2"))
    p.download_track(T(1))
    check("downloading again does nothing", len(calls) == 1)

    client, resolver = NoNetwork(), Resolver()
    p2 = yt(tmpdir, client=client, resolver=resolver)          # "restart": new provider, same store
    p2.start_playback(uris=["ytmusic:track:v1"])
    loaded = p2._player.loaded
    check("after a restart, a downloaded track plays from the local file",
          loaded and loaded[0] == [path], str(loaded))
    check("…without ytmusicapi or the stream resolver (no network)",
          client.touched == [] and resolver.touched == [], f"{client.touched} {resolver.touched}")
    check("…with its stored metadata", p2._current_track() and p2._current_track().get("name") == "Song 1")
    p2._prefetch_tick()
    check("prefetch skips local files", resolver.touched == [], str(resolver.touched))

    def failing(url, base):
        write(base + ".webm.part", 10)
        raise RuntimeError("HTTP Error 403")
    p3 = yt(tmpdir, downloader=failing)
    raised = None
    try:
        p3.download_track(T(5))
    except ProviderError as exc:
        raised = str(exc)
    check("a failed download is a ProviderError", raised and "Download failed" in raised, str(raised))
    check("…and leaves no partial file", not os.listdir(os.path.join(tmpdir, "off", ".partial")))
    check("remove_download", p.remove_download("v1") and not p.is_downloaded("v1") and not os.path.exists(path))
    p4 = YTMusicProvider(client=NoNetwork(), player=FakePlayer(), offline=None)
    check("without a store: no can_download", not p4.capabilities.can_download)


class Resp:
    def __init__(self, body=None, ctype="application/json", data=b"", status=200):
        self.body, self.status_code, self.data = body, status, data
        self.headers = {"Content-Type": ctype}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self): return self.body
    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.data), 1000):
            yield self.data[i:i + 1000]


class SubSession:
    def __init__(self, allow_download=True):
        self.calls = []
        self.allow = allow_download
    def get(self, url, params=None, timeout=None, stream=False):
        ep = urlparse(url).path.rsplit("/", 1)[-1]
        self.calls.append(ep)
        if ep == "download":
            if not self.allow:
                return Resp({"subsonic-response": {"status": "failed", "error": {"code": 50}}})
            return Resp(ctype="audio/flac", data=b"F" * 5000)
        if ep == "stream":
            return Resp(ctype="audio/mpeg", data=b"M" * 3000)
        return Resp({"subsonic-response": {"status": "ok"}})


def sub(tmpdir, sess, offline_store="auto"):
    return SubsonicProvider(url="https://music.example.org", user="me", password="topsecret",
                            session=sess, player=FakePlayer(), offline=offline_store,
                            library=LocalLibrary("subsonic", path=os.path.join(tmpdir, "lib.json")))


def test_subsonic():
    tmpdir = tempfile.mkdtemp(prefix="spt_off_sub_")
    sess = SubSession()
    p = sub(tmpdir, sess)
    check("subsonic: can_download", p.capabilities.can_download)
    check("one folder per server + user, no secret in it",
          "music.example.org_me" in p.offline.root and "topsecret" not in p.offline.root, p.offline.root)
    store = OfflineStore("subsonic", root=os.path.join(tmpdir, "off"), max_bytes=100 * MB)
    p = sub(tmpdir, sess, store)
    tr = {"id": "s1", "uri": "subsonic:track:s1", "type": "track", "name": "One", "_suffix": "flac",
          "artists": [{"name": "B"}], "album": {"name": "LP"}}
    path = p.download_track(tr)
    check("original file through `download`, with its suffix",
          sess.calls[-1] == "download" and path.endswith(".flac") and os.path.getsize(path) == 5000, path)
    sess2 = SubSession(allow_download=False)
    p2 = sub(tmpdir, sess2, store)
    path2 = p2.download_track({"id": "s2", "uri": "subsonic:track:s2", "type": "track", "name": "Two"})
    check("no download permission: falls back to `stream`",
          sess2.calls[-2:] == ["download", "stream"] and path2.endswith(".mp3"), str(sess2.calls))
    check("no secret in file names", "topsecret" not in path and "topsecret" not in path2)
    sess3 = SubSession()
    p3 = sub(tmpdir, sess3, store)
    p3.start_playback(uris=["subsonic:track:s1"])
    check("playing a downloaded Subsonic track: local file, no HTTP call",
          p3._player.loaded[0] == [path] and sess3.calls == [], str(sess3.calls))


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
class UiFake:
    display_name = "Fake"
    def __init__(self, can=True):
        self.capabilities = Capabilities(can_download=can)
        self.store = set(); self.downloads = []; self.removed = []
        self.offline = None
    def unsupported_message(self, cap): return "Offline downloads is not available with Fake."
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self
    def search(self, *a, **k): return {}
    def fmt_duration(self, ms): return "0:01"
    def check_saved_tracks(self, ids, on_batch=None): return [False] * len(ids)
    def is_downloaded(self, tid): return tid in self.store
    def download_track(self, t):
        self.downloads.append(t["id"]); self.store.add(t["id"]); return "/x/" + t["id"]
    def remove_download(self, tid):
        self.removed.append(tid); self.store.discard(tid); return True
    def offline_usage(self): return (len(self.store) * MB, 1024 * MB, len(self.store))
    def offline_tracks(self):
        return [{"id": i, "uri": f"x:track:{i}", "name": f"N{i}", "artists": [], "album": {}} for i in sorted(self.store)]


class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake


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


def status(app):
    try: return str(app.status_line.render())
    except Exception: return ""


ROWS = [{"_pos": i, "id": f"t{i}", "uri": f"x:track:t{i}", "title": f"{'alpha' if i % 2 else 'beta'} {i}",
         "artist": "A", "album": "L", "dur": "0:01"} for i in range(6)]


async def test_ui():
    config.LOCAL_CFG = {}
    fake = UiFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._refresh_library_items(); await pilot.pause()
        names = [getattr(li, "data", {}).get("name") for li in app.lib_list.children]
        check("Library shows Downloaded with can_download", "Downloaded" in names, str(names))
        t = app._render_tracks_table("List", [dict(r) for r in ROWS], None,
                                     context_uris=[r["uri"] for r in ROWS], profile="playlist")
        await pilot.pause(); await pilot.pause()
        t.focus(); t.move_cursor(row=2); await pilot.pause()
        await pilot.press("o")
        await poll(lambda: fake.downloads == ["t2"])
        check("o downloads the highlighted track", fake.downloads == ["t2"], str(fake.downloads))
        ok = await poll(lambda: str(t.get_row_at(2)[0]) == "↓")
        check("the row gets the ↓ mark", ok, repr(str(t.get_row_at(2)[0])))
        check("other rows unchanged", str(t.get_row_at(1)[0]) == "")
        await poll(lambda: "Downloads finished" in status(app))
        check("summary on the status line", "Downloads finished: 1 ok" in status(app), status(app))
        app._set_heart_icon(t, 2, True)
        check("download column before the heart: ↓ and ❤ in their own cells",
              str(t.get_row_at(2)[0]) == "↓" and str(t.get_row_at(2)[1]) == "❤",
              repr([str(c) for c in t.get_row_at(2)[:2]]))
        await pilot.press("o")
        await poll(lambda: fake.removed == ["t2"])
        check("o again deletes the copy", fake.removed == ["t2"] and "deleted" in status(app), status(app))

        # O with a filter: only the visible rows.
        app._apply_view_filter(t, "alpha", announce=False); await pilot.pause()
        t.focus(); await pilot.pause()
        await pilot.press("O")
        await poll(lambda: len(fake.downloads) == 4)
        check("O downloads only the filtered rows", fake.downloads[1:] == ["t1", "t3", "t5"], str(fake.downloads))
        await pilot.press("O"); await pilot.pause()
        check("O when all are there says so", "already downloaded" in status(app), status(app))

        app._open_downloaded_table()
        await pilot.pause(); await pilot.pause()
        dt = app.query_one("#tracks_table")
        check("Library > Downloaded lists the copies",
              [r["id"] for r in dt._model_rows] == ["t1", "t3", "t5"], str([r.get("id") for r in dt._model_rows]))
        check("…with usage in the title", "3.0 MB" in str(app.right_panel.border_title) or "3 MB" in str(app.right_panel.border_title),
              str(app.right_panel.border_title))

        # Settings cap.
        app._save_offline_cap_setting("2 GB")
        check("Settings: cap saved", config.LOCAL_CFG.get("offline_max_bytes") == 2048 * MB)
        check("invalid cap refused", app._save_offline_cap_setting("5 KB") is False
              and config.LOCAL_CFG.get("offline_max_bytes") == 2048 * MB)


async def test_download_column():
    """The ↓ column exists for every provider, before the heart; states; header
    dimmed without can_download; narrow fit drops it first; works with s/S."""
    config.LOCAL_CFG = {}
    fake = UiFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("List", [dict(r) for r in ROWS], None, profile="playlist")
        await pilot.pause(); await pilot.pause()
        labels = [str(c.label) for c in t.ordered_columns]
        check("column order: ↓ then ♥", labels[:2] == ["↓", "♥"], str(labels))
        check("1 cell wide", t.ordered_columns[0].width == 1)
        check("header bright when the provider can download",
              "cyan" in str(t.ordered_columns[0].label.style))
        fake.store.add("t0"); app._dl_active_id = "t1"; app._dl_queued_ids = {"t2"}
        app._repaint_rows_from_model(t); await pilot.pause()
        cells = [str(t.get_row_at(i)[0]) for i in range(4)]
        check("states: downloaded ↓, downloading ⇣, queued ·, none", cells == ["↓", "⇣", "·", ""], str(cells))
        app._dl_active_id = None; app._dl_queued_ids = set()
        q = app._create_table_with_full_width(["#", "↓", "♥", "Title"], ["num", "dl", "heart", "title"])
        check("queue-style tables keep ↓ before ♥", [str(c.label) for c in q.ordered_columns][1:3] == ["↓", "♥"])
        keep, _, _ = app._fit_columns(["dl", "heart", "title", "artist", "album", "dur", "added"],
                                      ["↓", "♥", "Title", "Artist", "Album", "Duration", "Added"], panel_w=60)
        check("narrow terminal: the ↓ column is the first to go", "dl" not in keep and "heart" in keep, str(keep))
        t2 = app._render_search_table("R", [dict(r, type="track") for r in ROWS], check_saved=False)
        await pilot.pause(); await pilot.pause()
        check("search results have the column too",
              [str(c.label) for c in t2.ordered_columns][:2] == ["↓", "♥"] and str(t2.get_row_at(0)[0]) == "↓")
        check("…and the saved heart moved to column 1", t2._col_saved == 1)

    # Spotify: the column is there, dimmed; o explains why it cannot download.
    from spt_tui.providers.spotify import DOWNLOAD_UNSUPPORTED, SpotifyProvider
    fake = UiFake(can=False)
    fake.unsupported_message = lambda cap: DOWNLOAD_UNSUPPORTED
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        t = app._render_tracks_table("List", [dict(r) for r in ROWS], None, profile="playlist")
        await pilot.pause(); await pilot.pause()
        check("column present without can_download, header dimmed",
              str(t.ordered_columns[0].label) == "↓" and "555555" in str(t.ordered_columns[0].label.style))
        t.focus(); await pilot.press("o"); await pilot.pause()
        check("o explains the Spotify limitation", "DRM" in status(app) and fake.downloads == [], status(app))
    sp = SpotifyProvider()
    check("SpotifyProvider: no can_download, explained", not sp.capabilities.can_download
          and sp.unsupported_message("can_download") == DOWNLOAD_UNSUPPORTED)


def test_durations():
    """D: a copy is never stored without a duration when one can be known, and
    old copies stored with 0 get it back (no re-download)."""
    tmpdir = tempfile.mkdtemp(prefix="spt_off_dur_")
    def dl_with_info(url, base):
        return write(base + ".webm", 1000), {"duration_ms": 123000}
    p = yt(tmpdir, downloader=dl_with_info)
    p.download_track({"id": "a1", "uri": "ytmusic:track:a1", "type": "track", "name": "A", "duration_ms": 0})
    check("duration from yt-dlp's info dict when the row had none", p.offline.track("a1")["duration_ms"] == 123000)
    p2 = yt(tmpdir, downloader=lambda url, base: write(base + ".webm", 1000))
    p2._remember({"id": "a2", "uri": "ytmusic:track:a2", "type": "track", "name": "B", "duration_ms": 61000,
                  "album": {"name": "LP"}})
    p2.download_track({"id": "a2", "uri": "ytmusic:track:a2", "type": "track", "name": "B"})
    check("…or from the provider's own copy of the track (no request)",
          p2.offline.track("a2")["duration_ms"] == 61000 and p2.offline.track("a2")["album"]["name"] == "LP")
    orig = offline.probe_duration_ms
    offline.probe_duration_ms = lambda path, **k: 45000
    try:
        p2.download_track({"id": "a3", "uri": "ytmusic:track:a3", "type": "track", "name": "C"})
    finally:
        offline.probe_duration_ms = orig
    check("…or by probing the downloaded file", p2.offline.track("a3")["duration_ms"] == 45000)

    # Backfill of an index written by the old code (all zeros).
    root = tempfile.mkdtemp(prefix="spt_off_bf_")
    st = OfflineStore("x", root=root, max_bytes=100 * MB)
    tmp = tempfile.mkdtemp()
    for i in range(3):
        st.add(dict(T(i), duration_ms=0), write(os.path.join(tmp, f"g{i}.webm"), 100))
    os.remove(st.path_for("v2"))           # one copy lost on disk
    import json
    st2 = OfflineStore("x", root=root, max_bytes=100 * MB)
    st2._load()["v2"] = {"file": "v2.webm", "size": 100, "added": 1, "used": 1, "track": dict(T(2), duration_ms=0)}
    st2._save()
    st3 = OfflineStore("x", root=root, max_bytes=100 * MB)
    check("three entries without duration", len(st3.missing_durations()) == 3)
    n = st3.backfill_durations(probe=lambda path: 200000 if path.endswith("v0.webm") else 150000)
    check("backfill fills the copies whose file exists, no download", n == 2
          and st3.track("v0")["duration_ms"] == 200000 and st3.track("v1")["duration_ms"] == 150000, str(n))
    with open(st3.index_path) as fh:
        raw = json.load(fh)
    check("index format kept (version 1, tracks)", raw.get("version") == 1 and "v0" in raw["tracks"])
    check("a probe that cannot tell leaves 0 (sorted last later)",
          OfflineStore("x", root=root).backfill_durations(probe=lambda p: 0) == 0)
    check("parse_clock", offline.parse_clock("3:05") == 185000 and offline.parse_clock("1:00:01") == 3601000
          and offline.parse_clock("") == 0)
    if shutil.which("ffprobe"):
        check("probe_duration_ms on a non-audio file is 0, never raises",
              offline.probe_duration_ms(os.path.join(tmp, "g0.webm")) == 0
              if os.path.exists(os.path.join(tmp, "g0.webm")) else True)


class DlFake(UiFake):
    def __init__(self):
        super().__init__()
        self.entries = [
            {"track": {"id": "d0", "uri": "x:track:d0", "name": "Charlie", "artists": [{"name": "Zed"}],
                       "album": {"name": "B"}, "duration_ms": 200000}, "added": 3000, "size": 3 * MB},
            {"track": {"id": "d1", "uri": "x:track:d1", "name": "alpha", "artists": [{"name": "Amy"}],
                       "album": {"name": "C"}, "duration_ms": 0}, "added": 2000, "size": 1 * MB},
            {"track": {"id": "d2", "uri": "x:track:d2", "name": "Bravo", "artists": [{"name": "Max"}],
                       "album": {"name": "A"}, "duration_ms": 100000}, "added": 1000, "size": 2 * MB},
        ]
        self.store = {"d0", "d1", "d2"}
        self.played = []
        self.backfilled = 0
    def offline_entries(self): return [dict(e) for e in self.entries]
    def offline_missing_durations(self): return 0 if self.backfilled else 1
    def offline_backfill(self, should_stop=None):
        self.entries[1]["track"]["duration_ms"] = 300000
        self.backfilled += 1
        return 1
    def start_playback(self, **kw): self.played.append(kw)
    def shuffle(self, *a, **k): pass
    def fmt_duration(self, ms):
        s = int((ms or 0) / 1000); return f"{s // 60}:{s % 60:02d}"


async def test_downloaded_sort_and_queue():
    """E: s/S on Library > Downloaded (own preference, Size and Date added,
    unknown durations last). D: the backfill repaints it. F: playing from it
    names the queue context Downloaded."""
    from spt_tui import playlist_sort
    config.LOCAL_CFG = {"playlist_sort": "artist"}
    fake = DlFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        fake.backfilled = 1                          # no backfill for the first part
        app._open_downloaded_table(); await pilot.pause(); await pilot.pause()
        t = app.query_one("#tracks_table")
        ids = lambda: [r["id"] for r in t._model_rows]
        check("default order: newest download first", ids() == ["d0", "d1", "d2"], str(ids()))
        check("unknown duration shows blank, not 0:00", t._model_rows[1]["dur"] == "")
        check("Added column shows the download date", t._model_rows[0]["added"] == __import__("datetime").date.fromtimestamp(3000).isoformat())
        t.focus(); await pilot.pause()
        await pilot.press("s"); await pilot.pause()          # title
        check("s: by title", ids() == ["d1", "d2", "d0"], str(ids()))
        check("its own preference", config.LOCAL_CFG.get("downloaded_sort") == "title"
              and config.LOCAL_CFG.get("playlist_sort") == "artist")
        for _ in range(3):
            await pilot.press("s"); await pilot.pause()      # artist, album, duration
        check("duration: unknown last", ids() == ["d2", "d0", "d1"], str(ids()))
        await pilot.press("S"); await pilot.pause()
        check("duration desc: unknown still last", ids() == ["d0", "d2", "d1"], str(ids()))
        await pilot.press("s"); await pilot.pause()          # added asc
        check("date added", ids() == ["d2", "d1", "d0"], str(ids()))
        await pilot.press("s"); await pilot.pause()          # size
        check("size", app._pl_sort_pref("downloaded")[0] == "size" and ids() == ["d1", "d2", "d0"], str(ids()))
        check("title says the order", "sort: Size" in str(t._view_title))
        # Filter + play: the real position in the sorted list, context named Downloaded.
        app._apply_view_filter(t, "charlie", announce=False); await pilot.pause()
        t.focus(); await pilot.pause()
        await pilot.press("enter")
        await poll(lambda: fake.played)
        got = fake.played[-1] if fake.played else {}
        check("filtered + sorted row plays from its place in the view order",
              got.get("uris") == ["x:track:d0", "x:track:d1", "x:track:d2"], str(got))
        check("queue context: Downloaded / Downloaded",
              getattr(app, "_play_context", None) == {"name": "Downloaded", "source": "Downloaded"},
              str(getattr(app, "_play_context", None)))
        app._close_view_filter(t); await pilot.pause()
        # Multi-Add keeps the selected track across a re-sort.
        app._multi_add_mode = True; app._multi_add_table = t; app._multi_add_selected_rows = {0}
        sel_id = t._model_rows[0]["id"]
        await pilot.press("s"); await pilot.pause()          # wraps to default
        sel_after = [t._model_rows[i]["id"] for i in app._multi_add_selected_rows]
        check("Multi-Add selection follows the track when sorting", sel_after == [sel_id], f"{sel_after} vs {sel_id}")
        app._multi_add_mode = False; app._multi_add_selected_rows = set()
        # Remembered between sessions.
        check("preference loads back", playlist_sort.load_pref(config.load_local_config(), keys=playlist_sort.DOWNLOADED_SORT_KEYS,
              cfg_key="downloaded_sort", cfg_desc_key="downloaded_sort_desc") == ("default", False))

        # D: backfill repaints the open view.
        fake.backfilled = 0; app._backfill_done_for = set()
        app._open_downloaded_table(); await pilot.pause()
        ok = await poll(lambda: any(r.get("dur") == "5:00" for r in app.query_one("#tracks_table")._model_rows))
        check("backfill runs in the background and Downloaded repaints with the durations", ok)
        check("…said on the status line", "durations filled in" in status(app), status(app))


async def test_queue_origins():
    """F: the queue's Source / "Next from" for the views added this session."""
    config.LOCAL_CFG = {}
    fake = DlFake()
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        def play_first(table):
            fake.played.clear(); app._last_played = None
            app._play_row(0, table)
        # Radio
        app._right_view = None
        app._new_view_token("radio", "track:x")
        rows = [dict(r, type="track") for r in ROWS[:3]]
        t = app._render_tracks_table("[b]Radio:[/b] Seed  [dim](song radio · 3 tracks)[/dim]", rows, None,
                                     context_uris=[r["uri"] for r in rows], profile="radio")
        t._queue_name = "Radio: Seed"
        await pilot.pause()
        play_first(t); await poll(lambda: fake.played)
        check("radio: Next from 'Radio: Seed', Source Radio",
              app._play_context == {"name": "Radio: Seed", "source": "Radio"}, str(app._play_context))
        # Artist page
        app._new_view_token("artist", "ar1")
        st = app._render_search_table("Artist: Band — 3 top tracks", rows, check_saved=False, layout="artist_page")
        st._context_uris = [r["uri"] for r in rows]; st._queue_name = "Band · top tracks"
        await pilot.pause()
        play_first(st); await poll(lambda: fake.played)
        check("artist page: 'Band · top tracks', Source Artist",
              app._play_context == {"name": "Band · top tracks", "source": "Artist"}, str(app._play_context))
        # A filtered playlist keeps 'Playlist' (it really is one)
        app._new_view_token("playlist", "pl9")
        pt = app._render_tracks_table("[b]My list[/b]", rows, None, context_uris=[r["uri"] for r in rows])
        pt._pl_base_title = "[b]My list[/b]"
        await pilot.pause()
        app._apply_view_filter(pt, "alpha", announce=False); await pilot.pause()
        play_first(pt); await poll(lambda: fake.played)
        check("filtered playlist: its name, Source Playlist",
              app._play_context == {"name": "My list", "source": "Playlist"}, str(app._play_context))
        # The queue view shows them (mpv-backed rows come from the provider's queue).
        app._play_context = {"name": "Downloaded", "source": "Downloaded"}
        class Q(DlFake):
            pass
        rows_q = app._provider_queue_rows() if hasattr(app, "_provider_queue_rows") else None
        check("non-mpv fake: provider queue rows not used (Spotify path unchanged)", rows_q is None)


class LikeFake(DlFake):
    """check_saved_tracks / save / remove like a provider (H)."""
    def __init__(self, local=False, can_like=True):
        super().__init__()
        self.liked = {"d0", "d2"}
        self.check_calls = []
        self.capabilities = Capabilities(can_download=True, can_like=can_like, local_library=local)
    def check_saved_tracks(self, ids, on_batch=None):
        self.check_calls.append(list(ids))
        return [i in self.liked for i in ids]
    def save_tracks(self, ids): self.liked.update(ids); return True
    def remove_tracks(self, ids): self.liked.difference_update(ids); return True


async def test_downloaded_likes():
    """H: Downloaded paints its hearts (one batched lookup), they survive s/S
    and the / filter, f updates them, and no heart / lookup without likes."""
    for local in (True, False):
        config.LOCAL_CFG = {}
        fake = LikeFake(local=local)
        fake.backfilled = 1
        app = TApp(fake)
        async with app.run_test(size=(140, 34)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app._open_downloaded_table(); await pilot.pause(); await pilot.pause()
            t = app.query_one("#tracks_table")
            hc = t._fit_fields.index("heart")
            def hearts():
                out = {}
                for i, r in enumerate(t._model_rows):
                    d = app._model_to_display_row(t, i)
                    if d is not None:
                        out[r["id"]] = str(t.get_row_at(d)[hc]) == "❤"
                return out
            ok = await poll(lambda: hearts() == {"d0": True, "d1": False, "d2": True})
            mode = "local library (sync)" if local else "remote (worker)"
            check(f"{mode}: Downloaded paints the liked hearts", ok, str(hearts()))
            check(f"{mode}: one batched lookup for all rows", fake.check_calls[:1] == [["d0", "d1", "d2"]], str(fake.check_calls))
            t.focus(); await pilot.pause()
            await pilot.press("s"); await pilot.pause()
            check(f"{mode}: hearts follow the rows after s", hearts() == {"d0": True, "d1": False, "d2": True}, str(hearts()))
            app._apply_view_filter(t, "alpha", announce=False); await pilot.pause()
            check(f"{mode}: and with the / filter", hearts() == {"d1": False}, str(hearts()))
            app._close_view_filter(t); await pilot.pause()
            # f on d1 likes it here and in other tables.
            other = app._render_search_table("R", [{"type": "track", "id": "d1", "uri": "x:track:d1", "title": "alpha",
                                                     "artist": "", "album": "", "saved": False}], check_saved=False)
            await pilot.pause()
            app._open_downloaded_table(); await pilot.pause(); await pilot.pause()
            t = app.query_one("#tracks_table"); hc = t._fit_fields.index("heart")
            await poll(lambda: hearts().get("d0") is True)
            row = next(i for i, r in enumerate(t._model_rows) if r["id"] == "d1")
            t.focus(); t.move_cursor(row=t._filter_rows.index(row) if t._filter_rows else row); await pilot.pause()
            await pilot.press("f")
            ok = await poll(lambda: hearts().get("d1") is True)
            check(f"{mode}: f likes the song and paints its heart", ok and "d1" in fake.liked, str(hearts()))

    config.LOCAL_CFG = {}
    fake = LikeFake(can_like=False); fake.backfilled = 1
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._open_downloaded_table(); await pilot.pause(); await pilot.pause()
        t = app.query_one("#tracks_table")
        lab = next(c.label for c in t.ordered_columns if str(c.label) == "♥")
        check("no likes: no lookup at all", fake.check_calls == [])
        check("…and the heart header is dimmed", "555555" in str(getattr(lab, "style", "")), str(getattr(lab, "style", "")))


async def test_ui_unsupported():
    config.LOCAL_CFG = {}
    fake = UiFake(can=False)
    app = TApp(fake)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._refresh_library_items(); await pilot.pause()
        names = [getattr(li, "data", {}).get("name") for li in app.lib_list.children]
        check("no Downloaded entry without can_download", "Downloaded" not in names)
        t = app._render_tracks_table("List", [dict(r) for r in ROWS], None, profile="playlist")
        await pilot.pause(); await pilot.pause()
        t.focus(); await pilot.press("o"); await pilot.pause()
        check("o without support: notice, no call",
              fake.downloads == [] and "not available" in status(app), status(app))


if __name__ == "__main__":
    test_store()
    test_ytmusic()
    test_subsonic()
    asyncio.run(test_ui())
    asyncio.run(test_download_column())
    test_durations()
    asyncio.run(test_downloaded_sort_and_queue())
    asyncio.run(test_queue_origins())
    asyncio.run(test_downloaded_likes())
    asyncio.run(test_ui_unsupported())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
