"""yt-dlp updater: version logic, install detection, update commands per install kind,
daily check cache, hot reload of the module, and the Settings section. No network.

Run standalone:  python tests/test_ytdlp_update.py
"""
import os, sys, json, types, tempfile, asyncio, subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spt_tui import ytdlp_update as yu

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

# --- versions
check("newer date", yu.is_newer("2026.11.01", "2026.10.26"))
check("nightly suffix is newer", yu.is_newer("2026.10.26.123456", "2026.10.26"))
check("same is not newer", not yu.is_newer("2026.10.26", "2026.10.26"))
check("older is not newer", not yu.is_newer("2026.10.01", "2026.10.26"))
check("junk is never newer", not yu.is_newer("abc", "2026.10.26") and not yu.is_newer("2026.10.26", ""))

# --- latest + daily cache
tmp = tempfile.mkdtemp()
yu.config.CACHE_DIR = tmp
calls = []
def fetch_ok(url):
    calls.append(url); return json.dumps({"info": {"version": "2026.11.05"}}).encode()
check("latest from PyPI json", yu.latest_version(fetch_ok) == "2026.11.05")
calls.clear()
t = [1000.0]
check("cached_latest asks once", yu.cached_latest(fetch=fetch_ok, now=lambda: t[0]) == "2026.11.05" and len(calls) == 1)
yu.cached_latest(fetch=fetch_ok, now=lambda: t[0] + 3600)
check("no second request within a day", len(calls) == 1)
yu.cached_latest(fetch=fetch_ok, now=lambda: t[0] + 90000)
check("asks again after a day", len(calls) == 2)
def fetch_bad(url): raise OSError("offline")
check("offline keeps the last answer", yu.cached_latest(force=True, fetch=fetch_bad, now=lambda: t[0] + 99999) == "2026.11.05")
check("offline and nothing cached -> None", (os.remove(yu._check_file()), yu.latest_version(fetch_bad))[1] is None)

# --- install detection
real_prefix = sys.prefix
sys.frozen = True
check("frozen detected", yu.detect_install().kind == "frozen")
del sys.frozen
sys.prefix = "/home/u/.local/share/pipx/venvs/spotify-tui"
check("pipx detected", yu.detect_install().kind in ("pipx",) if yu.importlib.util.find_spec("yt_dlp") else True)
sys.prefix = real_prefix

# --- update commands
class P:
    def __init__(self, rc=0, out="", err=""): self.returncode, self.stdout, self.stderr = rc, out, err
log = []
def run_ok(cmd, **kw): log.append(cmd); return P(0, "2026.11.05\n")
res = yu.update(yu.Install("venv", "x"), run=run_ok, fetch=fetch_ok)
check("venv/pip: pip install --upgrade yt-dlp with this interpreter",
      res.ok and log[0][:5] == [sys.executable, "-m", "pip", "install", "--upgrade"], str(log))
def run_managed(cmd, **kw): return P(1, "", "error: externally-managed-environment")
res = yu.update(yu.Install("pip", "x"), run=run_managed)
check("externally managed python explains pipx", (not res.ok) and "pipx" in res.message, res.message)
res = yu.update(yu.Install("none", "x"))
check("not installed says so", not res.ok and "not installed" in res.message)
def run_boom(cmd, **kw): raise subprocess.TimeoutExpired(cmd, 1)
res = yu.update(yu.Install("pip", "x"), run=run_boom)
check("a timeout never raises", not res.ok)

# --- hot reload: the module is forgotten so the next import is fresh
fake = types.ModuleType("yt_dlp"); sub = types.ModuleType("yt_dlp.version"); sub.__version__ = "1.0"
sys.modules["yt_dlp"], sys.modules["yt_dlp.version"] = fake, sub
check("module_version reads the loaded module", yu.module_version() == "1.0")
yu.reload_ytdlp()
check("reload_ytdlp forgets yt_dlp and submodules", "yt_dlp" not in sys.modules and "yt_dlp.version" not in sys.modules)

# --- frozen download: checksum verified, files land in <data>/tools, mismatch replaces nothing
import hashlib
exe, zipapp = b"EXE-BYTES", b"ZIP-BYTES"
def mk(sums_ok=True):
    good = {"yt-dlp.exe": hashlib.sha256(exe).hexdigest(), "yt-dlp": hashlib.sha256(zipapp).hexdigest()}
    if not sums_ok: good["yt-dlp.exe"] = "0" * 64
    def f(url):
        if url == yu.GITHUB_LATEST: return json.dumps({"tag_name": "2026.11.05"}).encode()
        name = url.rsplit("/", 1)[1]
        if name == "SHA2-256SUMS": return "".join(f"{h}  {n}\n" for n, h in good.items()).encode()
        return {"yt-dlp.exe": exe, "yt-dlp": zipapp}[name]
    return f
real_name = os.name
os.name = "nt"
try:
    r = yu._update_frozen(mk())
    check("frozen update stores both files", r.ok and open(os.path.join(yu.tools_dir(), "yt-dlp.exe"), "rb").read() == exe
          and open(os.path.join(yu.tools_dir(), "yt-dlp"), "rb").read() == zipapp, r.message)
    before = open(os.path.join(yu.tools_dir(), "yt-dlp.exe"), "rb").read()
    r = yu._update_frozen(mk(sums_ok=False))
    check("bad checksum is refused and nothing is replaced", (not r.ok) and "Checksum" in r.message
          and open(os.path.join(yu.tools_dir(), "yt-dlp.exe"), "rb").read() == before, r.message)
finally:
    os.name = real_name
sys.frozen = True
yu.apply_user_tools()
check("apply_user_tools puts tools first on PATH and sys.path",
      os.environ["PATH"].split(os.pathsep)[0] == yu.tools_dir() and os.path.join(yu.tools_dir(), "yt-dlp") in sys.path)
del sys.frozen

# --- Settings section
os.environ["SPT_TUI_CACHE_DIR"] = tempfile.mkdtemp()
from spt_tui.app import SptPy
class TApp(SptPy): pass
async def settings():
    app = TApp()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause(0.3)
        app.action_prompt_settings()
        await pilot.pause(0.5)
        check("Settings shows the yt-dlp section", getattr(app, "ytdlp_select", None) is not None and app.ytdlp_select.is_attached)
        ids = [o.id for o in app.ytdlp_select.options]
        check("yt-dlp section offers update and check", ids == ["update", "check"], str(ids))
        started = []
        yu_update = yu.update
        yu.update = lambda *a, **k: (started.append(1), yu.Result(True, "yt-dlp updated with pip.", "2026.11.05"))[1]
        app.ytdlp_select.highlighted = 0
        app.ytdlp_select.action_select()
        await pilot.pause(1.0)
        yu.update = yu_update
        check("Enter on Update runs the updater", started == [1])
        app._finish_settings()
        check("Settings teardown drops the yt-dlp widgets", not hasattr(app, "ytdlp_select"))
asyncio.run(settings())

bad = [n for n, ok, _ in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} checks passed")
sys.exit(1 if bad else 0)
