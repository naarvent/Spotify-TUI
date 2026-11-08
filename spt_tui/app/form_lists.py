"""Lists that live inside a right-panel form (Settings, Import from Spotify).

Textual's OptionList wraps around: Down on the last option jumps back to the first and Up on the
first jumps to the last, so the keyboard could never leave the list towards the next field. Here the
arrows move on to the neighbouring form field at the edges (and stop at the ends of the form)."""
from __future__ import annotations

from textual.widgets import OptionList, SelectionList


class _EdgeLeaves:
    def _at_edge(self, step: int) -> bool:
        """True when no enabled option lies beyond the highlighted one in direction ``step``."""
        hl = self.highlighted
        if hl is None:
            return False
        rng = range(hl + 1, self.option_count) if step > 0 else range(hl - 1, -1, -1)
        return not any(not self.get_option_at_index(i).disabled for i in rng)

    def _leave(self, step: int) -> bool:
        leave = getattr(self.app, "_focus_adjacent_form_field", None)
        return bool(callable(leave) and self._at_edge(step) and leave(self, step))

    def action_cursor_down(self) -> None:
        if not self._leave(1):
            super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if not self._leave(-1):
            super().action_cursor_up()


class FormOptionList(_EdgeLeaves, OptionList):
    pass


class FormSelectionList(_EdgeLeaves, SelectionList):
    pass
