"""SecretStore: env var > keyring > secret-tool, the password goes through stdin
(never argv) and nothing is stored when there is no secure backend.
Fake keyring and fake command runner; no real keyring is touched.

Run standalone:  python tests/test_secret_store.py
"""
import os, sys, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.secret_store import SecretStore

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))


class KR:
    def __init__(self): self.d = {}
    def get_password(self, s, a): return self.d.get((s, a))
    def set_password(self, s, a, p): self.d[(s, a)] = p


class Run:
    def __init__(self, lookup_out="", rc=0):
        self.calls = []; self.lookup_out = lookup_out; self.rc = rc
    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        class R: pass
        r = R(); r.returncode = self.rc; r.stderr = ""
        r.stdout = self.lookup_out if argv[1] == "lookup" else ""
        return r


def test_env_first():
    os.environ["SPT_TEST_PW"] = "from-env"
    try:
        kr = KR(); kr.set_password("spt-tui", "acc", "from-kr")
        s = SecretStore(keyring_module=kr)
        check("env var wins over the keyring", s.get("acc", env="SPT_TEST_PW") == "from-env")
    finally:
        os.environ.pop("SPT_TEST_PW", None)


def test_keyring():
    kr = KR()
    s = SecretStore(keyring_module=kr, which=lambda n: None)
    check("backend: keyring", s.backend() == "keyring")
    check("set via keyring", s.set("acc", "pw") == "keyring" and kr.d[("spt-tui", "acc")] == "pw")
    check("get via keyring", s.get("acc") == "pw")


def test_secret_tool():
    run = Run(lookup_out="tool-pw\n")
    s = SecretStore(use_keyring=False, which=lambda n: "/usr/bin/secret-tool" if n == "secret-tool" else None, run=run)
    check("backend: secret-tool", s.backend() == "secret-tool")
    check("lookup strips the newline", s.get("acc") == "tool-pw")
    check("store returns the backend", s.set("acc", "hunter2") == "secret-tool")
    argv, kw = run.calls[-1]
    check("store passes the password on stdin", kw.get("input") == "hunter2")
    check("…and never on the command line", "hunter2" not in " ".join(argv), repr(argv))
    check("attributes service/account used", argv[:2] == ["secret-tool", "store"] and "service" in argv and "account" in argv)
    run.rc = 1
    check("store failure -> None", s.set("acc", "x") is None)


def test_no_backend():
    s = SecretStore(use_keyring=False, which=lambda n: None)
    check("no backend reported", s.backend() is None)
    check("set refuses (returns None)", s.set("acc", "pw") is None)
    check("get -> None", s.get("acc") is None)


ALL = [test_env_first, test_keyring, test_secret_tool, test_no_backend]

def main():
    for fn in ALL:
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (crashed)", False, traceback.format_exc())
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== SUMMARY (secret_store) ====")
    print(f"{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name)
    return passed == len(RESULTS)

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
