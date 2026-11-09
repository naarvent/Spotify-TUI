"""Spicy Lyrics source: response parser (synthetic samples), attribution, 30-day cache,
rate limiting and error handling, track matching, source order, degradation without a
key, Settings. Everything runs against fake HTTP / a local server with a FAKE key; the
real API and the real keyring are never touched.

Run standalone:  python tests/test_spicy_lyrics.py
"""
import os, sys, json, asyncio, tempfile, threading, time, logging, io

os.environ.setdefault("SPT_TUI_CACHE_DIR", tempfile.mkdtemp(prefix="spt_spicy_"))
os.environ["SPT_SPICY_LYRICS"] = "on"        # this suite drives the source with injected fakes
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui import config, karaoke, spicy_lyrics as sl
from spt_tui.app import SptPy

RESULTS = []
FAKE_KEY = "sl_sk_FAKEKEYFORTESTS0000"
SID = "4PTG3Z6ehGkBFwjybzWkR8"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


# --------------------------------------------------------------------------- #
# Synthetic samples in the documented shape
# --------------------------------------------------------------------------- #
def syl(t, a, b, part=False, **kw):
    d = {"Text": t, "StartTime": a, "EndTime": b, "IsPartOfWord": part}
    d.update(kw)
    return d


def body_syllable(source="spicy_lyrics", maker=True):
    b = {
        "Type": "Syllable", "StartTime": 1.0, "EndTime": 40.0, "id": SID,
        "ids": {"spotify": SID}, "source": source,
        "Content": [
            {"Type": "Vocal", "OppositeAligned": False,
             "Lead": {"Syllables": [syl("We're", 1.0, 1.5), syl("no", 1.5, 1.9), syl("stran", 1.9, 2.2, True),
                                    syl("gers", 2.2, 2.8), syl("x", 2.8, 3.0, TransliteratedText="ex")],
                      "StartTime": 1.0, "EndTime": 3.0},
             "Background": [{"Syllables": [syl("(ooh)", 2.0, 2.5)], "StartTime": 2.0, "EndTime": 2.5},
                            {"Syllables": [syl("yeah", 2.6, 2.9)], "StartTime": 2.6, "EndTime": 2.9}]},
            {"Type": "Vocal", "OppositeAligned": True,
             "Lead": {"Syllables": [syl("Answer", 5.0, 5.6), syl("back", 5.6, 6.0)], "StartTime": 5.0, "EndTime": 6.0}},
            {"Type": "Vocal", "Lead": {"Syllables": [], "StartTime": 7, "EndTime": 8}},     # empty: skipped
            {"Type": "Vocal", "Lead": {"Syllables": [syl("Late", 20.0, 20.5)], "StartTime": 20.0, "EndTime": 20.5}},
        ],
    }
    if source == "spicy_lyrics":
        b["UploadAttribution"] = {"Uploader": {"id": "1", "username": "up\x1b[31mloader", "url": "https://spicylyrics.org/uid/1"}}
        if maker:
            b["UploadAttribution"]["Maker"] = {"id": "2", "username": "the maker", "url": "https://spicylyrics.org/uid/2"}
    return b


def body_line(source="spotify"):
    return {"Type": "Line", "StartTime": 1.0, "EndTime": 30.0, "id": SID, "ids": {"spotify": SID}, "source": source,
            "Content": [{"Type": "Vocal", "OppositeAligned": False, "Text": "First line", "StartTime": 1.0, "EndTime": 9.0},
                        {"Type": "Vocal", "OppositeAligned": True, "Text": "Second line (bg)", "StartTime": 4.0, "EndTime": 6.0}]}


def body_static(source="spotify"):
    return {"Type": "Static", "id": SID, "ids": {"spotify": SID}, "source": source,
            "Lines": [{"Text": "plain one"}, {"Text": ""}, {"Text": "plain two"}]}


def env(body):
    return {"Body": body, "Status": 200, "Type": "object"}


# --------------------------------------------------------------------------- #
def test_parse_syllable():
    r = sl.parse_lyrics(env(body_syllable()))
    check("Syllable: kind and 3 real lines (empty one skipped)", r.kind == "Syllable"
          and len([k for k in r.klines if not k.interlude]) == 3, str(r.kind))
    k = r.klines[0] if not r.klines[0].interlude else r.klines[1]
    check("short intro (1 s) is not an interlude", not r.klines[0].interlude)
    check("times are seconds -> ms, per syllable", (k.words[0].start, k.words[0].end) == (1000, 1500)
          and k.start == 1000 and k.end == 3000)
    check("IsPartOfWord glues syllables, words get a space", k.text == "We're no strangers x"
          and [w.text for w in k.words] == ["We're ", "no ", "stran", "gers ", "x"], k.text)
    check("text length equals the words (the wipe indexes into it)", len(k.text) == sum(len(w.text) for w in k.words))
    check("real word times", k.real_words and karaoke.has_real_words(r.klines))
    check("syllable romanisation is kept", k.roman == "ex", repr(k.roman))
    check("background vocals become bg words", "".join(w.text for w in k.bg) == "(ooh) yeah", repr([w.text for w in k.bg]))
    ks = [x for x in r.klines if not x.interlude]
    check("OppositeAligned -> agent v2, else v1", [x.agent for x in ks] == ["v1", "v2", "v1"])
    check("long gap between lines becomes an interlude", any(x.interlude and x.start == 6000 for x in r.klines))
    check("line view tuples", r.lines[0] == (1000, "We're no strangers x") and len(r.lines) == 3)
    out = karaoke.render_fixed(r.klines, 1500, width=60, rows=9)
    check("renders in the fixed geometry", len(out) == 9)
    check("duet is justified left/right", karaoke._justify(ks[1], True) == "right")


def test_parse_line_static_bad():
    r = sl.parse_lyrics(body_line())          # bare Body is accepted too
    ks = r.klines
    check("Line: approximate words, not real", r.kind == "Line" and not karaoke.has_real_words(ks)
          and karaoke.approximate_count(ks) == 2)
    check("Line: duet agent kept, end never past the next line", ks[1].agent == "v2" and ks[0].end <= ks[1].start)
    s = sl.parse_lyrics(env(body_static()))
    check("Static: no klines, blank lines dropped", s.kind == "Static" and s.klines is None
          and [t for _, t in s.lines] == ["plain one", "plain two"])
    for name, bad in (("unknown type", {"Type": "Weird", "Content": []}),
                      ("empty syllable content", {"Type": "Syllable", "Content": []}),
                      ("empty static", {"Type": "Static", "Lines": []}),
                      ("not a dict", [1, 2]),
                      ("lines without lead", {"Type": "Syllable", "Content": [{"Type": "Vocal"}]})):
        try:
            sl.parse_lyrics(bad); ok = False
        except ValueError:
            ok = True
        check(f"bad response refused: {name}", ok)


def test_attribution():
    a = sl.parse_lyrics(env(body_syllable())).attribution
    check("community sync: provider + uploader + maker with urls", a.provider == "Spicy Lyrics"
          and a.uploader.url.endswith("/uid/1") and a.maker.name == "the maker")
    check("control characters are stripped from names", "\x1b" not in a.uploader.name, repr(a.uploader.name))
    rows = sl.attribution_rows(a, 80)
    plain = [r.plain for r in rows]
    check("3 rows: provider, uploader (link), maker (link)", len(rows) == 3 and plain[0] == "Lyrics: Spicy Lyrics"
          and "https://spicylyrics.org/uid/1" in plain[1] and plain[2].startswith("Made by "), str(plain))
    check("names are real hyperlinks", any(sp.style and getattr(sp.style, "link", None) for sp in rows[1].spans))
    a2 = sl.parse_lyrics(env(body_syllable(maker=False))).attribution
    check("no maker -> no maker row", len(sl.attribution_rows(a2, 80)) == 2 and a2.maker is None)
    for src, label in (("apple_music", "Apple Music"), ("spotify", "Spotify")):
        a3 = sl.parse_lyrics(env(body_syllable(source=src))).attribution
        rows = sl.attribution_rows(a3, 80)
        check(f"{src}: provider only, no empty credit", a3.provider == label and len(rows) == 1
              and rows[0].plain == f"Lyrics: {label}")
    b = body_syllable(source="spotify"); b["source"] = "weird"
    check("unidentified source says unknown", sl.parse_lyrics(b).attribution.provider == "unknown source")
    b = body_syllable(source="apple_music")
    b["UploadAttribution"] = {"Uploader": {"username": "sneaky", "url": "https://x"}}
    check("uploader of a non-community source is never shown",
          sl.parse_lyrics(b).attribution.uploader is None)
    b = body_syllable(); b["UploadAttribution"]["Uploader"]["url"] = "javascript:alert(1)"
    check("only https urls are kept", sl.parse_lyrics(b).attribution.uploader.url == "")
    m = sl.attribution_markup(a)
    check("markup version carries the same names", "the maker" in m and "Spicy Lyrics" in m)


class Clock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t


def test_cache():
    d = tempfile.mkdtemp(prefix="spicy_cache_")
    clk = Clock()
    c = sl.SpicyCache(d, clock=clk)
    check("miss on empty", c.get("lyrics:a") is None)
    c.put("lyrics:a", kind="lyrics", body={"x": 1})
    check("hit", c.get("lyrics:a")["body"] == {"x": 1})
    clk.t += 29 * 86400
    check("still valid at 29 days", c.get("lyrics:a") is not None)
    clk.t += 2 * 86400
    p = c._path("lyrics:a")
    check("expired at 31 days: not returned", c.get("lyrics:a") is None)
    check("…and deleted when read", not os.path.exists(p))
    check("TTL is the API's 30 days", sl.CACHE_TTL == 30 * 86400)
    # prune removes expired files nobody reads
    c.put("lyrics:old", kind="lyrics", body={})
    clk.t += 31 * 86400
    c.put("lyrics:new", kind="lyrics", body={})
    n = c.prune()
    names = os.listdir(d)
    check("prune leaves only the unexpired one", len(names) == 1
          and c.get("lyrics:new") is not None, str(names))
    # negative entries: max_age
    c.put("lyrics:neg", kind="notfound")
    clk.t += 2 * 86400
    check("a 'not found' is only trusted for a day", c.get("lyrics:neg", max_age=sl.NEG_TTL) is None
          and c.get("lyrics:neg") is not None)
    # corrupt file
    pth = c._path("lyrics:bad")
    open(pth, "w").write("{not json")
    check("corrupt entry: miss and removed", c.get("lyrics:bad") is None and not os.path.exists(pth))
    # caps
    c2 = sl.SpicyCache(tempfile.mkdtemp(prefix="spicy_cache_"), clock=clk)
    old_max = sl.CACHE_MAX_FILES
    sl.CACHE_MAX_FILES = 5
    try:
        for i in range(9):
            clk.t += 1
            c2.put(f"k{i}", kind="lyrics", body={})
        c2.prune()
        check("file cap evicts the oldest", len(os.listdir(c2.dir)) == 5 and c2.get("k0") is None and c2.get("k8") is not None)
    finally:
        sl.CACHE_MAX_FILES = old_max


def test_rate_limiter():
    clk = Clock(100.0)
    rl = sl.RateLimiter(3, 10.0, clock=clk)
    check("3 calls allowed", all(rl.try_acquire() for _ in range(3)))
    check("4th within the window refused", not rl.try_acquire())
    clk.t += 10.5
    check("window slides", rl.try_acquire())
    rl.block_for(30)
    check("cool-down refuses everything", not rl.try_acquire())
    clk.t += 31
    check("…and ends", rl.try_acquire())
    check("default stays under the real limit (60/50 s)", sl.RATE_MAX_CALLS < 60 and sl.RATE_WINDOW >= 50)


class FakeHttp:
    """Records requests; `script` maps a path prefix to (status, headers, obj) or an exception."""
    def __init__(self, script):
        self.script, self.calls = script, []
    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        for pre, resp in self.script.items():
            if pre in url:
                if isinstance(resp, Exception):
                    raise resp
                st, hd, obj = resp
                return st, hd, (obj if isinstance(obj, bytes) else json.dumps(obj).encode())
        return 404, {}, b"{}"


def mk_client(http, key=FAKE_KEY, **kw):
    return sl.SpicyClient(lambda: key, http=http, **kw)


def test_client_errors():
    h = FakeHttp({"/v1/lyrics/": (200, {}, env(body_line()))})
    c = mk_client(h)
    out = c.lyrics(SID)
    url, hd, to = h.calls[0]
    check("GET /v1/lyrics/{id} with a Bearer header and a timeout", url.endswith("/v1/lyrics/" + SID)
          and hd["Authorization"] == "Bearer " + FAKE_KEY and to and out["Body"]["Type"] == "Line")
    check("User-Agent does not borrow the provider's name", "spicy" not in hd["User-Agent"].lower())

    def kind(http, call=lambda c: c.lyrics(SID)):
        try:
            call(mk_client(http)); return None
        except sl.SpicyError as e:
            return e.kind
    check("401 -> auth", kind(FakeHttp({"/v1": (401, {}, {})})) == "auth")
    check("403 -> auth", kind(FakeHttp({"/v1": (403, {}, {})})) == "auth")
    check("404 -> notfound", kind(FakeHttp({"/v1": (404, {}, {})})) == "notfound")
    check("500 -> server", kind(FakeHttp({"/v1": (500, {}, {})})) == "server")
    check("network error -> network", kind(FakeHttp({"/v1": TimeoutError("x")})) == "network")
    check("invalid JSON -> bad", kind(FakeHttp({"/v1": (200, {}, b"<html>")})) == "bad")
    check("JSON that is not an object -> bad", kind(FakeHttp({"/v1": (200, {}, b"[1]")})) == "bad")
    # 429: Retry-After starts a cool-down, no more requests are made
    h = FakeHttp({"/v1": (429, {"retry-after": "20"}, {})})
    c = mk_client(h)
    try:
        c.lyrics(SID)
    except sl.SpicyError as e:
        k1, ra = e.kind, e.retry_after
    try:
        c.lyrics(SID)
    except sl.SpicyError as e:
        k2 = e.kind
    check("429 -> rate with Retry-After, then no request at all while cooling down",
          k1 == "rate" and ra == 20 and k2 == "rate" and len(h.calls) == 1, f"{k1} {ra} {k2} {len(h.calls)}")
    # local limiter
    h = FakeHttp({"/v1": (200, {}, env(body_line()))})
    c = mk_client(h, limiter=sl.RateLimiter(2, 50.0))
    c.lyrics(SID); c.lyrics(SID)
    try:
        c.lyrics(SID); ok = False
    except sl.SpicyError as e:
        ok = e.kind == "rate"
    check("own rate limiter stops the 3rd call before the network", ok and len(h.calls) == 2)
    # no key
    try:
        sl.SpicyClient(lambda: None, http=h).lyrics(SID); ok = False
    except sl.SpicyError as e:
        ok = e.kind == "auth"
    check("no key: refused before any request", ok and len(h.calls) == 2)
    # search parameters
    h = FakeHttp({"/v1/tracks/search": (200, {}, {"Body": {"results": [{"id": SID}]}})})
    res = mk_client(h).search("Song", "Band", "LP", 213.5)
    q = h.calls[0][0]
    check("search sends title/artist/album/duration", all(x in q for x in ("title=Song", "artist=Band", "album=LP", "duration=213.500"))
          and res == [{"id": SID}], q)


def test_local_server():
    """A real HTTP round trip against a throwaway server on 127.0.0.1 (fake key)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + FAKE_KEY:
                self.send_response(401); self.end_headers(); self.wfile.write(b"{}"); return
            body = json.dumps(env(body_syllable())).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("RateLimit-Remaining", "59"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        r = sl.parse_lyrics(sl.SpicyClient(lambda: FAKE_KEY, base=base).lyrics(SID))
        check("round trip over HTTP parses", r.kind == "Syllable" and seen[0][1] == "Bearer " + FAKE_KEY)
        try:
            sl.SpicyClient(lambda: "wrong", base=base).lyrics(SID); k = None
        except sl.SpicyError as e:
            k = e.kind
        check("401 from a real socket -> auth", k == "auth")
    finally:
        srv.shutdown()


def test_key_never_leaks():
    buf = io.StringIO()
    hnd = logging.StreamHandler(buf)
    sl.logger.addHandler(hnd)
    old = sl.logger.level
    sl.logger.setLevel(logging.DEBUG)
    try:
        for resp in ((401, {}, {}), (429, {"retry-after": "5"}, {}), (500, {}, {}), TimeoutError(FAKE_KEY)):
            svc = sl.SpicyService(sl.SpicyCache(tempfile.mkdtemp(prefix="spicy_c_")), key_fn=lambda: FAKE_KEY,
                                  client=mk_client(FakeHttp({"/v1": resp})), debounce=0)
            out = svc.fetch(provider="spotify", track_id=SID, title="T", artist="A")
            check(f"error outcome is harmless ({resp if not isinstance(resp, Exception) else 'timeout'})",
                  out.status == "error" and out.result is None and FAKE_KEY not in out.notice)
        check("the key never reaches the log", FAKE_KEY not in buf.getvalue())
    finally:
        sl.logger.removeHandler(hnd); sl.logger.setLevel(old)
    # source code / config: no key material anywhere in the repo's modules
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = []
    for dp, _, fs in os.walk(os.path.join(root, "spt_tui")):
        for f in fs:
            if f.endswith(".py") and "sl_sk_" in open(os.path.join(dp, f), encoding="utf-8").read().replace('"sl_sk_..."', ""):
                bad.append(f)
    check("no literal key in the package (only the doc prefix)", bad == [] or bad == ["spicy_lyrics.py"], str(bad))


def test_search_matching():
    def r(i, title, artists, dur, tier):
        return {"id": i, "title": title, "artists": artists, "duration": dur, "tier": tier}
    a = "a" * 22; b = "b" * 22; c_ = "c" * 22
    res = [r(a, "Song (Live)", ["Band"], 300.0, "version"), r(b, "Song", ["Band"], 200.0, "exact")]
    check("skips a 'version' result even when it is first", sl.pick_search_result(res, title="Song", artist="Band", duration_s=200)["id"] == b)
    check("exact but 20 s off -> refused", sl.pick_search_result([r(b, "Song", ["Band"], 220.0, "exact")], title="Song", artist="Band", duration_s=200) is None)
    check("exact without our duration is accepted", sl.pick_search_result([r(b, "Song", ["Band"], 220.0, "exact")], title="Song", artist="Band") is not None)
    check("different title is refused even if 'exact'", sl.pick_search_result([r(b, "Other thing", ["Band"], 200, "exact")], title="Song", artist="Band", duration_s=200) is None)
    check("cover by someone else is refused", sl.pick_search_result([r(c_, "Song", ["Cover"], 200, "other_artist")], title="Song", artist="Band", duration_s=200) is None)
    check("likely needs duration + title + artist", sl.pick_search_result([r(c_, "Song", ["Band"], 201.0, "likely")], title="Song", artist="Band", duration_s=200) is not None
          and sl.pick_search_result([r(c_, "Song", ["Band"], 201.0, "likely")], title="Song", artist="Band") is None
          and sl.pick_search_result([r(c_, "Song", ["Nobody"], 201.0, "likely")], title="Song", artist="Band", duration_s=200) is None)
    check("accents and remaster suffixes do not break the match",
          sl.pick_search_result([r(b, "Canción", ["Banda"], 200, "exact")], title="Cancion - 2011 Remaster", artist="Banda", duration_s=200) is not None)
    check("ids that are not Spotify ids are ignored", sl.pick_search_result([r("short", "Song", ["Band"], 200, "exact")], title="Song", artist="Band", duration_s=200) is None)
    check("empty results", sl.pick_search_result([], title="Song", artist="Band") is None)


def mk_service(script, key=FAKE_KEY, sleeps=None, **kw):
    http = FakeHttp(script)
    cache = sl.SpicyCache(tempfile.mkdtemp(prefix="spicy_svc_"))
    cl = mk_client(http, key=key)
    svc = sl.SpicyService(cache, key_fn=lambda: key, client=cl, debounce=kw.pop("debounce", 0),
                          sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
    return svc, http


def test_service():
    svc, http = mk_service({"/v1/lyrics/": (200, {}, env(body_syllable()))})
    out = svc.fetch(provider="spotify", track_id=SID, title="Song", artist="Band", duration_ms=200000)
    check("Spotify provider: one request, no search", out.status == "found" and len(http.calls) == 1
          and "/v1/lyrics/" + SID in http.calls[0][0])
    out2 = svc.fetch(provider="spotify", track_id=SID, title="Song", artist="Band")
    check("second time comes from the cache: no request", out2.status == "found" and len(http.calls) == 1)
    out3 = svc.fetch(provider="spotify", track_id=SID, title="Song", artist="Band", force=True)
    check("force refetches", out3.status == "found" and len(http.calls) == 2)

    # other providers: search, validate, then lyrics
    script = {"/v1/tracks/search": (200, {}, {"Body": {"results": [
                  {"id": "x" * 22, "title": "Song", "artists": ["Band"], "duration": 200.1, "tier": "exact"}]}}),
              "/v1/lyrics/": (200, {}, env(body_line()))}
    svc, http = mk_service(script)
    out = svc.fetch(provider="ytmusic", track_id="dQw4w9WgXcQ", title="Song", artist="Band", album="LP", duration_ms=200000)
    urls = [c[0] for c in http.calls]
    check("ytmusic: search then lyrics with the id the search returned", out.status == "found" and len(urls) == 2
          and "/v1/tracks/search" in urls[0] and urls[1].endswith("/v1/lyrics/" + "x" * 22), str(urls))
    svc.fetch(provider="ytmusic", track_id="dQw4w9WgXcQ", title="Song", artist="Band", album="LP", duration_ms=200000)
    check("search answer and lyrics are cached (no repeat requests)", len(http.calls) == 2)
    # a Subsonic id that looks like a Spotify id must NOT be used directly
    svc, http = mk_service(script)
    svc.fetch(provider="subsonic", track_id=SID, title="Song", artist="Band", duration_ms=200000)
    check("subsonic: a 22-char id is still searched, never trusted", "/v1/tracks/search" in http.calls[0][0])
    # unusable search result -> notfound, remembered, no lyrics request
    bad = {"/v1/tracks/search": (200, {}, {"Body": {"results": [
        {"id": "y" * 22, "title": "Song (Remix)", "artists": ["Band"], "duration": 200, "tier": "version"}]}}),
           "/v1/lyrics/": (200, {}, env(body_line()))}
    svc, http = mk_service(bad)
    o = svc.fetch(provider="ytmusic", track_id="v", title="Song", artist="Band", duration_ms=200000)
    o2 = svc.fetch(provider="ytmusic", track_id="v", title="Song", artist="Band", duration_ms=200000)
    check("no trustworthy match -> notfound, no lyrics fetched, answer remembered", o.status == o2.status == "notfound"
          and len(http.calls) == 1 and all("/v1/lyrics/" not in c[0] for c in http.calls))
    # 404 lyrics -> notfound and negative cache
    svc, http = mk_service({"/v1/lyrics/": (404, {}, {})})
    o = svc.fetch(provider="spotify", track_id=SID, title="S", artist="B")
    o2 = svc.fetch(provider="spotify", track_id=SID, title="S", artist="B")
    check("lyrics 404 -> notfound, remembered a day", o.status == o2.status == "notfound" and len(http.calls) == 1)
    # no key
    svc, http = mk_service({"/v1": (200, {}, env(body_line()))}, key=None)
    o = svc.fetch(provider="spotify", track_id=SID, title="S", artist="B")
    check("no key: skipped silently, zero requests, no notice", o.status == "skipped" and not http.calls and not o.notice)
    # auth failure -> notice once, then paused (no more requests)
    svc, http = mk_service({"/v1": (401, {}, {})})
    o = svc.fetch(provider="spotify", track_id=SID, title="S", artist="B")
    o2 = svc.fetch(provider="spotify", track_id="z" * 22, title="S", artist="B")
    check("401: discreet notice, then the source rests (1 request total)", o.status == "error" and "rejected" in o.notice
          and o2.status == "skipped" and len(http.calls) == 1)
    # 429
    svc, http = mk_service({"/v1": (429, {"retry-after": "9"}, {})})
    o = svc.fetch(provider="spotify", track_id=SID, title="S", artist="B")
    check("429: error + notice, LRCLIB takes over", o.status == "error" and o.notice_kind == "rate")
    # stale (track changed while waiting) -> no request at all
    sleeps = []
    svc, http = mk_service({"/v1": (200, {}, env(body_line()))}, sleeps=sleeps, debounce=0.35)
    o = svc.fetch(provider="spotify", track_id=SID, title="S", artist="B", is_current=lambda: False)
    check("song changed during the debounce: skipped, 0 requests", o.status == "skipped" and not http.calls and sleeps == [0.35])
    # broken payload
    svc, http = mk_service({"/v1/lyrics/": (200, {}, env({"Type": "Weird"}))})
    o = svc.fetch(provider="spotify", track_id=SID, title="S", artist="B")
    check("unusable payload -> error, nothing cached", o.status == "error" and not os.listdir(svc.cache.dir))
    # cache expiry through the service (30 days)
    clk = Clock()
    http = FakeHttp({"/v1/lyrics/": (200, {}, env(body_line()))})
    svc = sl.SpicyService(sl.SpicyCache(tempfile.mkdtemp(prefix="spicy_svc_"), clock=clk), key_fn=lambda: FAKE_KEY,
                          client=mk_client(http), debounce=0)
    svc.fetch(provider="spotify", track_id=SID, title="S", artist="B")
    clk.t += 31 * 86400
    svc.fetch(provider="spotify", track_id=SID, title="S", artist="B")
    check("an entry older than 30 days is refetched, not used", len(http.calls) == 2)


def test_key_lookup_and_flag():
    class St:
        def __init__(self, v): self.v = v
        def get(self, acc, env=None): return self.v if acc == sl.KEY_ACCOUNT else None
    check("env var wins", sl.resolve_key(St("kr"), env={sl.KEY_ENV: " envkey "}) == ("envkey", "env"))
    check("keyring next", sl.resolve_key(St("kr"), env={}) == ("kr", "keyring"))
    check("nothing -> (None, None)", sl.resolve_key(St(None), env={}) == (None, None))
    check("account/service match what teofu stored", sl.KEY_ACCOUNT == "spicy-lyrics")
    check("on by default", sl.enabled_in({}, env={}) and sl.enabled_in(None, env={}))
    check("config false turns it off", not sl.enabled_in({"spicy_lyrics": False}, env={}))
    check("SPT_SPICY_LYRICS=off turns it off", not sl.enabled_in({}, env={"SPT_SPICY_LYRICS": "off"}))
    check("hint tells both ways to add the key", "secret-tool" in sl.key_hint() and sl.KEY_ENV in sl.key_hint())


# --------------------------------------------------------------------------- #
# App level
# --------------------------------------------------------------------------- #
class Fake:
    name = "spotify"
    def has_cached_token(self): return True
    def user_playlists(self, limit=50, offset=0): return {"items": [], "next": None}
    def devices(self): return []
    def get_playback(self): return {}
    def ensure(self): return self


class TApp(SptPy):
    def __init__(self, script=None, key=FAKE_KEY):
        super().__init__(); self.spotify = Fake(); self.fetches = 0
        self.http = FakeHttp(script or {})
        try:                                   # every case starts with an empty LRCLIB cache
            os.remove(os.path.join(config.CACHE_DIR, "lyrics_cache.json"))
        except OSError:
            pass
        self._spicy_svc = sl.SpicyService(
            sl.SpicyCache(tempfile.mkdtemp(prefix="spicy_app_")), key_fn=lambda: key,
            client=mk_client(self.http, key=key), debounce=0)
    def _fetch_synced_lyrics(self, **kw):
        self.fetches += 1
        return self._parse_lrc("[00:00.00]lrclib first\n[00:02.00]lrclib second\n[00:09.00]lrclib later"), "found"


def pause_intervals(app):
    for a in ("_now_sync_interval", "_now_tick_interval", "_now_interval", "_devices_interval", "_queue_interval"):
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


def box_text(app, width=120):
    from rich.console import Console
    r = app.lyrics_box.content
    if isinstance(r, str):
        from rich.text import Text
        r = Text.from_markup(r)
    c = Console(width=width, record=True, color_system=None)
    c.print(r)
    return c.export_text()


def set_track(app, tid=SID, pos=0, playing=False):
    app._bar_last_track = {"id": tid, "name": "Song", "artists": [{"name": "Band"}], "album": {"name": "LP"},
                           "duration_ms": 60000}
    app._now_internal_track_id = tid
    app._now_internal_pos_ms = pos
    app._now_internal_is_playing = playing
    app._now_internal_dur_ms = 60000


async def test_app_order_and_attribution():
    d = os.path.join(config.CACHE_DIR, "lyrics")
    os.makedirs(d, exist_ok=True)
    # 1) Spicy Lyrics beats LRCLIB; karaoke is word-synced and shows the credit in a fixed box.
    config.LOCAL_CFG = {"lyrics_mode": "karaoke"}
    app = TApp({"/v1/lyrics/": (200, {}, env(body_syllable()))})
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app, pos=1500)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        await pilot.pause(); await pilot.pause()
        check("Spicy Lyrics used before LRCLIB (no LRCLIB request)", app.fetches == 0 and len(app.http.calls) == 1
              and app._lyrics_source == "Spicy Lyrics")
        t = box_text(app)
        check("karaoke status says word-synced · Spicy Lyrics", "word-synced · Spicy Lyrics" in t, t[-300:])
        check("credit is visible with the lyrics (provider, uploader, maker, links)",
              "Lyrics: Spicy Lyrics" in t and "uid/1" in t and "the maker" in t, t[-500:])
        box_h = app.lyrics_box.content_size.height
        check("geometry still exactly the box height", len(app._karaoke_rows) == box_h, f"{len(app._karaoke_rows)} vs {box_h}")
        # frames at different positions keep the same row count and the same credit rows
        counts = set()
        for pos in (1500, 2500, 5200, 12000, 20200):
            app._now_internal_pos_ms = pos
            app._update_lyrics_highlight(); await pilot.pause()
            counts.add((len(app._karaoke_rows), app._karaoke_rows[-1].plain))
        check("credit stays on the last row across frames", len(counts) == 1, str(counts))
        # switch to the line view: the credit is under the lines
        app.action_lyrics_mode(); await pilot.pause()
        t = box_text(app)
        check("line view shows the credit too", "Lyrics: Spicy Lyrics" in t and "the maker" in t, t[-300:])
        # leaving the song
    # 2) Line type -> line mode shows it, karaoke says approximate, provider only for Spotify source.
    config.LOCAL_CFG = {}
    app = TApp({"/v1/lyrics/": (200, {}, env(body_line("spotify")))})
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app, pos=1500)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        t = box_text(app)
        check("Line type: its lines and the Spotify credit (no uploader row)", "First line" in t and "Lyrics: Spotify" in t
              and "Uploaded by" not in t and app.fetches == 0, t[-300:])
        app.action_lyrics_mode(); await pilot.pause()
        t = box_text(app)
        check("Line type in karaoke is marked approximate", "approximate" in t)
    # 3) Static from Spicy: LRCLIB (synced) wins
    app = TApp({"/v1/lyrics/": (200, {}, env(body_static("apple_music")))})
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        t = box_text(app)
        check("Static is not used while LRCLIB has synced lines", app.fetches == 1 and "lrclib" in t and "Lyrics: LRCLIB" in t, t[-300:])
    # 4) no key: exactly the old behaviour, LRCLIB credit, nothing asked
    app = TApp({"/v1": (200, {}, env(body_line()))}, key=None)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        await pilot.pause()
        t = box_text(app)
        check("no key: LRCLIB used, no Spicy request, no error", app.fetches == 1 and not app.http.calls
              and "lrclib first" in t and "Lyrics: LRCLIB" in t, t[-300:])
        check("…and no warning on the status line", "Spicy" not in str(app.status_line.render()))
    # 5) switch off in config: not even the cache
    config.LOCAL_CFG = {"spicy_lyrics": False}
    app = TApp({"/v1": (200, {}, env(body_line()))})
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        check("Settings flag off: LRCLIB only", app.fetches == 1 and not app.http.calls)
    config.LOCAL_CFG = {}
    # 6) errors fall through to LRCLIB with one discreet notice
    app = TApp({"/v1": (401, {}, {})})
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        await pilot.pause(); await pilot.pause()
        check("401: LRCLIB lyrics shown", app.fetches == 1 and "lrclib" in box_text(app))
        check("…with a one-line notice (and no key in it)", "rejected" in str(app.status_line.render())
              and FAKE_KEY not in str(app.status_line.render()))
    # 7) a local file beats Spicy Lyrics
    with open(os.path.join(d, "band - song.lrc"), "w", encoding="utf-8") as fh:
        fh.write("[00:01.00]my own file\n[00:03.00]second\n")
    app = TApp({"/v1": (200, {}, env(body_line()))})
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app)
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        t = box_text(app)
        check("local file first: no Spicy request, credit says Local file", not app.http.calls and app.fetches == 0
              and "my own file" in t and "Lyrics: Local file" in t, t[-300:])
    os.remove(os.path.join(d, "band - song.lrc"))
    # 8) a non-Spotify provider resolves through search
    class YT(Fake):
        name = "ytmusic"
    script = {"/v1/tracks/search": (200, {}, {"Body": {"results": [
        {"id": SID, "title": "Song", "artists": ["Band"], "duration": 60.0, "tier": "exact"}]}}),
              "/v1/lyrics/": (200, {}, env(body_syllable("apple_music")))}
    app = TApp(script)
    app.spotify = YT()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(); pause_intervals(app)
        set_track(app, tid="dQw4w9WgXcQ")
        app.action_toggle_lyrics()
        await poll(lambda: app._lyrics_lines)
        urls = [c[0] for c in app.http.calls]
        check("ytmusic track: search, validated match, then lyrics", len(urls) == 2 and "search" in urls[0]
              and app.fetches == 0, str(urls))
        check("Apple Music credit: provider only", "Lyrics: Apple Music" in box_text(app) and "Uploaded by" not in box_text(app))
    # the key is never in the config JSON
    cfgtxt = json.dumps(config.LOCAL_CFG)
    check("the key is not in the config", FAKE_KEY not in cfgtxt)


async def test_settings():
    from textual.widgets import OptionList
    saved = []
    orig_save = config.save_local_config
    config.save_local_config = lambda cfg: saved.append(dict(cfg))
    config.LOCAL_CFG = {}
    try:
        app = TApp({})
        async with app.run_test(size=(150, 60)) as pilot:
            await pilot.pause(); pause_intervals(app)
            app.action_prompt_settings(); await pilot.pause(); await pilot.pause()
            sel = getattr(app, "spicy_select", None)
            check("Settings has the Spicy Lyrics option, on by default", isinstance(sel, OptionList) and sel.highlighted == 0)
            check("it is part of the Tab/arrow form", sel in app._right_form_fields())
            st = await poll(lambda: "Key:" in str(app.spicy_key_status.content) and "checking" not in str(app.spicy_key_status.content)
                            and str(app.spicy_key_status.content))
            check("key status line appears (found or how to add it)", bool(st), str(getattr(app.spicy_key_status, 'content', '')))
            check("the key value is never shown", FAKE_KEY not in str(st))
            sel.highlighted = 1
            sel.action_select(); await pilot.pause()
            check("choosing 'Off' saves spicy_lyrics=false", config.LOCAL_CFG.get("spicy_lyrics") is False and saved)
            sel.highlighted = 0
            sel.action_select(); await pilot.pause()
            check("choosing 'on' saves true", config.LOCAL_CFG.get("spicy_lyrics") is True)

            # API key field: goes to the (fake) keyring, never to spt_config.json
            from textual.widgets import Input
            from spt_tui import secret_store
            class _Store:
                def __init__(self): self.d = {}
                def set(self, acct, val): self.d[acct] = val; return "fake"
                def get(self, acct): return self.d.get(acct)
            fake = _Store()
            orig_ds = secret_store.default_store
            secret_store.default_store = lambda: fake
            try:
                ki = getattr(app, "spicy_key_input", None)
                check("Settings has a hidden API key field", isinstance(ki, Input) and ki.password is True)
                check("the key field is part of the Tab/arrow form", ki in app._right_form_fields())
                saved.clear()
                app.on_input_submitted(type("Ev", (), {"input": ki, "value": "  " + FAKE_KEY + " "})())
                await pilot.pause()
                check("submitting stores the key in the keyring", fake.d.get(sl.KEY_ACCOUNT) == FAKE_KEY, str(fake.d))
                check("the key never reaches spt_config.json", FAKE_KEY not in json.dumps(config.LOCAL_CFG) and
                      FAKE_KEY not in json.dumps(saved))
                check("the field is cleared after saving", ki.value == "")
                fake.d.clear()
                app.on_input_submitted(type("Ev", (), {"input": ki, "value": ""})())
                check("empty input keeps the current key (stores nothing)", not fake.d)
                check("store_key rejects an empty value", sl.store_key("   ", store=fake) is None)
            finally:
                secret_store.default_store = orig_ds
    finally:
        config.save_local_config = orig_save
        config.LOCAL_CFG = {}


if __name__ == "__main__":
    test_parse_syllable()
    test_parse_line_static_bad()
    test_attribution()
    test_cache()
    test_rate_limiter()
    test_client_errors()
    test_local_server()
    test_key_never_leaks()
    test_search_matching()
    test_service()
    test_key_lookup_and_flag()
    asyncio.run(test_app_order_and_attribution())
    asyncio.run(test_settings())
    okc = sum(1 for _, c, _ in RESULTS if c)
    print(f"{okc}/{len(RESULTS)} checks passed")
    sys.exit(0 if okc == len(RESULTS) else 1)
