"""Keyboard navigation, focus handling and section movement."""

from __future__ import annotations

import threading

from textual.widgets import Input, Static, ListView, ListItem, Label, DataTable
from textual.events import Key
from textual.css.query import NoMatches
from rich.markup import escape as rich_escape

try:
    import requests
except Exception:
    requests = None
try:
    import pyfiglet
except Exception:
    pyfiglet = None

from ..config import logger
from ..constants import WELCOME

class NavigationMixin:
    def _new_view_token(self, kind: str, ident: str = "") -> int:
        # A destructive confirmation belongs to the view where it was armed.
        # Do not carry it into a search, another playlist, or any other view.
        if getattr(self, "_pending_remove_track", None):
            self._pending_remove_track = None
            try:
                self._clear_status_line()
            except Exception:
                pass
        prev = getattr(self, "_right_view", None)
        if prev:
            try:
                self._view_stack.append(prev)
            except Exception:
                logger.exception("_new_view_token: failed pushing prev view on stack")
        self._view_counter += 1
        token = self._view_counter

        self._right_view = (kind, ident, token, None)
        return token

    def _is_current_view(self, kind: str, ident: str, token: int) -> bool:
        try:
            rv = getattr(self, "_right_view", None)
            if not rv: return False
            return tuple(rv[:3]) == (kind, ident, token)
        except Exception:
            return False

    def _safe_update_right(self, kind: str, ident: str, token: int, content: str):
        if self._is_current_view(kind, ident, token):
            self.right_panel.update(content)

    def _focus_section_by_idx(self, idx: int):
        # Leaving/returning to the sections cancels any running search pulse so
        # it never lingers or stacks (a fresh focus restarts it).
        self._stop_search_pulse()

        self.level = self.LVL_SECTIONS
        self.section_idx = idx % len(self.section_order)
        which = self.section_order[self.section_idx]
        self._help_focused = False
        try:
            self.query_one("#help_wrap").remove_class("-active")
        except Exception:
            pass
        for sec_id in ("search", "lib", "pl"):
            sec = getattr(self, f"section_{sec_id}", None)
            if sec:
                if sec_id == which: sec.add_class("-active")
                else: sec.remove_class("-active")

        try:
            self.set_focus(self.query_one("#left_col"))
        except Exception:
            logger.exception("Error setting section focus")

    def _update_section_highlight(self, which: str):
        for sec_id in ("search", "lib", "pl"):
            sec = getattr(self, f"section_{sec_id}", None)
            if not sec: continue
            if sec_id == which: sec.add_class("-active")
            else: sec.remove_class("-active")
        try:
            hw = self.query_one("#help_wrap")
            if which == "help": hw.add_class("-active")
            else: hw.remove_class("-active")
        except Exception:
            pass

    def _focus_right_view(self) -> bool:
        """Focus the content view open on the right (tracks table / list) so its
        rows can be navigated again. Returns False if nothing focusable is open."""
        right = getattr(self, "right_panel", None)
        if right is None:
            return False
        try:
            for wtype in (DataTable, ListView):
                nodes = list(right.query(wtype))
                if nodes:
                    w = nodes[0]
                    self.level = self.LVL_VIEW
                    try:
                        w.focus()
                    except Exception:
                        self.set_focus(w)
                    return True
        except Exception:
            logger.exception("_focus_right_view failed")
        return False

    def _focus_help_box(self):
        """Move focus to the Help box (Enter there opens Help)."""
        try:
            hb = self.query_one("#help_box", Static)
            try:
                hb.can_focus = True
            except Exception:
                pass
            self._help_focused = True
            self.level = self.LVL_SECTIONS
            self._update_section_highlight("help")
            try:
                hb.focus()
            except Exception:
                self.set_focus(hb)
        except Exception:
            logger.exception("_focus_help_box failed")

    # Tab order across the top-level focus stops, matching the visual layout:
    # Search and Help on top, then Library and Playlists, then the open content.
    _TAB_STOPS = ("search", "help", "lib", "pl", "content")

    def _current_tab_stop(self) -> str:
        if getattr(self, "_help_focused", False):
            return "help"
        # Focus somewhere inside the right-hand content panel?
        try:
            foc = getattr(self, "focused", None)
            rp = getattr(self, "right_panel", None)
            node = foc
            while node is not None:
                if node is rp:
                    return "content"
                node = getattr(node, "parent", None)
        except Exception:
            pass
        if self.level in (self.LVL_SECTIONS, self.LVL_SECTION_CONTENT):
            if 0 <= self.section_idx < len(self.section_order):
                return self.section_order[self.section_idx]
        return "search"

    def _focus_tab_stop(self, stop: str) -> bool:
        """Move focus to one stop without entering a section's content. Returns
        False if the stop has nothing to focus (e.g. no content view is open)."""
        try:
            if stop == "help":
                self._focus_help_box(); return True
            if stop == "content":
                return self._focus_right_view()
            if stop in self.section_order:
                self._focus_section_by_idx(self.section_order.index(stop))
                return True
        except Exception:
            logger.exception("_focus_tab_stop(%r) failed", stop)
        return False

    def _cycle_tab(self, step: int) -> None:
        stops = self._TAB_STOPS
        try:
            i = stops.index(self._current_tab_stop())
        except ValueError:
            i = 0
        n = len(stops)
        # Walk to the next focusable stop, skipping any that can't take focus
        # (e.g. "content" when only the welcome screen is up).
        for k in range(1, n + 1):
            if self._focus_tab_stop(stops[(i + step * k) % n]):
                return

    def action_cursor_up(self):
        try:
            focused = getattr(self, 'focused', None)
            rv = getattr(self, '_right_view', None)
            if isinstance(focused, Input) and rv and isinstance(rv, (tuple, list)) and len(rv) >= 1 and rv[0] == 'settings':
                return
            self._focus_section_by_idx((self.section_idx - 1) % len(self.section_order))
        except Exception:
            logger.exception("action_cursor_up failed")

    def action_cursor_down(self):
        try:
            focused = getattr(self, 'focused', None)
            rv = getattr(self, '_right_view', None)
            if isinstance(focused, Input) and rv and isinstance(rv, (tuple, list)) and len(rv) >= 1 and rv[0] == 'settings':
                return
            self._focus_section_by_idx((self.section_idx + 1) % len(self.section_order))
        except Exception:
            logger.exception("action_cursor_down failed")

    def on_key(self, event: Key) -> None:
        try:
            if getattr(self, "_search_capture_next", False):
                k = event.key
                if isinstance(k, str) and len(k) == 1:
                    try:
                        self.search_input.value = (getattr(self, 'search_input', None).value or "") + k
                        self.search_input.focus()
                    except Exception:
                        logger.exception("Failed inserting captured char into search_input")
                    self._search_capture_next = False
                    event.stop(); return
                else:

                    try: self.search_input.focus()
                    except Exception: pass
                    self._search_capture_next = False

            focused = getattr(self, "focused", None)

            # Tab / Shift+Tab cycle focus across the top-level stops (Search,
            # Help, Library, Playlists, main content) without diving into any
            # section's content. A form Input keeps its normal Tab traversal, and
            # the search grid's own panels consume Tab before this (widgets.py),
            # so this never fires while cycling result panels.
            if getattr(event, "key", "") in ("tab", "shift+tab", "backtab"):
                if isinstance(focused, Input) and focused is not getattr(self, "search_input", None):
                    return
                step = -1 if event.key in ("shift+tab", "backtab") else 1
                try: event.stop(); event.prevent_default()
                except Exception: pass
                self._cycle_tab(step)
                return

            # RIGHT arrow behaviour:
            #  - inside an open view (LVL_VIEW): do nothing, never steal focus out.
            #  - on the Search section: always go to the Help box.
            #  - on Library/Playlists: jump (back) into the open right-side view.
            try:
                if getattr(event, "key", "") == "right" and not getattr(event, "ctrl", False) and not getattr(self, "_help_focused", False):
                    if self.level == self.LVL_VIEW:
                        event.stop(); return
                    if self.level in (self.LVL_SECTIONS, self.LVL_SECTION_CONTENT):
                        if self.level == self.LVL_SECTIONS and self.section_order[self.section_idx] == "search":
                            self._focus_help_box(); event.stop(); return
                        if self._focus_right_view():
                            event.stop(); return
            except Exception:
                logger.exception("right-arrow navigation failed")

            # LEFT while on the Help box -> back to the Search section.
            try:
                if getattr(event, "key", "") == "left" and not getattr(event, "ctrl", False) and getattr(self, "_help_focused", False):
                    try:
                        self._focus_section_by_idx(self.section_order.index("search"))
                    except Exception:
                        self._focus_section_by_idx(0)
                    event.stop(); return
            except Exception:
                logger.exception("left-arrow from Help failed")

            try:
                rv = getattr(self, '_right_view', None)
                if isinstance(focused, Input) and rv and isinstance(rv, (tuple, list)) and len(rv) >= 1 and rv[0] == 'settings':
                    key = getattr(event, 'key', None)
                    inputs = []
                    try:
                        inputs = [getattr(self, 'seek_vol_down_input', None), getattr(self, 'seek_vol_up_input', None), getattr(self, 'seek_track_input', None), getattr(self, 'seek_episode_input', None), getattr(self, 'lyrics_cache_input', None)]
                        inputs = [i for i in inputs if i is not None]
                    except Exception:
                        inputs = []
                    try:
                        if key in ('up', 'down'):
                            idx = None
                            for i, w in enumerate(inputs):
                                try:
                                    if w is focused or getattr(w, 'id', None) == getattr(focused, 'id', None):
                                        idx = i; break
                                except Exception:
                                    continue
                            if idx is None:
                                try: event.stop()
                                except Exception: pass
                                return
                            if key == 'down':
                                if idx < len(inputs) - 1:
                                    try: inputs[idx + 1].focus()
                                    except Exception: pass
                                try: event.stop()
                                except Exception: pass
                                return
                            else:
                                if idx > 0:
                                    try: inputs[idx - 1].focus()
                                    except Exception: pass
                                try: event.stop()
                                except Exception: pass
                                return

                        return
                    except Exception:
                        try: event.stop()
                        except Exception: pass
                        return
            except Exception:
                pass

            try:
                if event.key in ("right",) and not getattr(event, 'ctrl', False):
                    try:
                        def _is_within(widget, container_id):
                            try:
                                cur = widget
                                while cur is not None:
                                    if getattr(cur, 'id', None) == container_id:
                                        return True
                                    cur = getattr(cur, 'parent', None)
                            except Exception:
                                pass
                            return False
                        left_col = self.query_one('#left_col')
                        try:
                            rv = getattr(self, '_right_view', None)
                            if left_col and _is_within(focused, 'left_col') and rv and isinstance(rv, (tuple, list)) and len(rv) >= 1 and rv[0] == 'settings':
                                rp = getattr(self, 'right_panel', None)
                                if rp:
                                    # focus first Input child
                                    for ch in rp.children:
                                        try:
                                            if isinstance(ch, Input) or getattr(ch, 'focus', None):
                                                ch.focus(); event.stop(); return
                                        except Exception:
                                            continue
                        except Exception:
                            pass
                        if left_col and _is_within(focused, 'left_col'):
                            if getattr(self, '_last_right_focus', None) is not None:
                                try:
                                    lr = self._last_right_focus

                                    if isinstance(lr, tuple) and lr[0] == 'listview':
                                        try:
                                            lv = self.query_one(f"#{lr[1]}")
                                            try:
                                                if lr[2] is not None:
                                                    lv.index = lr[2]
                                            except Exception:
                                                pass
                                            try:
                                                lv.focus(); event.stop();

                                                try: del self._last_right_focus
                                                except Exception: self._last_right_focus = None
                                                return
                                            except Exception:
                                                pass
                                        except Exception:
                                            pass
                                    elif isinstance(lr, tuple) and lr[0] == 'widget':
                                        try:
                                            w = lr[1]
                                            if getattr(w, 'focus', None):
                                                w.focus(); event.stop();
                                                try: del self._last_right_focus
                                                except Exception: self._last_right_focus = None
                                                return
                                        except Exception:
                                            pass
                                    else:
                                        try:
                                            if getattr(lr, 'focus', None):
                                                lr.focus(); event.stop(); return
                                        except Exception:
                                            try: del self._last_right_focus
                                            except Exception: self._last_right_focus = None
                                except Exception:
                                    try: del self._last_right_focus
                                    except Exception: self._last_right_focus = None
                            try:
                                rv = getattr(self, '_right_view', None)
                                if rv and isinstance(rv, (tuple, list)) and len(rv) >= 1 and rv[0] == 'settings':
                                    rp = getattr(self, 'right_panel', None)
                                    if rp:
                                        for ch in rp.children:
                                            try:
                                                if isinstance(ch, Input) or getattr(ch, 'focus', None):
                                                    ch.focus(); event.stop(); return
                                            except Exception:
                                                continue
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        rp = getattr(self, 'right_panel', None)
                        if rp and (_is_within(focused, 'right') or getattr(focused, 'id', '') in ('right', 'pl_list', 'lib_list')):
                            try:
                                left_col = self.query_one('#left_col')
                                self.level = self.LVL_SECTIONS
                                left_col.focus(); event.stop(); return
                            except Exception:
                                pass
                    except Exception:
                        pass

                    if isinstance(focused, ListView) and getattr(focused, 'id', '') in ('pl_list', 'lib_list'):
                        if getattr(focused, 'index', None) is not None:
                            try:
                                self.action_open(); event.stop(); return
                            except Exception:
                                pass

                    try:
                        rp = getattr(self, 'right_panel', None)
                        if rp:
                            for ch in rp.children:
                                try:
                                    if getattr(ch, 'focus', None):
                                        ch.focus(); event.stop(); return
                                except Exception:
                                    continue
                    except Exception:
                        pass

                if event.key in ("left",) and not getattr(event, 'ctrl', False):
                    try:
                        def _is_within(widget, container_id):
                            try:
                                cur = widget
                                while cur is not None:
                                    if getattr(cur, 'id', None) == container_id:
                                        return True
                                    cur = getattr(cur, 'parent', None)
                            except Exception:
                                pass
                            return False

                        try:
                            left_col = self.query_one('#left_col')
                            try:
                                if _is_within(focused, 'right') or getattr(focused, 'id', '') in ('pl_list', 'lib_list'):
                                    try:
                                        def _find_parent_listview(w):
                                            cur = w
                                            while cur is not None:
                                                if getattr(cur, 'id', '') in ('pl_list', 'lib_list') or cur.__class__.__name__ == 'ListView':
                                                    return cur
                                                cur = getattr(cur, 'parent', None)
                                            return None

                                        lv = _find_parent_listview(focused)
                                        if lv is not None:
                                            if getattr(focused, 'id', '') in ('pl_list', 'lib_list') or focused is lv:
                                                idx = getattr(focused, 'index', None)
                                                self._last_right_focus = ('listview', getattr(lv, 'id', ''), idx)
                                            else:
                                                idx = None
                                                try:
                                                    for i, ch in enumerate(lv.children):
                                                        if ch is focused:
                                                            idx = i; break
                                                except Exception:
                                                    idx = getattr(focused, 'index', None)
                                                self._last_right_focus = ('listview', getattr(lv, 'id', ''), idx)
                                        else:
                                            try:
                                                self._last_right_focus = ('widget', focused)
                                            except Exception:
                                                self._last_right_focus = None
                                    except Exception:
                                        self._last_right_focus = None
                            except Exception:
                                pass
                            self.level = self.LVL_SECTIONS
                            left_col.focus(); event.stop(); return
                        except Exception:
                            pass
                    except Exception:
                        pass
            except Exception:
                logger.exception('left/right navigation failed')

            if isinstance(focused, ListView) and getattr(focused, "id", "") == "multi_review_list":
                if event.key == "enter" and not getattr(event, 'shift', False):
                    try:
                        event.stop()
                    except Exception:
                        pass
                    idx = focused.index
                    if idx is None:
                        return
                    sel = getattr(self, '_multi_review_selected', set())
                    if idx in sel:
                        sel.remove(idx)
                    else:
                        sel.add(idx)
                    self._multi_review_selected = sel
                    try:
                        li: ListItem = focused.children[idx]
                        base = getattr(li, 'data', {}) or {}
                        liked = base.get('liked', False)
                        marker_like = '♥' if liked else ' '
                        marker_sel = '◉' if idx in sel else ' '
                        text = f"[{marker_sel}][{marker_like}] {base.get('title','')} — {base.get('artist','')}"
                        if li.children:
                            li.children[0].update(text)
                        else:
                            li.update(Label(text))
                    except Exception:
                        logger.exception('Failed updating multi-review label')
                    try:
                        next_idx = (idx + 1) % len(focused.children)
                        focused.index = next_idx
                    except Exception:
                        pass
                    return

                if (event.key == "o" and getattr(event, 'ctrl', False)) or event.key == "ctrl+o":
                    try:
                        event.stop()
                    except Exception:
                        pass
                    focused = getattr(self, 'multi_review_list', None)
                    if not focused:
                        try: self._notify('[b]No items to add.[/b]', warn=True)
                        except Exception: pass
                        return
                    sel_all = set()
                    for i in range(len(focused.children)):
                        try:
                            sel_all.add(i)
                        except Exception:
                            continue
                    self._multi_review_selected = sel_all
                    try:
                        for i in range(len(focused.children)):
                            try:
                                li: ListItem = focused.children[i]
                                base = getattr(li, 'data', {}) or {}
                                liked = base.get('liked', False)
                                marker_like = '♥' if liked else ' '
                                marker_sel = '◉' if i in sel_all else ' '
                                text = f"[{marker_sel}][{marker_like}] {base.get('title','')} — {base.get('artist','')}"
                                if li.children:
                                    li.children[0].update(text)
                                else:
                                    li.update(Label(text))
                            except Exception:
                                continue
                    except Exception:
                        logger.exception('Failed updating multi-review labels for select-all')
                    try:
                        self._notify(f"[b]Selected {len(sel_all)} items.[/b]")
                    except Exception:
                        pass
                    return

                if (event.key == "a" and getattr(event, 'ctrl', False)) or event.key == "ctrl+a":
                    try:
                        event.stop()
                    except Exception:
                        pass
                    sel = getattr(self, '_multi_review_selected', set())
                    focused = getattr(self, 'multi_review_list', None)
                    if not focused:
                        try: self._notify('[b]No items to add.[/b]', warn=True)
                        except Exception: pass
                        return
                    uris = []
                    for i in sorted(list(sel)):
                        try:
                            li: ListItem = focused.children[i]
                            data = getattr(li, 'data', {}) or {}
                            u = data.get('uri') or data.get('id')
                            if u: uris.append(u)
                        except Exception:
                            continue
                    if not uris:
                        try: self._notify('[b]No items selected to add.[/b]', warn=True)
                        except Exception: pass
                        return
                    self._pending_multi_add_uris = uris
                    self._show_playlists_for_adding()
                    return

            if getattr(self, '_multi_add_mode', False):
                if event.key == "a" and getattr(event, 'ctrl', False):
                    table = getattr(self, '_multi_add_table', None) or (focused if isinstance(focused, DataTable) else None)
                    if table is None:
                        try: self._notify('[b]No table selected for multi-add.[/b]', warn=True)
                        except Exception: pass
                        return
                    try:
                        candidates = list(getattr(table, 'row_to_uri', {}).keys())
                        if not candidates and hasattr(table, '_model_rows'):
                            candidates = list(range(len(getattr(table, '_model_rows', []))))
                    except Exception:
                        candidates = []
                    if not candidates:
                        try: self._notify('[b]No selectable rows found in table.[/b]', warn=True)
                        except Exception: pass
                        return
                    all_sel = all((r in self._multi_add_selected_rows) for r in candidates)
                    if all_sel:
                        self._multi_add_selected_rows.difference_update(candidates)
                    else:
                        for r in candidates: self._multi_add_selected_rows.add(r)
                    titles = []
                    for r in list(self._multi_add_selected_rows)[:10]:
                        titles.append(getattr(table, 'row_to_title', {}).get(r, ''))
                    try:
                        self._notify(f"[b]Multi-add:[/b] {len(self._multi_add_selected_rows)} selected  [dim]{' | '.join(titles)}[/dim]  Ctrl+Enter to choose playlist, Esc to cancel.", sticky=True)
                    except Exception:
                        pass
                    return

                if event.key == "enter":
                    if getattr(event, 'ctrl', False):
                        table = getattr(self, '_multi_add_table', None)
                        if table is None:
                            try: self._notify('[b]No table selected for multi-add.[/b]', warn=True)
                            except Exception: pass
                            return
                        uris = []
                        for r in list(self._multi_add_selected_rows):
                            u = getattr(table, 'row_to_uri', {}).get(r) or getattr(table, 'row_to_id', {}).get(r)
                            if u: uris.append(u)
                        if not uris:
                            try: self._notify('[b]No tracks selected to add.[/b]', warn=True)
                            except Exception: pass
                            return
                        self._pending_multi_add_uris = uris
                        self._show_playlists_for_adding()
                        return

                try:
                    if isinstance(focused, DataTable):
                        table = focused
                        row = self._get_cursor_row(table)
                        if row is None:
                            try: self._notify('[b]No row selected. Move to a track and press Enter to select.[/b]', warn=True)
                            except Exception: pass
                            return
                        if getattr(self, '_multi_add_table', None) is None:
                            self._multi_add_table = table
                        elif self._multi_add_table is not table:
                            self._multi_add_selected_rows.clear(); self._multi_add_table = table
                        if row in self._multi_add_selected_rows:
                            self._multi_add_selected_rows.remove(row)
                        else:
                            self._multi_add_selected_rows.add(row)
                        titles = []
                        for r in list(self._multi_add_selected_rows)[:10]:
                            titles.append(getattr(table, 'row_to_title', {}).get(r, ''))
                        try:
                            self._notify(f"[b]Multi-add:[/b] {len(self._multi_add_selected_rows)} selected  [dim]{' | '.join(titles)}[/dim]  Ctrl+Enter to choose playlist, Esc to cancel.", sticky=True)
                        except Exception:
                            pass
                        return
                except Exception:
                    logger.exception('multi-add enter handler failed')
                    return

            try:
                ci = getattr(self, 'client_id_input', None)
                cs = getattr(self, 'client_secret_input', None)
                ri = getattr(self, 'redirect_input', None)
                cred_inputs = [w for w in (ci, cs, ri) if w is not None]
                if focused in cred_inputs and event.key in ("up", "down"):
                    try:
                        idx = cred_inputs.index(focused)
                        if event.key == "down":
                            nxt = cred_inputs[(idx + 1) % len(cred_inputs)]
                        else:
                            nxt = cred_inputs[(idx - 1) % len(cred_inputs)]
                        try:
                            nxt.focus()
                        except Exception:
                            pass
                        try: event.stop()
                        except Exception: pass
                        return
                    except Exception:
                        pass
            except Exception:
                pass
            if isinstance(focused, Input):
                return
            if event.key == "enter":
                focused = getattr(self, "focused", None)
                # A big-container add first asks for confirmation on its own
                # screen; Enter there means "yes, choose a playlist".
                pend = getattr(self, "_pending_container_add", None)
                rvc = getattr(self, "_right_view", None)
                if pend and rvc and rvc[0] == "confirm_bulk_add":
                    try: event.stop()
                    except Exception: pass
                    self._pending_container_add = None
                    self._pending_multi_add_uris = pend.get("uris") or None
                    self._pending_add_uri = None
                    self._show_playlists_for_adding()
                    return
                try:
                    if isinstance(focused, ListView) and getattr(focused, "id", None) == "add_pl_list":
                        event.stop()
                        idx = focused.index
                        if idx is None:
                            return
                        li: ListItem = focused.children[idx]
                        pdata = getattr(li, "data", {}) or {}
                        pl_id = pdata.get("id")
                        pl_name = pdata.get("name")
                        right = self._clear_right()
                        right.update(f"[b]Adding to playlist:[/b] {rich_escape(pl_name)} …")
                        def _worker_add():
                            try:
                                ok = False
                                pending_multi = getattr(self, '_pending_multi_add_uris', None)
                                pending_single = getattr(self, '_pending_add_uri', None)
                                if pl_id and pending_multi:
                                    try:
                                        ok = self.spotify.add_items_to_playlist(pl_id, pending_multi)
                                    except Exception as e:
                                        ok = False
                                        try:
                                            right.update(f"[b]Error adding to playlist:[/b] {rich_escape(str(e))}")
                                        except Exception:
                                            pass
                                elif pl_id and pending_single:
                                    try:
                                        ok = self.spotify.add_items_to_playlist(pl_id, [pending_single])
                                    except Exception as e:
                                        ok = False
                                        try:
                                            right.update(f"[b]Error adding to playlist:[/b] {rich_escape(str(e))}")
                                        except Exception:
                                            pass
                                if ok:
                                    # The cached tracks no longer match the playlist.
                                    self._invalidate_playlist_cache(pl_id)
                                    try:
                                        right.update(f"[b]Added to playlist:[/b] {rich_escape(pl_name)}")
                                    except Exception:
                                        pass
                                    try:
                                        threading.Thread(target=lambda: self._load_playlists(force=True), daemon=True).start()
                                    except Exception:
                                        pass
                                else:
                                    try: right.update('[b]Failed to add to playlist.[/b]')
                                    except Exception: pass
                            except Exception:
                                logger.exception('Error in add-to-playlist worker')
                            finally:
                                self._pending_add_uri = None
                                self._pending_multi_add_uris = None
                                try:
                                    self._multi_add_mode = False
                                    self._clear_status_line()
                                    self._multi_add_table = None
                                    self._multi_add_selected_rows.clear()
                                except Exception:
                                    pass
                        threading.Thread(target=_worker_add, daemon=True).start()
                        return
                except Exception:
                    logger.exception('add-to-playlist enter handler failed')
                self.action_open(); event.stop(); return

            if event.key == "escape":
                self.action_escape_to_menu(); event.stop(); return

            if event.key in ("up", "down", "j", "k") and self.level == self.LVL_SECTIONS:
                event.stop()
        except Exception:
            logger.exception("on_key error")

    def action_toggle_sidebar(self) -> None:
        """Hide/show the left column, giving its 38 fixed columns to the content.

        The CSS for the collapsed state already existed and `_welcome_dims` read
        the class, but nothing ever set it, so the sidebar could not be hidden.
        """
        try:
            grid = self.query_one("#grid")
            left = self.query_one("#left_col")
        except NoMatches:
            return
        collapsed = not grid.has_class("left-collapsed")
        grid.set_class(collapsed, "left-collapsed")
        left.set_class(collapsed, "left-collapsed")
        if collapsed:
            # Focus cannot stay on a hidden widget.
            try:
                self._focus_right_view()
            except Exception:
                logger.debug("focusing the content panel after collapsing failed")
        self._notify("[b]Sidebar hidden[/b]  press again to show it" if collapsed
                     else "[b]Sidebar shown[/b]")
        if getattr(self, "_welcome_on", False):
            # The welcome art is sized to the panel, so re-render it at the new
            # width; tables re-fit themselves from ContentPanel.on_resize.
            try:
                self.call_after_refresh(self._paint_welcome)
            except Exception:
                logger.debug("repainting the welcome after collapsing failed")

    def action_escape_to_menu(self) -> None:
        # Escape backs out of an armed removal instead of leaving the view.
        if getattr(self, "_pending_remove_track", None):
            self._cancel_remove_track()
            return
        try:
            self.right_panel.remove_class("lyrics-mode")
        except Exception: pass
        self._clear_right()
        self._paint_welcome()   # size-appropriate welcome (responsive)
        # Back at the menu there is no active right-hand view: invalidate the
        # view token so any in-flight loader (library, playlist, search) sees it
        # is no longer current and does not paint its table over the menu.
        self._right_view = None
        try:
            if self.spotify.has_cached_token():
                threading.Thread(target=self._load_playlists, args=(True,), daemon=True).start()
        except Exception:
            logger.exception("Could not start playlist loader when returning to menu")

        for attr in ("_devices_interval",):
            it = getattr(self, attr, None)
            if it:
                try: it.pause()
                except Exception: pass
                setattr(self, attr, None)
        try:
            if getattr(self, '_queue_interval', None):
                try: self._queue_interval.pause()
                except Exception: pass
                self._queue_interval = None
        except Exception:
            pass

        # Full, idempotent lyrics teardown (also stops the 0.4s tick, which the
        # old ad-hoc cleanup here missed — it paused a never-set _lyrics_timer).
        self._leave_lyrics_mode()
        self._stop_search_pulse()

        try:
            if hasattr(self, "lib_list") and self.lib_list is not None:
                self.lib_list.index = None; self.lib_list.blur()
            if hasattr(self, "pl_list") and self.pl_list is not None:
                self.pl_list.index = None; self.pl_list.blur()
        except Exception: pass
        try:
            if getattr(self, '_multi_add_mode', False):
                self._multi_add_mode = False
                self._clear_status_line()
                self._multi_add_table = None
                self._multi_add_selected_rows.clear()
                self._pending_multi_add_uris = None
        except Exception:
            pass
        # A pending big-container add confirmation is abandoned by leaving.
        try:
            self._pending_container_add = None
        except Exception:
            pass
        try:
            if getattr(self, 'confirm_delete_input', None):
                try: del self.confirm_delete_input
                except Exception: pass
            try: del self._pending_delete_playlist
            except Exception: pass
            try:
                if getattr(self, 'confirm_delete_confirm_input', None):
                    try: del self.confirm_delete_confirm_input
                    except Exception: pass
            except Exception:
                pass
        except Exception:
            pass
        try:
            self.level = self.LVL_SECTIONS
            self.section_idx = 0
            self._update_section_highlight(self.section_order[self.section_idx])
            left_col = self.query_one("#left_col"); self.set_focus(left_col)
        except Exception:
            try: self.set_focus(getattr(self, 'search_input', None))
            except Exception: pass
        try:
            if getattr(self, '_help_on', False):
                self._help_on = False
        except Exception:
            pass

    def action_focus_search(self) -> None:
        try:

            try:

                idx = self.section_order.index("search") if hasattr(self, 'section_order') else 0
            except Exception:
                idx = 0
            try:
                self._focus_section_by_idx(idx)

                self._enter_section()
            except Exception:
                try: self.search_input.focus()
                except Exception: pass

            self._search_capture_next = True
            self._start_search_pulse()
            return
        except Exception:
            logger.exception("action_focus_search failed")

    # ------------------------------------------------------------------ #
    # Search focus feedback: a short outline pulse + text hint. Uses Textual
    # timers only (no threads); restarts cleanly and never stacks timers.
    # ------------------------------------------------------------------ #
    _SEARCH_PULSE_STEPS = 6        # 6 * 0.22s ~= 1.3s, within the 0.8–1.5s window

    def _apply_search_pulse(self, on: bool):
        try:
            w = self.query_one("#search_wrap")
            if on:
                w.add_class("-search-pulse")
            else:
                w.remove_class("-search-pulse")
        except Exception:
            pass

    def _start_search_pulse(self):
        # Restart from scratch so repeated activations don't accumulate timers.
        self._stop_search_pulse(restore_title=False)
        self._search_pulse_count = 0
        self._apply_search_pulse(True)
        try:
            self.search_title.update("Search •")     # "Search •" — colour-independent hint
        except Exception:
            pass
        try:
            self._search_pulse_timer = self.set_interval(0.22, self._search_pulse_tick, pause=False)
        except Exception:
            logger.exception("could not start search pulse")
            self._search_pulse_timer = None

    def _search_pulse_tick(self):
        self._search_pulse_count = getattr(self, "_search_pulse_count", 0) + 1
        self._apply_search_pulse(self._search_pulse_count % 2 == 0)
        if self._search_pulse_count >= self._SEARCH_PULSE_STEPS:
            self._stop_search_pulse()

    def _stop_search_pulse(self, restore_title: bool = True):
        t = getattr(self, "_search_pulse_timer", None)
        if t is not None:
            try:
                t.pause()
            except Exception:
                pass
            self._search_pulse_timer = None
        self._apply_search_pulse(False)
        if restore_title:
            try:
                txt = (self.search_input.value or "") if getattr(self, "search_input", None) else ""
                self.search_title.update(f"Search: {rich_escape(txt)}" if txt else "Search")
            except Exception:
                pass

    def action_toggle_multi_add(self) -> None:
        try:
            if getattr(self, '_multi_add_mode', False):
                self._multi_add_mode = False
                self._clear_status_line()
                self._multi_add_table = None
                self._multi_add_selected_rows.clear()
                self._pending_multi_add_uris = None
                try: self.right_panel.update(WELCOME)
                except Exception: pass
                return

            focused = getattr(self, 'focused', None)
            if not isinstance(focused, DataTable):
                try: self._notify('[b]Focus a track table (search/playlist/liked) and press Ctrl+L to open multi-review.[/b]')
                except Exception: pass
                return

            table = focused
            rows = []
            try:
                model_rows = getattr(table, '_model_rows', None) or []
                for i, r in enumerate(model_rows):
                    uri = r.get('uri') or r.get('id') or getattr(table, 'row_to_uri', {}).get(i)
                    title = r.get('title') or getattr(table, 'row_to_title', {}).get(i) or ''
                    artist = r.get('artist') or ''
                    rows.append({'id': r.get('id'), 'uri': uri, 'title': title, 'artist': artist})
            except Exception:
                for i in getattr(table, 'row_to_title', {}).keys():
                    uri = getattr(table, 'row_to_uri', {}).get(i)
                    title = getattr(table, 'row_to_title', {}).get(i) or ''
                    artist = getattr(table, 'row_to_obj', {}).get(i, {})
                    artist = (artist.get('artists') and ', '.join(a.get('name') for a in artist.get('artists', []))) if isinstance(artist, dict) else ''
                    rows.append({'id': getattr(table, 'row_to_id', {}).get(i), 'uri': uri, 'title': title, 'artist': artist})

            right = self._clear_right()
            right.mount(Static('[b]Multi-Review:[/b] Toggle selection with Enter (does not play). Press Ctrl+A to confirm or Ctrl+O to select all.', markup=True))

            items = []
            for r in rows:
                sel_marker = ' '
                like_marker = ' '
                txt = f"[{sel_marker}][{like_marker}] {r.get('title','')} — {r.get('artist','')}"
                li = ListItem(Label(txt))
                li.data = r
                items.append(li)
            self.multi_review_list = ListView(*items, id='multi_review_list')
            right.mount(self.multi_review_list)
            self._multi_review_selected = set()

            try:
                self.multi_review_list.focus()
            except Exception:
                pass

            def _worker_likes(rows_local):
                try:
                    ids = [r.get('id') for r in rows_local if r.get('id')]
                    liked = []
                    if ids:
                        try:
                            liked = self.spotify.check_saved_tracks(ids)
                        except Exception:
                            liked = [False] * len(ids)

                    def _paint_likes():
                        try:
                            for idx, r in enumerate(rows_local):
                                if not r.get('id'): continue
                                li = self.multi_review_list.children[idx]
                                is_liked = (idx < len(liked) and liked[idx])
                                try:
                                    rows_local[idx]['liked'] = is_liked
                                except Exception:
                                    pass
                                marker_like = '♥' if is_liked else ' '
                                sel_set = getattr(self, '_multi_review_selected', set()) or set()
                                marker_sel = '◉' if idx in sel_set else ' '
                                label_text = f"[{marker_sel}][{marker_like}] {r.get('title','')} — {r.get('artist','')}"
                                try:
                                    if li.children:
                                        li.children[0].update(label_text)
                                    else:
                                        li.update(Label(label_text))
                                except Exception:
                                    pass
                        except Exception:
                            logger.exception('Error painting likes')
                    try:
                        self.call_from_thread(_paint_likes)
                    except Exception:
                        if getattr(self, '_closing', False):
                            logger.debug("multi-review likes paint dropped during teardown")
                        else:
                            logger.exception("multi-review likes paint: call_from_thread failed")
                except Exception:
                    logger.exception('multi-review likes worker failed')
            threading.Thread(target=_worker_likes, args=(rows,), daemon=True).start()

            self._multi_add_mode = True
            self._multi_add_table = table
            self._multi_add_selected_rows = set()
            self.level = self.LVL_VIEW
            return
        except Exception:
            logger.exception('action_toggle_multi_add failed')

    def action_delete(self) -> None:
        try:
            # A removal is armed: this second press is the confirmation.
            if getattr(self, "_pending_remove_track", None):
                self._apply_pending_remove_track()
                return
            focused = getattr(self, 'focused', None)
            if isinstance(focused, ListView) and getattr(focused, 'id', '') == 'pl_list' and focused.index is not None:
                li: ListItem = focused.children[focused.index]
                pdata = getattr(li, 'data', {}) or {}
                pl_name = pdata.get('name') or ''
                pl_id = pdata.get('id')
                if not pl_id:
                    try: self._notify('[b]Could not determine playlist id to delete.[/b]', warn=True)
                    except Exception: pass
                    return
                self._new_view_token('confirm_delete', pl_id)
                right = self._clear_right()
                right.update(f"[b]Delete playlist:[/b] {rich_escape(pl_name)}\nType the playlist name exactly to confirm deletion and press Enter.")
                self.confirm_delete_input = Input(placeholder=f"Type: {pl_name}")
                right.mount(self.confirm_delete_input)
                self.confirm_delete_input.focus()
                self._pending_delete_playlist = {'id': pl_id, 'name': pl_name}
                self.level = self.LVL_VIEW
                return

            focused = getattr(self, 'focused', None)
            if isinstance(focused, DataTable) and getattr(focused, 'id', '') == 'tracks_table':
                row = self._get_cursor_row(focused)
                if row is None:
                    try: self._notify('[b]No track selected to delete.[/b]', warn=True)
                    except Exception: pass
                    return
                rv = getattr(self, '_right_view', None)
                pl_id = None; pdata = None
                if rv and len(rv) >= 3 and rv[0] == 'playlist':
                    pl_id = rv[1]; pdata = rv[3]
                if not pl_id:
                    try: self._notify('[b]Not viewing a playlist; cannot delete track here.[/b]')
                    except Exception: pass
                    return
                uri = getattr(focused, 'row_to_uri', {}).get(row) or getattr(focused, 'row_to_id', {}).get(row)
                if not uri:
                    try: self._notify('[b]Could not determine track URI to remove.[/b]', warn=True)
                    except Exception: pass
                    return

                title = getattr(focused, 'row_to_title', {}).get(row, '') or 'this track'
                self._confirm_remove_track(pl_id, uri, title, pdata)
                return

            try:
                self._notify('[b]Delete action not applicable in current context.[/b]')
            except Exception:
                pass
        except Exception:
            logger.exception('action_delete failed')

    def _confirm_remove_track(self, pl_id: str, track_uri: str, title: str, pdata=None) -> None:
        """Arm a removal and ask first.

        Deleting a whole playlist makes you type its name, but removing a track
        used to fire on the keypress — the small destructive action had less of a
        safety net than the big one.
        """
        rv = getattr(self, "_right_view", None)
        self._pending_remove_track = {"playlist": pl_id, "uri": track_uri,
                                      "title": title, "pdata": pdata,
                                      "view": tuple(rv[:3]) if rv else None}
        self._notify(f"[b]Remove[/b] {rich_escape(str(title))} [b]from the playlist?[/b]  "
                     "Ctrl+D again to confirm, Esc to cancel.", warn=True, sticky=True)

    def _cancel_remove_track(self) -> None:
        if getattr(self, "_pending_remove_track", None) is None:
            return
        self._pending_remove_track = None
        self._notify("[b]Removal cancelled.[/b]")

    def _apply_pending_remove_track(self) -> None:
        pending = getattr(self, "_pending_remove_track", None)
        if not pending:
            return
        origin = pending.get("view")
        current = getattr(self, "_right_view", None)
        if origin is not None and (not current or tuple(current[:3]) != origin):
            self._pending_remove_track = None
            self._clear_status_line()
            return
        self._pending_remove_track = None
        self._clear_status_line()
        self._remove_track_from_playlist(pending["playlist"], pending["uri"],
                                         pending.get("pdata"))

    def _remove_track_from_playlist(self, pl_id: str, track_uri: str, pdata=None) -> None:
        """Remove one track from a playlist, off-thread, then reopen the view.

        spotipy renamed this call across versions, so the known names are tried
        in turn, each with the plain URI and then a normalised one.
        """
        def worker():
            try:
                sp = self.spotify.ensure()
                tid = self.spotify._normalize_track_id(track_uri) or track_uri
                candidates = [track_uri, f'spotify:track:{tid}', f'spotify:episode:{tid}']
                removed = False
                methods = [
                    'playlist_remove_all_occurrences_of_items',
                    'playlist_remove_specific_occurrences_of_items',
                    'playlist_remove_items',
                    'playlist_remove_tracks',
                ]
                for mname in methods:
                    try:
                        if hasattr(sp, mname):
                            func = getattr(sp, mname)
                            func(pl_id, [candidates[0]])
                            removed = True; break
                    except Exception:
                        try:
                            func(pl_id, [candidates[1]])
                            removed = True; break
                        except Exception:
                            pass
                if removed:
                    # Before the reopen below, or it would paint the cached rows
                    # and the deleted track would flash back on screen.
                    self._invalidate_playlist_cache(pl_id)
                    try:
                        if pdata:
                            self.call_from_thread(lambda: threading.Thread(target=lambda: self._open_playlist_table(pdata), daemon=True).start())
                        else:
                            try: threading.Thread(target=lambda: self._load_playlists(force=True), daemon=True).start()
                            except Exception: pass
                        self.call_from_thread(lambda: self._notify('[b]Track removed from playlist.[/b]'))
                    except Exception:
                        pass
                else:
                    try: self.call_from_thread(lambda: self._notify('[b]Could not remove track from playlist.[/b]', warn=True))
                    except Exception: pass
            except Exception:
                logger.exception('Error removing track from playlist')

        threading.Thread(target=worker, daemon=True).start()

    def _get_cursor_row(self, table: DataTable):
        row = getattr(table, "cursor_row", None)
        if row is not None: return row
        coord = getattr(table, "cursor_coordinate", None)
        if coord is not None and hasattr(coord, "row"): return coord.row
        return None

    def action_open(self):
        if getattr(self, "_help_focused", False):
            self._help_focused = False
            try:
                self.query_one("#help_wrap").remove_class("-active")
            except Exception:
                pass
            self.action_help(); return
        if self.level == self.LVL_SECTIONS:
            self._enter_section(); return
        focused = self.focused
        which = self.section_order[self.section_idx]
        if self.level == self.LVL_SECTION_CONTENT:
            if which == "search":
                self.search_input.focus(); return
            if which == "lib" and isinstance(focused, ListView) and focused.id == "lib_list" and focused.index is not None:
                li: ListItem = focused.children[focused.index]
                name = (getattr(li, "data", {}) or {}).get("name", "")
                self._open_library_item(name)
                return
            if which == "pl" and isinstance(focused, ListView) and focused.id == "pl_list" and focused.index is not None:
                li: ListItem = focused.children[focused.index]
                pdata = getattr(li, "data", {})
                if pdata.get("type") == "playlist":
                    self._open_playlist_table(pdata); return
        if isinstance(focused, DataTable):
            fid = getattr(focused, "id", "")
            if fid == "tracks_table" and hasattr(focused, "row_to_uri"):
                row = self._get_cursor_row(focused)
                if row is not None: self._play_row(row, focused)
            elif fid in ("search_table", "songs_table", "artists_table",
                         "albums_table", "playlists_table") and hasattr(focused, "row_to_type"):
                row = self._get_cursor_row(focused)
                if row is None: return
                rtype = focused.row_to_type.get(row)
                if rtype == "track":

                    self._play_row(row, focused); return
                if rtype == "playlist":
                    obj = focused.row_to_obj.get(row)
                    if obj: self._open_playlist_table(obj); return
                if rtype == "album":
                    obj = focused.row_to_obj.get(row)
                    if obj: self._open_album_table(obj); return
                if rtype == "single":

                    obj = focused.row_to_obj.get(row)
                    if obj:
                        self._open_album_table(obj); return

                    uri = getattr(focused, 'row_to_uri', {}).get(row)
                    if uri and ('track' in (uri or '')):
                        self._play_row(row, focused); return
                if rtype == "artist":
                    obj = focused.row_to_obj.get(row)
                    if obj: self._open_artist_table(obj); return
                if rtype == "episode":
                    self._play_row(row, focused); return
                if rtype == "podcast":
                    obj = focused.row_to_obj.get(row)
                    if obj: self._open_podcast_table(obj); return
            elif fid == "devices_table" and hasattr(focused, "row_to_device"):
                row = self._get_cursor_row(focused)
                if row is not None:
                    dev_id = focused.row_to_device.get(row)
                    if dev_id:
                        threading.Thread(target=lambda: self.spotify.transfer(dev_id, force_play=True), daemon=True).start()
                        self._back_one_level()

    def _enter_section(self):
        which = self.section_order[self.section_idx]
        self.level = self.LVL_SECTION_CONTENT
        if which == "search":
            self.search_input.focus()
        elif which == "lib":
            if self.lib_list.index is None and self.lib_list.children:
                self._suppress_first_selection = True; self.lib_list.index = 0
            self.lib_list.focus()
        elif which == "pl":
            if self.pl_list.index is None and self.pl_list.children:
                self._suppress_first_selection = True; self.pl_list.index = 0
            self.pl_list.focus()

    def _back_one_level(self):
        # Leaving any view via "back" must stop the devices poller if it was
        # running (e.g. the post-transfer exit path). Idempotent no-op otherwise.
        self._stop_devices_interval()

        try:
            if getattr(self, "_view_stack", None):
                prev = None
                try:
                    prev = self._view_stack.pop()
                except Exception:
                    prev = None
                if prev:
                    try:
                        self._restore_view(prev)
                        return
                    except Exception:
                        logger.exception("_back_one_level: failed restoring previous view")
        except Exception:
            logger.exception("_back_one_level: error checking view_stack")

        if self.level == self.LVL_VIEW:
            self.right_panel.update(WELCOME)
            self.level = self.LVL_SECTION_CONTENT
            which = self.section_order[self.section_idx]
            if which == "search": self.search_input.focus()
            elif which == "lib": self.lib_list.focus()
            else: self.pl_list.focus()
            return
        if self.level == self.LVL_SECTION_CONTENT:
            self._focus_section_by_idx(self.section_idx)
            try: self.query_one("#left_col").focus()
            except Exception: self.focus()
            return
        self._focus_section_by_idx(self.section_idx)
