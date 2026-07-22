"""Deterministic unit tests for local_player.py. No Textual, no network, no real
librespot. The process factory, clock, sleep, browser opener and cache dir are
all injected, and a FakeSpotify records device/playback/transfer calls.

Run standalone:  python tests/test_local_player.py
Exit code is 0 only if every check passes.
"""
import os, sys, time, tempfile, shutil, traceback, threading, subprocess

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


def test_is_authenticated():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        check("no creds -> not authenticated", lp.is_authenticated() is False)
        os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
        open(lp.credentials_path(), "w").close()
        check("creds file -> authenticated", lp.is_authenticated() is True)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_build_argv():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        run = lp._build_argv("/x/librespot", login=False)
        login = lp._build_argv("/x/librespot", login=True)
        check("argv starts with binary", run[0] == "/x/librespot", repr(run[:1]))
        check("argv carries name", "--name" in run and "SPT-TUI Local" in run)
        check("argv carries cache dir", os.path.join(tmp, "librespot") in run)
        check("run mode has no oauth", "--enable-oauth" not in run)
        check("login mode enables oauth", "--enable-oauth" in login)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


class FakeProc:
    def __init__(self, argv, lines=None, write_creds_path=None):
        self.argv = argv
        self._lines = list(lines or [])
        self._write_creds_path = write_creds_path
        self._alive = True
        self.terminated = False
        self.killed = False
        self.stdout = self
    def poll(self):
        return None if self._alive else 0
    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        # EOF: simulate librespot having cached credentials by now.
        if self._write_creds_path:
            os.makedirs(os.path.dirname(self._write_creds_path), exist_ok=True)
            open(self._write_creds_path, "w").close()
            self._write_creds_path = None
        return ""
    def terminate(self):
        self.terminated = True; self._alive = False
    def kill(self):
        self.killed = True; self._alive = False
    def wait(self, timeout=None):
        self._alive = False
        return 0


class FakePopen:
    """Callable replacement for subprocess.Popen. Records the last spawn."""
    def __init__(self, lines=None, creds_for_login=None):
        self.lines = lines
        self.creds_for_login = creds_for_login   # cache path written when --enable-oauth present
        self.spawns = []
        self.last = None
    def __call__(self, argv, **kw):
        login = "--enable-oauth" in argv
        creds = self.creds_for_login if (login and self.creds_for_login) else None
        proc = FakeProc(argv, lines=(self.lines if login else None), write_creds_path=creds)
        self.spawns.append(argv); self.last = proc
        return proc


def test_spawn_and_is_running():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        check("not running before spawn", lp.is_running() is False)
        ok = lp._spawn(login=False)
        check("spawn returns True", ok is True)
        check("running after spawn", lp.is_running() is True)
        check("headless argv, no oauth", "--enable-oauth" not in fp.last.argv)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_stop_terminates():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        lp._spawn(login=False)
        proc = fp.last
        lp.stop()
        check("stop terminates process", proc.terminated is True)
        check("not running after stop", lp.is_running() is False)
        lp.stop()  # idempotent, must not raise
        check("stop is idempotent", True)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_spawn_no_binary():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    orig_which = lp_mod.shutil.which
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp_mod.shutil.which = lambda name: None
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=FakePopen())
        check("spawn with no binary -> False", lp._spawn(login=False) is False)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        lp_mod.shutil.which = orig_which
        _clean_env(); config.LOCAL_CFG = old


def test_stop_reaps_after_kill():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        lp._spawn(login=False)
        proc = fp.last
        calls = {"wait": 0}
        def fake_wait(timeout=None):
            calls["wait"] += 1
            if calls["wait"] == 1:
                raise subprocess.TimeoutExpired(cmd="librespot", timeout=timeout)
            proc._alive = False
            return 0
        proc.wait = fake_wait
        lp.stop()
        check("terminate then kill on timeout", proc.killed is True)
        check("reaps after kill (2nd wait)", calls["wait"] >= 2, str(calls["wait"]))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_spawn_popen_raises_returns_false():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        def raising_popen(argv, **kw):
            raise RuntimeError("boom")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=raising_popen)
        check("spawn returns False on Popen failure", lp._spawn(login=False) is False)
        check("not running after failed spawn", lp.is_running() is False)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_extract_url():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp)
        u = lp._extract_url("Please browse to https://accounts.spotify.com/authorize?x=1 now")
        check("extracts url", u == "https://accounts.spotify.com/authorize?x=1", repr(u))
        check("no url -> None", lp._extract_url("nothing here") is None)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_authenticate_opens_url_and_caches():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        creds = os.path.join(tmp, "librespot", "credentials.json")
        fp = FakePopen(lines=["go to https://accounts.spotify.com/authorize?y=2\n"],
                       creds_for_login=creds)
        opened = []
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp,
                         open_url=lambda u: opened.append(u), sleep=lambda s: None)
        ok = lp.authenticate()
        check("authenticate returns True", ok is True)
        check("browser opened with auth url",
              opened == ["https://accounts.spotify.com/authorize?y=2"], repr(opened))
        check("login spawn used oauth", "--enable-oauth" in fp.last.argv)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_authenticate_short_circuits_when_cached():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
        open(os.path.join(tmp, "librespot", "credentials.json"), "w").close()
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        check("already-auth authenticate True", lp.authenticate() is True)
        check("no spawn when already authenticated", fp.spawns == [], repr(fp.spawns))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_start_headless_when_authenticated():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
        open(os.path.join(tmp, "librespot", "credentials.json"), "w").close()
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        check("start returns True", lp.start() is True)
        check("headless spawn (no oauth)", "--enable-oauth" not in fp.last.argv)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_authenticate_times_out_bounded():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        clock = [1000.0]; calls = {"sleep": 0}
        def fake_sleep(s):
            calls["sleep"] += 1; clock[0] += 30.0   # cross the 120s deadline fast
        # login-mode fake proc that emits nothing and never caches creds, stays alive
        fp = FakePopen(lines=[], creds_for_login=None)
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp,
                         now=lambda: clock[0], sleep=fake_sleep)
        ok = lp.authenticate()
        check("timeout -> False", ok is False)
        check("bounded: gave up via deadline", calls["sleep"] >= 1, str(calls["sleep"]))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_start_returns_true_when_running():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
        open(os.path.join(tmp, "librespot", "credentials.json"), "w").close()
        fp = FakePopen()
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp)
        lp._spawn(login=False)                # now running
        n_before = len(fp.spawns)
        check("start() True when already running", lp.start() is True)
        check("no extra spawn when running", len(fp.spawns) == n_before, str(fp.spawns))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_start_delegates_to_authenticate_when_unauth():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        creds = os.path.join(tmp, "librespot", "credentials.json")
        fp = FakePopen(lines=["auth https://accounts.spotify.com/authorize?z=3\n"],
                       creds_for_login=creds)
        lp = LocalPlayer(FakeSpotify(), cache_dir=tmp, popen=fp,
                         open_url=lambda u: None, sleep=lambda s: None)
        ok = lp.start()
        check("start() authenticates when unauth", ok is True)
        check("start() used oauth login", "--enable-oauth" in fp.last.argv)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def _authed_player(tmp, spotify, **kw):
    """Helper: an available, already-authenticated LocalPlayer over a temp dir."""
    fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
    os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
    os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
    open(os.path.join(tmp, "librespot", "credentials.json"), "w").close()
    return LocalPlayer(spotify, cache_dir=tmp, popen=FakePopen(),
                       now=kw.get("now", time.time), sleep=lambda s: None)


def test_autostart_transfers_when_idle():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        sp = FakeSpotify(devices=[{"id": "LOCAL1", "name": "SPT-TUI Local", "is_active": False}],
                         playback={"is_playing": False})
        lp = _authed_player(tmp, sp)
        lp.maybe_autostart()
        check("idle: transferred to local", sp.transfers == [("LOCAL1", False)], repr(sp.transfers))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_does_not_steal():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        sp = FakeSpotify(devices=[{"id": "LOCAL1", "name": "SPT-TUI Local", "is_active": False},
                                  {"id": "PHONE", "name": "Phone", "is_active": True}],
                         playback={"is_playing": True})
        lp = _authed_player(tmp, sp)
        lp.maybe_autostart()
        check("playing elsewhere: NO transfer", sp.transfers == [], repr(sp.transfers))
        check("local player still running", lp.is_running() is True)
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_device_never_appears():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        # No device with our name ever shows up; the bounded wait must give up.
        sp = FakeSpotify(devices=[{"id": "PHONE", "name": "Phone", "is_active": True}],
                         playback={"is_playing": False})
        clock = [1000.0]
        lp = _authed_player(tmp, sp, now=lambda: clock[0])
        # advance the clock past DEVICE_WAIT_SECONDS on each sleep
        lp._sleep = lambda s: clock.__setitem__(0, clock[0] + 5.0)
        lp.maybe_autostart()
        check("no device: no transfer, no crash", sp.transfers == [], repr(sp.transfers))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_skipped_when_not_authenticated():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin  # available but NOT authenticated
        fp = FakePopen()
        sp = FakeSpotify()
        lp = LocalPlayer(sp, cache_dir=tmp, popen=fp, sleep=lambda s: None)
        lp.maybe_autostart()
        check("no creds: no spawn on autostart", fp.spawns == [], repr(fp.spawns))
        check("no creds: no transfer", sp.transfers == [])
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_disabled_flag():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        fake_bin = os.path.join(tmp, "b"); open(fake_bin, "w").close()
        os.environ["SPT_LIBRESPOT_PATH"] = fake_bin
        os.makedirs(os.path.join(tmp, "librespot"), exist_ok=True)
        open(os.path.join(tmp, "librespot", "credentials.json"), "w").close()
        os.environ["SPT_LOCAL_AUTOSTART"] = "false"
        fp = FakePopen()
        sp = FakeSpotify(devices=[{"id": "LOCAL1", "name": "SPT-TUI Local"}],
                         playback={"is_playing": False})
        lp = LocalPlayer(sp, cache_dir=tmp, popen=fp, sleep=lambda s: None)
        lp.maybe_autostart()
        check("autostart disabled: no transfer", sp.transfers == [], repr(sp.transfers))
        check("autostart disabled: no spawn", fp.spawns == [], repr(fp.spawns))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


def test_autostart_get_playback_error_skips_transfer():
    _clean_env()
    old = config.LOCAL_CFG; config.LOCAL_CFG = {}
    try:
        tmp = tempfile.mkdtemp(prefix="lp_")
        class RaisingPlaybackSpotify(FakeSpotify):
            def get_playback(self):
                raise RuntimeError("playback boom")
        sp = RaisingPlaybackSpotify(
            devices=[{"id": "LOCAL1", "name": "SPT-TUI Local", "is_active": False}])
        lp = _authed_player(tmp, sp)
        lp.maybe_autostart()
        check("get_playback error -> NO transfer (fail safe)", sp.transfers == [], repr(sp.transfers))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _clean_env(); config.LOCAL_CFG = old


ALL = [test_cfg_defaults, test_cfg_env_precedence, test_discovery_override_first,
       test_discovery_bundled_then_path, test_discovery_none_disables, test_disabled_flag,
       test_is_authenticated, test_build_argv, test_spawn_and_is_running, test_stop_terminates, test_spawn_no_binary,
       test_stop_reaps_after_kill, test_spawn_popen_raises_returns_false, test_extract_url, test_authenticate_opens_url_and_caches,
       test_authenticate_short_circuits_when_cached, test_start_headless_when_authenticated]

ALL += [test_authenticate_times_out_bounded, test_start_returns_true_when_running,
        test_start_delegates_to_authenticate_when_unauth]

ALL += [test_autostart_transfers_when_idle, test_autostart_does_not_steal,
        test_autostart_device_never_appears, test_autostart_skipped_when_not_authenticated,
        test_autostart_disabled_flag, test_autostart_get_playback_error_skips_transfer]

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
