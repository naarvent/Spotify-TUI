"""First-time authorisation of the local player (librespot --enable-oauth).

* the "Browse to:" URL is opened ONCE, even after many banner lines, and
  without waiting one tick per line;
* the UI gets events: auth_url (opened / not opened + why), auth_ok,
  auth_timeout, auth_failed; the status line shows a sticky "Authorize …"
  line and replaces it at the end;
* LocalPlayer opens with the shared opener by default (never a no-op);
* choosing "SPT-TUI Local (start)" in Devices never sends the sentinel id
  to Spotify (that was the "404 Device not found"), and Enter's two events
  only act once.

Offline: popen, open_url and the clock are injected.
Run standalone:  python tests/test_local_player_auth.py
"""
import os, sys, asyncio, tempfile, threading, time

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_lpauth_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config, opener
from spt_tui.local_player import LocalPlayer, LOCAL_START_SENTINEL
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

URL = "https://accounts.spotify.com/authorize?response_type=code&client_id=x&state=y"


class Out:
    """stdout of the fake librespot: yields lines, then blocks until closed."""
    def __init__(self, lines):
        self.lines = list(lines)
        self.closed = threading.Event()
    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        self.closed.wait(5)
        return ""


class Proc:
    def __init__(self, lines, die_after=None, clock=None):
        self.stdout = Out(lines)
        self.returncode = None
        self.die_after, self.clock = die_after, clock
    def poll(self):
        if self.die_after is not None and self.clock.t >= self.die_after:
            self.returncode = 1
        return self.returncode
    def terminate(self): self.returncode = -15; self.stdout.closed.set()
    def kill(self): self.terminate()
    def wait(self, timeout=None): return self.returncode


class Clock:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def sleep(self, s):
        self.t += s
        time.sleep(0.001)          # let the reader thread run


def make(lines, *, creds_at=None, opener_fn=None, die_after=None):
    tmp = tempfile.mkdtemp(prefix="lp_auth_")
    exe = os.path.join(tmp, "librespot-bin"); open(exe, "w").close()
    os.environ["SPT_LIBRESPOT_PATH"] = exe
    clock = Clock()
    events, opened = [], []
    holder = {}

    def popen(argv, **kw):
        p = Proc(lines, die_after=die_after, clock=clock)
        holder["proc"] = p
        return p

    lp = LocalPlayer(object(), cache_dir=tmp, popen=popen,
                     open_url=opener_fn or (lambda u: opened.append(u) or holder.__setitem__("t_open", clock.t) or True),
                     now=clock.now, sleep=clock.sleep, on_event=lambda k, d: events.append((k, d)))
    if creds_at is not None:
        orig = lp.is_authenticated
        def auth():
            if clock.t >= creds_at:
                os.makedirs(os.path.dirname(lp.credentials_path()), exist_ok=True)
                open(lp.credentials_path(), "w").write("{}")
            return orig()
        lp.is_authenticated = auth
    lp._holder = holder
    return lp, clock, events, opened


def test_url_opened_once_and_ok():
    banner = [f"[INFO librespot] line {i}\n" for i in range(200)]
    lp, clock, events, opened = make(banner + [f"Browse to: {URL}\n", "OAuth server listening\n"], creds_at=3.0)
    ok = lp.authenticate()
    check("authenticated", ok)
    check("URL opened exactly once", opened == [URL], str(opened))
    first = next((k for k, d in events if k == "auth_url"), None)
    check("events: auth_url then auth_ok", [k for k, _ in events] == ["auth_url", "auth_ok"], str(events))
    t_open = lp._holder.get("t_open")
    check("the URL is not delayed by the 200 banner lines (opened within 1 s of the fake clock;"
          " one line per 0.1 s tick took 20 s)", t_open is not None and t_open < 1.0, str(t_open))


def test_opener_failure_visible():
    def bad(url): raise opener.OpenURLError("'abrir' not found")
    lp, clock, events, _ = make([f"Browse to: {URL}\n"], creds_at=1.0, opener_fn=bad)
    lp.authenticate()
    ev = dict(events)
    check("opener failure reported to the UI", ev.get("auth_url", {}).get("opened") is False
          and "abrir" in (ev["auth_url"].get("error") or ""), str(events))
    lp2, _, events2, _ = make([f"Browse to: {URL}\n"], creds_at=1.0, opener_fn=lambda u: False)
    lp2.authenticate()
    check("an opener returning False also counts as not opened", dict(events2)["auth_url"]["opened"] is False)


def test_timeout_and_death():
    lp, clock, events, _ = make([f"Browse to: {URL}\n"])
    lp.OAUTH_WAIT_SECONDS = 2.0
    check("no credentials: authenticate() False", lp.authenticate() is False)
    check("timeout event with the URL", events[-1][0] == "auth_timeout" and events[-1][1]["url"] == URL, str(events))
    lp, clock, events, _ = make(["ERROR port 5588 already in use\n"], die_after=0.5)
    lp.authenticate()
    check("librespot dying: auth_failed with its reason", events and events[-1][0] == "auth_failed", str(events))


def test_default_opener():
    lp = LocalPlayer(object(), cache_dir=tempfile.mkdtemp())
    check("default opener is the shared helper, not a no-op", lp._open_url is opener.open_url)
    spawned = []
    how = opener.open_url(URL, opener=["abrir"], which=lambda n: "/usr/bin/" + n,
                          spawn=lambda argv, **k: spawned.append((argv, k)), fallback=None)
    check("shared opener: open_command detached", how == "abrir" and spawned[0][0] == ["abrir", URL]
          and spawned[0][1].get("start_new_session") is True)
    used = []
    how = opener.open_url(URL, opener=["nope"], which=lambda n: None, spawn=None,
                          fallback=lambda u: used.append(u) or True)
    check("missing open_command: webbrowser fallback", how == "browser" and used == [URL])
    raised = None
    try:
        opener.open_url(URL, opener=["nope"], which=lambda n: None, fallback=lambda u: False)
    except opener.OpenURLError as exc:
        raised = str(exc)
    check("nothing works: OpenURLError with the reason", raised and "not found" in raised, str(raised))


class Sp:
    def __init__(self): self.transfers = []
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def get_playback(self): return {}
    def ensure(self): return self
    def devices(self): return [{"id": "PH", "name": "Phone", "type": "Smartphone", "is_active": True}]
    def transfer(self, dev_id, force_play=False): self.transfers.append(dev_id)


class FakeLP:
    device_name = "SPT-TUI Local"
    def __init__(self): self.activations = 0
    def is_available(self): return True
    def is_running(self): return False
    def start_and_activate(self):
        self.activations += 1; return "LOCAL1"


class TApp(SptPy):
    def __init__(self, sp): super().__init__(); self.spotify = sp


async def test_app():
    config.LOCAL_CFG = {}
    sp = Sp()
    app = TApp(sp)
    async with app.run_test(size=(140, 34)) as pilot:
        await pilot.pause()
        for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval"):
            t = getattr(app, a, None)
            if t is not None:
                try: t.pause()
                except Exception: pass
        app._local_player_event("auth_url", {"url": URL, "opened": True})
        await pilot.pause()
        line = str(app.status_line.render())
        check("sticky 'Authorize … in your browser' line", "Authorize SPT-TUI Local in your browser" in line
              and app._status_timer is None, line)
        app._local_player_event("auth_url", {"url": URL, "opened": False, "error": "'abrir' not found"})
        await pilot.pause()
        line = str(app.status_line.render())
        check("not opened: says why and shows the link", "Could not open the browser" in line and "accounts.spotify.com" in line, line)
        app._local_player_event("auth_ok", {}); await pilot.pause()
        check("authorized: replaced by a short notice", "authorized" in str(app.status_line.render())
              and app._status_timer is not None)
        app._local_player_event("auth_timeout", {"url": URL, "seconds": 120}); await pilot.pause()
        check("timeout: clear message with what to do", "Devices (d)" in str(app.status_line.render()))

        # Devices: the "(start)" row never transfers the sentinel to Spotify.
        app.local_player = FakeLP()
        app.action_manage_devices()
        t0 = time.time()
        while time.time() - t0 < 3:
            await pilot.pause()
            try:
                tb = app.query_one("#devices_table")
                if tb.row_count == 2: break
            except Exception:
                pass
            await asyncio.sleep(0.05)
        tb = app.query_one("#devices_table")
        tb.focus(); tb.move_cursor(row=0); await pilot.pause()
        await pilot.press("enter")
        t0 = time.time()
        while time.time() - t0 < 2 and not app.local_player.activations:
            await pilot.pause(); await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        check("Enter on '(start)' activates the local player once",
              app.local_player.activations == 1, str(app.local_player.activations))
        check("…and never PUTs the sentinel id to Spotify", LOCAL_START_SENTINEL not in sp.transfers
              and sp.transfers == [], str(sp.transfers))


if __name__ == "__main__":
    test_url_opened_once_and_ok()
    test_opener_failure_visible()
    test_timeout_and_death()
    test_default_opener()
    asyncio.run(test_app())
    os.environ.pop("SPT_LIBRESPOT_PATH", None)
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
