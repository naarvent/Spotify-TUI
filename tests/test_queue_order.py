"""Add to queue (mpv-backed providers, e.g. YouTube Music): the queued song plays NEXT, after the ones
already queued and before the rest of the playlist; `next` plays it and it leaves the queue; shuffle /
unshuffle / a new play keep it coherent. Uses a REAL mpv with tiny local audio files (no network).

Run standalone:  python tests/test_queue_order.py
"""
import os, sys, wave, struct, tempfile, shutil, time, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spt_tui import config
from spt_tui.players.mpv import MpvPlayer
from spt_tui.providers.ytmusic import YTMusicProvider

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


def make_wav(path, secs=20):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * 8000 * secs)


class LocalProvider(YTMusicProvider):
    """YouTube Music provider whose 'stream' is a local file."""
    def stream_url(self, track):
        return track["wav"]


def wait(pred, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred(): return True
        except Exception: pass
        time.sleep(0.05)
    return False


def main():
    from shutil import which
    if not which("mpv"):
        print("SKIP - mpv is not installed"); print("\n0/0 checks passed"); return 0
    d = tempfile.mkdtemp(prefix="spt_queue_")
    old = (config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG)
    config.CACHE_DIR = d; config.CONFIG_PATH = os.path.join(d, "spt_config.json"); config.LOCAL_CFG = {}
    player = None
    try:
        tracks = {}
        for n in ["A", "B", "C", "D", "E", "F", "Q1", "Q2"] + [f"L{i:02d}" for i in range(30)]:
            p = os.path.join(d, f"{n}.wav"); make_wav(p)
            tracks[n] = {"id": f"id{n:>9}".replace(" ", "x"), "uri": f"ytmusic:track:id{n:>9}".replace(" ", "x"),
                         "name": n, "artists": [], "duration_ms": 20000, "wav": p}
        player = MpvPlayer(extra_args=["--ao=null"], register_atexit=False)
        prov = LocalProvider(client=object(), player=player, which=lambda n: "/usr/bin/" + n)
        for t in tracks.values(): prov._remember(t)
        names = lambda lst: [t["name"] for t in lst]
        def now(): return (prov._current_track() or {}).get("name")
        def upcoming(): return names(prov.queue()["queue"])
        def parts():
            q = prov.queue()["queue"]
            return names([t for t in q if t.get("_queued")]), names([t for t in q if not t.get("_queued")])
        def in_mpv():
            prov.get_playback()            # the app's 1.5 s sync tick (it consumes played queued songs)
            return [os.path.basename(e["filename"])[:-4] for e in player.playlist_entries()]
        U = lambda *ns: [tracks[n]["uri"] for n in ns]

        prov.start_playback(uris=U(*"ABCDEF"))
        check("playing A first", wait(lambda: now() == "A"), str(now()))

        prov.add_to_queue(tracks["Q1"]["uri"])
        check("queued song is shown FIRST in the upcoming queue (not at the very end)",
              upcoming()[:1] == ["Q1"] and upcoming()[-1] == "F", str(upcoming()))
        prov.add_to_queue(tracks["Q2"]["uri"])
        check("a second queued song goes after the first (FIFO), before the playlist",
              upcoming() == ["Q1", "Q2", "B", "C", "D", "E", "F"], str(upcoming()))

        prov.next()
        check("next plays the queued song", wait(lambda: now() == "Q1"), str(now()))
        check("…and it leaves the queue", upcoming() == ["Q2", "B", "C", "D", "E", "F"], str(upcoming()))
        prov.next()
        check("next again plays the second queued song", wait(lambda: now() == "Q2"), str(now()))
        prov.next()
        check("then the playlist continues where it was (B)", wait(lambda: now() == "B"), str(now()))
        check("the queue is back to the plain playlist", upcoming() == ["C", "D", "E", "F"], str(upcoming()))

        # queue while the playlist is mid-way, then shuffle on / off keeps the queue next
        prov.add_to_queue(tracks["Q1"]["uri"])
        prov.shuffle(True); time.sleep(0.3)
        check("shuffle ON: the queued song is still next", upcoming()[:1] == ["Q1"], str(upcoming()))
        prov.add_to_queue(tracks["Q2"]["uri"])
        check("shuffle ON: a new queued song goes after it", upcoming()[:2] == ["Q1", "Q2"], str(upcoming()))
        prov.shuffle(False); time.sleep(0.3)
        check("shuffle OFF: the queued songs are still next, in order", upcoming()[:2] == ["Q1", "Q2"], str(upcoming()))
        check("nothing was lost or duplicated by shuffling", sorted(upcoming() + [now()]) ==
              sorted(["B", "C", "D", "E", "F", "Q1", "Q2"]), str(upcoming()) + " now=" + str(now()))

        # a queued song that already played must not come back after a shuffle
        prov.next()
        check("next reached the first queued song again", wait(lambda: now() == "Q1"), str(now()))
        prov.shuffle(True); time.sleep(0.3)
        check("a queued song that already played is not pinned again after shuffling (only Q2 is, right after the current)",
              upcoming()[:1] == ["Q2"] and len(prov._queued_ids) == 1, f"{upcoming()} ids={prov._queued_ids}")
        prov.shuffle(False)

        # a new play replaces everything, the user's queue included
        prov.start_playback(uris=U(*"ABC"))
        wait(lambda: now() == "A")
        check("starting another playlist drops the old queue", upcoming() == ["B", "C"], str(upcoming()))
        prov.add_to_queue(tracks["Q1"]["uri"])
        check("…and queueing works again from scratch", upcoming() == ["Q1", "B", "C"], str(upcoming()))

        # --- what already played leaves mpv's playlist (Spotify-like), so it can never come back
        prov.start_playback(uris=U(*"ABCD"))
        wait(lambda: now() == "A")
        prov.add_to_queue(tracks["Q1"]["uri"])
        check("two parts: the queued song is marked _queued, the playlist is not",
              parts() == (["Q1"], ["B", "C", "D"]), str(parts()))
        check("queue() keeps the Spotify API shape", set(prov.queue()) == {"currently_playing", "queue"}
              and prov.queue()["currently_playing"]["name"] == "A", str(prov.queue().keys()))
        prov.next(); wait(lambda: now() == "Q1")
        check("the queued song playing is neither in 'next in queue' nor in the context",
              parts() == ([], ["B", "C", "D"]), str(parts()))
        prov.next(); wait(lambda: now() == "B")
        check("once it played, the queued song is removed from mpv's playlist", in_mpv() == list("ABCD"), str(in_mpv()))
        prov.repeat("context")
        prov.next(); wait(lambda: now() == "C"); prov.next(); wait(lambda: now() == "D")
        check("repeat=context wraps to the start, without the current track or the played queued song",
              upcoming() == ["A", "B", "C"], str(upcoming()))
        prov.add_to_queue(tracks["Q2"]["uri"])
        check("repeat=context: the queued song comes first, then the wrapped context",
              parts() == (["Q2"], ["A", "B", "C"]), str(parts()))
        prov.repeat("track")
        check("repeat=track: only the user's queue is listed (the context does not advance)",
              parts() == (["Q2"], []), str(parts()))
        prov.repeat("off")
        check("repeat=off at the last track: only the queued song is next", parts() == (["Q2"], []), str(parts()))

        # --- the same song in the playlist AND in the user's queue / twice in the playlist
        prov.start_playback(uris=U("A", "B", "C", "B", "D"))
        wait(lambda: now() == "A")
        prov.next(); wait(lambda: now() == "B"); prov.next(); wait(lambda: now() == "C"); prov.next()
        wait(lambda: now() == "B" and player.get("playlist-pos") == 3)
        check("a song twice in the playlist: 'next' follows the REAL position (the 2nd B), not the first",
              upcoming() == ["D"], str(upcoming()))
        prov.start_playback(uris=U(*"ABCD"))
        wait(lambda: now() == "A")
        prov.add_to_queue(tracks["C"]["uri"])
        check("a queued song that is also in the playlist shows in both parts",
              parts() == (["C"], ["B", "C", "D"]), str(parts()))
        prov.next(); wait(lambda: now() == "C")
        check("…the queued copy plays first and leaves 'next in queue'", parts() == ([], ["B", "C", "D"]), str(parts()))
        prov.next(); wait(lambda: now() == "B")
        prov.next(); wait(lambda: now() == "C")
        check("…then the playlist's own copy plays later, with the right 'next'", upcoming() == ["D"], str(upcoming()))
        check("…and only the playlist's copy is left in mpv", in_mpv() == list("ABCD"), str(in_mpv()))

        # --- the context part is limited to QUEUE_CONTEXT_LIMIT (here 20; by default the
        # configurable queue_context_limit, 300); the user's queue never is
        prov.QUEUE_CONTEXT_LIMIT = 20
        L = [f"L{i:02d}" for i in range(30)]
        prov.start_playback(uris=U(*L))
        wait(lambda: now() == "L00")
        prov.add_to_queue(tracks["Q1"]["uri"]); prov.add_to_queue(tracks["Q2"]["uri"])
        mine, rest = parts()
        check("limit: all queued songs + the next 20 of the context",
              mine == ["Q1", "Q2"] and rest == L[1:21], f"{mine} {rest}")
        prov.shuffle(True)
        mine, rest = parts()
        cur = now()
        check("shuffle ON: queued songs still first, context limited to 20, current not listed",
              mine == ["Q1", "Q2"] and len(rest) == 20 and cur == "L00" and cur not in rest and len(set(rest)) == 20,
              f"{mine} {rest} now={cur}")
        ents = player.playlist_entries()
        ci = next(i for i, e in enumerate(ents) if e.get("current"))
        real = [os.path.basename(e["filename"])[:-4] for e in ents[ci + 1:]]
        check("shuffle ON: the list is mpv's real order, and ALL 29 other tracks + the 2 queued are still ahead",
              (mine + rest) == real[:22] and len(real) == 31, f"{mine + rest} vs {real[:22]} ({len(real)} ahead)")
        prov.next(); wait(lambda: now() == "Q1")
        prov.shuffle(False)
        check("shuffle OFF: the remaining queued song is still first", parts()[0] == ["Q2"], str(parts()))
        prov.next(); wait(lambda: now() == "Q2"); prov.next()
        wait(lambda: now() not in ("Q2", None))
        check("after both queued songs played, mpv holds exactly the playlist again",
              sorted(in_mpv()) == L, str(in_mpv()))
        prov.shuffle(False)

        # Enter in the Queue view: jump to the picked entry (position in queue())
        prov.start_playback(uris=U(*"ABCDEF"))
        wait(lambda: now() == "A")
        prov.add_to_queue(tracks["Q1"]["uri"])
        prov.play_queue_position(3)            # Q1, B, C, D ...
        check("play_queue_position jumps to the picked entry", wait(lambda: now() == "D"), str(now()))
        check("the entries skipped (incl. the queued one) are gone from the queue",
              upcoming() == ["E", "F"], str(upcoming()))

        # nothing playing: the song starts right away
        player.command("stop"); time.sleep(0.3)
        prov2 = LocalProvider(client=object(), player=player, which=lambda n: "/usr/bin/" + n)
        prov2._remember(tracks["D"])
        prov2.add_to_queue(tracks["D"]["uri"])
        check("queueing with nothing playing starts that song", wait(lambda: (prov2._current_track() or {}).get("name") == "D"),
              str(prov2._current_track()))
    except Exception:
        check("queue test (crashed)", False, traceback.format_exc(limit=6))
    finally:
        try:
            if player: player.close()
        except Exception: pass
        config.CACHE_DIR, config.CONFIG_PATH, config.LOCAL_CFG = old
        shutil.rmtree(d, ignore_errors=True)
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    for n, c, dd in RESULTS:
        if not c: print("  FAIL:", n, "::", dd)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
