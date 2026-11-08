"""The "Visualizer" view (`v`): cava bars over the whole content panel.

`v` opens it in the same frame as a playlist / the Queue (title on #right's
frame, the lyrics view's song header at a fixed position, the bars below
filling the rest); `v` again or Esc leave it.
`M` (Shift+M; `m` stays Mute) switches plain bars ⇄ mirrored. Any other view
taking the panel (``_clear_right``) stops cava, so it never runs unseen.

Repaints are driven by a timer at ``visualizer_fps`` (spt_config.json, 30 by
default, 10–60; Settings > Visualizer) that reads cava's latest frame and skips identical frames.
While nothing plays, cava is SIGSTOPped and the timer slows down.
"""
from __future__ import annotations

from typing import Optional

from textual.widgets import Static
from rich.console import Group
from rich.markup import escape as rich_escape
from rich.text import Text

from .. import config
from .. import visualizer as viz
from ..config import logger


class VisualizerMixin:
    VIS_IDLE_TICK = 0.5

    def _vis_fps(self) -> int:
        cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
        try:
            n = int(cfg.get("visualizer_fps") or viz.DEFAULT_FPS)
        except (TypeError, ValueError):
            n = viz.DEFAULT_FPS
        return max(viz.MIN_FPS, min(viz.MAX_FPS, n))

    def _vis_default_mirror(self) -> bool:
        """Settings > default mode: mirrored unless the config says ``bars``."""
        cfg = config.LOCAL_CFG if isinstance(config.LOCAL_CFG, dict) else {}
        return str(cfg.get("visualizer_mode") or "mirrored").strip().lower() != "bars"

    def _vis_is_mirror(self) -> bool:
        """Mirror now: Shift+M's choice for this session, else the configured default."""
        v = getattr(self, "_vis_mirror", None)
        return self._vis_default_mirror() if v is None else bool(v)

    def _apply_visualizer_mode(self, mirrored: bool) -> None:
        """Settings changed the default: it replaces any Shift+M choice and repaints a running view."""
        self._vis_mirror = bool(mirrored)
        self._vis_last_key = None

    def _apply_visualizer_fps(self) -> None:
        """Settings changed ``visualizer_fps``: a running visualizer picks it up at once (cava is
        restarted with the new framerate and the repaint timer re-armed); otherwise the next
        ``v`` reads it from the config."""
        if not getattr(self, "_vis_on", False) or getattr(self, "_vis_proc", None) is None:
            return
        self._stop_visualizer_process()
        self._vis_start_process()

    def action_toggle_visualizer(self) -> None:
        """`v`: open the visualizer, or close it (back to the menu)."""
        if getattr(self, "_vis_on", False):
            self._go_to_menu()
            return
        if not viz.find_cava():
            self._notify("[b]The visualizer needs cava[/b] (not found on PATH). Install it, e.g. "
                         "[b]sudo pacman -S cava[/b], and press v again.", warn=True, seconds=7.0)
            return
        self._open_visualizer()

    def action_visualizer_mirror(self) -> None:
        """`M` in the visualizer: plain bars ⇄ mirrored (bass in the middle)."""
        if not getattr(self, "_vis_on", False):
            return
        self._vis_mirror = not self._vis_is_mirror()
        self._vis_last_key = None
        self._notify("[b]Visualizer:[/b] " + ("mirrored" if self._vis_mirror else "bars"))

    def _vis_size(self):
        box = getattr(self, "_vis_box", None)
        w = h = 0
        try:
            cs = box.content_size
            w, h = int(cs.width or 0), int(cs.height or 0)
        except Exception:
            pass
        if not w:
            w = max(20, int(getattr(self.size, "width", 100)) - 44)
        if not h:
            h = max(6, int(getattr(self.size, "height", 30)) - 12)
        return w, h

    def _open_visualizer(self) -> None:
        self._leave_lyrics_mode()
        self._new_view_token("visualizer", "")
        right = self._clear_right()           # also stops a previous cava
        right.add_class("table-view")
        right.border_title = "Visualizer · cava"
        self._vis_box = Static("", classes="visualizer_box")
        right.mount(self._vis_box)
        self._vis_on = True
        self._vis_last_key = None
        self._vis_proc = None
        self._vis_paints = 0
        self.level = self.LVL_VIEW
        # Start cava once the box has its real width (bars = width / 2).
        self.call_after_refresh(self._vis_start_process)

    def _vis_start_process(self) -> None:
        if not getattr(self, "_vis_on", False):
            return
        w, _ = self._vis_size()
        bars = viz.bar_count(w)
        proc = viz.CavaProcess(bars, self._vis_fps())
        if not proc.start():
            self._vis_on = False
            self._notify(f"[b]Visualizer:[/b] {rich_escape(proc.error or 'cava failed')}", warn=True)
            try:
                self._vis_box.update(f"[b]Visualizer unavailable:[/b] {rich_escape(proc.error or '')}")
            except Exception:
                pass
            return
        self._vis_proc = proc
        self._vis_width = w
        self._vis_set_timer(1.0 / self._vis_fps())

    def _vis_set_timer(self, period: float) -> None:
        t = getattr(self, "_vis_timer", None)
        if t is not None:
            try: t.stop()
            except Exception: pass
        self._vis_period = period
        self._vis_timer = self.set_interval(period, self._vis_tick, pause=False)

    def _vis_tick(self) -> None:
        if not getattr(self, "_vis_on", False):
            return
        proc = getattr(self, "_vis_proc", None)
        if proc is None:
            return
        playing = bool(getattr(self, "_now_internal_is_playing", False))
        # Nothing plays: freeze cava (no CPU) and tick slowly; resume on play.
        if not playing:
            proc.pause()
            if self._vis_period != self.VIS_IDLE_TICK:
                self._vis_set_timer(self.VIS_IDLE_TICK)
        else:
            proc.resume()
            if self._vis_period != 1.0 / self._vis_fps():
                self._vis_set_timer(1.0 / self._vis_fps())
        if not proc.alive() and proc.error:
            self._vis_on = False
            self._notify(f"[b]Visualizer stopped:[/b] {rich_escape(proc.error)}", warn=True)
            return
        w, h = self._vis_size()
        if abs(w - getattr(self, "_vis_width", w)) >= 4:
            # The panel was resized: restart cava with a bar count that fits.
            self._stop_visualizer_process()
            self._vis_start_process()
            return
        frame = proc.latest() or [0] * proc.bars
        top = self._vis_header_rows(w, h)
        rows = max(1, h - len(top))
        key = (viz.frame_key(frame, rows), w, h, self._vis_is_mirror(),
               tuple(t.plain for t in top))
        if key == getattr(self, "_vis_last_key", None):
            return
        self._vis_last_key = key
        bars = viz.render_bars(frame, rows=rows, width=w, accent=self._accent_hex(),
                               mirror=self._vis_is_mirror())
        # Exactly the box height, one row per Text (as the karaoke view): the
        # song header never moves, the bars take what is left.
        self._vis_rows = top + bars
        self._vis_header_count = len(top)
        self._vis_box.update(Group(*self._vis_rows), layout=False)
        self._vis_paints += 1

    VIS_MIN_BAR_ROWS = 4

    def _vis_header_rows(self, w: int, h: int):
        """The lyrics view's song header (same figlet title/artist, colours and
        narrow-terminal fallback), memoised per song and size, + a spacer."""
        title = self._vis_title(header=True)
        cache_key = (title, w, h)
        cached = getattr(self, "_vis_head_cache", None)
        if cached and cached[0] == cache_key:
            return cached[1]
        head = self._header_rows(big=True, title=title, width=w)
        if len(head) + 1 + self.VIS_MIN_BAR_ROWS > h:
            head = self._header_rows(big=False, title=title, width=w)
        rows = head + [Text("")]
        if len(rows) + self.VIS_MIN_BAR_ROWS > h:
            rows = head[:1]
        self._vis_head_cache = (cache_key, rows)
        return rows

    def _vis_title(self, header: bool = False) -> str:
        """"Song — Artist" of what plays (``header``: without the paused tag,
        so the big title does not re-render on play/pause)."""
        item = getattr(self, "_bar_last_track", None) or {}
        name = item.get("name") or ""
        artists = ", ".join(a.get("name", "") for a in (item.get("artists") or []) if isinstance(a, dict))
        if not name:
            return "Nothing playing"
        state = "" if header or getattr(self, "_now_internal_is_playing", False) else "  (paused)"
        return f"{name} — {artists}{state}" if artists else f"{name}{state}"

    def _stop_visualizer_process(self) -> None:
        t = getattr(self, "_vis_timer", None)
        if t is not None:
            try: t.stop()
            except Exception: pass
            self._vis_timer = None
        p = getattr(self, "_vis_proc", None)
        self._vis_proc = None
        if p is not None:
            try:
                p.stop()
            except Exception:
                logger.exception("stopping cava failed")

    def _stop_visualizer(self) -> None:
        """Idempotent: leave the visualizer (cava killed, timer stopped)."""
        if not getattr(self, "_vis_on", False) and getattr(self, "_vis_proc", None) is None:
            return
        self._vis_on = False
        self._stop_visualizer_process()
        self._vis_box = None
