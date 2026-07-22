"""Deterministic unit tests for local_player.py. No Textual, no network, no real
librespot. The process factory, clock, sleep, browser opener and cache dir are
all injected, and a FakeSpotify records device/playback/transfer calls.

Run standalone:  python tests/test_local_player.py
Exit code is 0 only if every check passes.
"""
import os, sys, time, tempfile, shutil, traceback, threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spt_tui.config as config
import spt_tui.local_player as lp_mod
from spt_tui.local_player import LocalPlayer, LOCAL_START_SENTINEL

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeSpotify:
    """Minimal SpotifyClient stand-in. `devices` is a list; playback is a dict."""
    def __init__(self, devices=None, playback=None):
        self._devices = list(devices or [])
        self._playback = dict(playback or {})
        self.transfers = []          # (device_id, force_play)
    def devices(self):
        return list(self._devices)
    def get_playback(self):
        return dict(self._playback)
    def transfer(self, device_id, force_play=False):
        self.transfers.append((device_id, force_play))


def _clean_env():
    for k in ("SPT_LOCAL_PLAYER", "SPT_LIBRESPOT_PATH", "SPT_LOCAL_AUTOSTART", "SPT_LOCAL_NAME"):
        os.environ.pop(k, None)


def test_cfg_defaults():
    _clean_env()
    old = config.LOCAL_CFG
    config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("default name", lp.device_name == "SPT-TUI Local", lp.device_name)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        config.LOCAL_CFG = old


def test_cfg_env_precedence():
    _clean_env()
    old = config.LOCAL_CFG
    config.LOCAL_CFG = {"local_player_name": "FromFile"}
    os.environ["SPT_LOCAL_NAME"] = "FromEnv"
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("env overrides config file", lp.device_name == "FromEnv", lp.device_name)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_discovery_override_first():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "my_librespot.bin")
        open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("override path wins", lp.binary_path() == fake_bin, repr(lp.binary_path()))
        check("available with binary", lp.is_available() is True)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_discovery_bundled_then_path(monkey_which=None):
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    orig_which = lp_mod.shutil.which
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        exe = "librespot.exe" if os.name == "nt" else "librespot"
        bin_dir = os.path.join(tmp, "bin"); os.makedirs(bin_dir)
        bundled = os.path.join(bin_dir, exe); open(bundled, "w").close()
        lp_mod.shutil.which = lambda name: "/usr/bin/librespot"
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("bundled path beats PATH", lp.binary_path() == bundled, repr(lp.binary_path()))
        os.remove(bundled)
        check("falls back to PATH", lp.binary_path() == "/usr/bin/librespot", repr(lp.binary_path()))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        lp_mod.shutil.which = orig_which
        _clean_env(); config.LOCAL_CFG = old


def test_discovery_none_disables():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    orig_which = lp_mod.shutil.which
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp_mod.shutil.which = lambda name: None
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("no binary -> not available", lp.is_available() is False)
        check("no binary -> path None", lp.binary_path() is None)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        lp_mod.shutil.which = orig_which
        _clean_env(); config.LOCAL_CFG = old


def test_disabled_flag():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        os.environ["SPT_LOCAL_PLAYER"] = "false"
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("disabled flag -> not available", lp.is_available() is False)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


ALL = [test_cfg_defaults, test_cfg_env_precedence, test_discovery_override_first,
       test_discovery_bundled_then_path, test_discovery_none_disables, test_disabled_flag]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (local_player) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
