"""Scraper anónimo de Spotify (spt_tui/scraper): token, consultas persistidas, parsers y cliente.
Sin red: HTTP simulado con fixtures reales recortados (tests/fixtures/scraper/).
La prueba en vivo es aparte: tests/check_scraper_live.py.

Run standalone:  python tests/test_scraper.py
"""
import os, sys, json, copy, traceback
from urllib.parse import urlsplit, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from spt_tui.scraper import (ScraperClient, parse_ref, URLError, NotFoundError, ParsingError,
                             TokenError, NetworkError, RateLimitedError)
from spt_tui.scraper import pathfinder, token as tokmod, parse
from spt_tui.scraper.http import Http
from spt_tui.scraper.token import AnonymousTokenProvider

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

def fx(name):
    with open(os.path.join(HERE, "fixtures", "scraper", name), encoding="utf-8") as f:
        return json.load(f)

PID = "37i9dQZF1DXcBWIGoYBM5M"
TID = "4uLU6hMCjMI75M1A2tKUQC"


class Resp:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code = status; self._body = body; self.headers = headers or {}
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def embed_html(token="TOK1", expires=10**13):
    nd = {"props": {"pageProps": {"state": {"settings": {"session": {
        "accessToken": token, "accessTokenExpirationTimestampMs": expires}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></html>'


class FakeHttp:
    """Sustituye a Http: `route(url, headers)` devuelve Resp o lanza."""
    def __init__(self, route):
        self.route = route; self.calls = []
    def get(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        return self.route(url, dict(headers or {}))
    def close(self): pass


def page_of(union, offset, limit):
    """Trozo de una playlist grande simulada."""
    u = copy.deepcopy(union)
    items = u["content"]["items"]
    u["content"]["items"] = items[offset:offset + limit]
    return u


def big_playlist(n):
    """Playlist de n elementos (cada 7º es un episodio) construida con el fixture."""
    base = fx("playlist_page.json")["data"]["playlistV2"]
    tpl = base["content"]["items"][0]
    items = []
    for i in range(n):
        it = copy.deepcopy(tpl)
        d = it["itemV2"]["data"]
        d["uri"] = f"spotify:track:{i:022d}"; d["name"] = f"T{i}"
        if i % 7 == 6:
            d["__typename"] = "Episode"
        items.append(it)
    base = copy.deepcopy(base)
    base["content"]["items"] = items; base["content"]["totalCount"] = n
    return base


def make_route(playlist_union, token_ok=True, fail_page_at=None, stale_once=False, state=None):
    state = state if state is not None else {"tokens": 0, "401": 0}
    def route(url, headers):
        if "/embed/track/" in url:
            state["tokens"] += 1
            return Resp(200, text=embed_html(f"TOK{state['tokens']}"))
        q = parse_qs(urlsplit(url).query)
        v = json.loads(q["variables"][0])
        if stale_once and state["401"] == 0:
            state["401"] += 1
            return Resp(401, {"message": "token"})
        if headers.get("Authorization") != f"Bearer TOK{state['tokens']}":
            return Resp(401, {})
        if q["operationName"][0] == "getTrack":
            return Resp(200, fx("track.json"))
        off, lim = v["offset"], v["limit"]
        if fail_page_at is not None and off == fail_page_at:
            return Resp(200, {"errors": [{"message": "PersistedQueryNotFound"}]})
        return Resp(200, {"data": {"playlistV2": page_of(playlist_union, off, lim)}})
    return route, state


# --------------------------------------------------------------------------- #
def test_parse_ref():
    check("ref: URL", parse_ref(f"https://open.spotify.com/playlist/{PID}") == ("playlist", PID))
    check("ref: URL con ?si= e intl", parse_ref(f"https://open.spotify.com/intl-es/playlist/{PID}?si=abc") == ("playlist", PID))
    check("ref: URL embed", parse_ref(f"open.spotify.com/embed/track/{TID}") == ("track", TID))
    check("ref: URI", parse_ref(f"spotify:playlist:{PID}") == ("playlist", PID))
    check("ref: ID con tipo", parse_ref(PID, "playlist") == ("playlist", PID))
    for bad in ("", "hola", PID, "https://example.com/playlist/" + PID, "spotify:playlist:corto",
                "https://open.spotify.com/playlist", "https://open.spotify.com/user/" + PID):
        try:
            parse_ref(bad); ok = False
        except URLError:
            ok = True
        check(f"ref: rechaza {bad[:30]!r}", ok)


def test_pathfinder():
    url = pathfinder.build_url("playlist", PID, overrides={"offset": 100})
    q = parse_qs(urlsplit(url).query)
    v = json.loads(q["variables"][0]); ext = json.loads(q["extensions"][0])
    check("pathfinder: operación y hash", q["operationName"] == ["fetchPlaylist"]
          and ext["persistedQuery"]["sha256Hash"] == pathfinder.OPERATIONS["playlist"].sha256)
    check("pathfinder: variables + overrides", v["uri"] == f"spotify:playlist:{PID}" and v["offset"] == 100 and v["limit"] == 100)
    check("pathfinder: URL base", url.startswith(pathfinder.PATHFINDER_URL + "?"))
    check("pathfinder: cabeceras", pathfinder.auth_headers("X")["Authorization"] == "Bearer X")
    for fn, exc, nm in (
        (lambda: pathfinder.classify_response(401, {}), TokenError, "401→TokenError"),
        (lambda: pathfinder.classify_response(200, None), ParsingError, "sin JSON→ParsingError"),
        (lambda: pathfinder.classify_response(200, {"errors": [{"message": "PersistedQueryNotFound"}]}), ParsingError, "hash rotado→ParsingError"),
        (lambda: pathfinder.classify_response(200, {}), ParsingError, "sin data→ParsingError"),
        (lambda: pathfinder.classify_response(200, {"data": {"x": None}}), NotFoundError, "data nulo→NotFoundError"),
        (lambda: pathfinder.classify_response(200, {"data": {}}), NotFoundError, "data vacío→NotFoundError")):
        try:
            fn(); ok = False
        except exc:
            ok = True
        check(f"pathfinder: {nm}", ok)
    check("pathfinder: data ok", pathfinder.classify_response(200, {"data": {"a": 1}}) == {"a": 1})
    try:
        pathfinder.build_url("nada", PID); ok = False
    except KeyError:
        ok = True
    check("pathfinder: tipo sin operación → KeyError", ok)


def test_token():
    check("token: extrae sesión", tokmod.get_session(tokmod.extract_next_data(embed_html("AB", 5))).access_token == "AB")
    for html, nm in (("<html></html>", "sin script"), ('<script id="__NEXT_DATA__" type="application/json">{no</script>', "JSON roto"),
                     ('<script id="__NEXT_DATA__" type="application/json">[]</script>', "no objeto")):
        try:
            tokmod.extract_next_data(html); ok = False
        except ParsingError:
            ok = True
        check(f"token: {nm} → ParsingError", ok)
    try:
        tokmod.get_session({"props": {"pageProps": {}}}); ok = False
    except ParsingError:
        ok = True
    check("token: falta session → ParsingError", ok)
    try:
        tokmod.get_session(tokmod.extract_next_data(embed_html("", 5))); ok = False
    except ParsingError:
        ok = True
    check("token: accessToken vacío → ParsingError", ok)
    check("token: caduca con margen", tokmod.is_stale(100_000, 40_000) and not tokmod.is_stale(100_000, 39_999))

    now = [1000]
    n = {"c": 0}
    def route(url, headers):
        n["c"] += 1
        return Resp(200, text=embed_html(f"T{n['c']}", expires=1000 + 120_000))
    p = AnonymousTokenProvider(FakeHttp(route), now_ms=lambda: now[0])
    a = p.token(); b = p.token()
    check("token: se cachea", a == b == "T1" and n["c"] == 1)
    now[0] = 1000 + 120_000 - 59_000
    check("token: se renueva cerca de caducar", p.token() == "T2")
    p.invalidate()
    check("token: invalidate fuerza renovación", p.token() == "T3")
    check("token: repr no filtra el token", "T3" not in repr(p) and "T3" not in repr(p._session))

    for r, nm in ((lambda u, h: Resp(403, text=""), "HTTP 403"), (lambda u, h: Resp(200, text="<html>"), "HTML sin datos"),
                  (lambda u, h: (_ for _ in ()).throw(NetworkError("x")), "red caída")):
        try:
            AnonymousTokenProvider(FakeHttp(r)).token(); ok = False
        except TokenError:
            ok = True
        check(f"token: {nm} → TokenError", ok)


def test_parse():
    t = parse.parse_track(fx("track.json")["data"]["trackUnion"])
    check("track: campos", t["id"] == TID and t["name"] == "Never Gonna Give You Up"
          and t["artists"][0]["name"] == "Rick Astley" and t["duration_ms"] == 213573 and t["type"] == "track")
    check("track: álbum con portadas", t["album"]["name"] and t["album"]["images"] and t["album"]["id"])
    u = copy.deepcopy(fx("track.json")["data"]["trackUnion"]); u.pop("duration")
    try:
        parse.parse_track(u); ok = False
    except ParsingError as e:
        ok = "duration" in str(e)
    check("track: falta duración → ParsingError con la ruta", ok)

    pl = parse.parse_playlist(fx("playlist_page.json")["data"]["playlistV2"])
    check("playlist: cabecera", pl["name"] == "Today’s Top Hits" and pl["id"] == PID and pl["owner"]["display_name"] == "Spotify")
    check("playlist: total bruto 50", pl["tracks"]["total"] == 50)
    check("playlist: el episodio se omite (3 de 4)", len(pl["tracks"]["items"]) == 3)
    it = pl["tracks"]["items"][0]
    check("playlist: pista con artista, álbum y fecha", it["track"]["artists"][0]["name"] and it["track"]["album"]["name"]
          and it["added_at"].endswith("Z") and it["track"]["id"] == it["track"]["uri"].split(":")[-1])
    check("playlist: contador bruto cuenta el episodio", parse.raw_item_count(fx("playlist_page.json")["data"]["playlistV2"]) == 4)
    try:
        parse.parse_playlist({}); ok = False
    except ParsingError:
        ok = True
    check("playlist: vacía → ParsingError", ok)


def test_client_track_and_playlist():
    route, st = make_route(big_playlist(10))
    c = ScraperClient(http=FakeHttp(route))
    t = c.track(f"https://open.spotify.com/track/{TID}")
    check("cliente: track por URL", t["name"] == "Never Gonna Give You Up")
    check("cliente: pidió un token y lo envió", st["tokens"] == 1 and c.http.calls[-1][1]["Authorization"] == "Bearer TOK1")
    try:
        c.track(f"https://open.spotify.com/playlist/{PID}"); ok = False
    except URLError:
        ok = True
    check("cliente: tipo equivocado → URLError", ok)

    n = 250
    union = big_playlist(n)
    expected = [f"{i:022d}" for i in range(n) if i % 7 != 6]
    route, st = make_route(union)
    c = ScraperClient(http=FakeHttp(route))
    pl = c.playlist(PID, max_tracks=None)
    ids = [i["track"]["id"] for i in pl["tracks"]["items"]]
    check("cliente: pagina las 250 (sin episodios, sin repetir, en orden)", ids == expected, f"{len(ids)} vs {len(expected)}")
    check("cliente: 3 páginas", sum("fetchPlaylist" in u for u, _ in c.http.calls) == 3)
    check("cliente: no marca parcial", not pl["tracks"].get("partial"))

    route, st = make_route(union)
    c = ScraperClient(http=FakeHttp(route))
    pl = c.playlist(PID, max_tracks=30)
    check("cliente: max_tracks=30 recorta", [i["track"]["id"] for i in pl["tracks"]["items"]] == expected[:30])
    check("cliente: max_tracks=30 no pide más páginas", sum("fetchPlaylist" in u for u, _ in c.http.calls) == 1)
    check("cliente: playlist_tracks devuelve solo pistas", c.playlist_tracks(PID, max_tracks=5)[0]["id"] == expected[0])

    route, st = make_route(union, fail_page_at=100)
    c = ScraperClient(http=FakeHttp(route))
    pl = c.playlist(PID, max_tracks=None)
    check("cliente: página rota → parcial con lo recogido", pl["tracks"].get("partial") is True
          and 0 < len(pl["tracks"]["items"]) < len(expected))

    route, st = make_route(union, stale_once=True)
    c = ScraperClient(http=FakeHttp(route))
    pl = c.playlist(PID, max_tracks=5)
    check("cliente: 401 → renueva el token y reintenta", len(pl["tracks"]["items"]) == 5 and st["tokens"] == 2)

    def always401(url, headers):
        return Resp(200, text=embed_html()) if "/embed/" in url else Resp(401, {})
    c = ScraperClient(http=FakeHttp(always401))
    try:
        c.playlist(PID); ok = False
    except TokenError:
        ok = True
    check("cliente: 401 persistente → TokenError (no bucle)", ok)

    def notfound(url, headers):
        if "/embed/" in url: return Resp(200, text=embed_html())
        return Resp(200, {"data": {"playlistV2": {"__typename": "NotFound", "message": "no"}}})
    try:
        ScraperClient(http=FakeHttp(notfound)).playlist(PID); ok = False
    except NotFoundError:
        ok = True
    check("cliente: NotFound de Spotify → NotFoundError", ok)

    def rotated(url, headers):
        if "/embed/" in url: return Resp(200, text=embed_html())
        return Resp(200, {"errors": [{"message": "PersistedQueryNotFound"}], "data": None})
    try:
        ScraperClient(http=FakeHttp(rotated)).track(TID); ok = False
    except ParsingError as e:
        ok = "PersistedQueryNotFound" in str(e)
    check("cliente: hash rotado → ParsingError claro", ok)


class FakeSession:
    def __init__(self, seq): self.seq = list(seq); self.headers = {}; self.n = 0
    def get(self, url, headers=None, timeout=None):
        self.n += 1
        x = self.seq.pop(0)
        if isinstance(x, Exception): raise x
        return x
    def close(self): pass


def test_http():
    import requests
    sleeps = []
    def mk(seq, **kw):
        s = FakeSession(seq)
        return Http(session=s, sleep=sleeps.append, min_interval=0, **kw), s
    h, s = mk([Resp(503), Resp(200, text="ok")])
    check("http: reintenta 503 y acaba bien", h.get("u").text == "ok" and s.n == 2)
    h, s = mk([requests.ConnectionError("x"), Resp(200, text="ok")])
    check("http: reintenta fallo de red", h.get("u").text == "ok")
    h, s = mk([Resp(503)] * 3)
    try:
        h.get("u"); ok = False
    except NetworkError:
        ok = s.n == 3
    check("http: 3 intentos y NetworkError", ok)
    h, s = mk([Resp(404)])
    try:
        h.get("u?x=1"); ok = False
    except NotFoundError:
        ok = s.n == 1
    check("http: 404 → NotFoundError sin reintentos", ok)
    h, s = mk([Resp(429, headers={"Retry-After": "2"}), Resp(200, text="ok")])
    sleeps.clear()
    check("http: 429 respeta Retry-After", h.get("u").text == "ok" and 2.0 in sleeps)
    h, s = mk([Resp(429)] * 3)
    try:
        h.get("u"); ok = False
    except RateLimitedError:
        ok = True
    check("http: 429 persistente → RateLimitedError", ok)
    h, s = mk([Resp(401, {})])
    check("http: 401 se devuelve (lo clasifica el cliente)", h.get("u").status_code == 401)
    t = [0.0]; waited = []
    h = Http(session=FakeSession([Resp(200)] * 2), sleep=lambda x: (waited.append(x), t.__setitem__(0, t[0] + x)),
             clock=lambda: t[0], min_interval=0.25)
    h.get("a"); h.get("b")
    check("http: respeta el intervalo mínimo", waited and abs(waited[0] - 0.25) < 1e-9)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (excepción)", False, traceback.format_exc(limit=3))
    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n{ok}/{len(RESULTS)} checks passed")
    sys.exit(0 if ok == len(RESULTS) else 1)
