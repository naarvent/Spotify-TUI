"""The big ASCII song header (figlet title + artist) is centred on the panel in
the Karaoke (`k`) and Visualizer (`v`) views.

Regression: the figlet rows were padded to the panel width and then given
`justify="center"`; Rich strips the trailing spaces of each row before it
centres, so every row was moved by a different amount (the rows of a block did
not line up) and the block ended ~11 columns right of centre. Now each block is
a rectangle centred as a unit with real spaces (`_header_rows`).

Checked on the real rendered rows (`render_lines`) at several sizes:
  1. all rows of a block sit on the same columns (block == rectangle);
  2. the block centre is the centre of the usable width (+-1 column);
  3. title and artist share the same centre axis (+-1);
  4. the header height is the same in every frame (karaoke and visualizer).
Sizes too narrow for the art fall back to one centred line: that is checked too.

Run standalone:  python tests/test_header_alignment.py
"""
import os, sys, asyncio, tempfile, textwrap, time
os.environ.setdefault("SPT_SPICY_LYRICS", "off")   # never reach the real API from a suite

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_hdralign_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.geometry import Region
from spt_tui import config
from spt_tui import visualizer as viz
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

TMP = tempfile.mkdtemp(prefix="spt_hdralign_cava_")
FAKE = os.path.join(TMP, "cava")
with open(FAKE, "w") as fh:
    fh.write(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys, time, re
        cfg = open(sys.argv[sys.argv.index("-p") + 1]).read()
        bars = int(re.search(r"bars = (\\d+)", cfg).group(1))
        while True:
            sys.stdout.write(";".join(["300"] * bars) + ";\\n"); sys.stdout.flush()
            time.sleep(0.05)
        """))
os.chmod(FAKE, 0o755)

TITLE, ARTIST = "Falling Behind", "Laufey"


class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self


class TApp(SptPy):
    def __init__(self):
        super().__init__(); self.spotify = Fake()
    def _fetch_synced_lyrics(self, **kw):
        return self._parse_lrc("[00:00.00]first line\n[00:02.00]second line\n[00:09.00]later"), "found"


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
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


def rendered(box):
    """Plain text of every row of the box CONTENT as drawn (the widget's own
    padding, top rows and left columns, cut off)."""
    g = box.gutter
    strips = box.render_lines(Region(0, 0, box.size.width + g.left, box.size.height + g.top))
    return ["".join(seg.text for seg in st)[g.left:] for st in strips][g.top:]


def check_header(label, app, box, width, header_rows, title):
    plain = rendered(box)
    head = plain[:header_rows]
    parts = app._header_parts(True, title, width)
    arts = [p for p in parts if p[0] == "art"]
    if not arts:
        txt = [r for r in head if r.strip()]
        check(f"{label}: fallback is one centred line", len(txt) == 1, str(txt))
        if txt:
            l = len(txt[0]) - len(txt[0].lstrip()); r = len(txt[0].rstrip())
            check(f"{label}: fallback centre within 1 col of {width / 2}", abs((l + r) / 2 - width / 2) <= 1,
                  f"{(l + r) / 2}")
        return
    check(f"{label}: title and artist as art", len(arts) == 2)
    centres = []
    row = 0
    for _, lines, _c in arts:
        bw = max(len(x) for x in lines)
        block = head[row:row + len(lines)]
        row += len(lines)
        # one common offset puts every art row exactly where it is drawn
        # (and nothing else is on those rows)
        fits = [o for o in range(0, max(1, width - bw + 1))
                if all(r.strip() == "" if not l.strip() else
                       (r[:o].strip() == "" and r[o:o + bw].rstrip() == l.rstrip().ljust(bw).rstrip()
                        and r[o + bw:].strip() == "")
                       for r, l in zip(block, lines))]
        check(f"{label}: all {len(lines)} rows of a block start on the same column", len(fits) == 1, str(fits))
        off = fits[0] if fits else 0
        centre = off + bw / 2
        centres.append(centre)
        check(f"{label}: block centre {centre} within 1 col of {width / 2}", abs(centre - width / 2) <= 1)
    check(f"{label}: title and artist share one axis", abs(centres[0] - centres[1]) <= 1, str(centres))


async def run_vis(size):
    viz.find_cava = lambda: FAKE
    config.LOCAL_CFG = {}
    app = TApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._bar_last_track = {"name": TITLE, "artists": [{"name": ARTIST}]}
        app._now_internal_is_playing = True
        await pilot.press("v")
        await poll(pilot, lambda: getattr(app, "_vis_proc", None) is not None and app._vis_paints > 2)
        w, h = app._vis_size()
        heads = set()
        for _ in range(4):
            await asyncio.sleep(0.06); app._vis_tick(); await pilot.pause()
            heads.add(app._vis_header_count)
        check(f"visualizer {size}: header height fixed", len(heads) == 1, str(heads))
        check_header(f"visualizer {size} (w={w})", app, app._vis_box, w, app._vis_header_count,
                     f"{TITLE} — {ARTIST}")
        app._stop_visualizer_process()


async def run_karaoke(size):
    config.LOCAL_CFG = {"lyrics_mode": "karaoke"}
    app = TApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause(); pause_intervals(app)
        app._bar_last_track = {"id": "h1", "name": TITLE, "artists": [{"name": ARTIST}], "album": {"name": "LP"},
                               "duration_ms": 60000}
        app._now_internal_track_id = "h1"; app._now_internal_pos_ms = 0
        app._now_internal_is_playing = False; app._now_internal_dur_ms = 60000
        app.action_toggle_lyrics()
        await poll(pilot, lambda: getattr(app, "_lyrics_lines", None))
        app._lyrics_interval.pause()
        await pilot.pause(); await pilot.pause()
        w, h = app._lyrics_box_size()
        counts = set()
        for pos in (500, 3000, 9500):
            app._now_internal_pos_ms = pos
            app._update_lyrics_highlight(); await pilot.pause()
            counts.add(app._karaoke_header_rows)
        check(f"karaoke {size}: header height fixed", len(counts) == 1, str(counts))
        title = getattr(app, "_lyrics_title", "") or f"{TITLE} — {ARTIST}"
        # the header rows are the first ones of the status-less top block
        top, _, _ = app._karaoke_layout()
        n_head = len(app._header_rows(big=True, width=w)) if app._header_parts(True, title, w)[0][0] == "art" \
            else len(app._header_rows(big=False, width=w))
        check_header(f"karaoke {size} (w={w})", app, app.lyrics_box, w, n_head, title)


def main():
    for size in ((160, 50), (140, 45), (120, 40), (100, 28), (90, 24)):
        asyncio.run(run_vis(size))
        asyncio.run(run_karaoke(size))
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
