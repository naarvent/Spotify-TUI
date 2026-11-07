"""Audio visualizer backed by `cava` (real system audio, any provider).

Idea from Rigellute/spotify-tui's "Analysis" view (`v`), which drew 12 pitch
bars from Spotify's Audio Analysis API. That API is closed to new apps since
November 2024, and it was never the real audio anyway: here `cava` listens to
what the system plays (its default is the monitor of the default output), so
it works the same with Spotify, YouTube Music, Subsonic or MPRIS.

* ``CavaProcess`` writes a private config (``method = raw``, ``data_format =
  ascii``, ``ascii_max_range = 1000``, ``;`` between bars, ``\\n`` between
  frames, to stdout), starts cava, and a reader thread keeps only the LATEST
  frame (the UI never queues frames). Only ``[input] method/source`` are
  copied from ``~/.config/cava/config`` when that file sets them (the file is
  read, never written).
* The child dies with us: ``PR_SET_PDEATHSIG`` (Linux) makes the kernel send it
  SIGTERM if spt dies without cleaning up; ``stop()`` and an ``atexit`` hook
  cover the normal paths. ``pause()`` / ``resume()`` SIGSTOP/SIGCONT it, so a
  paused song costs no CPU at all.
* ``render_bars`` is pure: values 0..1000 -> rows of ``▁▂▃▄▅▆▇█`` (eighths of a
  cell), coloured bottom→top with a truecolor gradient of the accent.
"""
from __future__ import annotations

import atexit
import configparser
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import weakref
from typing import List, Optional, Sequence

from rich.text import Text

from .config import logger

MAX_RANGE = 1000
BLOCKS = " ▁▂▃▄▅▆▇█"
DEFAULT_FPS = 30
MIN_FPS, MAX_FPS = 10, 60
USER_CONFIG = os.path.expanduser("~/.config/cava/config")

_LIVE: "weakref.WeakSet[CavaProcess]" = weakref.WeakSet()


def find_cava() -> Optional[str]:
    return shutil.which("cava")


def user_input_settings(path: str = USER_CONFIG) -> dict:
    """``{"method": …, "source": …}`` from the user's cava config, only the keys
    that are really set (commented lines are ignored). Never raises."""
    out = {}
    try:
        cp = configparser.ConfigParser(strict=False, inline_comment_prefixes=(";", "#"),
                                       comment_prefixes=(";", "#"), interpolation=None)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            cp.read_file(fh)
        if cp.has_section("input"):
            for k in ("method", "source"):
                v = cp.get("input", k, fallback=None)
                if v and v.strip():
                    out[k] = v.strip()
    except Exception:
        pass
    return out


def build_config(bars: int, fps: int, input_settings: Optional[dict] = None) -> str:
    bars = max(2, int(bars))
    fps = max(MIN_FPS, min(MAX_FPS, int(fps)))
    lines = ["[general]", f"bars = {bars}", f"framerate = {fps}", ""]
    inp = input_settings or {}
    if inp:
        lines.append("[input]")
        for k in ("method", "source"):
            if inp.get(k):
                lines.append(f"{k} = {inp[k]}")
        lines.append("")
    lines += ["[output]", "method = raw", "raw_target = /dev/stdout", "data_format = ascii",
              f"ascii_max_range = {MAX_RANGE}", "bar_delimiter = 59", "frame_delimiter = 10",
              "channels = mono", ""]
    return "\n".join(lines)


def parse_frame(line: str) -> Optional[List[int]]:
    """'12;400;0;\\n' -> [12, 400, 0] (None for anything else)."""
    parts = [p for p in (line or "").strip().split(";") if p != ""]
    if not parts:
        return None
    try:
        return [max(0, min(MAX_RANGE, int(p))) for p in parts]
    except ValueError:
        return None


def _set_pdeathsig():          # runs in the child, between fork and exec
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)          # PR_SET_PDEATHSIG = 1
    except Exception:
        pass


class CavaProcess:
    def __init__(self, bars: int, fps: int = DEFAULT_FPS, *, cava: Optional[str] = None,
                 input_settings: Optional[dict] = None):
        self.bars = int(bars)
        self.fps = int(fps)
        self.cava = cava or find_cava()
        self.input_settings = user_input_settings() if input_settings is None else input_settings
        self.proc: Optional[subprocess.Popen] = None
        self._cfg_dir: Optional[str] = None
        self._lock = threading.Lock()
        self._frame: Optional[List[int]] = None
        self.frames_read = 0
        self.paused = False
        self.error: Optional[str] = None

    # -- lifecycle -------------------------------------------------------- #
    def start(self) -> bool:
        if not self.cava:
            self.error = "cava is not installed"
            return False
        self._cfg_dir = tempfile.mkdtemp(prefix="spt-cava-")
        cfg = os.path.join(self._cfg_dir, "config")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(build_config(self.bars, self.fps, self.input_settings))
        try:
            self.proc = subprocess.Popen(
                [self.cava, "-p", cfg], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                preexec_fn=_set_pdeathsig if os.name == "posix" else None)
        except Exception as exc:
            self.error = f"cava could not start ({type(exc).__name__})"
            self._cleanup_cfg()
            return False
        _LIVE.add(self)
        threading.Thread(target=self._reader, name="spt-cava", daemon=True).start()
        return True

    def _reader(self) -> None:
        proc = self.proc
        try:
            for line in proc.stdout:
                f = parse_frame(line)
                if f is not None:
                    with self._lock:
                        self._frame = f
                        self.frames_read += 1
        except Exception:
            pass
        if proc.poll() is None:
            return
        if proc.returncode not in (0, -signal.SIGTERM, -signal.SIGKILL) and not self.error:
            self.error = f"cava exited ({proc.returncode})"

    def latest(self) -> Optional[List[int]]:
        with self._lock:
            return list(self._frame) if self._frame is not None else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def pause(self) -> None:
        if self.alive() and not self.paused:
            try:
                self.proc.send_signal(signal.SIGSTOP); self.paused = True
            except Exception:
                pass

    def resume(self) -> None:
        if self.alive() and self.paused:
            try:
                self.proc.send_signal(signal.SIGCONT)
            except Exception:
                pass
        self.paused = False

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                if self.paused:
                    p.send_signal(signal.SIGCONT)      # a stopped process does not handle SIGTERM
                p.terminate()
                p.wait(timeout=1.5)
            except Exception:
                try:
                    p.kill(); p.wait(timeout=1.0)
                except Exception:
                    logger.debug("cava did not die")
        self.paused = False
        self._cleanup_cfg()
        _LIVE.discard(self)

    def _cleanup_cfg(self) -> None:
        if self._cfg_dir:
            shutil.rmtree(self._cfg_dir, ignore_errors=True)
            self._cfg_dir = None


@atexit.register
def _stop_all() -> None:
    for c in list(_LIVE):
        try:
            c.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _rgb(h: str):
    h = (h or "#27e1c1").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (39, 225, 193)


def _mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient(accent: str, rows: int) -> List[str]:
    """Row colours bottom→top: a darker accent at the floor, the accent in the
    middle, almost white at the top."""
    acc = _rgb(accent)
    dark = tuple(int(c * 0.45) for c in acc)
    top = _mix(acc, (255, 255, 255), 0.55)
    top = _rgb(top)
    out = []
    for r in range(max(1, rows)):
        t = r / float(max(1, rows - 1))
        out.append(_mix(dark, acc, t * 2) if t < 0.5 else _mix(acc, top, (t - 0.5) * 2))
    return out


def bar_count(width: int, bar_w: int = 1, gap: int = 1) -> int:
    return max(2, (max(4, int(width)) + gap) // (bar_w + gap))


def levels(values: Sequence[int], rows: int) -> List[int]:
    """Heights in eighths of a cell (0 .. rows*8)."""
    full = max(1, rows) * 8
    return [int(round(min(MAX_RANGE, max(0, v)) * full / MAX_RANGE)) for v in values]


def render_bars(values: Sequence[int], *, rows: int, width: int, accent: str = "#27e1c1",
                mirror: bool = False, gap: int = 1) -> List[Text]:
    """Exactly ``rows`` one-row Texts of ``width`` cells (bars centred)."""
    rows = max(1, int(rows))
    vals = list(values or [])
    if mirror and vals:
        half = vals[: (len(vals) + 1) // 2]
        vals = list(reversed(half)) + half[: len(vals) - len(half)]
    lv = levels(vals, rows)
    cols = gradient(accent, rows)
    used = len(lv) * (1 + gap) - gap if lv else 0
    pad = max(0, (int(width) - used) // 2)
    out: List[Text] = []
    for r in range(rows - 1, -1, -1):           # top row first
        t = Text(" " * pad, no_wrap=True, overflow="crop")
        row_chars = []
        for i, h in enumerate(lv):
            fill = max(0, min(8, h - r * 8))
            row_chars.append(BLOCKS[fill])
            if gap and i < len(lv) - 1:
                row_chars.append(" " * gap)
        t.append("".join(row_chars), style=cols[r])
        out.append(t)
    return out


def frame_key(values: Sequence[int], rows: int) -> tuple:
    return tuple(levels(values or [], rows))
