"""'Import from Spotify' view: pick what to import, run it in the background.

Visible (Library menu entry and an entry in the provider picker) when the
active provider can take an import (``can_import_from_spotify``: YouTube
Music) and Spotify credentials are configured. Reading Spotify is read-only
(see spt_tui.spotify_import); the active provider is never touched.
"""

from __future__ import annotations

import threading
from typing import List, Optional

from rich.markup import escape as rich_escape
from textual.containers import VerticalScroll
from textual.widgets import Button, Static

from .form_lists import FormSelectionList

from .. import spotify_import as si
from ..config import logger

IMPORT_ENTRY = "Import from Spotify"
PICKER_IMPORT_ID = "__import_from_spotify__"


class SpotifyImportMixin:
    # Overridable in tests: () -> read-only spotipy-like client.
    _spotify_reader_factory = staticmethod(si.default_spotify_reader)

    def _spotify_import_supported(self) -> bool:
        """The active provider explicitly supports it (objects without
        capabilities — test fakes, Spotify — do not)."""
        caps = getattr(getattr(self, "spotify", None), "capabilities", None)
        return bool(getattr(caps, "can_import_from_spotify", False) is True)

    # Overridable in tests: () -> an anonymous Spotify link reader (spt_tui.scraper.ScraperClient).
    @staticmethod
    def _scraper_client_factory():
        from ..scraper import ScraperClient
        return ScraperClient()

    def _import_link_dispatch(self, raw: str) -> bool:
        """Library > Import Playlists: route what was pasted according to the active provider.
        Returns True when handled here; False lets the original flow (follow a playlist by id) run."""
        kind, ref = si.classify_import_input(raw)
        name = getattr(getattr(self, "spotify", None), "name", "")
        if self._spotify_import_supported():                     # ytmusic: can copy Spotify playlists
            if kind == "spotify_playlist":
                self._import_playlist_from_link(ref)
                return True
            if kind == "spotify_other":
                self._notify(f"[b]That is a Spotify {rich_escape(str(ref))} link.[/b] Import Playlists takes "
                             "playlist links; for albums or artists use 'Import from Spotify' (needs a "
                             "Premium developer app).", warn=True, seconds=9.0)
                return True
            return False
        if name in ("spotify", "spotify_mpris") and kind == "youtube":
            self._notify("[b]That is a YouTube link.[/b] This provider adds Spotify playlists. Press P and "
                         "choose ytmusic to import YouTube Music playlists.", warn=True, seconds=9.0)
            return True
        return False

    def _import_playlist_from_link(self, pid: str) -> None:
        """Read a public Spotify playlist by link (no account) and import it as a local playlist."""
        if getattr(self, "_import_running", False) or getattr(self, "_import_link_busy", False):
            self._notify("[b]An import is already running[/b] (Esc cancels it).", warn=True)
            return
        self._import_link_busy = True
        self._notify("[b]Reading the Spotify playlist…[/b]", sticky=True)

        def failed(exc: Exception) -> None:
            self._import_link_busy = False
            logger.warning("import by link failed: %s: %s", type(exc).__name__, exc)
            self._notify(f"[b]{rich_escape(si.describe_link_error(exc))}[/b]", warn=True, seconds=10.0)

        def worker():
            from ..scraper_source import ScraperSource
            client = None
            try:
                client = self._scraper_client_factory()
                src = ScraperSource(client)
                info = src.load(pid)
            except Exception as exc:
                try:
                    self.call_from_thread(failed, exc)
                except Exception:
                    logger.debug("link import error paint dropped")
                return
            finally:
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass

            def go() -> None:
                self._import_link_busy = False
                key = f"pl:{info['id']}"
                self._import_playlists = {key: info}
                if info["partial"]:
                    self._notify(f"[b]Only {info['read']} of {info['total']} songs could be read.[/b] Import it "
                                 "again later to complete it.", warn=True, seconds=8.0)
                elif info["skipped"]:
                    self._notify(f"[b]{info['read']} songs read; {info['skipped']} items (podcast episodes or "
                                 "local files) are skipped.[/b]", seconds=6.0)
                self._start_spotify_import([key], source=src)
            try:
                self.call_from_thread(go)
            except Exception:
                self._import_link_busy = False
                logger.debug("link import start dropped")
        threading.Thread(target=worker, daemon=True).start()

    def _spotify_import_visible(self) -> bool:
        return self._spotify_import_supported() and si.spotify_credentials_configured()

    # ------------------------------------------------------------------ #
    def action_import_from_spotify(self) -> None:
        if getattr(self, "_import_running", False):
            self._notify("[b]An import is already running[/b] (Esc cancels it).", warn=True)
            return
        if not self._spotify_import_supported():
            self._notify("[b]Import from Spotify works into YouTube Music's local library.[/b] "
                         "Press P and choose ytmusic first.", warn=True, seconds=8.0)
            return
        if not si.spotify_credentials_configured():
            self._notify(f"[b]{rich_escape(si.NO_CREDENTIALS_HELP)}[/b]", warn=True, seconds=12.0)
            return
        if not si.spotify_token_cached():
            right = self._clear_right()
            right.update(f"[b]Import from Spotify[/b]\n\n{rich_escape(si.NO_TOKEN_HELP)}")
            self._notify("[b]Spotify sign-in needed for the import[/b] (see the panel).", warn=True)
            return
        self._new_view_token("spotify_import", "")
        right = self._clear_right()
        right.update("[b]Import from Spotify[/b]\n\nReading your Spotify library (read-only)…")
        self.level = self.LVL_VIEW

        def worker():
            try:
                source = si.SpotifySource(self._spotify_reader_factory())
                summary = source.summary()
            except Exception as exc:
                logger.exception("Spotify import: reading the library failed")
                msg = str(exc) or type(exc).__name__
                try:
                    self.call_from_thread(lambda: self._clear_right().update(
                        f"[b]Import from Spotify[/b]\n\nCould not read your Spotify library: "
                        f"{rich_escape(msg)}"))
                except Exception:
                    pass
                return
            try:
                self.call_from_thread(self._show_import_selection, source, summary)
            except Exception:
                logger.debug("import selection paint dropped")
        threading.Thread(target=worker, daemon=True).start()

    def _show_import_selection(self, source, summary: dict) -> None:
        rv = getattr(self, "_right_view", None)
        if not rv or rv[0] != "spotify_import":
            return
        right = self._clear_right()
        self._right_view = rv
        options = [(f"Liked Songs ({summary.get('liked', 0)})", "liked"),
                   (f"Saved Albums ({summary.get('albums', 0)})", "albums"),
                   (f"Followed Artists ({summary.get('artists', 0)})", "artists")]
        self._import_playlists = {}
        for p in summary.get("playlists") or []:
            key = f"pl:{p['id']}"
            self._import_playlists[key] = p
            options.append((f"Playlist: {p['name']} ({p.get('total', 0)})", key))
        self._import_source = source
        self.import_selection = FormSelectionList(*options, classes="import-selection")
        self._import_start_button = Button("Import selected", variant="primary", classes="import-start")
        form = VerticalScroll(
            Static("⇪  Import from Spotify", classes="settings-title"),
            Static(f"Into {rich_escape(getattr(self.spotify, 'display_name', 'this provider'))}'s local "
                   "library. Spotify is only read, never changed. Each song is matched by search; "
                   "doubtful matches are imported marked '(? check match)'.", classes="settings-subtitle"),
            self.import_selection,
            self._import_start_button,
            Static("Space mark · Tab to the button · Enter start · Esc back (or cancel a running import)",
                   classes="settings-keys"),
            classes="settings-form")
        right.mount(form)
        self.level = self.LVL_VIEW
        try:
            self.import_selection.focus()
        except Exception:
            pass

    def on_button_pressed(self, event) -> None:
        if event.button is getattr(self, "_import_start_button", None):
            event.stop()
            chosen = list(getattr(self.import_selection, "selected", []) or [])
            if not chosen:
                self._notify("[b]Mark at least one item (Space) to import.[/b]", warn=True)
                return
            self._start_spotify_import(chosen)

    def _selection_from_keys(self, keys: List[str]) -> List[dict]:
        sel = []
        for k in keys:
            if k in ("liked", "albums", "artists"):
                sel.append({"kind": k})
            elif k.startswith("pl:"):
                p = (getattr(self, "_import_playlists", {}) or {}).get(k) or {}
                sel.append({"kind": "playlist", "id": k[3:], "name": p.get("name") or k[3:]})
        # Playlists first, then the library sections (order of the list).
        return sel

    def _start_spotify_import(self, keys: List[str], source=None) -> None:
        source = source or getattr(self, "_import_source", None)
        selection = self._selection_from_keys(keys)
        if source is None or not selection:
            return
        self._import_cancel = threading.Event()
        self._import_running = True
        prov = self.spotify
        right = self._clear_right()
        self._right_view = ("spotify_import", "running", 0)
        right.update("[b]Importing from Spotify…[/b]\n\nProgress is on the status line. Esc cancels; "
                     "a later import resumes from where it stopped.")

        def progress(msg: str) -> None:
            try:
                self.call_from_thread(self._notify, f"[b]{rich_escape(msg)}[/b]  (Esc to cancel)", sticky=True)
            except Exception:
                pass

        def worker():
            result = None
            try:
                importer = si.SpotifyImporter(source, prov, progress=progress, cancel=self._import_cancel,
                                              matcher=getattr(self, "_import_matcher_factory", lambda p: None)(prov))
                result = importer.run(selection)
            except Exception as exc:
                logger.exception("Spotify import failed")
                result = {"summary": {}, "cancelled": False, "aborted": str(exc), "report": None, "items": []}
            finally:
                self._import_running = False
            try:
                self.call_from_thread(self._show_import_result, result)
            except Exception:
                logger.debug("import result paint dropped")
            try:
                self._load_playlists(force=True)
            except Exception:
                logger.exception("reloading playlists after import failed")
        threading.Thread(target=worker, daemon=True).start()

    def _cancel_spotify_import(self) -> bool:
        ev = getattr(self, "_import_cancel", None)
        if getattr(self, "_import_running", False) and ev is not None:
            ev.set()
            self._notify("[b]Cancelling the import…[/b] (what is done so far is kept)", sticky=True)
            return True
        return False

    def _show_import_result(self, result: dict) -> None:
        s = result.get("summary") or {}
        line = (f"{s.get('matched', 0)} matched, {s.get('doubtful', 0)} doubtful, "
                f"{s.get('not_found', 0)} not found" + (f", {s['errors']} errors" if s.get("errors") else ""))
        if result.get("aborted"):
            head = f"Import stopped: {result['aborted']}"
        elif result.get("cancelled"):
            head = "Import cancelled (kept what was done; run it again to resume)"
        else:
            head = "Import finished"
        report = result.get("report")
        self._notify(f"[b]{rich_escape(head)}:[/b] {rich_escape(line)}",
                     warn=bool(result.get("aborted") or result.get("cancelled")), seconds=12.0)
        rv = getattr(self, "_right_view", None)
        if rv and rv[0] == "spotify_import":
            lines = [f"[b]{rich_escape(head)}[/b]", "", rich_escape(line), ""]
            for it in result.get("items") or []:
                lines.append(f"• {rich_escape(it.get('name', ''))}: {it.get('imported', 0)} imported, "
                             f"{len(it.get('doubtful', []))} doubtful, {len(it.get('not_found', []))} not found")
            if report:
                lines += ["", f"Report (doubtful / not found): {rich_escape(str(report))}"]
            self._clear_right().update("\n".join(lines))
            self._right_view = ("spotify_import", "done", 0)
