"""Custom Textual widgets."""

from __future__ import annotations

from textual.widgets import DataTable
from textual.events import MouseEvent

class ResizableDataTable(DataTable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resizing = False
        self._resize_col = None
        self._resize_start_x = 0
        self._orig_width = 0

    def _get_columns_list(self):
        cols = getattr(self, "columns", None)
        if cols:
            return cols
        cols = getattr(self, "_columns", None)
        if cols:
            return cols
        return []

    def _col_width(self, col):
        for attr in ("width", "preferred_width", "render_width", "size"):
            w = getattr(col, attr, None)
            if isinstance(w, int):
                return w
            try:
                if w is not None:
                    return int(w)
            except Exception:
                pass
        return 10

    def on_mouse_down(self, event: MouseEvent) -> None:
        try:
            if getattr(event, "button", 1) != 1:
                return super().on_mouse_down(event)
            if getattr(event, "y", 0) is None:
                return super().on_mouse_down(event)
            if event.y > 1:
                return super().on_mouse_down(event)

            cols = self._get_columns_list()
            if not cols:
                return super().on_mouse_down(event)

            cum = 0
            for idx, col in enumerate(cols):
                w = self._col_width(col)
                cum += w
                if abs(event.x - cum) <= 1:
                    self._resizing = True
                    self._resize_col = idx
                    self._resize_start_x = event.x
                    self._orig_width = w
                    try:
                        event.stop()
                    except Exception:
                        pass
                    return None
        except Exception:
            pass
        try:
            return super().on_mouse_down(event)
        except AttributeError:
            return None

    def on_mouse_move(self, event: MouseEvent) -> None:
        try:
            if not getattr(self, "_resizing", False):
                return super().on_mouse_move(event)
            cols = self._get_columns_list()
            if not cols or self._resize_col is None or self._resize_col >= len(cols):
                return super().on_mouse_move(event)
            delta = int(event.x - self._resize_start_x)
            new_w = max(3, int(self._orig_width + delta))
            col = cols[self._resize_col]
            try:
                if hasattr(col, "width"):
                    col.width = new_w
                elif hasattr(col, "preferred_width"):
                    col.preferred_width = new_w
                else:
                    setattr(col, "width", new_w)
            except Exception:
                try:
                    setattr(col, "width", new_w)
                except Exception:
                    pass
            try:
                self.refresh(layout=True)
            except Exception:
                try: self.refresh()
                except Exception: pass
            try:
                event.stop()
            except Exception:
                pass
            return None
        except Exception:
            pass
        try:
            return super().on_mouse_move(event)
        except AttributeError:
            return None

    def on_mouse_up(self, event: MouseEvent) -> None:
        if getattr(self, "_resizing", False):
            self._resizing = False
            self._resize_col = None
            try:
                event.stop()
            except Exception:
                pass
            return None
        try:
            return super().on_mouse_up(event)
        except AttributeError:
            return None
