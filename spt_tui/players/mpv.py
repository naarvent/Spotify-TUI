"""A small ``mpv`` controller shared by the Subsonic and YouTube Music providers.

mpv runs as our own subprocess (``--idle --no-video``) and is driven through its
JSON IPC socket (``--input-ipc-server``): one JSON object per line, each
command tagged with a ``request_id`` so its reply can be told apart from the
asynchronous events mpv also writes to the socket.

The player owns: launching mpv lazily on first use, the playlist (mpv's own
playlist *is* the queue), pause / seek / volume, reading position and duration
for the progress bar, and cleaning the subprocess up on ``close()`` (also
registered with ``atexit`` so a crash does not leave an orphan mpv playing).

Every outside touch point — process factory, socket connector, ``which``,
clock and sleep — is injectable, so the tests need neither mpv nor audio.

Transport: a Unix domain socket on Linux/macOS, a Windows named pipe
(the ``pipe`` namespace, see ``PIPE_PREFIX``) on Windows. Both expose the same tiny interface
(``sendall`` / ``recv`` with timeout / ``close``), so everything above the
connector is platform-independent.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Callable, Iterable, List, Optional

from ..config import logger


class MpvError(RuntimeError):
    pass


PIPE_PREFIX = r"\\.\pipe" + "\\"


def _is_pipe_path(path: str) -> bool:
    return str(path).startswith(PIPE_PREFIX)


def _unix_connect(path: str, timeout: float = 2.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    return s


class _WinPipe:
    """Client end of a Windows named pipe with the socket-like surface MpvPlayer uses.

    ``recv`` honours a timeout by polling ``PeekNamedPipe`` (a plain ``ReadFile``
    on a pipe blocks forever if mpv stops answering), and raises ``socket.timeout``
    exactly like a socket would, so ``_read_line`` needs no Windows special case.
    A broken pipe (mpv exited) reads as EOF (``b""``), which becomes "mpv closed
    the IPC socket" and triggers the usual reconnect/restart.
    """

    POLL = 0.01
    ERROR_BROKEN_PIPE = 109
    ERROR_NO_DATA = 232
    ERROR_PIPE_NOT_CONNECTED = 233

    def __init__(self, path: str, timeout: float = 2.0):
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        self._wt = wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.ReadFile.restype = wintypes.BOOL
        k.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k.WriteFile.restype = wintypes.BOOL
        k.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD),
                                    ctypes.POINTER(wintypes.DWORD),
                                    ctypes.POINTER(wintypes.DWORD)]
        k.PeekNamedPipe.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        self._k = k
        GENERIC_READ, GENERIC_WRITE, OPEN_EXISTING = 0x80000000, 0x40000000, 3
        h = k.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if h is None or h == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())  # an OSError: caller retries
        self._h = h
        self._timeout = timeout

    def settimeout(self, timeout):
        self._timeout = timeout

    def sendall(self, data: bytes) -> None:
        ct, wt = self._ct, self._wt
        view = memoryview(data)
        while len(view):
            n = wt.DWORD(0)
            buf = (ct.c_char * len(view)).from_buffer_copy(view)
            if not self._k.WriteFile(self._h, buf, len(view), ct.byref(n), None):
                raise ct.WinError(ct.get_last_error())
            view = view[n.value:]

    def recv(self, size: int) -> bytes:
        ct, wt = self._ct, self._wt
        deadline = time.monotonic() + (self._timeout if self._timeout is not None else 1e9)
        spins = 0
        while True:
            avail = wt.DWORD(0)
            if not self._k.PeekNamedPipe(self._h, None, 0, None, ct.byref(avail), None):
                err = ct.get_last_error()
                if err in (self.ERROR_BROKEN_PIPE, self.ERROR_NO_DATA,
                           self.ERROR_PIPE_NOT_CONNECTED):
                    return b""
                raise ct.WinError(err)
            if avail.value:
                n = min(int(size), avail.value)
                buf = ct.create_string_buffer(n)
                got = wt.DWORD(0)
                if not self._k.ReadFile(self._h, buf, n, ct.byref(got), None):
                    err = ct.get_last_error()
                    if err in (self.ERROR_BROKEN_PIPE, self.ERROR_NO_DATA,
                               self.ERROR_PIPE_NOT_CONNECTED):
                        return b""
                    raise ct.WinError(err)
                return buf.raw[:got.value]
            if time.monotonic() >= deadline:
                raise socket.timeout("timed out")
            # mpv normally answers in well under a millisecond, so spin (yielding the
            # CPU) for the first moments instead of sleeping: a fixed 10 ms sleep
            # cost ~10 ms per command, and loading a queue is hundreds of commands.
            # Back off when the wait is long (idle event stream) to stay cheap.
            spins += 1
            time.sleep(0 if spins < 50 else 0.0005 if spins < 400 else self.POLL)

    def close(self) -> None:
        h, self._h = getattr(self, "_h", None), None
        if h:
            self._k.CloseHandle(h)


def _pipe_connect(path: str, timeout: float = 2.0):
    return _WinPipe(path, timeout)


def _default_connect(path: str):
    return _pipe_connect(path) if _is_pipe_path(path) else _unix_connect(path)


def _default_ipc_path(unique: str) -> str:
    name = f"spt-tui-mpv-{os.getpid()}-{unique}"
    if os.name == "nt":
        return PIPE_PREFIX + name
    return os.path.join(tempfile.gettempdir(), name + ".sock")


class MpvPlayer:
    # How long to wait for mpv to create its IPC socket after spawning.
    START_TIMEOUT = 5.0
    # Per-command reply timeout.
    REPLY_TIMEOUT = 3.0

    def __init__(self, *, binary: Optional[str] = None, extra_args: Iterable[str] = (),
                 socket_path: Optional[str] = None,
                 popen: Callable = subprocess.Popen,
                 connect: Optional[Callable[[str], object]] = None,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 now: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 initial_volume: int = 70,
                 register_atexit: bool = True):
        self._binary = binary
        self._extra_args = list(extra_args or ())
        # Per-instance name (id) so two players in one process never collide.
        self._socket_path = socket_path or _default_ipc_path(f"{id(self):x}")
        self._popen = popen
        self._connect = connect or _default_connect
        self._which = which
        self._now = now
        self._sleep = sleep
        self._volume = max(0, min(100, int(initial_volume)))
        self._proc = None
        self._sock = None
        self._buf = b""
        self._req = 0
        self._lock = threading.RLock()
        self._closed = False
        # end-file events with reason "error" (unplayable / removed item),
        # collected while reading replies; drained by take_errors().
        self._errors: List[dict] = []
        if register_atexit:
            atexit.register(self.close)

    # ------------------------------------------------------------------ #
    # Availability and lifecycle
    # ------------------------------------------------------------------ #
    def binary_path(self) -> Optional[str]:
        if self._binary and os.path.isfile(self._binary):
            return self._binary
        if os.name == "nt":
            # Ask for mpv.exe explicitly: a bare "mpv" resolves to mpv.com (the console
            # wrapper) first because .COM precedes .EXE in PATHEXT. The wrapper is a
            # separate process, so terminate() on it can leave the real mpv.exe
            # playing after the app exits.
            exe = self._which("mpv.exe")
            if exe:
                return exe
        return self._which("mpv")

    def unavailable_reason(self) -> Optional[str]:
        if self.binary_path() is None:
            return "mpv was not found on PATH (install mpv)."
        return None

    def is_available(self) -> bool:
        return self.unavailable_reason() is None

    def is_running(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def _argv(self, binary: str) -> List[str]:
        # --ytdl-raw-options=no-playlist=: a watch URL is one track; mpv must
        # never expand it into a list and play on by itself. --keep-open=no:
        # at the end of our queue mpv goes idle (the TUI decides what is next).
        argv = [binary, "--idle=yes", "--no-video", "--no-terminal",
                "--force-window=no", "--audio-display=no", "--keep-open=no",
                "--ytdl-raw-options=no-playlist=",
                f"--volume={self._volume}",
                f"--input-ipc-server={self._socket_path}", *self._extra_args]
        # Diagnostics: SPT_MPV_LOG=<file> makes mpv write a verbose log (stutters,
        # restarts, yt-dlp failures) without touching the normal quiet behaviour.
        log = os.environ.get("SPT_MPV_LOG")
        if log:
            argv += [f"--log-file={log}", "--msg-level=all=v"]
        return argv

    def start(self) -> None:
        """Spawn mpv (if not already running) and connect to its socket."""
        with self._lock:
            if self._closed:
                raise MpvError("player is closed")
            if self.is_running() and self._sock is not None:
                return
            reason = self.unavailable_reason()
            if reason:
                raise MpvError(reason)
            if not self.is_running():
                self._disconnect()
                self._remove_socket_file()
                argv = self._argv(self.binary_path())
                logger.info("mpv: starting %s", " ".join(argv[:1] + argv[1:6]))
                self._proc = self._popen(argv, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
            deadline = self._now() + self.START_TIMEOUT
            last_exc = None
            while self._now() < deadline:
                if self._proc is not None and self._proc.poll() is not None:
                    raise MpvError("mpv exited during startup")
                try:
                    self._sock = self._connect(self._socket_path)
                    self._buf = b""
                    return
                except OSError as exc:
                    last_exc = exc
                    self._sleep(0.05)
            raise MpvError(f"could not connect to mpv's IPC socket: {last_exc}")

    def _disconnect(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    def close(self) -> None:
        """Quit mpv and remove its socket. Idempotent; safe at interpreter exit."""
        with self._lock:
            self._closed = True
            if self._sock is not None:
                try:
                    self._send({"command": ["quit"], "request_id": 0})
                except Exception:
                    pass
            self._disconnect()
            p, self._proc = self._proc, None
        if p is not None:
            try:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        try:
                            p.wait(timeout=3)
                        except Exception:
                            logger.exception("mpv did not exit after kill")
            except Exception:
                logger.exception("error stopping mpv")
        self._remove_socket_file()

    def _remove_socket_file(self) -> None:
        if _is_pipe_path(self._socket_path):
            return  # a named pipe vanishes with its last handle; nothing on disk
        try:
            if os.path.exists(self._socket_path):
                os.unlink(self._socket_path)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # JSON IPC
    # ------------------------------------------------------------------ #
    def _send(self, obj: dict) -> None:
        data = (json.dumps(obj) + "\n").encode("utf-8")
        self._sock.sendall(data)

    def _read_line(self, deadline: float) -> Optional[bytes]:
        while b"\n" not in self._buf:
            if self._now() > deadline:
                return None
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise MpvError("mpv closed the IPC socket")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def command(self, *args):
        """Run one mpv command and return its ``data``. Raises MpvError when mpv
        answers with an error (e.g. a property is unavailable while idle)."""
        with self._lock:
            self.start()
            self._req += 1
            rid = self._req
            try:
                self._send({"command": list(args), "request_id": rid})
                deadline = self._now() + self.REPLY_TIMEOUT
                while True:
                    line = self._read_line(deadline)
                    if line is None:
                        raise MpvError(f"mpv did not answer {args[0]!r}")
                    try:
                        msg = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if msg.get("request_id") != rid:
                        # An event, or a reply to someone else. Keep load
                        # failures: mpv skips an unplayable entry by itself and
                        # the provider must tell the user which one it was.
                        if msg.get("event") == "end-file" and msg.get("reason") == "error":
                            self._errors.append(msg)
                            del self._errors[:-50]
                        continue
                    if msg.get("error") not in (None, "success"):
                        raise MpvError(f"{args[0]}: {msg.get('error')}")
                    return msg.get("data")
            except OSError as exc:
                # Broken pipe / mpv died: forget the socket so the next command
                # restarts mpv instead of failing forever.
                self._disconnect()
                raise MpvError(f"IPC failure: {exc}") from exc
            except MpvError as exc:
                if "closed" in str(exc):
                    self._disconnect()
                raise

    def get(self, prop: str, default=None):
        try:
            return self.command("get_property", prop)
        except MpvError:
            return default

    def set(self, prop: str, value) -> None:
        self.command("set_property", prop, value)

    # ------------------------------------------------------------------ #
    # Playback API used by the providers
    # ------------------------------------------------------------------ #
    def load(self, urls: List[str], start_index: int = 0) -> None:
        """Replace the playlist with ``urls`` and start at ``start_index``."""
        urls = [u for u in (urls or []) if u]
        if not urls:
            raise MpvError("nothing to play")
        start_index = max(0, min(int(start_index or 0), len(urls) - 1))
        self.command("playlist-clear")
        self.command("loadfile", urls[start_index], "replace")
        # Items before the start go before it, the rest after, so prev/next walk
        # the list in order.
        for u in urls[start_index + 1:]:
            self.command("loadfile", u, "append")
        for i, u in enumerate(urls[:start_index]):
            self.command("loadfile", u, "append")
            # Newly appended entry is last; move it in front of the current one.
            count = int(self.get("playlist-count", 0) or 0)
            self.command("playlist-move", count - 1, i)
        self.set("pause", False)

    def append(self, url: str) -> None:
        """Add ``url`` at the end of the queue (and start it if mpv is idle)."""
        self.command("loadfile", url, "append-play")

    def pause(self) -> None:
        self.set("pause", True)

    def resume(self) -> None:
        self.set("pause", False)

    def next(self) -> None:
        # Like a Spotify skip: the next track plays even if we were paused
        # (mpv keeps `pause` across entries, which froze the progress bar).
        self.command("playlist-next", "force")
        self.set("pause", False)

    def prev(self) -> None:
        self.command("playlist-prev", "force")
        self.set("pause", False)

    def take_errors(self) -> List[dict]:
        """Load failures seen since the last call (end-file reason=error)."""
        with self._lock:
            errs, self._errors = self._errors, []
        return errs

    def playlist_entries(self) -> List[dict]:
        """mpv's playlist entries ({"filename", "id", "current"...})."""
        return [e for e in (self.get("playlist", []) or []) if isinstance(e, dict)]

    def insert_at(self, url: str, index: int) -> Optional[int]:
        """Insert ``url`` at ``index`` of mpv's playlist (it does not start playing) and return the
        id of the new entry, or None if it could not be found afterwards."""
        with self._lock:
            n = len(self.playlist_entries())
            index = max(0, min(int(index), n))
            self.command("loadfile", url, "insert-at", index)
            entries = self.playlist_entries()
            return entries[index].get("id") if 0 <= index < len(entries) else None

    def replace_entry(self, index: int, url: str, expect: str) -> bool:
        """Swap the entry at ``index`` for ``url`` (same place in the queue), only if it still
        holds ``expect`` and is not the one playing. Returns whether it was replaced.
        Used to hand mpv an already-resolved stream URL for an upcoming entry."""
        with self._lock:
            entries = self.playlist_entries()
            if not (0 <= index < len(entries)):
                return False
            e = entries[index]
            if e.get("filename") != expect or e.get("current") or e.get("playing"):
                return False
            self.command("loadfile", url, "insert-at", int(index))
            self.command("playlist-remove", int(index) + 1)
            return True

    def remove_ids(self, ids: Iterable[int]) -> int:
        """Remove the entries with these ``ids`` (never the current one). Returns how many."""
        want = set(ids or ())
        if not want:
            return 0
        with self._lock:
            entries = self.playlist_entries()
            idx = [i for i, e in enumerate(entries)
                   if e.get("id") in want and not e.get("current") and not e.get("playing")]
            for i in reversed(idx):           # from the end, so earlier indexes stay valid
                self.command("playlist-remove", i)
            return len(idx)

    def reorder(self, ids: List[int]) -> bool:
        """Put the entries in the order of their ``ids`` (with playlist-move; the playing entry
        keeps playing). False when the playlist changed meanwhile."""
        with self._lock:
            cur = [e.get("id") for e in self.playlist_entries()]
            if sorted(cur, key=str) != sorted(ids, key=str):
                return False
            for t, eid in enumerate(ids):
                i = cur.index(eid)
                if i != t:
                    self.command("playlist-move", i, t)
                    cur.insert(t, cur.pop(i))
            return True

    def play_index(self, index: int) -> None:
        self.command("playlist-play-index", int(index))
        self.set("pause", False)

    def seek(self, seconds: float) -> None:
        self.command("seek", max(0.0, float(seconds)), "absolute")

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        if self.is_running():
            self.set("volume", self._volume)

    def set_loop(self, state: str) -> None:
        """Spotify-style repeat: 'off', 'context' (whole list), 'track'."""
        self.set("loop-file", "inf" if state == "track" else "no")
        self.set("loop-playlist", "inf" if state == "context" else "no")

    def playlist_urls(self) -> List[str]:
        out = []
        for entry in self.get("playlist", []) or []:
            if isinstance(entry, dict) and entry.get("filename"):
                out.append(entry["filename"])
        return out

    def state(self) -> dict:
        """Snapshot for the now-playing bar. Never raises; when mpv is not
        running everything reads as stopped."""
        if not self.is_running():
            return {"running": False, "idle": True, "paused": True, "position": 0.0,
                    "duration": 0.0, "index": -1, "volume": self._volume}
        try:
            idle = bool(self.get("idle-active", True))
            pos = self.get("time-pos", 0.0) or 0.0
            dur = self.get("duration", 0.0) or 0.0
            paused = bool(self.get("pause", False))
            idx = self.get("playlist-pos", -1)
            vol = self.get("volume", self._volume)
        except Exception:
            logger.exception("mpv state read failed")
            return {"running": self.is_running(), "idle": True, "paused": True,
                    "position": 0.0, "duration": 0.0, "index": -1, "volume": self._volume}
        return {"running": True, "idle": idle, "paused": paused,
                "position": float(pos), "duration": float(dur),
                "index": int(idx if idx is not None else -1),
                "volume": int(round(float(vol if vol is not None else self._volume)))}
