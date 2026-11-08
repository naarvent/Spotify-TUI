"""Visualizer (`v`) with a FAKE cava (a script printing ascii frames).

* config: raw/ascii/1000/";"/"\\n" to stdout; only [input] method/source taken
  from the user's cava config (commented lines ignored; file never written);
* frames parsed, latest kept; render: exact rows, ▁▂▃▄▅▆▇█ eighths, gradient,
  mirror; identical frames not repainted;
* app: v opens it in #right's frame, v again / Esc (one) close it and KILL cava;
  another view also kills it; paused playback SIGSTOPs cava; no cava -> notice;
* if the parent dies (SIGKILL), cava dies too (PR_SET_PDEATHSIG).

Run standalone:  python tests/test_visualizer.py
"""
import os, sys, asyncio, tempfile, time, subprocess, signal, textwrap

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_vis_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config, visualizer as viz
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

TMP = tempfile.mkdtemp(prefix="spt_fakecava_")
FAKE = os.path.join(TMP, "cava")
with open(FAKE, "w") as fh:
    fh.write(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys, time, re
        cfg = open(sys.argv[sys.argv.index("-p") + 1]).read()
        open({os.path.join(TMP, "seen_config")!r}, "w").write(cfg)
        bars = int(re.search(r"bars = (\\d+)", cfg).group(1))
        i = 0
        while True:
            i += 1
            vals = [((i * 37 + b * 101) % 1001) for b in range(bars)]
            sys.stdout.write(";".join(map(str, vals)) + ";\\n"); sys.stdout.flush()
            time.sleep(0.02)
        """))
os.chmod(FAKE, 0o755)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_pure():
    cfg = viz.build_config(40, 30, {"method": "pipewire", "source": "auto"})
    check("config: raw ascii to stdout",
          all(x in cfg for x in ("method = raw", "data_format = ascii", "ascii_max_range = 1000",
                                 "bar_delimiter = 59", "frame_delimiter = 10", "raw_target = /dev/stdout",
                                 "bars = 40", "framerate = 30")))
    check("config: user input source carried", "[input]\nmethod = pipewire\nsource = auto" in cfg)
    check("config: no [input] when the user sets none", "[input]" not in viz.build_config(10, 30, {}))
    user = os.path.join(TMP, "usercfg")
    with open(user, "w") as fh:
        fh.write("[general]\nbars = 3\n[input]\n; method = pulse\nmethod = pipewire ; trailing\n# source = x\n")
    before = open(user).read()
    check("user config: only set keys, comments ignored", viz.user_input_settings(user) == {"method": "pipewire"},
          str(viz.user_input_settings(user)))
    check("user config is never written", open(user).read() == before)
    check("missing user config: nothing", viz.user_input_settings(os.path.join(TMP, "none")) == {})
    check("parse_frame", viz.parse_frame("0;500;1000;2000;\n") == [0, 500, 1000, 1000]
          and viz.parse_frame("garbage") is None and viz.parse_frame("\n") is None)
    rows = viz.render_bars([0, 500, 1000], rows=4, width=20, accent="#27e1c1")
    check("render: exactly the rows asked", len(rows) == 4 and all(r.no_wrap for r in rows))
    col = [r.plain.strip("\n") for r in rows]
    check("render: full bar is █ on every row, half bar reaches half",
          all(c.rstrip()[-1] == "█" for c in col) and col[-1].split()[0] == "█" and col[0].strip().startswith("█"),
          str(col))
    check("render: centred", rows[0].plain.startswith(" " * ((20 - 5) // 2)))
    check("gradient: dark floor, bright top", viz.gradient("#27e1c1", 5)[0] != viz.gradient("#27e1c1", 5)[-1])
    m = viz.render_bars([1000, 0, 0, 0], rows=1, width=7, mirror=True)
    check("mirror: bass in the middle", m[0].plain.strip() == "  █ █" or "█ █" in m[0].plain, repr(m[0].plain))
    check("frame_key equal for equal frames", viz.frame_key([1, 2], 4) == viz.frame_key([1, 2], 4))
    check("bar count follows the width", viz.bar_count(80) == 40 and viz.bar_count(10) == 5)


def test_process():
    p = viz.CavaProcess(8, 30, cava=FAKE, input_settings={})
    check("fake cava starts", p.start())
    t0 = time.time()
    while p.latest() is None and time.time() - t0 < 5:
        time.sleep(0.02)
    f = p.latest()
    check("frames read (latest kept)", f is not None and len(f) == 8 and p.frames_read >= 1)
    pid = p.proc.pid
    p.pause(); n = p.frames_read; time.sleep(0.3)
    check("pause = SIGSTOP: no frames while paused", p.frames_read - n <= 1, f"{p.frames_read - n}")
    p.resume(); time.sleep(0.3)
    check("resume: frames again", p.frames_read > n + 2)
    p.pause()
    cfg_dir = p._cfg_dir
    p.stop()
    time.sleep(0.1)
    check("stop() kills it (even while stopped)", not alive(pid))
    check("its temporary config is removed", cfg_dir and not os.path.exists(cfg_dir))
    q = viz.CavaProcess(4, 30, cava=None, input_settings={})
    q.cava = None
    check("no cava: start() is False with a reason", not q.start() and "not installed" in (q.error or ""))


def test_parent_death():
    """spt killed with SIGKILL (no cleanup at all): cava must not be orphaned."""
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r})
        from spt_tui import visualizer as viz
        p = viz.CavaProcess(4, 30, cava={FAKE!r}, input_settings={{}})
        p.start(); print(p.proc.pid, flush=True)
        time.sleep(30)
        """)
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline().strip())
    check("child running", alive(child))
    parent.send_signal(signal.SIGKILL); parent.wait()
    t0 = time.time()
    while alive(child) and time.time() - t0 < 3:
        time.sleep(0.05)
    check("parent SIGKILLed -> cava dies too (PR_SET_PDEATHSIG)", not alive(child))


class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self


class TApp(SptPy):
    def __init__(self): super().__init__(); self.spotify = Fake()


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_queue_interval", "_devices_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass


async def poll(pilot, fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        await pilot.pause()
        try:
            if fn(): return True
        except Exception:
            pass
        await asyncio.sleep(0.03)
    return False


async def test_app():
    orig = viz.find_cava
    viz.find_cava = lambda: FAKE
    try:
        config.LOCAL_CFG = {}
        app = TApp()
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app._bar_last_track = {"name": "Song", "artists": [{"name": "Band"}]}
            app._now_internal_is_playing = True
            await pilot.press("v")
            ok = await poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None and app._vis_paints > 2)
            check("v opens the visualizer and paints frames", ok)
            proc = app._vis_proc
            pid = proc.proc.pid
            check("in #right's frame with its title", app.right_panel.has_class("table-view")
                  and "Visualizer" in str(app.right_panel.border_title))
            check("bars = panel width / 2", proc.bars == viz.bar_count(app._vis_size()[0]))
            seen = open(os.path.join(TMP, "seen_config")).read()
            check("cava got the private raw/ascii config", "method = raw" in seen and "data_format = ascii" in seen)
            want = [t.plain for t in app._fixed_rows(app._render_header(big=True, title="Song — Band",
                                                                        width=app._vis_size()[0]), app._vis_size()[0])]
            got = [t.plain for t in app._vis_rows[:app._vis_header_count]]
            check("the lyrics view's song header on top (same code)", got[:len(want)] == want and len(want) >= 1,
                  f"{got[:2]} vs {want[:2]}")
            check("frame = box height exactly", len(app._vis_rows) == app._vis_size()[1],
                  f"{len(app._vis_rows)} vs {app._vis_size()[1]}")
            # Header rows identical across frames; changes with the track.
            heads = set()
            for _ in range(6):
                await asyncio.sleep(0.05); app._vis_tick()
                heads.add(tuple(t.plain for t in app._vis_rows[:app._vis_header_count]))
            check("header rows do not change between frames", len(heads) == 1)
            app._bar_last_track = {"name": "Other", "artists": [{"name": "Act"}]}
            app._vis_tick(); await pilot.pause()
            want2 = [t.plain for t in app._fixed_rows(app._render_header(big=True, title="Other — Act",
                                                                         width=app._vis_size()[0]), app._vis_size()[0])]
            check("header follows the track", [t.plain for t in app._vis_rows[:len(want2)]] == want2)
            n_head = app._vis_header_count
            app._apply_title_bar(False); await pilot.pause(); await pilot.pause()
            app._vis_last_key = None; app._vis_tick()
            check("title bar hidden: still exactly the (taller) box", len(app._vis_rows) == app._vis_size()[1]
                  and app._vis_header_count == n_head)
            app._apply_title_bar(True); await pilot.pause(); await pilot.pause()
            app._bar_last_track = {"name": "Song", "artists": [{"name": "Band"}]}
            # Identical frames are not repainted: freeze the frame.
            real_latest = proc.latest
            proc.latest = lambda: [500] * proc.bars      # cava keeps sending the same frame
            app._vis_tick()
            n = app._vis_paints
            for _ in range(5):
                app._vis_tick()
            check("identical frames are not repainted", app._vis_paints == n, f"{n} -> {app._vis_paints}")
            proc.latest = real_latest
            # Paused song: cava SIGSTOPped, slow tick.
            app._now_internal_is_playing = False
            app._vis_tick(); await pilot.pause()
            check("nothing playing: cava paused and the tick slows down",
                  proc.paused and app._vis_period == app.VIS_IDLE_TICK)
            app._now_internal_is_playing = True
            app._vis_tick()
            check("playing again: resumed at the FPS", not proc.paused and abs(app._vis_period - 1 / 30) < 1e-9)
            await pilot.press("M"); await pilot.pause()
            check("default is mirrored; Shift+M switches to bars", app._vis_mirror is False)
            await pilot.press("m"); await pilot.pause()
            check("m is still Mute (not mirror)", app._vis_mirror is False)
            await pilot.press("v"); await pilot.pause(); await pilot.pause()
            check("v again closes it and goes to the menu", app._right_view is None and not app._vis_on)
            time.sleep(0.1)
            check("…and cava is dead", not alive(pid))
            # Esc (one) closes it too.
            await pilot.press("v")
            await poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None)
            pid = app._vis_proc.proc.pid
            await pilot.press("escape"); await pilot.pause(); await pilot.pause()
            check("one Esc closes it, back to the menu", app._right_view is None and not alive(pid))
            # Another view also kills it.
            await pilot.press("v")
            await poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None)
            pid = app._vis_proc.proc.pid
            app.action_help(); await pilot.pause()
            time.sleep(0.1)
            check("opening another view kills cava", not alive(pid) and not app._vis_on)
            await pilot.press("v")
            await poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None)
            last_pid = app._vis_proc.proc.pid
        time.sleep(0.1)
        check("closing the app kills cava", not alive(last_pid))

        viz.find_cava = lambda: None
        app = TApp()
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await pilot.press("v"); await pilot.pause()
            check("no cava: clear notice, no view, no crash",
                  "needs cava" in str(app.status_line.render()) and not getattr(app, "_vis_on", False))
    finally:
        viz.find_cava = orig


if __name__ == "__main__":
    test_pure()
    test_process()
    test_parent_death()
    asyncio.run(test_app())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
