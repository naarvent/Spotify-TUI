"""Tests for the configurable lyrics-cache size setting.

Covers the human-readable size parser/formatter (config.parse_size /
config.format_size), the save/validate handler (_save_lyrics_cache_setting),
persistence + reload of the cap, and that the configured cap actually bounds
the on-disk cache when it is trimmed.

Network is not touched. The cache dir, config path and in-memory config are all
redirected to a temp location so the user's real config is never written.

Run standalone:  python tests/test_lyrics_cache_size.py
"""
import os, sys, json, asyncio, tempfile, shutil, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy
from spt_tui import config

MB = 1024 * 1024
GB = 1024 * 1024 * 1024

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
    def fmt_duration(self, ms): return "0:00"
    def _normalize_track_id(self, x): return x

class TApp(SptPy):
    def __init__(self, fake): super().__init__(); self.spotify = fake

def make_app():
    # Bypass Textual's __init__ for the pure save/trim helpers (no UI needed).
    return SptPy.__new__(SptPy)

def with_tmp_config(fn):
    """Redirect the cache dir, config path and in-memory config to a temp area
    so nothing touches the real user config."""
    tmp = tempfile.mkdtemp(prefix="spt_cache_size_")
    old_cache, old_cfg_path, old_local = config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG
    config.CACHE_DIR = tmp
    config.CONFIG_PATH = os.path.join(tmp, "spt_config.json")
    config.LOCAL_CFG = {}
    try:
        fn(tmp)
    finally:
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG = old_cache, old_cfg_path, old_local
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Pure parser tests
# --------------------------------------------------------------------------- #
def test_parse_mb():
    check("parse 200 MB", config.parse_size("200 MB") == 200 * MB, f"got={config.parse_size('200 MB')}")
    check("parse 500 MB", config.parse_size("500 MB") == 500 * MB)
    check("parse 1024 MB", config.parse_size("1024 MB") == 1024 * MB)

def test_parse_gb():
    check("parse 1 GB", config.parse_size("1 GB") == 1 * GB)
    check("parse 2 GB", config.parse_size("2 GB") == 2 * GB)
    check("parse 4 GB", config.parse_size("4 GB") == 4 * GB)

def test_parse_formatting_variants():
    check("case-insensitive unit (200mb)", config.parse_size("200mb") == 200 * MB)
    check("case-insensitive unit (1gb)", config.parse_size("1gb") == 1 * GB)
    check("no space between value and unit", config.parse_size("200MB") == 200 * MB)
    check("surrounding whitespace tolerated", config.parse_size("   1   GB  ") == 1 * GB)
    check("decimal value (1.5 GB)", config.parse_size("1.5 GB") == int(1.5 * GB))

def test_parse_invalid():
    check("bare number rejected (unit required)", config.parse_size("200") is None)
    check("unsupported unit KB rejected", config.parse_size("200 KB") is None)
    check("unsupported unit TB rejected", config.parse_size("1 TB") is None)
    check("zero rejected", config.parse_size("0 MB") is None)
    check("negative rejected", config.parse_size("-5 MB") is None)
    check("garbage rejected", config.parse_size("banana") is None)
    check("empty string rejected", config.parse_size("") is None)
    check("non-string rejected", config.parse_size(None) is None and config.parse_size(123) is None)

def test_format_size():
    check("format 200 MB", config.format_size(200 * MB) == "200 MB", f"got={config.format_size(200 * MB)!r}")
    check("format 1 GB", config.format_size(1 * GB) == "1 GB", f"got={config.format_size(1 * GB)!r}")
    check("format 2 GB", config.format_size(2 * GB) == "2 GB")
    check("format 1.5 GB", config.format_size(int(1.5 * GB)) == "1.5 GB", f"got={config.format_size(int(1.5 * GB))!r}")
    check("format default (2 MB)", config.format_size(config.LYRICS_CACHE_DEFAULT_BYTES) == "2 MB")

def test_parse_format_roundtrip():
    for s in ("200 MB", "500 MB", "1 GB", "2 GB", "4 GB"):
        b = config.parse_size(s)
        check(f"roundtrip {s}", config.format_size(b) == s, f"{s} -> {b} -> {config.format_size(b)!r}")


# --------------------------------------------------------------------------- #
# Save / validate handler tests
# --------------------------------------------------------------------------- #
def test_save_valid_persists():
    def body(tmp):
        app = make_app()
        ok = app._save_lyrics_cache_setting("500 MB")
        check("valid size saved returns True", ok is True)
        check("cap applied to live session", getattr(app, "_LYRICS_CACHE_MAX_BYTES", None) == 500 * MB)
        check("value stored in LOCAL_CFG as bytes", config.LOCAL_CFG.get("lyrics_cache_max_bytes") == 500 * MB)
        on_disk = json.load(open(config.CONFIG_PATH, encoding="utf-8"))
        check("value persisted to disk as bytes", on_disk.get("lyrics_cache_max_bytes") == 500 * MB, f"disk={on_disk}")
    with_tmp_config(body)

def test_save_invalid_format_rejected():
    def body(tmp):
        app = make_app()
        check("invalid format returns False", app._save_lyrics_cache_setting("banana") is False)
        check("bare number returns False", app._save_lyrics_cache_setting("500") is False)
        check("nothing persisted on invalid input", "lyrics_cache_max_bytes" not in config.LOCAL_CFG)
    with_tmp_config(body)

def test_save_min_max_boundaries():
    def body(tmp):
        app = make_app()
        lo = config.format_size(config.LYRICS_CACHE_MIN_BYTES)   # "1 MB"
        hi = config.format_size(config.LYRICS_CACHE_MAX_BYTES_LIMIT)  # "8 GB"
        check("minimum valid value accepted", app._save_lyrics_cache_setting(lo) is True, f"lo={lo}")
        check("maximum valid value accepted", app._save_lyrics_cache_setting(hi) is True, f"hi={hi}")
    with_tmp_config(body)

def test_save_out_of_range_rejected():
    def body(tmp):
        app = make_app()
        check("below floor rejected (0.5 MB)", app._save_lyrics_cache_setting("0.5 MB") is False)
        check("above ceiling rejected (9 GB)", app._save_lyrics_cache_setting("9 GB") is False)
        check("nothing persisted on out-of-range input", "lyrics_cache_max_bytes" not in config.LOCAL_CFG)
    with_tmp_config(body)


# --------------------------------------------------------------------------- #
# Persistence / reload tests
# --------------------------------------------------------------------------- #
def test_persist_reload_roundtrip():
    def body(tmp):
        # 1) Save from a first "session".
        app1 = make_app()
        check("save 300 MB", app1._save_lyrics_cache_setting("300 MB") is True)
        # 2) Simulate a restart: reload the config file from disk and construct a
        #    fresh full app (its __init__ applies the persisted cap).
        config.LOCAL_CFG = config.load_local_config()
        check("reloaded config keeps the value", config.LOCAL_CFG.get("lyrics_cache_max_bytes") == 300 * MB)
        app2 = TApp(Fake())
        check("restarted app applies the persisted cap",
              app2._LYRICS_CACHE_MAX_BYTES == 300 * MB, f"cap={app2._LYRICS_CACHE_MAX_BYTES}")
    with_tmp_config(body)

def test_missing_key_falls_back_to_default():
    def body(tmp):
        # No key in config -> class default stands, no crash (backward compat /
        # no migration needed).
        config.LOCAL_CFG = {}
        app = TApp(Fake())
        check("missing config falls back to default cap",
              app._LYRICS_CACHE_MAX_BYTES == config.LYRICS_CACHE_DEFAULT_BYTES,
              f"cap={app._LYRICS_CACHE_MAX_BYTES}")
    with_tmp_config(body)


# --------------------------------------------------------------------------- #
# The configured cap actually bounds the on-disk cache
# --------------------------------------------------------------------------- #
def test_configured_cap_bounds_trim():
    def body(tmp):
        app = make_app()
        app._lyrics_cache_data = {}
        app._LYRICS_CACHE_MAX = 100000            # let the byte cap be the binding limit
        check("cap set to 1 MB via setting", app._save_lyrics_cache_setting("1 MB") is True)
        big = "L" * 4000                            # ~4 KB per entry
        for i in range(600):                        # ~2.4 MB of data, over the 1 MB cap
            app._lyrics_cache_data[f"k{i}"] = {"status": "found", "lines": [[1, big]], "ts": i}
        app._lyrics_cache_save()
        size = os.path.getsize(os.path.join(tmp, "lyrics_cache.json"))
        check("configured cap bounds the cache file", size <= 1 * MB + 64 * 1024, f"size={size}")
        c = app._lyrics_cache_data
        check("newest kept, oldest evicted under configured cap", "k599" in c and "k0" not in c, f"n={len(c)}")
        check("cache not emptied entirely", len(c) >= 1, f"n={len(c)}")
    with_tmp_config(body)


# --------------------------------------------------------------------------- #
# End-to-end settings-wizard test (renamed action + chained fields)
# --------------------------------------------------------------------------- #
class FakeEvent:
    def __init__(self, widget, value): self.input = widget; self.value = value

def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval",
              "_devices_interval", "_queue_interval", "_lyrics_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def test_settings_wizard_end_to_end():
    tmp = tempfile.mkdtemp(prefix="spt_cache_size_ui_")
    old_cache, old_cfg_path, old_local = config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG
    config.CACHE_DIR = tmp
    config.CONFIG_PATH = os.path.join(tmp, "spt_config.json")
    config.LOCAL_CFG = {}
    app = TApp(Fake())
    try:
        async def run():
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause(); pause_intervals(app)
                # Renamed action must exist and open the settings screen.
                check("renamed action_prompt_settings exists", hasattr(app, "action_prompt_settings"))
                app.action_prompt_settings()
                await pilot.pause()
                check("settings view token is 'settings'",
                      getattr(app, "_right_view", (None,))[0] == "settings",
                      f"rv={getattr(app, '_right_view', None)}")
                for attr in ("seek_vol_down_input", "seek_vol_up_input", "seek_track_input",
                             "seek_episode_input", "lyrics_cache_input"):
                    check(f"{attr} mounted", getattr(app, attr, None) is not None)

                # Walk the wizard: four numeric fields then the size field.
                app.on_input_submitted(FakeEvent(app.seek_vol_down_input, "8"))
                app.on_input_submitted(FakeEvent(app.seek_vol_up_input, "9"))
                app.on_input_submitted(FakeEvent(app.seek_track_input, "7"))
                app.on_input_submitted(FakeEvent(app.seek_episode_input, "20"))
                check("seek values persisted through the chain",
                      config.LOCAL_CFG.get("seek_volume_down") == 8 and
                      config.LOCAL_CFG.get("seek_seconds_episode") == 20,
                      f"cfg={config.LOCAL_CFG}")
                # Final field: human-readable size.
                app.on_input_submitted(FakeEvent(app.lyrics_cache_input, "1 GB"))
                await pilot.pause()
                check("lyrics cache size persisted from the wizard",
                      config.LOCAL_CFG.get("lyrics_cache_max_bytes") == 1 * GB,
                      f"cfg={config.LOCAL_CFG}")
                check("wizard tore down its inputs on finish",
                      getattr(app, "lyrics_cache_input", None) is None)
        asyncio.run(run())
    finally:
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG = old_cache, old_cfg_path, old_local
        shutil.rmtree(tmp, ignore_errors=True)


ALL = [test_parse_mb, test_parse_gb, test_parse_formatting_variants, test_parse_invalid,
       test_format_size, test_parse_format_roundtrip,
       test_save_valid_persists, test_save_invalid_format_rejected,
       test_save_min_max_boundaries, test_save_out_of_range_rejected,
       test_persist_reload_roundtrip, test_missing_key_falls_back_to_default,
       test_configured_cap_bounds_trim, test_settings_wizard_end_to_end]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (lyrics cache size) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
