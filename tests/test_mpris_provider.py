"""Spotify Free via MPRIS: playerctl control of the user's own player, the
now-playing parse, and opening open.spotify.com links with the opener command
(``abrir`` by default, never xdg-open). Fake runner/spawner; nothing real runs.

Run standalone:  python tests/test_mpris_provider.py
"""
import os, sys, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.providers.mpris import SpotifyMprisProvider, spotify_web_url, _SEP
from spt_tui.providers.base import ProviderError

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class Done:
    def __init__(self, out="", rc=0, err=""):
        self.stdout, self.returncode, self.stderr = out, rc, err


class FakeCtl:
    """playerctl stand-in: two players, one of them a browser on open.spotify.com."""
    def __init__(self, players=("chromium.instance42", "vlc"), status="Playing"):
        self.players = list(players); self.calls = []; self.status = status
    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        args = argv[1:]
        player = None
        if args[:1] == ["-p"]:
            player, args = args[1], args[2:]
        if args == ["-l"]:
            return Done("\n".join(self.players))
        if args[:1] == ["metadata"]:
            fmt = args[2]
            if fmt.startswith("{{xesam:url}}"):
                return Done("https://open.spotify.com/track/abc123" if player == "chromium.instance42" else "file:///x.mp3")
            fields = [player, self.status, "/org/mpris/MediaPlayer2/firefox", "https://open.spotify.com/track/abc123?si=1",
                      "Song", "Artist A, Artist B", "Album", "215000000", "61500000", "0.65"]
            return Done(_SEP.join(fields))
        return Done("")


class Spawn:
    def __init__(self): self.calls = []
    def __call__(self, argv, **kw): self.calls.append((list(argv), kw))


def make(have_playerctl=True, have_opener=True, **kw):
    ctl, spawn = FakeCtl(**kw), Spawn()
    def which(n):
        if n == "playerctl":
            return "/usr/bin/playerctl" if have_playerctl else None
        if n == "abrir":
            return "/home/u/.local/bin/abrir" if have_opener else None
        return None
    p = SpotifyMprisProvider(run=ctl, spawn=spawn, which=which, opener=["abrir"])
    return p, ctl, spawn


def test_urls():
    check("track uri -> open.spotify.com/track", spotify_web_url("spotify:track:XYZ") == "https://open.spotify.com/track/XYZ")
    check("playlist uri", spotify_web_url("spotify:playlist:P1") == "https://open.spotify.com/playlist/P1")
    check("web URL passes through", spotify_web_url("https://open.spotify.com/album/A") == "https://open.spotify.com/album/A")
    check("non-Spotify uri -> None", spotify_web_url("subsonic:track:1") is None)


def test_open_with_abrir():
    p, ctl, spawn = make()
    p.start_playback(uris=["spotify:track:XYZ", "spotify:track:other"])
    argv, kw = spawn.calls[-1]
    check("Enter opens the track page with `abrir <url>`", argv == ["abrir", "https://open.spotify.com/track/XYZ"], repr(argv))
    check("opener is detached (new session)", kw.get("start_new_session") is True)
    check("xdg-open is never used", all("xdg-open" not in " ".join(a) for a, _ in spawn.calls))
    p.start_playback(context_uri="spotify:playlist:P1", offset={"uri": "spotify:track:T9"})
    check("context + offset uri opens that track", spawn.calls[-1][0][-1] == "https://open.spotify.com/track/T9")
    p.start_playback(context_uri="spotify:album:A1", offset={"position": 3})
    check("context without a track uri opens the context page", spawn.calls[-1][0][-1] == "https://open.spotify.com/album/A1")
    try:
        p.start_playback(uris=["local:file"]); raised = False
    except ProviderError:
        raised = True
    check("non-openable item -> ProviderError", raised)
    os.environ["SPT_OPEN_COMMAND"] = "my-open --new"
    try:
        q = SpotifyMprisProvider(run=ctl, spawn=spawn, which=lambda n: "/bin/" + n)
        check("opener configurable via SPT_OPEN_COMMAND", q._opener == ["my-open", "--new"])
    finally:
        os.environ.pop("SPT_OPEN_COMMAND", None)
    q = SpotifyMprisProvider(run=ctl, spawn=spawn, which=lambda n: "/bin/" + n)
    check("default opener is `abrir`", q._opener == ["abrir"])


def test_player_detection_and_controls():
    p, ctl, _ = make()
    p.pause()
    check("auto-detects the browser player playing open.spotify.com",
          ctl.calls[-1] == ["playerctl", "-p", "chromium.instance42", "pause"], repr(ctl.calls[-1]))
    p.resume(); p.next(); p.prev()
    cmds = [c[-1] for c in ctl.calls[-3:]]
    check("play / next / previous", cmds == ["play", "next", "previous"], repr(cmds))
    p.seek_ms(61500)
    check("seek -> playerctl position <seconds>", ctl.calls[-1][-2:] == ["position", "61.500"])
    p.set_volume(40)
    check("volume -> playerctl volume 0..1", ctl.calls[-1][-2:] == ["volume", "0.40"])
    n = len([c for c in ctl.calls if c[1:] == ["-l"]])
    p.pause()
    check("player choice cached (no extra -l)", len([c for c in ctl.calls if c[1:] == ["-l"]]) == n)
    p2, ctl2, _ = make(players=("spotify", "firefox"))
    p2.pause()
    check("desktop Spotify client preferred by name", ctl2.calls[-1][:3] == ["playerctl", "-p", "spotify"])
    p3, ctl3, _ = make(players=("vlc",))
    ctl3.players = ["vlc"]
    try:
        p3.pause(); raised = None
    except ProviderError as exc:
        raised = exc
    check("no Spotify player -> clear error, nothing sent", raised and "open.spotify.com" in str(raised))


def test_now_playing():
    p, ctl, _ = make()
    pb = p.get_playback()
    it = pb.get("item") or {}
    check("track id parsed from the web player URL", it.get("id") == "abc123" and it.get("uri") == "spotify:track:abc123", repr(it))
    check("title/artists/album", it.get("name") == "Song" and [a["name"] for a in it["artists"]] == ["Artist A", "Artist B"])
    check("duration and position from microseconds", it.get("duration_ms") == 215000 and pb["progress_ms"] == 61500)
    check("is_playing from status", pb["is_playing"] is True)
    check("volume percent", pb["device"]["volume_percent"] == 65)
    p2, ctl2, _ = make(status="Paused")
    check("paused status", p2.get_playback()["is_playing"] is False)


def test_without_playerctl():
    p, ctl, spawn = make(have_playerctl=False)
    c = p.capabilities
    check("no playerctl: control disabled, opening links still allowed",
          not c.can_control_playback and not c.can_seek and c.can_play)
    check("no playerctl: notice says to install it", "playerctl" in (p.status_notice() or ""))
    check("no playerctl: get_playback is empty, no subprocess", p.get_playback() == {} and ctl.calls == [])
    p.start_playback(uris=["spotify:track:Q"])
    check("no playerctl: Enter still opens the link", spawn.calls[-1][0] == ["abrir", "https://open.spotify.com/track/Q"])
    p2, _, _ = make(have_opener=False)
    check("no opener: can_play False with a notice", not p2.capabilities.can_play and "open_command" in (p2.status_notice() or ""))


def test_scope():
    p, _, _ = make()
    c = p.capabilities
    check("MPRIS mode: no queue / devices / shuffle via the Web API", not c.can_control_queue
          and not c.can_list_devices and not c.can_shuffle_repeat)
    check("MPRIS mode keeps Web API likes/playlists/search", c.can_like and c.can_edit_playlists and c.can_search)
    check("MPRIS mode never switches to the Free degraded set", p.is_free_account() is False)
    import spt_tui.providers.mpris as m
    src = open(m.__file__, encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]   # skip the module docstring that lists what is forbidden
    check("no sp_dc cookie, no xdg-open, no headless browser in the code",
          "sp_dc" not in body and "xdg-open" not in body and "headless" not in body.lower()
          and "selenium" not in body.lower() and "playwright" not in body.lower())


ALL = [test_urls, test_open_with_abrir, test_player_detection_and_controls, test_now_playing,
       test_without_playerctl, test_scope]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (mpris_provider) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
