"""Phase 6 tests: the welcome screen adapts to the terminal size (large /
medium / small / minimal), updates on resize without touching other open views
and without flicker, and shows the new author 'by naarvent_ :)'.

Run standalone:  python tests/test_welcome_responsive.py
"""
import os, sys, asyncio, traceback
os.environ.setdefault("SPT_SPICY_LYRICS", "off")   # never reach the real API from a suite

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.app import SptPy

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

def pause_intervals(app):
    for a in ("_now_sync_interval","_now_tick_interval","_now_interval","_devices_interval","_queue_interval"):
        t = getattr(app, a, None)
        if t is not None:
            try: t.pause()
            except Exception: pass

def right_text(app):
    return str(getattr(app.right_panel, "_Static__content", "") or "")

def has_braille(s):
    return any(ord(c) >= 0x2800 for c in s)

async def settle(pilot, n=8):
    for _ in range(n):
        await pilot.pause()


def test_initial_variant_per_size():
    expect = {(160, 40): "large", (140, 40): "medium", (70, 20): "small", (40, 12): "minimal"}
    async def run(w, h, want):
        app = TApp(Fake())
        async with app.run_test(size=(w, h)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await settle(pilot)
            check(f"{w}x{h} -> '{want}' welcome variant", app._welcome_variant == want,
                  f"got={app._welcome_variant}")
            txt = right_text(app)
            check(f"{w}x{h} welcome shows author 'by naarvent_'", "naarvent_" in txt, f"txt={txt[:60]!r}")
            if want == "large":
                check("large keeps the portrait (braille) art", has_braille(txt))
            elif want in ("medium", "compact"):
                check(f"{want} keeps the Spotify art without the portrait",
                      ("██" in txt) and not has_braille(txt), f"braille={has_braille(txt)}")
            elif want == "small":
                check("small is the compact 3-line header", "Spotify in your terminal" in txt and not has_braille(txt))
            else:
                check("minimal is title + author only (no big art)",
                      "SPT-TUI" in txt and "Spotify in your terminal" not in txt and not has_braille(txt))
    for (w, h), want in expect.items():
        asyncio.run(run(w, h, want))


def test_no_old_author_string_anywhere():
    from spt_tui import constants as c
    check("old 'by me :)' removed from WELCOME", "by me :)" not in c.WELCOME)
    check("WELCOME_AUTHOR is the new author", c.WELCOME_AUTHOR == "by naarvent_ :)")


def test_resize_sequence_tracks_and_no_flicker():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await settle(pilot)
            seq = [(160, 40, "large"), (70, 20, "small"), (160, 40, "large"),
                   (40, 12, "minimal"), (140, 40, "medium")]
            ok = True
            for w, h, want in seq:
                await pilot.resize_terminal(w, h)
                await settle(pilot)
                if app._welcome_variant != want:
                    ok = False
                    check(f"resize {w}x{h} -> {want}", False, f"got={app._welcome_variant}")
            check("welcome variant tracks large->small->large->minimal->medium", ok)
            # no flicker: re-rendering only when the variant changes -> repeating
            # the same size must not change the stored variant
            before = app._welcome_variant
            await pilot.resize_terminal(140, 40)   # same as last -> medium
            await settle(pilot)
            check("re-resizing to the same variant is a no-op", app._welcome_variant == before)
    asyncio.run(run())


def _resize_with_view_open(open_view):
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await settle(pilot)
            open_view(app)
            await pilot.pause()
            check("opening a view clears the welcome flag", app._welcome_on is False)
            await pilot.resize_terminal(70, 20)
            await settle(pilot)
            txt = right_text(app)
            check("resize with a view open does not paint the welcome",
                  app._welcome_on is False and "naarvent_" not in txt, f"welcome_on={app._welcome_on}")
    return run

def test_resize_with_help_open():
    asyncio.run(_resize_with_view_open(lambda app: app.action_help())())
def test_resize_with_playlist_open():
    asyncio.run(_resize_with_view_open(lambda app: app._open_playlist_table({"id":"p","name":"P","uri":"u"}))())
def test_resize_with_lyrics_open():
    def openl(app):
        app._bar_last_track = {"id":"a","name":"S","artists":[{"name":"A"}],"album":{"name":"Al"},"duration_ms":1000}
        app._now_internal_track_id = "a"
        app._open_lyrics_view()
    asyncio.run(_resize_with_view_open(openl)())


def test_tiny_size_no_crash():
    async def run():
        app = TApp(Fake())
        async with app.run_test(size=(30, 8)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await settle(pilot)
            check("very small terminal renders minimal welcome without crashing",
                  app._welcome_variant in ("minimal", "small") and "SPT-TUI" in right_text(app),
                  f"variant={app._welcome_variant} txt={right_text(app)!r}")
    asyncio.run(run())


def test_blocks_are_rectangular_and_titled():
    from spt_tui.constants import build_welcome, WELCOME_TUI_ART, WELCOME_AUTHOR
    for v in ("large", "medium", "compact", "small", "minimal"):
        lines = build_welcome(v).split("\n")
        check(f"{v}: every line has the same width (a centred block keeps its shape)",
              len({len(l) for l in lines}) == 1, str(sorted({len(l) for l in lines})))
    for v in ("large", "medium"):
        txt = build_welcome(v)
        check(f"{v}: has the TUI figlet (same font as Spotify) under Spotify",
              all(l.strip() in txt for l in WELCOME_TUI_ART.split("\n")))
    lines = build_welcome("medium").split("\n")
    tui_rows = [i for i, l in enumerate(lines) if l.startswith(WELCOME_TUI_ART.split("\n")[0])]
    ia = next(i for i, l in enumerate(lines) if WELCOME_AUTHOR in l)
    tui_h = len(WELCOME_TUI_ART.split("\n"))
    check("medium: 'by naarvent_ :)' sits on the middle row of TUI, to its right",
          tui_rows and ia == tui_rows[0] + tui_h // 2
          and lines[ia].index(WELCOME_AUTHOR) >= max(len(l) for l in WELCOME_TUI_ART.split("\n")),
          f"{tui_rows} {ia}")
    a0 = lines[ia].index(WELCOME_AUTHOR)
    tw = max(len(l) for l in WELCOME_TUI_ART.split("\n"))
    free = len(lines[0]) - tw
    check("…centred in the free space right of TUI", abs((a0 - tw) - (free - len(WELCOME_AUTHOR)) / 2) <= 1,
          f"{a0} {tw} {free}")
    large = build_welcome("large").split("\n")
    braille = [i for i, l in enumerate(large) if any(0x2800 <= ord(ch) <= 0x28ff for ch in l)]
    check("large: the headphones sit in the vertical middle of the title block",
          abs((braille[0] + braille[-1]) / 2 - (len(large) - 1) / 2) <= 1.5, f"{braille[0]}..{braille[-1]} of {len(large)}")


def test_art_always_fits_the_panel():
    async def run(w, h):
        app = TApp(Fake())
        async with app.run_test(size=(w, h)) as pilot:
            await pilot.pause(); pause_intervals(app)
            await settle(pilot)
            cs = app.right_panel.content_size
            lines = right_text(app).split("\n")
            wid = max(len(l) for l in lines)
            if cs.width and cs.height:
                check(f"{w}x{h}: the welcome ({wid}x{len(lines)}) fits the panel ({cs.width}x{cs.height})",
                      wid <= cs.width and len(lines) <= cs.height or app._welcome_variant == "minimal",
                      app._welcome_variant)
    for sz in [(200, 50), (160, 40), (140, 40), (120, 32), (100, 30), (80, 24), (70, 20)]:
        asyncio.run(run(*sz))


ALL = [test_blocks_are_rectangular_and_titled, test_art_always_fits_the_panel, test_initial_variant_per_size, test_no_old_author_string_anywhere,
       test_resize_sequence_tracks_and_no_flicker, test_resize_with_help_open,
       test_resize_with_playlist_open, test_resize_with_lyrics_open, test_tiny_size_no_crash]

def main():
    for fn in ALL:
        try: fn()
        except Exception: check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (welcome responsive) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            last = (detail or "").strip().splitlines()[-1] if detail else ""
            print("  FAIL:", name, "::", last)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
