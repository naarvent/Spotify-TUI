"""Radio / discovery (`R`): a list of tracks like the highlighted track,
artist or playlist, from the active provider's ``radio()``.

The seed comes from what has focus: a track row (any track table, search
results, the queue, the artist page), an artist row, a playlist row or the
highlighted sidebar playlist; with nothing usable focused, the track that is
playing. The result opens as a normal tracks view ("Radio: …"): Enter plays
from that row and the rest of the radio follows. It does not start playing on
its own. Providers without a radio (``can_radio`` False) get the usual
status-line notice; a provider whose radio endpoint is gone (Spotify's
recommendations for development-mode apps) explains it there too.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from textual.widgets import DataTable, ListView
from rich.markup import escape as rich_escape

from ..config import logger
from ..providers.base import ProviderError, ProviderUnsupported, RADIO_SEEDS


class RadioMixin:
    RADIO_LIMIT = 50
    RADIO_HELP = "Radio starts from a track, an artist or a playlist."

    # ------------------------------------------------------------------ #
    # Seed
    # ------------------------------------------------------------------ #
    @staticmethod
    def _seed_name(obj: dict, fallback: str = "") -> str:
        return (obj or {}).get("name") or (obj or {}).get("title") or fallback

    def _radio_seed_from_row(self, table: DataTable) -> Optional[Dict]:
        row = self._get_cursor_row(table)
        if row is None:
            return None
        model = getattr(table, "_model_rows", None) or []
        mr = model[row] if isinstance(row, int) and 0 <= row < len(model) else {}
        mr = mr if isinstance(mr, dict) else {}
        rtype = (getattr(table, "row_to_type", {}) or {}).get(row) or mr.get("type") or "track"
        raw = (getattr(table, "row_to_obj", {}) or {}).get(row) or mr.get("raw") or {}
        raw = raw if isinstance(raw, dict) else {}
        rid = (getattr(table, "row_to_id", {}) or {}).get(row) or mr.get("id") or raw.get("id")
        if rtype == "single":
            rtype = "album"
        if not rid:
            return {"kind": rtype, "id": None}
        name = mr.get("title") or self._seed_name(raw, str(rid))
        return {"kind": rtype, "id": rid, "name": name, "raw": raw or mr}

    def _radio_seed(self) -> Optional[Dict]:
        """What `R` should start a radio from, or None (nothing usable)."""
        focused = getattr(self, "focused", None)
        if isinstance(focused, ListView) and getattr(focused, "id", "") == "pl_list" \
                and focused.index is not None:
            try:
                pdata = getattr(focused.children[focused.index], "data", {}) or {}
            except Exception:
                pdata = {}
            if pdata.get("type") == "playlist" and pdata.get("id"):
                return {"kind": "playlist", "id": pdata["id"], "name": self._seed_name(pdata, "playlist"),
                        "raw": pdata}
        if isinstance(focused, DataTable) and getattr(focused, "id", "") != "devices_table":
            seed = self._radio_seed_from_row(focused)
            if seed is not None:
                return seed
        cached = getattr(self, "_bar_last_track", None) or {}
        if cached.get("id") and (cached.get("type") or "track") == "track":
            return {"kind": "track", "id": cached["id"], "name": cached.get("name") or cached["id"],
                    "raw": cached}
        return None

    # ------------------------------------------------------------------ #
    # Action and view
    # ------------------------------------------------------------------ #
    def action_start_radio(self) -> None:
        """`R`: radio from the highlighted track / artist / playlist."""
        if not self._require_cap("can_radio"):
            return
        seed = self._radio_seed()
        if not seed or not seed.get("id") or seed.get("kind") not in RADIO_SEEDS:
            self._notify(f"[b]{self.RADIO_HELP}[/b] Highlight one and press R.", warn=True)
            return
        self._open_radio_view(seed)

    def _radio_rows(self, tracks: List[dict]) -> List[Dict]:
        fmt = getattr(self.spotify, "fmt_duration", None)
        rows, seen = [], set()
        for t in tracks or []:
            if not isinstance(t, dict) or not t.get("uri") or t["uri"] in seen:
                continue
            seen.add(t["uri"])
            alb = t.get("album") if isinstance(t.get("album"), dict) else {}
            rows.append({
                "_pos": len(rows), "id": t.get("id"), "uri": t["uri"], "type": "track",
                "title": t.get("name", "(no title)"),
                "artist": ", ".join(a.get("name", "") for a in (t.get("artists") or []) if isinstance(a, dict)),
                "album": alb.get("name", ""),
                "dur": fmt(t.get("duration_ms") or 0) if callable(fmt) else "",
                "raw": t,
            })
        return rows

    def _open_radio_view(self, seed: Dict, push_stack: bool = True) -> None:
        kind, sid = seed["kind"], seed["id"]
        ident = f"{kind}:{sid}"
        if push_stack:
            token = self._new_view_token("radio", ident)
        else:
            self._view_counter += 1
            token = self._view_counter
        self._right_view = ("radio", ident, token, dict(seed))
        name = seed.get("name") or sid
        right = self._clear_right()
        right.update(f"[b]Starting radio:[/b] {rich_escape(str(name))} …")

        def worker():
            notes: List[str] = []
            rows: List[Dict] = []
            failure = None
            try:
                res = self.spotify.radio(kind, sid, seed=seed.get("raw"), limit=self.RADIO_LIMIT) or {}
                rows = self._radio_rows(res.get("tracks") or [])
                notes = [str(n) for n in (res.get("notes") or [])]
            except ProviderUnsupported as exc:
                failure = str(exc) or self.RADIO_HELP
            except ProviderError as exc:
                failure = str(exc)
            except Exception as exc:
                logger.exception("radio failed")
                failure = f"The radio could not be loaded ({type(exc).__name__})."

            def paint():
                if not self._is_current_view("radio", ident, token):
                    return
                if failure or not rows:
                    msg = failure or "No similar tracks found."
                    self._clear_right().update(f"[b]Radio:[/b] {rich_escape(str(name))}\n\n{rich_escape(msg)}")
                    self._notify(f"[b]Radio:[/b] {rich_escape(msg)}", warn=True, seconds=7.0)
                    return
                label = {"track": "song", "artist": "artist", "playlist": "playlist"}.get(kind, kind)
                title = f"[b]Radio:[/b] {rich_escape(str(name))}  [dim]({label} radio · {len(rows)} tracks)[/dim]"
                table = self._render_tracks_table(title, rows, None,
                                                  context_uris=[r["uri"] for r in rows], profile="radio")
                table._radio_seed = dict(seed)
                table._queue_name = f"Radio: {name}"
                extra = ("  ·  " + "  ·  ".join(rich_escape(n) for n in notes)) if notes else ""
                self._notify(f"[b]Radio ready:[/b] {len(rows)} tracks. Enter plays from a row.{extra}",
                             seconds=6.0)
                self._fill_track_likes(table, rows, lambda: self._is_current_view("radio", ident, token))

            try:
                self.call_from_thread(paint)
            except Exception:
                if not getattr(self, "_closing", False):
                    logger.exception("radio paint: call_from_thread failed")

        threading.Thread(target=worker, daemon=True).start()
