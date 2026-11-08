"""MpvPlayer: JSON IPC protocol, playlist/queue, state for the progress bar and
subprocess cleanup — against a fake mpv (fake process + in-memory socket that
answers like mpv's IPC). No mpv binary, no audio.

Run standalone:  python tests/test_mpv_player.py
"""
import os, sys, json, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.players.mpv import MpvPlayer, MpvError

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class FakeProc:
    def __init__(self, argv):
        self.argv = argv; self.rc = None; self.terminated = False; self.killed = False
    def poll(self): return self.rc
    def terminate(self): self.terminated = True; self.rc = 0
    def kill(self): self.killed = True; self.rc = -9
    def wait(self, timeout=None): return self.rc


class FakeMpv:
    """In-memory mpv: a playlist, a few properties, and the IPC reply format
    (plus an unsolicited event before each reply, which the client must skip)."""
    def __init__(self):
        self.playlist = []; self.pos = -1
        self.props = {"pause": False, "volume": 70, "time-pos": 12.5, "duration": 200.0}
        self.commands = []
        self.fail_next = None
        self.pending_error = None

    def handle(self, cmd):
        self.commands.append(cmd)
        name = cmd[0]
        if self.fail_next == name:
            self.fail_next = None
            return {"error": "property unavailable"}
        if name == "playlist-clear":
            self.playlist = []; self.pos = -1
        elif name == "loadfile":
            url, mode = cmd[1], cmd[2]
            if mode == "replace":
                self.playlist = [url]; self.pos = 0
            else:
                self.playlist.append(url)
                if mode == "append-play" and self.pos < 0:
                    self.pos = len(self.playlist) - 1
        elif name == "playlist-move":
            a, b = cmd[1], cmd[2]
            item = self.playlist.pop(a); self.playlist.insert(b, item)
            if b <= self.pos:
                self.pos += 1
        elif name == "playlist-next":
            self.pos = min(self.pos + 1, len(self.playlist) - 1)
        elif name == "playlist-prev":
            self.pos = max(0, self.pos - 1)
        elif name == "set_property":
            self.props[cmd[1]] = cmd[2]
        elif name == "seek":
            self.props["time-pos"] = float(cmd[1])
        elif name == "get_property":
            prop = cmd[1]
            if prop == "playlist-count":
                return {"error": "success", "data": len(self.playlist)}
            if prop == "playlist-pos":
                return {"error": "success", "data": self.pos}
            if prop == "idle-active":
                return {"error": "success", "data": self.pos < 0}
            if prop == "path":
                return {"error": "success", "data": self.playlist[self.pos] if self.pos >= 0 else None}
            if prop == "playlist":
                return {"error": "success", "data": [{"filename": u} for u in self.playlist]}
            if prop not in self.props:
                return {"error": "property not found"}
            return {"error": "success", "data": self.props[prop]}
        return {"error": "success", "data": None}


class FakeSock:
    def __init__(self, mpv):
        self.mpv = mpv; self.out = b""; self.closed = False; self.broken = False
    def sendall(self, data):
        if self.broken:
            raise BrokenPipeError("broken")
        for line in data.decode().splitlines():
            msg = json.loads(line)
            reply = self.mpv.handle(msg["command"])
            reply["request_id"] = msg["request_id"]
            # An async event first, then the real reply.
            self.out += b'{"event":"property-change","name":"time-pos"}\n'
            if self.mpv.pending_error is not None:
                self.out += (json.dumps({"event": "end-file", "reason": "error",
                                         "playlist_entry_id": self.mpv.pending_error,
                                         "file_error": "loading failed"}) + "\n").encode()
                self.mpv.pending_error = None
            self.out += (json.dumps(reply) + "\n").encode()
    def recv(self, n):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk
    def close(self): self.closed = True


def make(fake=None, which=lambda n: "/usr/bin/mpv", connect_fail=0):
    fake = fake or FakeMpv()
    procs, socks = [], []
    fails = [connect_fail]
    def popen(argv, **kw):
        p = FakeProc(argv); procs.append(p); return p
    def connect(path):
        if fails[0] > 0:
            fails[0] -= 1
            raise FileNotFoundError(path)
        s = FakeSock(fake); socks.append(s); return s
    sock_path = os.path.join(tempfile.mkdtemp(prefix="spt_mpv_"), "mpv.sock")
    pl = MpvPlayer(popen=popen, connect=connect, which=which, socket_path=sock_path,
                   sleep=lambda s: None, register_atexit=False, extra_args=["--ytdl=yes"])
    return pl, fake, procs, socks


def test_unavailable():
    pl, *_ = make(which=lambda n: None)
    check("no mpv on PATH -> unavailable with reason", not pl.is_available()
          and "mpv" in (pl.unavailable_reason() or ""), repr(pl.unavailable_reason()))
    try:
        pl.start(); raised = False
    except MpvError:
        raised = True
    check("start() without mpv raises MpvError (no crash)", raised)
    st = pl.state()
    check("state() of a stopped player reads as idle", st["running"] is False and st["idle"])


def test_spawn_and_argv():
    pl, fake, procs, socks = make(connect_fail=3)
    pl.command("get_property", "volume")
    check("mpv spawned once", len(procs) == 1)
    argv = procs[0].argv
    check("argv: idle, no video, IPC socket", "--idle=yes" in argv and "--no-video" in argv
          and any(a.startswith("--input-ipc-server=") for a in argv), repr(argv))
    check("argv: extra provider args appended", "--ytdl=yes" in argv)
    check("socket connect retried until mpv is ready", len(socks) == 1)
    pl.command("get_property", "volume")
    check("second command reuses the process and socket", len(procs) == 1 and len(socks) == 1)


def test_reply_matching_and_errors():
    pl, fake, *_ = make()
    check("reply matched by request_id (event skipped)", pl.command("get_property", "volume") == 70)
    fake.fail_next = "get_property"
    try:
        pl.command("get_property", "volume"); raised = False
    except MpvError:
        raised = True
    check("mpv error reply raises MpvError", raised)
    check("get() swallows errors and returns default", pl.get("nonexistent", "dflt") == "dflt")


def test_load_and_queue():
    pl, fake, *_ = make()
    pl.load(["u0", "u1", "u2", "u3"], start_index=2)
    check("load keeps list order around the start", fake.playlist == ["u0", "u1", "u2", "u3"], repr(fake.playlist))
    check("load starts at start_index", fake.playlist[fake.pos] == "u2", f"pos={fake.pos}")
    check("load unpauses", fake.props["pause"] is False)
    pl.append("u4")
    check("append adds at the end (append-play)", fake.playlist[-1] == "u4"
          and fake.commands[-1] == ["loadfile", "u4", "append-play"])
    pl.next()
    check("next -> playlist-next", fake.playlist[fake.pos] == "u3")
    pl.prev()
    check("prev -> playlist-prev", fake.playlist[fake.pos] == "u2")
    check("playlist_urls reads mpv's playlist", pl.playlist_urls() == ["u0", "u1", "u2", "u3", "u4"])
    try:
        pl.load([]); raised = False
    except MpvError:
        raised = True
    check("load([]) refuses", raised)


def test_controls_and_state():
    pl, fake, *_ = make()
    pl.load(["a", "b"])
    pl.pause()
    check("pause sets the pause property", fake.props["pause"] is True)
    pl.resume()
    check("resume clears it", fake.props["pause"] is False)
    pl.seek(42.0)
    check("seek is absolute seconds", fake.commands[-1] == ["seek", 42.0, "absolute"])
    pl.seek(-5)
    check("negative seek clamped to 0", fake.commands[-1] == ["seek", 0.0, "absolute"])
    pl.set_volume(150)
    check("volume clamped to 100", fake.props["volume"] == 100)
    pl.set_loop("track")
    check("repeat track -> loop-file inf", fake.props.get("loop-file") == "inf" and fake.props.get("loop-playlist") == "no")
    pl.set_loop("context")
    check("repeat context -> loop-playlist inf", fake.props.get("loop-playlist") == "inf" and fake.props.get("loop-file") == "no")
    fake.props["time-pos"] = 30.25; fake.props["duration"] = 180.0
    st = pl.state()
    check("state: position/duration for the progress bar", st["position"] == 30.25 and st["duration"] == 180.0, repr(st))
    check("state: running, not idle, index", st["running"] and not st["idle"] and st["index"] == 0, repr(st))


def test_broken_socket_reconnects():
    pl, fake, procs, socks = make()
    pl.command("get_property", "volume")
    socks[0].broken = True
    try:
        pl.command("get_property", "volume"); raised = False
    except MpvError:
        raised = True
    check("broken pipe surfaces as MpvError", raised)
    check("reconnects on the next command", pl.command("get_property", "volume") == 70 and len(socks) == 2)
    check("…without spawning a second mpv while the first lives", len(procs) == 1)


def test_close_cleans_up():
    pl, fake, procs, socks = make()
    pl.command("get_property", "volume")
    open(pl._socket_path, "w").close()
    pl.close()
    check("close sends quit", fake.commands[-1] == ["quit"])
    check("close terminates the subprocess", procs[0].terminated)
    check("close closes the socket", socks[0].closed)
    check("close removes the socket file", not os.path.exists(pl._socket_path))
    pl.close()
    check("close is idempotent", True)
    try:
        pl.command("get_property", "volume"); raised = False
    except MpvError:
        raised = True
    check("a closed player refuses new commands", raised)


def test_load_errors_skip_and_no_autoplay():
    pl, fake, procs, socks = make()
    pl.load(["a", "b"])
    argv = procs[0].argv
    check("mpv never expands a URL into a playlist by itself", "--ytdl-raw-options=no-playlist=" in argv)
    check("mpv goes idle at the end of our queue", "--keep-open=no" in argv)
    fake.pending_error = 1
    pl.get("volume")
    errs = pl.take_errors()
    check("end-file reason=error events are collected", len(errs) == 1 and errs[0]["playlist_entry_id"] == 1, repr(errs))
    check("…and drained once", pl.take_errors() == [])
    fake.props["pause"] = True
    pl.next()
    check("next unpauses (a skip plays, like Spotify)", fake.props["pause"] is False)
    fake.props["pause"] = True
    pl.prev()
    check("prev unpauses", fake.props["pause"] is False)


ALL = [test_load_errors_skip_and_no_autoplay, test_unavailable, test_spawn_and_argv, test_reply_matching_and_errors, test_load_and_queue,
       test_controls_and_state, test_broken_socket_reconnects, test_close_cleans_up]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (mpv_player) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
