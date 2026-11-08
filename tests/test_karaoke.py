"""Karaoke lyrics (word by word).

* approximation: a line's sung time is split between its words by length,
  capped (a long gap is not "sung"), and a long gap becomes an interlude;
* enhanced LRC: <mm:ss.xx> word stamps parsed, cleaned out of the line text
  (the line view shows clean text), honoured by karaoke, shifted by [offset:];
* TTML: line/word times, background vocals, duet agents (left/right),
  romanisation (inline span and head transliteration), bad input refused,
  DOCTYPE/ENTITY refused;
* rendering: char wipe inside the current word, fading by distance, "● ● ●"
  filling, frame_key equal while nothing visible changes;
* app: line mode stays the default and unchanged; `k` toggles (remembered),
  tick 0.1 s only in karaoke; `]`/`[` shift the offset live; a local TTML /
  LRC file in <data>/lyrics/ beats LRCLIB (no network); the cache keeps
  working with word-timed lines; status says "approximate" or "word-synced";
* performance: render cost per frame and how many of the 0.1 s ticks really
  repaint (printed, with a generous bound so slow machines do not fail).

Run standalone:  python tests/test_karaoke.py
"""
import os, sys, asyncio, tempfile, time
os.environ.setdefault("SPT_SPICY_LYRICS", "off")   # never reach the real API from a suite

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_karaoke_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config, karaoke
from spt_tui.app import SptPy

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def test_approx():
    kl = karaoke.from_lines([(1000, "Hello world"), (3000, "Second line here"), (20000, "After the gap")], 40000)
    lines = [k for k in kl if not k.interlude]
    check("three lines", [k.text for k in lines] == ["Hello world", "Second line here", "After the gap"])
    w = lines[1].words
    check("words cover the sung span in order",
          w[0].start == 3000 and w[-1].end == lines[1].end and all(a.end == b.start for a, b in zip(w, w[1:])))
    check("longer word, longer time", (w[0].end - w[0].start) > (w[1].end - w[1].start))   # 'Second ' > 'line '
    check("sung time is capped (not stretched over the 16 s gap)", lines[1].end - lines[1].start < 3000,
          str(lines[1].end))
    gaps = [k for k in kl if k.interlude]
    check("the long gap is an interlude", len(gaps) == 1 and gaps[0].start == lines[1].end and gaps[0].end == 20000)
    check("approximate, not real", not karaoke.has_real_words(kl) and not lines[0].real_words
          and karaoke.approximate_count(kl) == 3)
    kl = karaoke.from_lines([(9000, "Late start")])
    check("long intro becomes an interlude", kl[0].interlude and kl[0].end == 9000)
    check("empty input", karaoke.from_lines([]) == [])
    kl = karaoke.from_lines([(1000, "a"), (1500, ""), (9000, "b")])
    check("blank LRC lines are gaps, not lines", [k.text for k in kl if not k.interlude] == ["a", "b"])


def test_enhanced_lrc():
    clean, words = karaoke.parse_enhanced_words("<00:01.00>Hello <00:01.50>world<00:02.20>", 1000)
    check("enhanced: clean text", clean == "Hello world", clean)
    check("enhanced: stamps", words == [[1000, "Hello "], [1500, "world"], [2200, ""]], str(words))
    check("no stamps -> None", karaoke.parse_enhanced_words("plain line", 0) == ("plain line", None))
    app = SptPy()
    lines = app._parse_lrc("[ti:x]\n[offset:+500]\n[00:01.00]<00:01.00>Hello <00:01.50>world<00:02.20>\n[00:05.00]plain")
    check("_parse_lrc keeps clean text for the line view", [l[1] for l in lines] == ["Hello world", "plain"], str(lines))
    check("…plain lines stay 2-tuples", len(lines[1]) == 2)
    check("…[offset:] shifts word stamps too", lines[0][0] == 500 and lines[0][2][0][0] == 500 and lines[0][2][1][0] == 1000,
          str(lines[0]))
    kl = karaoke.from_lines(lines)
    k = [x for x in kl if not x.interlude][0]
    check("karaoke uses the real word times", k.real_words and [(w.start, w.end) for w in k.words] == [(500, 1000), (1000, 1700)],
          str([(w.start, w.end) for w in k.words]))


TTML = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
  xmlns:itunes="http://music.apple.com/lyric-ttml-internal">
 <head><metadata><iTunesMetadata><transliterations><transliteration xml:lang="ja-Latn">
   <text for="L2">konnichiwa</text></transliteration></transliterations></iTunesMetadata></metadata></head>
 <body><div>
  <p begin="00:01.000" end="00:03.000" ttm:agent="v1" itunes:key="L1"><span begin="00:01.000" end="00:01.800">Hello</span> <span begin="00:01.800" end="00:03.000">there</span><span ttm:role="x-bg"><span begin="00:02.000" end="00:02.900">(yeah)</span></span></p>
  <p begin="4.5s" end="6s" ttm:agent="v2" itunes:key="L2"><span begin="4.5s" end="6s">こんにちは</span></p>
  <p begin="0:12.0" end="0:14.0" ttm:agent="v1">Untimed words here<span ttm:role="x-roman">romaji line</span></p>
 </div></body></tt>"""


def test_ttml():
    kl = karaoke.parse_ttml(TTML)
    lines = [k for k in kl if not k.interlude]
    check("TTML lines", [k.text for k in lines] == ["Hello there", "こんにちは", "Untimed words here"], str([k.text for k in lines]))
    check("word times", [(w.start, w.end) for w in lines[0].words] == [(1000, 1800), (1800, 3000)])
    check("seconds format", lines[1].start == 4500 and lines[1].end == 6000)
    check("background vocals", [w.text for w in lines[0].bg] == ["(yeah)"] and lines[0].bg[0].start == 2000)
    check("duet agents", lines[0].agent == "v1" and lines[1].agent == "v2")
    check("romanisation from the head", lines[1].roman == "konnichiwa")
    check("inline x-roman span", lines[2].roman == "romaji line" and "romaji" not in lines[2].text)
    check("a <p> without spans: approximate words", not lines[2].real_words and lines[2].words)
    check("interlude in the 6 s gap", any(k.interlude and k.start == 6000 and k.end == 12000 for k in kl))
    check("line tuples for the line view", karaoke.to_line_tuples(kl)[0] == (1000, "Hello there"))
    for bad, label in (("<html/>", "not TTML"), ("<tt", "broken XML"), ('<tt xmlns="x"><body/></tt>', "no lines"),
                       ('<!DOCTYPE tt [<!ENTITY a "aaaa">]><tt>&a;</tt>', "DOCTYPE/ENTITY")):
        try:
            karaoke.parse_ttml(bad); ok = False
        except ValueError:
            ok = True
        check(f"refused: {label}", ok)
    check("time formats", karaoke.parse_ttml_time("1:02.5") == 62500 and karaoke.parse_ttml_time("00:01:02.500") == 62500
          and karaoke.parse_ttml_time("62500ms") == 62500 and karaoke.parse_ttml_time("x") is None)


def plain(texts):
    return [t.plain for t in texts]


def test_render():
    kl = karaoke.from_lines([(1000, "Hello world"), (3000, "Second line"), (20000, "Third")], 40000)
    R = lambda pos: karaoke.render_fixed(kl, pos, width=60, rows=11)
    out = R(1000 + 250)                     # inside "Hello "
    cur = out[5]
    check("current line on the middle row", cur.plain == "Hello world", str(plain(out)))
    styles = [str(sp.style) for sp in cur.spans]
    check("sung chars in the accent, the end of the line still unsung",
          "e6ff71" in styles[0] and styles[-1] == "bold " + karaoke.UNSUNG_HEX, str(styles))
    k1 = karaoke.frame_key_fixed(kl, 1250)
    check("frame key stable when nothing visible changes", karaoke.frame_key_fixed(kl, 1251) == k1)
    check("frame key changes as the wipe moves", karaoke.frame_key_fixed(kl, 1700) != k1)
    nxt = next(t for t in out if t.plain == "Second line")
    far = next(t for t in out if t.plain == "Third")
    check("fading by distance (next line brighter than a far one)",
          str(nxt.style) != str(far.style) and str(nxt.style) == karaoke.FADE[0], f"{nxt.style} {far.style}")
    gap = next(k for k in kl if k.interlude)
    def lit(pos):
        t = R(pos)[5]
        return sum(1 for sp in t.spans if "e6ff71" in str(sp.style))
    check("interlude: dots on the middle row", R(gap.start + 10)[5].plain == "● ● ●")
    check("interlude dots fill up", lit(gap.start + 10) < lit(gap.end - 10), f"{lit(gap.start + 10)} {lit(gap.end - 10)}")
    tt = karaoke.render_fixed(karaoke.parse_ttml(TTML), 1500, width=60, rows=11)
    check("duet: v1 left", tt[5].plain == "Hello there" and tt[5].justify == "left", str(plain(tt)))
    check("background vocals under the line, parens not doubled", tt[6].plain == "(yeah)", str(plain(tt)))
    empty = karaoke.render_fixed([], 0, width=60, rows=11)
    check("no lines", len(empty) == 11 and empty[5].plain == "No Lyrics Found")


def test_perf():
    """Cost of a frame and of a tick that does not repaint (pure, no Textual)."""
    lines = [(i * 3500, f"line number {i} with a few words to sing along") for i in range(80)]
    kl = karaoke.from_lines(lines, 80 * 3500)
    n = 2000
    t0 = time.perf_counter()
    for i in range(n):
        karaoke.render_fixed(kl, (i * 37) % (80 * 3500), width=80, rows=11)
    per_render = (time.perf_counter() - t0) / n * 1000
    t0 = time.perf_counter()
    for i in range(n):
        karaoke.frame_key_fixed(kl, (i * 37) % (80 * 3500))
    per_key = (time.perf_counter() - t0) / n * 1000
    # Ticks at 0.1 s over 60 s of song: how many frames really change.
    keys = [karaoke.frame_key_fixed(kl, 10000 + k * 100) for k in range(600)]
    changes = sum(1 for a, b in zip(keys, keys[1:]) if a != b)
    print(f"  perf: render {per_render:.3f} ms/frame, frame_key {per_key:.4f} ms/tick, "
          f"{changes}/599 ticks repaint over 60 s at 10 Hz")
    check("render well under a tick (< 10 ms)", per_render < 10, f"{per_render:.3f} ms")
    check("frame_key is cheap (< 1 ms)", per_key < 1, f"{per_key:.4f} ms")
    check("not every tick repaints", changes < 599, str(changes))


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
class Fake:
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self


class TApp(SptPy):
    def __init__(self):
        super().__init__(); self.spotify = Fake()
        self.fetches = 0
    def _fetch_synced_lyrics(self, **kw):
        self.fetches += 1
        return self._parse_lrc("[00:00.00]first line\n[00:02.00]<00:02.00>word <00:02.50>timed<00:03.00>\n"
                               "[00:09.00]later"), "found"


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


def box_text(app):
    b = getattr(app, "lyrics_box", None)
    if b is None:
        return ""
    r = getattr(b, "content", None)
    if isinstance(r, str):
        from rich.text import Text
        r = Text.from_markup(r)
    try:
        from rich.console import Console
        c = Console(width=120, record=True, color_system=None)
        c.print(r)
        return c.export_text()
    except Exception:
        return str(r)


def set_track(app, tid="trk1", pos=0, playing=False):
    app._bar_last_track = {"id": tid, "name": "Song", "artists": [{"name": "Band"}], "album": {"name": "LP"},
                           "duration_ms": 60000}
    app._now_internal_track_id = tid
    app._now_internal_pos_ms = pos
    app._now_internal_is_playing = playing
    app._now_internal_dur_ms = 60000


async def test_app_modes():
    config.LOCAL_CFG = {}
    app = TApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app, pos=2600)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        await pilot.pause()
        check("default mode: lines", app._lyrics_mode() == "lines")
        check("line view: 0.4 s tick as before", abs(app._lyrics_interval._interval - 0.4) < 1e-6)
        t = box_text(app)
        check("line view shows the clean text of an enhanced line", "word timed" in t and "<00:" not in t, t[-300:])
        check("line view has no karaoke status", "karaoke" not in t)

        await pilot.press("k"); await pilot.pause()
        check("k: karaoke mode", app._lyrics_mode() == "karaoke")
        check("…remembered in the config", config.LOCAL_CFG.get("lyrics_mode") == "karaoke")
        check("karaoke while paused keeps the 0.4 s tick", abs(app._lyrics_interval._interval - 0.4) < 1e-6)
        app._now_internal_is_playing = True
        from spt_tui.app import playback as pb
        app._now_internal_last_wall_ms = pb._mono() * 1000.0
        app._update_lyrics_highlight()
        check("karaoke while playing: 0.1 s tick", abs(app._lyrics_interval._interval - 0.1) < 1e-6)
        app._now_internal_is_playing = False; app._now_internal_pos_ms = 2600
        app._update_lyrics_highlight()
        check("paused again: back to 0.4 s", abs(app._lyrics_interval._interval - 0.4) < 1e-6)
        t = box_text(app)
        check("karaoke status present", "karaoke ·" in t, t[-400:])
        check("partial word sync is reported as approximate (not every line has word times)",
              "approximate" in t, t[-400:])

        before = app._lyrics_paints
        for _ in range(5):
            app._update_lyrics_highlight()
        check("paused playback: ticks do not repaint", app._lyrics_paints == before,
              f"{before} -> {app._lyrics_paints}")

        await pilot.press("right_square_bracket"); await pilot.pause()
        check("] shifts the offset +0.25 s", app._lyrics_offset() == 250)
        check("…shown in the status", "offset +0.25 s" in box_text(app))
        await pilot.press("left_curly_bracket"); await pilot.pause()
        check("{ shifts -1 s", app._lyrics_offset() == -750)
        check("offset changes the lyrics position", app._lyrics_pos() == 2600 - 750)

        await pilot.press("k"); await pilot.pause()
        check("k again: back to lines with the 0.4 s tick",
              app._lyrics_mode() == "lines" and abs(app._lyrics_interval._interval - 0.4) < 1e-6)
        check("offset also applies to the line view", app._lyrics_pos() == 1850)
        check("one LRCLIB fetch, then the cache", app.fetches == 1)

        # Cached word-timed lines survive a reload (JSON round trip).
        app._lyrics_cache_data = None
        cached = app._lyrics_cache_get("trk1", "Song", "Band", 60000, "LP")
        check("cache keeps word-timed lines", cached and len(cached[1]) == 3 and cached[1][2][0][0] == 2000,
              str(cached)[:200])
        app.action_toggle_lyrics(); await pilot.pause()

    # Keys outside the lyrics view do nothing.
    config.LOCAL_CFG = {}
    app = TApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        await pilot.press("k"); await pilot.press("right_square_bracket"); await pilot.pause()
        check("k / ] outside lyrics: nothing changes", app._lyrics_mode() == "lines"
              and not getattr(app, "_lyrics_offsets", None))


async def test_app_local_ttml():
    config.LOCAL_CFG = {"lyrics_mode": "karaoke"}
    d = os.path.join(config.CACHE_DIR, "lyrics")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "band - song.ttml"), "w", encoding="utf-8") as fh:
        fh.write(TTML)
    app = TApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app, tid="other", pos=1500)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        await pilot.pause()
        check("local TTML used (no LRCLIB fetch)", app.fetches == 0 and app._lyrics_source == "TTML")
        t = box_text(app)
        check("karaoke from the TTML is word-synced (and says which lines are not)",
              "word-synced · TTML (≈ 1 line approximate)" in t, t[-400:])
        check("its lines are on screen", "Hello there" in t)
        # Playing: ticks repaint only when the frame changes; measure.
        set_track(app, tid="other", pos=1000, playing=True)
        from spt_tui.app import playback as pb
        app._now_internal_last_wall_ms = pb._mono() * 1000.0
        app._update_lyrics_highlight()          # switches to the 10 Hz tick…
        app._lyrics_interval.pause()            # …which this loop replaces, to count ticks
        app._lyrics_paints = 0
        cpu0, wall0 = time.process_time(), time.perf_counter()
        ticks = 0
        while time.perf_counter() - wall0 < 2.0:
            app._update_lyrics_highlight(); ticks += 1
            await asyncio.sleep(0.1)
        cpu = time.process_time() - cpu0
        print(f"  app perf: {ticks} ticks in 2 s, {app._lyrics_paints} repaints, CPU {cpu * 1000:.0f} ms "
              f"(incl. Textual + this test loop)")
        check("while playing, frames do repaint", app._lyrics_paints > 0)
        check("but not on every tick", app._lyrics_paints <= ticks, f"{app._lyrics_paints}/{ticks}")
    os.remove(os.path.join(d, "band - song.ttml"))

    # A broken file is ignored with a notice and LRCLIB is used.
    with open(os.path.join(d, "band - song.ttml"), "w", encoding="utf-8") as fh:
        fh.write("<tt><body></body></tt>")
    config.LOCAL_CFG = {}
    app = TApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app, tid="x2")
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        check("broken local file: falls back to LRCLIB", app.fetches == 1)
        check("…and says so", "Lyrics file ignored" in str(app.status_line.render()))
    os.remove(os.path.join(d, "band - song.ttml"))


LONG = "this is a really long lyric line that has to wrap over more than one row of the panel " * 3

GEOM_TTML = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>
  <p begin="0:01.0" end="0:02.5"><span begin="0:01.0" end="0:02.5">short</span></p>
  <p begin="0:03.0" end="0:05.0"><span begin="0:03.0" end="0:05.0">""" + LONG + """</span></p>
  <p begin="0:06.0" end="0:08.0"><span begin="0:06.0" end="0:08.0">with a choir</span><span ttm:role="x-bg"><span begin="0:06.5" end="0:07.5">(ooh ooh)</span></span><span ttm:role="x-roman">roman line</span></p>
  <p begin="0:20.0" end="0:22.0"><span begin="0:20.0" end="0:22.0">after the interlude</span></p>
  <p begin="0:23.0" end="0:25.0"><span begin="0:23.0" end="0:25.0">日本語の歌詞の行がとても長くて画面の幅を超えてしまう場合もある日本語の歌詞の行</span></p>
</div></body></tt>"""


def test_fixed_geometry_pure():
    kl = karaoke.parse_ttml(GEOM_TTML)
    shapes = []
    for pos in (500, 1500, 4000, 6800, 12000, 21000, 24000, 40000):
        rows = karaoke.render_fixed(kl, pos, width=50, rows=11)
        shapes.append(len(rows))
        if not all(r.no_wrap and r.cell_len <= 50 for r in rows):
            check(f"every row is one cropped row at {pos}", False, str([r.plain for r in rows]))
    check("render_fixed always returns exactly the body height", set(shapes) == {11}, str(shapes))
    def anchor_text(pos):
        return karaoke.render_fixed(kl, pos, width=50, rows=11)[11 // 2].plain
    check("current line on the middle row: short", anchor_text(1500) == "short")
    check("…long line (first wrapped row)", anchor_text(4000).startswith("this is a really long"))
    check("…choir line", anchor_text(6800) == "with a choir")
    check("…interlude dots", anchor_text(12000) == "● ● ●")
    check("…CJK line (wide chars measured in cells)", anchor_text(24000).startswith("日本語"))
    long_rows = karaoke.render_fixed(kl, 4000, width=50, rows=11)
    check("a long line takes at most MAX_LINE_ROWS rows and ends with …",
          long_rows[6].plain.endswith("…") and not long_rows[7].plain.startswith("this"), str([r.plain for r in long_rows]))
    choir = karaoke.render_fixed(kl, 6800, width=50, rows=11)
    check("choir under the line, roman under that", choir[6].plain == "(ooh ooh)" and choir[7].plain == "roman line",
          str([r.plain for r in choir]))
    check("choir in grey italic", "italic" in str(choir[6].spans[-1].style if choir[6].spans else choir[6].style))
    cur = karaoke.render_fixed(kl, 1750, width=50, rows=11)[5]
    styles = [str(sp.style) for sp in cur.spans]
    check("truecolor soft edge: more than two colours in the line being sung",
          len(set(styles)) >= 3, str(styles))
    check("blend()", karaoke.blend("#000000", "#ffffff", 0.5) == "#808080")


async def test_fixed_geometry_app():
    """The header and status rows stay on the same screen rows across frames
    (normal line, long line, choir, interlude, CJK)."""
    d = os.path.join(config.CACHE_DIR, "lyrics")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "band - song.ttml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(GEOM_TTML)
    for size in ((120, 40), (100, 28)):
        config.LOCAL_CFG = {"lyrics_mode": "karaoke"}
        app = TApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause(); pause_intervals(app)
            set_track(app, tid="g1", pos=0)
            app.action_toggle_lyrics()
            await poll(lambda: app._lyrics_lines)
            app._lyrics_interval.pause()
            await pilot.pause(); await pilot.pause()
            seen = []
            for pos in (1500, 4000, 6800, 12000, 21000, 24000):
                app._now_internal_pos_ms = pos
                app._update_lyrics_highlight()
                await pilot.pause()
                strips = app.lyrics_box.render_lines(app.lyrics_box.region.reset_offset)
                plain = ["".join(seg.text for seg in st) for st in strips]
                st_row = next((i for i, l in enumerate(plain) if "karaoke ·" in l), None)
                head = [l for l in plain[: app._karaoke_header_rows]]
                n_rows = len(app._karaoke_rows)
                box_h = app.lyrics_box.content_size.height
                anchor = app._karaoke_header_rows + (n_rows - app._karaoke_header_rows - app._karaoke_credit_rows) // 2
                seen.append((st_row, tuple(head), n_rows == box_h, app.lyrics_box.region, plain[anchor + 1].strip()[:12]))
            check(f"{size}: status row never moves", len({s[0] for s in seen}) == 1 and seen[0][0] is not None,
                  str([s[0] for s in seen]))
            check(f"{size}: title rows identical in every frame", len({s[1] for s in seen}) == 1)
            check(f"{size}: the frame is exactly the box height", all(s[2] for s in seen))
            check(f"{size}: the box itself never moves or resizes", len({s[3] for s in seen}) == 1)
            check(f"{size}: current line always on the same row",
                  [s[4] for s in seen][:4] == ["short", "this is a re", "with a choir", "● ● ●"], str([s[4] for s in seen]))
    os.remove(path)


if __name__ == "__main__":
    test_approx()
    test_enhanced_lrc()
    test_ttml()
    test_render()
    test_perf()
    test_fixed_geometry_pure()
    asyncio.run(test_fixed_geometry_app())
    asyncio.run(test_app_modes())
    asyncio.run(test_app_local_ttml())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
