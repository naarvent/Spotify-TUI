"""Word-by-word ("karaoke") lyrics: timing model, parsers and rendering.

An original implementation of an idea popularised by Spicy Lyrics (no code
from it): the current line lights up word by word, the other lines fade with
their distance to it, and instrumental gaps show as "● ● ●" filling up.

Timing comes from, in order of preference:

* real per-word times — enhanced LRC (``[00:12.00]<00:12.00>Hel<00:12.40>lo``)
  or a TTML file (Apple-style ``<p begin end ttm:agent><span begin end>``,
  with background vocals ``ttm:role="x-bg"`` and romanisation
  ``ttm:role="x-roman"`` / ``<transliteration>``);
* otherwise an APPROXIMATION from line times only: the line's sung time is
  split between its words by length (``approx_words``). The UI says so.

Everything here is pure (no Textual, no I/O) so it can be tested and timed.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from rich.text import Text

# Approximation constants. A line is assumed to be sung at ~70 ms per
# character (≈ 14 chars/s), never less than MIN_LINE_MS and never past the next
# line. What is left of a long gap becomes an interlude.
CHAR_MS = 70
MIN_LINE_MS = 1000
INTERLUDE_MIN_MS = 5000          # a gap this long shows "● ● ●"
INTERLUDE_DOTS = 3
LAST_LINE_MS = 6000              # end of the last line when nothing says otherwise

# Fading by distance to the current line (0 = current, handled apart).
FADE = ("#c8c8c8", "#9a9a9a", "#767676", "#5a5a5a", "#454545")
SUNG_STYLE = "bold {accent}"
UNSUNG_STYLE = "bold #f2f2f2"
BG_STYLE = "italic #8a8a8a"
ROMAN_STYLE = "italic #7a7a7a"


@dataclass
class Word:
    start: int
    end: int
    text: str           # includes its trailing space, so joining words gives the line


@dataclass
class KLine:
    start: int
    end: int                       # end of the SUNG part
    text: str
    words: List[Word] = field(default_factory=list)
    real_words: bool = False       # True = word times from the data, False = approximated
    agent: Optional[str] = None    # TTML ttm:agent (duets)
    bg: List[Word] = field(default_factory=list)   # background vocals
    roman: str = ""
    interlude: bool = False


# --------------------------------------------------------------------------- #
# Time parsing
# --------------------------------------------------------------------------- #
_LRC_STAMP = re.compile(r"<(\d+):(\d{1,2}(?:\.\d+)?)>")


def _mmss(m: str, s: str) -> int:
    return int(round((int(m) * 60 + float(s)) * 1000))


def parse_ttml_time(value: Optional[str]) -> Optional[int]:
    """'1:02.5', '00:01:02.500', '62.5s', '62500ms', '62.5' -> milliseconds."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        if v.endswith("ms"):
            return int(round(float(v[:-2])))
        if v.endswith("s") and ":" not in v:
            return int(round(float(v[:-1]) * 1000))
        parts = v.split(":")
        secs = 0.0
        for p in parts:
            secs = secs * 60 + float(p)
        return int(round(secs * 1000))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Enhanced LRC
# --------------------------------------------------------------------------- #
def parse_enhanced_words(text: str, line_ms: int) -> Tuple[str, Optional[List[list]]]:
    """Split ``<mm:ss.xx>`` word stamps out of an LRC line's text.

    Returns ``(clean_text, words)``; ``words`` is ``[[start_ms, word], …]``
    (a trailing stamp with no text is kept as ``[end_ms, ""]``) or None when
    the line has no word stamps."""
    first = _LRC_STAMP.search(text or "")
    if first is None:
        return text, None
    words: List[list] = []
    lead = text[:first.start()]
    if lead.strip():
        words.append([line_ms, lead])
    for m in _LRC_STAMP.finditer(text):
        nxt = _LRC_STAMP.search(text, m.end())
        words.append([_mmss(m.group(1), m.group(2)), text[m.end(): nxt.start() if nxt else len(text)]])
    clean = re.sub(r"\s+", " ", "".join(w for _, w in words)).strip()
    return clean, words


def _words_from_stamps(stamps: Sequence, line_start: int, line_end_hint: int) -> List[Word]:
    out: List[Word] = []
    items = [(int(s), str(t)) for s, t in stamps]
    for i, (s, t) in enumerate(items):
        if not t:
            continue        # a closing stamp: it only gives the previous word's end
        nxt = items[i + 1][0] if i + 1 < len(items) else None
        end = nxt if nxt is not None else min(line_end_hint, s + max(300, len(t.strip()) * CHAR_MS * 2))
        out.append(Word(s, max(s + 1, end), t))
    return out


# --------------------------------------------------------------------------- #
# Approximation
# --------------------------------------------------------------------------- #
def approx_words(text: str, start: int, end: int) -> List[Word]:
    """Spread ``[start, end)`` over the words of ``text`` by length (each word
    weighs its characters plus one for the gap after it)."""
    tokens = re.findall(r"\S+\s*", text or "")
    if not tokens:
        return []
    span = max(1, end - start)
    weights = [len(t.strip()) + 1 for t in tokens]
    total = float(sum(weights))
    out, t = [], float(start)
    for tok, w in zip(tokens, weights):
        d = span * (w / total)
        out.append(Word(int(round(t)), int(round(t + d)), tok))
        t += d
    out[-1].end = end
    return out


def sung_end(text: str, start: int, next_start: Optional[int]) -> int:
    est = start + max(MIN_LINE_MS, len((text or "").strip()) * CHAR_MS)
    if next_start is None:
        return max(est, start + LAST_LINE_MS // 2)
    return min(est, next_start) if next_start > start else start + 1


def _with_interludes(lines: List[KLine], duration_ms: int = 0) -> List[KLine]:
    out: List[KLine] = []
    lines = sorted(lines, key=lambda l: l.start)
    if lines and lines[0].start >= INTERLUDE_MIN_MS:
        out.append(KLine(0, lines[0].start, "", interlude=True))
    for i, ln in enumerate(lines):
        out.append(ln)
        nxt = lines[i + 1].start if i + 1 < len(lines) else None
        if nxt is not None and nxt - ln.end >= INTERLUDE_MIN_MS:
            out.append(KLine(ln.end, nxt, "", interlude=True))
    return out


def from_lines(lines: Sequence, duration_ms: int = 0) -> List[KLine]:
    """KLines from the app's line tuples: ``(ts, text)`` (approximate words) or
    ``(ts, text, [[ms, word], …])`` (enhanced LRC, real words). Empty text
    lines are gaps, not lines."""
    rows = [l for l in (lines or []) if l is not None]
    rows = sorted(rows, key=lambda l: int(l[0]))
    out: List[KLine] = []
    for i, row in enumerate(rows):
        ts, text = int(row[0]), str(row[1] or "")
        stamps = row[2] if len(row) > 2 and row[2] else None
        if not text.strip():
            continue
        # The next line (or a blank gap line) closes this one.
        nxt = next((int(r[0]) for r in rows[i + 1:] if int(r[0]) > ts), None)
        hint = nxt if nxt is not None else (duration_ms or ts + LAST_LINE_MS)
        if stamps:
            words = _words_from_stamps(stamps, ts, hint)
            end = words[-1].end if words else sung_end(text, ts, nxt)
            if stamps and not stamps[-1][1]:
                end = int(stamps[-1][0])
            out.append(KLine(ts, end, text, words, real_words=bool(words)))
        else:
            end = sung_end(text, ts, nxt)
            out.append(KLine(ts, end, text, approx_words(text, ts, end)))
    return _with_interludes(out, duration_ms)


# --------------------------------------------------------------------------- #
# TTML
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _attr(el, name: str) -> Optional[str]:
    for k, v in el.attrib.items():
        if k == name or k.endswith("}" + name):
            return v
    return None


def _span_words(spans, p_start, p_end) -> List[Word]:
    out = []
    for el in spans:
        b, e = parse_ttml_time(_attr(el, "begin")), parse_ttml_time(_attr(el, "end"))
        txt = "".join(el.itertext())
        tail = el.tail or ""
        if not txt:
            continue
        if tail and tail.isspace():
            txt += " "
        b = p_start if b is None else b
        e = (b + max(200, len(txt) * CHAR_MS)) if e is None else e
        out.append(Word(b, max(b + 1, e), txt))
    return out


def parse_ttml(xml_text: str) -> List[KLine]:
    """Lines (and words) of a TTML lyrics document. Raises ValueError when it
    is not TTML or has no timed lines."""
    # Lyrics files are the user's own, but a DTD can still declare entity
    # bombs: TTML never needs one, so refuse any.
    if re.search(r"<!(DOCTYPE|ENTITY)", xml_text or "", flags=re.IGNORECASE):
        raise ValueError("TTML with a DOCTYPE/ENTITY declaration is not accepted")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"not valid XML: {exc}") from exc
    if _local(root.tag) != "tt":
        raise ValueError("not a TTML document (<tt> root expected)")

    # Romanisation in the head: <transliteration><text for="L1">…</text>.
    roman_by_key: Dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) == "text" and _attr(el, "for"):
            roman_by_key[_attr(el, "for")] = re.sub(r"\s+", " ", "".join(el.itertext())).strip()

    lines: List[KLine] = []
    for p in root.iter():
        if _local(p.tag) != "p":
            continue
        start = parse_ttml_time(_attr(p, "begin"))
        end = parse_ttml_time(_attr(p, "end"))
        main_spans, bg_spans, roman = [], [], ""
        for child in list(p):
            if _local(child.tag) != "span":
                continue
            role = _attr(child, "role") or ""
            if role == "x-bg":
                inner = [c for c in child if _local(c.tag) == "span"]
                bg_spans.extend(inner or [child])
            elif role in ("x-roman", "x-translit"):
                roman = re.sub(r"\s+", " ", "".join(child.itertext())).strip()
            elif role == "x-translation":
                continue
            else:
                main_spans.append(child)
        words = _span_words(main_spans, start or 0, end or 0)
        if words:
            text = re.sub(r"\s+", " ", "".join(w.text for w in words)).strip()
        else:
            text = re.sub(r"\s+", " ", (p.text or "") + "".join(
                (c.tail or "") for c in p)).strip()
        if start is None:
            start = words[0].start if words else None
        if start is None or not text:
            continue
        if end is None:
            end = words[-1].end if words else start + max(MIN_LINE_MS, len(text) * CHAR_MS)
        if not words:
            words = approx_words(text, start, end)
            real = False
        else:
            real = True
        key = _attr(p, "key")
        lines.append(KLine(start, end, text, words, real_words=real, agent=_attr(p, "agent"),
                           bg=_span_words(bg_spans, start, end),
                           roman=roman or roman_by_key.get(key or "", "")))
    if not lines:
        raise ValueError("the TTML has no timed lines")
    return _with_interludes(lines)


def to_line_tuples(klines: Sequence[KLine]) -> List[tuple]:
    """For the classic line-by-line view (and its cache): ``(ts, text)``."""
    return [(k.start, k.text) for k in klines if not k.interlude]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def current_index(klines: Sequence[KLine], pos_ms: int) -> int:
    idx = 0
    for i, k in enumerate(klines):
        if k.start <= pos_ms:
            idx = i
        else:
            break
    return idx


def _filled_chars(words: Sequence[Word], pos_ms: int) -> int:
    """How many characters of the line are already sung (char-level wipe
    inside the current word)."""
    n = 0
    for w in words:
        if pos_ms >= w.end:
            n += len(w.text)
            continue
        if pos_ms > w.start:
            frac = (pos_ms - w.start) / float(max(1, w.end - w.start))
            n += int(len(w.text.rstrip()) * frac)
        break
    return n


def _justify(k: KLine, duet: bool) -> str:
    if not duet or not k.agent:
        return "center"
    if k.agent in ("v1",):
        return "left"
    if k.agent in ("v2",):
        return "right"
    return "center"


def _wipe(text: str, filled: int, sung: str, unsung: str) -> Text:
    t = Text()
    if filled > 0:
        t.append(text[:filled], style=sung)
    if filled < len(text):
        t.append(text[filled:], style=unsung)
    return t


def has_real_words(klines: Sequence[KLine]) -> bool:
    """True when at least one line carries real word times."""
    return any(k.real_words for k in klines if not k.interlude)


def approximate_count(klines: Sequence[KLine]) -> int:
    """Lines whose word times are approximated."""
    return sum(1 for k in klines if not k.interlude and not k.real_words)


# --------------------------------------------------------------------------- #
# Fixed-geometry rendering (the karaoke view)
#
# The view is built as an exact number of one-row Text objects (no_wrap,
# cropped), so nothing can reflow: the header and status rows never move and
# the current line always starts on the same body row (``anchor``), like
# Spicy Lyrics keeps the active line in place while the text scrolls past.
# --------------------------------------------------------------------------- #
from rich.cells import cell_len

MAX_LINE_ROWS = 2                   # a long lyric line wraps to at most this many rows
UNSUNG_HEX = "#f2f2f2"
SOFT_EDGE = 2                       # characters of gradient after the wipe front


def _hex_rgb(h: str):
    h = (h or "#ffffff").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def blend(a: str, b: str, t: float) -> str:
    """Mix two #rrggbb colours (t=0 -> a, t=1 -> b)."""
    t = max(0.0, min(1.0, float(t)))
    ra, ga, ba = _hex_rgb(a)
    rb, gb, bb = _hex_rgb(b)
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def wrap_offsets(text: str, width: int, max_rows: int = MAX_LINE_ROWS) -> List[Tuple[int, int]]:
    """Greedy word wrap of ``text`` into at most ``max_rows`` (start, end)
    slices of at most ``width`` cells. The last slice may be cut short (the
    caller adds "…"). CJK / wide characters are measured in cells."""
    width = max(1, int(width))
    out: List[Tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n and len(out) < max_rows:
        while i < n and text[i] == " ":
            i += 1
        if i >= n:
            break
        j, cells, last_space = i, 0, -1
        while j < n:
            w = cell_len(text[j])
            if cells + w > width:
                break
            if text[j] == " ":
                last_space = j
            cells += w
            j += 1
        if j < n and last_space > i and len(out) < max_rows - 1:
            j = last_space            # break at the last space that fits
        out.append((i, j))
        i = j
    return out


def _styled_line(k: KLine, pos_ms: int, accent: str, current: bool, base_style: str) -> Text:
    """The whole line as one Text: wiped with a soft gradient edge when
    current, a single faded colour otherwise."""
    text = k.text
    if not current:
        return Text(text, style=base_style)
    words = k.words
    t = Text()
    offset = 0
    front = None                      # (char index of the wipe front, fraction inside that char)
    for w in words:
        L = len(w.text)
        if pos_ms >= w.end:
            offset += L
            continue
        if pos_ms > w.start:
            frac = (pos_ms - w.start) / float(max(1, w.end - w.start))
            body = len(w.text.rstrip()) or L
            exact = frac * body
            front = (offset + int(exact), exact - int(exact))
        else:
            front = (offset, 0.0)
        break
    if front is None:
        front = (len(text), 0.0)
    fi, ff = front
    for idx, ch in enumerate(text):
        if idx < fi:
            t.append(ch, style=f"bold {accent}")
        elif idx < fi + SOFT_EDGE + 1:
            # Soft edge: the char being sung is partly lit, the next ones a little.
            d = (idx - fi) - ff
            mix = max(0.0, 1.0 - (d + 1) / float(SOFT_EDGE + 1))
            t.append(ch, style=f"bold {blend(UNSUNG_HEX, accent, mix)}")
        else:
            t.append(ch, style=f"bold {UNSUNG_HEX}")
    return t


def _rows_of(styled: Text, width: int, justify: str, max_rows: int = MAX_LINE_ROWS) -> List[Text]:
    plain = styled.plain
    spans = wrap_offsets(plain, width, max_rows)
    rows: List[Text] = []
    for r, (a, b) in enumerate(spans):
        row = styled[a:b]
        if r == len(spans) - 1 and b < len(plain.rstrip()):
            # Cut: end with an ellipsis that still fits.
            while row.cell_len > max(1, width - 1) and len(row.plain) > 1:
                row = row[:-1]
            row.append("…", style=row.spans[-1].style if row.spans else "")
        row.rstrip()
        row.justify = justify
        row.no_wrap = True
        row.overflow = "crop"
        rows.append(row)
    return rows or [Text("", justify=justify)]


def _one(text: str, style: str, justify: str = "center") -> Text:
    t = Text(text, style=style, justify=justify)
    t.no_wrap = True
    t.overflow = "ellipsis"
    return t


def _block(k: KLine, pos_ms: int, *, width: int, accent: str, current: bool, dist: int,
           duet: bool) -> List[Text]:
    """Rows of one line: its text (≤ MAX_LINE_ROWS), then background vocals and
    romanisation (current line and, for the roman, its neighbours)."""
    just = _justify(k, duet)
    if k.interlude:
        if current:
            span = max(1, k.end - k.start)
            n = min(INTERLUDE_DOTS, int(INTERLUDE_DOTS * (pos_ms - k.start) / span) + 1)
            frac = (INTERLUDE_DOTS * (pos_ms - k.start) / span) % 1.0
            t = Text(justify="center")
            for m in range(INTERLUDE_DOTS):
                if m:
                    t.append(" ")
                col = accent if m < n - 1 else (blend("#555555", accent, frac) if m == n - 1 else "#555555")
                t.append("●", style=f"bold {col}")
            t.no_wrap = True
            return [t]
        return [_one("● ● ●", FADE[min(dist, len(FADE)) - 1] if dist else "#555555")]
    style = FADE[min(dist, len(FADE)) - 1] if dist else FADE[0]
    rows = _rows_of(_styled_line(k, pos_ms, accent, current, style), width, just)
    if current and k.bg:
        raw = "".join(w.text for w in k.bg).strip()
        raw = raw if raw.startswith("(") else f"({raw})"
        lit = pos_ms >= k.bg[0].start
        filled = min(len(raw), _filled_chars(k.bg, pos_ms) + (1 if lit else 0))
        bt = _wipe(raw, filled, f"italic {blend('#8a8a8a', accent, 0.6)}", BG_STYLE)
        rows += _rows_of(bt, width, just, 1)
    if k.roman and dist <= 1:
        rows.append(_one(k.roman, ROMAN_STYLE, just))
    return rows


def render_fixed(klines: Sequence[KLine], pos_ms: int, *, width: int, rows: int,
                 accent: str = "#e6ff71") -> List[Text]:
    """Exactly ``rows`` one-row Texts. The current line's first row is always
    row ``rows // 2``; earlier lines fill upwards, later lines downwards, and
    whatever does not fit is cut at the edge (never reflowed)."""
    rows = max(1, int(rows))
    width = max(4, int(width))
    blank = lambda: _one("", "")
    if not klines:
        out = [blank() for _ in range(rows)]
        out[rows // 2] = _one("No Lyrics Found", "italic")
        return out
    duet = len({k.agent for k in klines if k.agent and not k.interlude}) > 1
    i = current_index(klines, pos_ms)
    before = pos_ms < klines[0].start
    anchor = rows // 2
    grid: List[Optional[Text]] = [None] * rows
    cur = _block(klines[i], pos_ms, width=width, accent=accent, current=not before, dist=0, duet=duet)
    for r, t in enumerate(cur):
        if anchor + r < rows:
            grid[anchor + r] = t
    # Upwards, one blank spacer row under the current line's neighbours.
    y = anchor - 2
    j = i - 1
    while y >= 0 and j >= 0:
        blk = _block(klines[j], pos_ms, width=width, accent=accent, current=False, dist=i - j, duet=duet)
        for t in reversed(blk):
            if y < 0:
                break
            grid[y] = t
            y -= 1
        j -= 1
    y = anchor + len(cur) + 1
    j = i + 1
    while y < rows and j < len(klines):
        blk = _block(klines[j], pos_ms, width=width, accent=accent, current=False, dist=j - i, duet=duet)
        for t in blk:
            if y >= rows:
                break
            grid[y] = t
            y += 1
        j += 1
    return [g if g is not None else blank() for g in grid]


def frame_key_fixed(klines: Sequence[KLine], pos_ms: int) -> tuple:
    """What changes on screen at ``pos_ms`` (equal keys = identical frames),
    at the resolution of the gradient edge (quarter characters)."""
    if not klines:
        return ()
    i = current_index(klines, pos_ms)
    k = klines[i]
    if pos_ms < k.start:
        return (i, -1)
    if k.interlude:
        span = max(1, k.end - k.start)
        return (i, int(INTERLUDE_DOTS * 4 * (pos_ms - k.start) / span))
    q = 0
    for w in k.words:
        if pos_ms >= w.end:
            q += 4 * len(w.text)
            continue
        if pos_ms > w.start:
            frac = (pos_ms - w.start) / float(max(1, w.end - w.start))
            q += int(4 * len(w.text.rstrip()) * frac)
        break
    return (i, q, _filled_chars(k.bg, pos_ms) if k.bg else 0)
