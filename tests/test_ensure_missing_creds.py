"""Issue #9 follow-ups: ensure() must say that the Spotify credentials are
missing instead of silently building an unauthenticated client (401s), and the
Now Playing bar must survive being read before any track was ever seen.

Run standalone:  python tests/test_ensure_missing_creds.py
"""
import os, sys, logging, io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spt_tui import config
from spt_tui.providers.spotify import SpotifyProvider, MissingCredentialsError
from spt_tui.app.playback import PlaybackMixin

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

old = (config.CLIENT_ID, config.CLIENT_SECRET, config.REDIRECT_URI)
try:
    # 1. No credentials: clear error, no client cached.
    config.CLIENT_ID = config.CLIENT_SECRET = config.REDIRECT_URI = None
    check("has_spotify_credentials False when empty", config.has_spotify_credentials() is False)
    p = SpotifyProvider()
    try:
        p.ensure(); err = None
    except MissingCredentialsError as e:
        err = str(e)
    check("ensure raises MissingCredentialsError", err is not None)
    check("message names what is missing", bool(err) and all(k in err for k in ("client_id", "client_secret", "redirect_uri")), str(err))
    check("no unauthenticated client was cached", p.sp is None)

    # 2. Partial credentials: only the missing ones are named.
    config.CLIENT_ID, config.CLIENT_SECRET = "cid", "sec"
    try:
        p.ensure(); err = None
    except MissingCredentialsError as e:
        err = str(e)
    check("partial: names only redirect_uri", bool(err) and "redirect_uri" in err and "client_id" not in err, str(err))

    # 3. All set: builds a client as before.
    config.REDIRECT_URI = "http://127.0.0.1:9999/callback"
    check("full credentials -> has_spotify_credentials True", config.has_spotify_credentials() is True)
    try:
        sp = p.ensure()
    except Exception as e:
        sp = None
    check("full credentials -> ensure returns a client", sp is not None)
finally:
    config.CLIENT_ID, config.CLIENT_SECRET, config.REDIRECT_URI = old

# 4. _bar_last_track is readable before anything was shown (the AttributeError).
class _Failing:
    def get_playback(self): return {}
    def recently_played(self, *a, **k): raise RuntimeError("401")
    def __getattr__(self, n): raise RuntimeError("401")
class _App(PlaybackMixin):
    spotify = _Failing()

buf = io.StringIO(); h = logging.StreamHandler(buf)
lg = logging.getLogger("spt_tui"); lg.addHandler(h)
try:
    res = _App()._get_current_or_last_track()
finally:
    lg.removeHandler(h)
check("_get_current_or_last_track returns (None,0,0,False)", res == (None, 0, 0, False), str(res))
check("no AttributeError logged for _bar_last_track", "_bar_last_track" not in buf.getvalue(), buf.getvalue()[-300:])

ok = sum(1 for _, c, _ in RESULTS if c)
print(f"{ok}/{len(RESULTS)} checks passed")
sys.exit(0 if ok == len(RESULTS) else 1)
