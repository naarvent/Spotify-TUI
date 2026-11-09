"""Word-by-word lyrics from the Spicy Lyrics developer API (optional source).

Order of lyric sources in the app: your own files (``<data>/lyrics/*.ttml|lrc``)
> this module (only with a key) > LRCLIB. Without a key everything behaves as
before: nothing is asked, nothing fails.

What the API does (verified against ``developers.spicylyrics.org`` and one real
request, 2026-11-07):

* ``GET /v1/lyrics/{id}`` — Spotify id (also ISRC / Apple Music id). Envelope
  ``{"Body": {...}, "Status": 200, "Type": "object"}``. ``Body.Type`` is
  ``Syllable`` (``Content[]`` of ``{Type:"Vocal", OppositeAligned, Lead:{Syllables:[{Text,
  StartTime, EndTime, IsPartOfWord}], StartTime, EndTime}, Background:[group…]}``),
  ``Line`` (``Content[]`` of ``{Text, StartTime, EndTime, OppositeAligned}``) or
  ``Static`` (``Lines[]`` of ``{Text}``, no times). Times are SECONDS (floats).
  ``Body.source`` is ``spicy_lyrics`` | ``apple_music`` | ``spotify`` | ``unknown``;
  ``Body.UploadAttribution.{Uploader,Maker}.{username,url}`` exist only for
  ``spicy_lyrics`` (``Maker`` may be absent).
* ``GET /v1/tracks/search?title=&artist=&album=&duration=`` — ``Body.results[]``
  best first, each with ``id`` (Spotify), ``title``, ``artists[]``, ``duration``
  (seconds) and ``tier`` (exact / likely / version / other_artist / related).
  A search counts as one request.
* Limit seen: 60 requests per 50 s per application (``RateLimit-*`` headers).

Terms honoured here (see ``developers.spicylyrics.org/terms``): attribution next
to the lyrics (:func:`attribution_rows`), cache of at most 30 days enforced when
reading and pruning (:class:`SpicyCache`), no export of the data, one key per
person (never in the config JSON, never logged).

Pure Python: no Textual, nothing here touches the UI thread; the network, the
clock, the sleep and the key lookup are injectable so tests run offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from rich.style import Style
from rich.text import Text

from . import karaoke
from .config import logger

API_BASE = "https://api.spicylyrics.org"
KEY_ACCOUNT = "spicy-lyrics"            # keyring: service "spt-tui", username "spicy-lyrics"
KEY_ENV = "SPT_SPICY_LYRICS_KEY"
CONFIG_FLAG = "spicy_lyrics"            # spt_config.json: false turns the source off (default on)
USER_AGENT = "spt-tui (terminal music client)"

CACHE_TTL = 30 * 24 * 3600              # the API's hard limit: refetch or discard within 30 days
NEG_TTL = 24 * 3600                     # "no lyrics for this track" is remembered a day
CACHE_MAX_FILES = 400
CACHE_MAX_BYTES = 40 * 1024 * 1024
PRUNE_EVERY = 3600

RATE_MAX_CALLS = 50                     # the observed limit is 60 per 50 s: keep a margin
RATE_WINDOW = 50.0
MAX_BODY_BYTES = 4 * 1024 * 1024
HTTP_TIMEOUT = 8.0
DEBOUNCE_S = 0.35                       # skipping through songs must not spend requests

SOURCE_LABELS = {"spicy_lyrics": "Spicy Lyrics", "apple_music": "Apple Music", "spotify": "Spotify"}

_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")


# --------------------------------------------------------------------------- #
# Key
# --------------------------------------------------------------------------- #
def resolve_key(store=None, *, env=None) -> Tuple[Optional[str], Optional[str]]:
    """``(key, where)`` with ``where`` = "env" | "keyring" | None. May touch the
    keyring (a D-Bus call): call it from a worker thread, not the UI thread."""
    env = os.environ if env is None else env
    v = (env.get(KEY_ENV) or "").strip()
    if v:
        return v, "env"
    try:
        if store is None:
            from .secret_store import default_store
            store = default_store()
        v = (store.get(KEY_ACCOUNT) or "").strip()
    except Exception:
        logger.exception("spicy lyrics: key lookup failed")
        return None, None
    return (v, "keyring") if v else (None, None)


def store_key(value: str, store=None) -> Optional[str]:
    """Put the key in the keyring (never in spt_config.json). Returns the backend
    used, or None if there is no secure place for it or the value is empty."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if store is None:
            from .secret_store import default_store
            store = default_store()
        return store.set(KEY_ACCOUNT, value)
    except Exception:
        logger.exception("spicy lyrics: storing the key failed")   # never log the value
        return None


def key_hint() -> str:
    """One line telling how to add the key (shown in Settings)."""
    return ("Add your key to the keyring: secret-tool store --label='SPT-TUI (spicy-lyrics)' "
            f"service spt-tui username {KEY_ACCOUNT}  (it asks for the key), or set {KEY_ENV}. "
            "Free key: developers.spicylyrics.org. Without it lyrics come from LRCLIB.")


ENV_SWITCH = "SPT_SPICY_LYRICS"         # "off"/"0"/"false" disables the source (the test runner sets it)


def enabled_in(cfg, env=None) -> bool:
    """Settings flag: on unless explicitly turned off (config or SPT_SPICY_LYRICS=off)."""
    env = os.environ if env is None else env
    if (env.get(ENV_SWITCH) or "").strip().lower() in ("off", "0", "false", "no"):
        return False
    return not (isinstance(cfg, dict) and cfg.get(CONFIG_FLAG) is False)


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #
@dataclass
class Contributor:
    name: str
    url: str = ""


@dataclass
class Attribution:
    provider: str                        # "Spicy Lyrics", "Apple Music", "Spotify", "LRCLIB", "Local file"…
    source: str = ""                     # raw API `source`, or "lrclib" / "local"
    uploader: Optional[Contributor] = None
    maker: Optional[Contributor] = None


def _clean_text(s, limit: int = 60) -> str:
    s = "".join(ch for ch in str(s or "") if unicodedata.category(ch)[0] != "C")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _safe_url(u) -> str:
    u = str(u or "").strip()
    return u if re.fullmatch(r"https://[^\s\x00-\x1f]{1,200}", u) else ""


def _contributor(d) -> Optional[Contributor]:
    if not isinstance(d, dict):
        return None
    name = _clean_text(d.get("username"))
    if not name:
        return None
    return Contributor(name, _safe_url(d.get("url")))


def attribution_from_body(body: dict) -> Attribution:
    """Provider always; uploader and maker only for a community sync and only if
    present (never an empty credit line). An unidentified source says "unknown"."""
    src = str(body.get("source") or "")
    provider = SOURCE_LABELS.get(src, "unknown source")
    att = Attribution(provider, src)
    if src == "spicy_lyrics":
        ua = body.get("UploadAttribution")
        if isinstance(ua, dict):
            att.uploader = _contributor(ua.get("Uploader"))
            att.maker = _contributor(ua.get("Maker"))
    return att


def attribution_rows(att: Optional[Attribution], width: int) -> List[Text]:
    """The credit as one-row ``Text``s (cropped, never wrapped): the provider, then
    uploader and maker with their profile link (also as a terminal hyperlink)."""
    if att is None:
        return []
    width = max(8, int(width))
    rows: List[Text] = []

    def row(label: str, who: Optional[Contributor] = None) -> Text:
        t = Text(no_wrap=True, overflow="crop", justify="center")
        t.append(label, style="dim")
        if who is not None:
            t.append(who.name, style=Style(dim=True, underline=bool(who.url), link=who.url or None))
            if who.url:
                t.append(f"  {who.url}", style="dim")
        return t

    rows.append(row(f"Lyrics: {att.provider}"))
    if att.uploader:
        rows.append(row("Uploaded by ", att.uploader))
    if att.maker:
        rows.append(row("Made by ", att.maker))
    return rows


def attribution_markup(att: Optional[Attribution]) -> str:
    """Same credit as dim Textual markup lines, for the line-by-line view."""
    if att is None:
        return ""
    from rich.markup import escape
    lines = [f"Lyrics: {att.provider}"]
    for lab, who in (("Uploaded by ", att.uploader), ("Made by ", att.maker)):
        if who:
            lines.append(f"{lab}{who.name}" + (f"  {who.url}" if who.url else ""))
    return "\n".join(f"[dim]{escape(x)}[/dim]" for x in lines)


# --------------------------------------------------------------------------- #
# Response -> karaoke model
# --------------------------------------------------------------------------- #
@dataclass
class SpicyResult:
    kind: str                                      # "Syllable" | "Line" | "Static"
    attribution: Attribution
    spotify_id: str = ""
    klines: Optional[List[karaoke.KLine]] = None   # Syllable / Line
    lines: List[tuple] = field(default_factory=list)   # line view tuples (ts_ms, text)


def _ms(v) -> Optional[int]:
    try:
        return int(round(float(v) * 1000))
    except (TypeError, ValueError):
        return None


def _group_words(group) -> Tuple[List[karaoke.Word], str]:
    """Syllables of a vocal group -> Words (one per syllable, so the wipe follows
    the real syllable times) and the romanisation if the data carries it."""
    out: List[karaoke.Word] = []
    roman_parts: List[str] = []
    syls = [s for s in (group.get("Syllables") or []) if isinstance(s, dict)
            and str(s.get("Text") or "").strip()]
    prev_end = 0
    for i, s in enumerate(syls):
        st = _ms(s.get("StartTime"))
        en = _ms(s.get("EndTime"))
        if st is None:
            st = prev_end
        if en is None or en < st:
            en = st
        txt = str(s.get("Text")).strip()
        last = i + 1 >= len(syls)
        if not last and not s.get("IsPartOfWord"):
            txt += " "
        out.append(karaoke.Word(st, en, txt))
        prev_end = en
        r = s.get("TransliteratedText")
        if r:
            sp = "" if (last or s.get("TransliteratedIsPartOfWord", s.get("IsPartOfWord"))) else " "
            roman_parts.append(str(r).strip() + sp)
    roman = _clean_text("".join(roman_parts) or group.get("TransliteratedText") or "", 200)
    return out, roman


def _syllable_lines(content) -> List[karaoke.KLine]:
    lines: List[karaoke.KLine] = []
    for item in content or []:
        if not isinstance(item, dict) or not isinstance(item.get("Lead"), dict):
            continue
        words, roman = _group_words(item["Lead"])
        if not words:
            continue
        text = "".join(w.text for w in words)
        start = words[0].start
        end = max(words[-1].end, start + 1)
        bg: List[karaoke.Word] = []
        groups = [g for g in (item.get("Background") or []) if isinstance(g, dict)]
        for j, g in enumerate(groups):
            gw, _ = _group_words(g)
            if gw and j + 1 < len(groups) and not gw[-1].text.endswith(" "):
                gw[-1].text += " "
            bg.extend(gw)
        lines.append(karaoke.KLine(start, end, text, words, real_words=True,
                                   agent="v2" if item.get("OppositeAligned") else "v1",
                                   bg=bg, roman=roman))
    return lines


def _line_lines(content) -> List[karaoke.KLine]:
    rows = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("Text") or "")).strip()
        st = _ms(item.get("StartTime"))
        if not text or st is None:
            continue
        rows.append((st, _ms(item.get("EndTime")), text, bool(item.get("OppositeAligned")),
                     _clean_text(item.get("TransliteratedText"), 200)))
    rows.sort(key=lambda r: r[0])
    out: List[karaoke.KLine] = []
    for i, (st, en, text, opp, roman) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else None
        guess = karaoke.sung_end(text, st, nxt)
        end = min(en, guess) if (en is not None and en > st) else guess
        end = max(end, st + 1)
        out.append(karaoke.KLine(st, end, text, karaoke.approx_words(text, st, end), real_words=False,
                                 agent="v2" if opp else "v1", roman=roman))
    return out


def parse_lyrics(payload) -> SpicyResult:
    """Envelope or bare ``Body`` -> :class:`SpicyResult`. Raises ``ValueError``
    when the shape is not one the app can use (the caller then moves on to the
    next source)."""
    body = payload.get("Body") if isinstance(payload, dict) and isinstance(payload.get("Body"), dict) else payload
    if not isinstance(body, dict):
        raise ValueError("not a lyrics object")
    kind = body.get("Type")
    att = attribution_from_body(body)
    sid = str(body.get("id") or "")
    if kind == "Syllable":
        kl = _syllable_lines(body.get("Content"))
    elif kind == "Line":
        kl = _line_lines(body.get("Content"))
    elif kind == "Static":
        texts = [re.sub(r"\s+", " ", str((x or {}).get("Text") or "")).strip()
                 for x in (body.get("Lines") or []) if isinstance(x, dict)]
        texts = [t for t in texts if t]
        if not texts:
            raise ValueError("static lyrics without lines")
        return SpicyResult("Static", att, sid, None, [(0, t) for t in texts])
    else:
        raise ValueError(f"unknown lyrics type {kind!r}")
    if not kl:
        raise ValueError("lyrics without timed lines")
    kl.sort(key=lambda k: k.start)
    dur = _ms(body.get("EndTime")) or 0
    full = karaoke._with_interludes(kl, dur)
    return SpicyResult(kind, att, sid, full, karaoke.to_line_tuples(full))


# --------------------------------------------------------------------------- #
# Matching a track without a Spotify id (search)
# --------------------------------------------------------------------------- #
def clean_title(title: str) -> str:
    t = title or ""
    t = re.sub(r"\s*[\(\[]\s*(feat|ft|featuring|with)\.?\s[^)\]]*[\)\]]", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*(\d{0,4}\s*)?(re-?master(ed)?|remaster|live|mono|stereo|radio edit|"
               r"single version|album version|deluxe|bonus track)\b.*$", "", t, flags=re.IGNORECASE)
    return t.strip() or (title or "").strip()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", clean_title(s or "")).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def pick_search_result(results, *, title: str, artist: str, duration_s: float = 0.0) -> Optional[dict]:
    """The result to trust, or None. Never "the first one": the API says how well
    each matches (``tier``) and a different version has different timings.

    * ``exact`` is taken when the title agrees and, if we know the length, it is
      within 3 s (the duration is what tells versions apart);
    * ``likely`` only with a known length within 2 s, the same normalised title
      and our artist among the result's artists."""
    want_t, want_a = _norm(title), _norm(artist)
    for r in results or []:
        if not isinstance(r, dict) or not _SPOTIFY_ID.match(str(r.get("id") or "")):
            continue
        tier = r.get("tier")
        if tier not in ("exact", "likely"):
            continue
        same_title = bool(want_t) and _norm(str(r.get("title") or "")) == want_t
        try:
            dd = abs(float(r.get("duration")) - float(duration_s)) if duration_s else None
        except (TypeError, ValueError):
            dd = None
        if tier == "exact":
            if same_title or (want_t and want_t in _norm(str(r.get("title") or ""))):
                if dd is None or dd <= 3.0:
                    return r
        else:
            arts = [_norm(a) for a in (r.get("artists") or []) if isinstance(a, str)]
            if same_title and dd is not None and dd <= 2.0 and want_a and any(
                    want_a == a or want_a in a or a in want_a for a in arts if a):
                return r
    return None


# --------------------------------------------------------------------------- #
# Errors, rate limit, HTTP
# --------------------------------------------------------------------------- #
class SpicyError(Exception):
    """``kind``: auth | rate | notfound | network | server | bad."""

    def __init__(self, kind: str, message: str = "", retry_after: float = 0.0):
        super().__init__(message or kind)
        self.kind = kind
        self.retry_after = retry_after


class RateLimiter:
    """Sliding window (default 50 calls / 50 s) plus a cool-down after a 429."""

    def __init__(self, max_calls: int = RATE_MAX_CALLS, window: float = RATE_WINDOW, clock=time.monotonic):
        self.max_calls, self.window, self.clock = max_calls, window, clock
        self._calls: List[float] = []
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            now = self.clock()
            if now < self._blocked_until:
                return False
            self._calls = [t for t in self._calls if now - t < self.window]
            if len(self._calls) >= self.max_calls:
                return False
            self._calls.append(now)
            return True

    def block_for(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(self._blocked_until, self.clock() + max(1.0, min(float(seconds), 300.0)))


def urllib_http(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, Dict[str, str], bytes]:
    """``(status, lower-case headers, body)``; HTTP errors are returned, not raised."""
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    with r:
        data = r.read(MAX_BODY_BYTES + 1)
        status = getattr(r, "status", None) or getattr(r, "code", 0)
        hdr = {k.lower(): v for k, v in r.headers.items()}
    return int(status), hdr, data


class SpicyClient:
    def __init__(self, key_fn: Callable[[], Optional[str]], *, http=urllib_http, base: str = API_BASE,
                 limiter: Optional[RateLimiter] = None, timeout: float = HTTP_TIMEOUT):
        self._key_fn, self._http, self.base = key_fn, http, base.rstrip("/")
        self.limiter = limiter or RateLimiter()
        self.timeout = timeout
        self.requests_made = 0

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        key = self._key_fn()
        if not key:
            raise SpicyError("auth", "no key")
        url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        if not self.limiter.try_acquire():
            raise SpicyError("rate", "local rate limit")
        headers = {"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": USER_AGENT}
        self.requests_made += 1
        try:
            status, hdr, data = self._http(url, headers, self.timeout)
        except Exception as exc:                 # URLError, timeout, reset…: never include headers
            raise SpicyError("network", type(exc).__name__) from None
        if len(data) > MAX_BODY_BYTES:
            raise SpicyError("bad", "response too large")
        if status == 429:
            ra = _num(hdr.get("retry-after"), 30.0)
            self.limiter.block_for(ra)
            raise SpicyError("rate", "429", ra)
        if status in (401, 403):
            raise SpicyError("auth", str(status))
        if status == 404:
            raise SpicyError("notfound", "404")
        if status == 503:
            ra = _num(hdr.get("retry-after"), 20.0)
            self.limiter.block_for(ra)
            raise SpicyError("server", "503", ra)
        if status != 200:
            raise SpicyError("server", str(status))
        try:
            obj = json.loads(data.decode("utf-8"))
        except ValueError:
            raise SpicyError("bad", "invalid JSON") from None
        if not isinstance(obj, dict):
            raise SpicyError("bad", "unexpected JSON")
        return obj

    def lyrics(self, track_id: str) -> dict:
        return self._get("/v1/lyrics/" + urllib.parse.quote(str(track_id), safe=""))

    def search(self, title: str, artist: str = "", album: str = "", duration_s: float = 0.0) -> List[dict]:
        p = {"title": title}
        if artist:
            p["artist"] = artist
        if album:
            p["album"] = album
        if duration_s:
            p["duration"] = f"{float(duration_s):.3f}"
        obj = self._get("/v1/tracks/search", p)
        res = (obj.get("Body") or {}).get("results") if isinstance(obj.get("Body"), dict) else None
        return res if isinstance(res, list) else []


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Cache (<= 30 days, enforced on read and on prune)
# --------------------------------------------------------------------------- #
class SpicyCache:
    """One JSON file per entry in ``directory`` with ``fetched_at``. An entry older
    than ``ttl`` is deleted when read and by :meth:`prune`. Raw API bodies are
    stored (so a parser fix applies to old entries); nothing is ever exported."""

    def __init__(self, directory: str, *, ttl: float = CACHE_TTL, clock=time.time):
        self.dir, self.ttl, self.clock = directory, ttl, clock
        self._last_prune = 0.0
        self._lock = threading.Lock()

    def _path(self, key: str) -> str:
        return os.path.join(self.dir, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def get(self, key: str, *, max_age: Optional[float] = None) -> Optional[dict]:
        p = self._path(key)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                e = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            self._remove(p)
            return None
        try:
            age = self.clock() - float(e["fetched_at"])
        except (KeyError, TypeError, ValueError):
            self._remove(p)
            return None
        if not isinstance(e, dict) or e.get("key") != key or age < 0 or age >= self.ttl:
            self._remove(p)
            return None
        if max_age is not None and age >= max_age:
            return None
        return e

    def put(self, key: str, **fields) -> None:
        e = {"v": 1, "key": key, "fetched_at": self.clock(), **fields}
        p = self._path(key)
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = f"{p}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(e, fh, ensure_ascii=False)
            os.replace(tmp, p)
        except OSError:
            logger.exception("spicy lyrics: cache write failed")
            return
        self.prune(throttle=True)

    @staticmethod
    def _remove(p: str) -> None:
        try:
            os.remove(p)
        except OSError:
            pass

    def prune(self, *, throttle: bool = False) -> int:
        """Delete expired entries, then the oldest ones above the file/byte caps.
        Returns how many files went."""
        with self._lock:
            now = self.clock()
            if throttle and now - self._last_prune < PRUNE_EVERY:
                return 0
            self._last_prune = now
            removed = 0
            items = []
            try:
                names = os.listdir(self.dir)
            except OSError:
                return 0
            for n in names:
                p = os.path.join(self.dir, n)
                if n.endswith(".tmp"):
                    try:
                        if now - os.path.getmtime(p) > 600:
                            self._remove(p); removed += 1
                    except OSError:
                        pass
                    continue
                if not n.endswith(".json"):
                    continue
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        ts = float(json.load(fh)["fetched_at"])
                    size = os.path.getsize(p)
                except (OSError, ValueError, KeyError, TypeError):
                    self._remove(p); removed += 1
                    continue
                if not (0 <= now - ts < self.ttl):
                    self._remove(p); removed += 1
                    continue
                items.append((ts, size, p))
            items.sort()
            total = sum(s for _, s, _ in items)
            while items and (len(items) > CACHE_MAX_FILES or total > CACHE_MAX_BYTES):
                ts, size, p = items.pop(0)
                self._remove(p); removed += 1
                total -= size
            return removed


# --------------------------------------------------------------------------- #
# The service the UI calls (from a worker thread)
# --------------------------------------------------------------------------- #
@dataclass
class Outcome:
    status: str                              # found | notfound | skipped | error
    result: Optional[SpicyResult] = None
    notice: str = ""                         # discreet one-line message for the UI (no key in it)
    notice_kind: str = ""


class SpicyService:
    DIRECT_ID_PROVIDERS = ("spotify",)

    def __init__(self, cache: SpicyCache, *, key_fn: Optional[Callable[[], Optional[str]]] = None,
                 client: Optional[SpicyClient] = None, sleep=time.sleep, debounce: float = DEBOUNCE_S):
        self.cache = cache
        self._key_fn = key_fn or (lambda: resolve_key()[0])
        self._key: Optional[str] = None
        self._key_checked = 0.0
        self.client = client or SpicyClient(self._cached_key)
        self._sleep, self.debounce = sleep, debounce
        self._cooldown_until = 0.0           # after auth failure: stop asking for a while

    def _cached_key(self) -> Optional[str]:
        now = time.monotonic()
        if self._key is None and now - self._key_checked > 30:
            self._key = self._key_fn() or None
            self._key_checked = now
        return self._key

    def has_key(self) -> bool:
        return bool(self._cached_key())

    # ---- id resolution -------------------------------------------------- #
    @staticmethod
    def direct_id(provider: str, track_id) -> Optional[str]:
        tid = str(track_id or "")
        if provider in SpicyService.DIRECT_ID_PROVIDERS and _SPOTIFY_ID.match(tid):
            return tid
        return None

    def fetch(self, *, provider: str, track_id, title: str, artist: str, album: str = "",
              duration_ms: int = 0, is_current: Callable[[], bool] = lambda: True,
              force: bool = False) -> Outcome:
        try:
            return self._fetch(provider, track_id, title, artist, album, duration_ms, is_current, force)
        except SpicyError as e:
            return self._error(e)
        except Exception:
            logger.exception("spicy lyrics: unexpected failure")
            return Outcome("error")

    def _error(self, e: SpicyError) -> Outcome:
        logger.warning("spicy lyrics: %s (%s)", e.kind, e)
        if e.kind == "auth":
            self._cooldown_until = time.monotonic() + 600
            self._key = None
            return Outcome("error", notice="Spicy Lyrics: the key was rejected, using LRCLIB (check Settings).",
                           notice_kind="auth")
        if e.kind == "rate":
            return Outcome("error", notice="Spicy Lyrics: request limit reached, using LRCLIB for now.",
                           notice_kind="rate")
        return Outcome("error")

    def _fetch(self, provider, track_id, title, artist, album, duration_ms, is_current, force) -> Outcome:
        if time.monotonic() < self._cooldown_until:
            return Outcome("skipped")
        if not self._cached_key():
            return Outcome("skipped")                      # no key: the old behaviour, silently
        dur_s = max(0.0, (duration_ms or 0) / 1000.0)
        sid = self.direct_id(provider, track_id)
        waited = False

        def before_network() -> bool:
            nonlocal waited
            if not waited:
                waited = True
                if self.debounce:
                    self._sleep(self.debounce)
            return is_current()

        if sid is None:
            rkey = "resolve:" + "|".join((provider, _norm(title), _norm(artist), str(int(round(dur_s / 3)))))
            e = self.cache.get(rkey)
            if e is not None:
                if e.get("kind") == "resolve_none":
                    if not force and self.cache.get(rkey, max_age=NEG_TTL) is not None:
                        return Outcome("notfound")
                else:
                    sid = e.get("id") or None
            if sid is None:
                if not before_network():
                    return Outcome("skipped")
                results = self.client.search(clean_title(title), artist, album, dur_s)
                pick = pick_search_result(results, title=title, artist=artist, duration_s=dur_s)
                if pick is None:
                    self.cache.put(rkey, kind="resolve_none", id=None)
                    return Outcome("notfound")
                sid = str(pick["id"])
                self.cache.put(rkey, kind="resolve", id=sid)
        lkey = "lyrics:" + sid
        if not force:
            e = self.cache.get(lkey)
            if e is not None:
                if e.get("kind") == "notfound":
                    if self.cache.get(lkey, max_age=NEG_TTL) is not None:
                        return Outcome("notfound")
                else:
                    try:
                        return Outcome("found", parse_lyrics(e.get("body")))
                    except ValueError:
                        pass                               # old/bad entry: refetch
        if not before_network():
            return Outcome("skipped")
        try:
            payload = self.client.lyrics(sid)
        except SpicyError as e:
            if e.kind == "notfound":
                self.cache.put(lkey, kind="notfound")
                return Outcome("notfound")
            raise
        try:
            res = parse_lyrics(payload)
        except ValueError as exc:
            logger.warning("spicy lyrics: unusable response (%s)", exc)
            return Outcome("error")
        self.cache.put(lkey, kind="lyrics", body=payload.get("Body", payload))
        return Outcome("found", res)
