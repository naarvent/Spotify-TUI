"""Issue #9: saving a setting right after entering the Spotify credentials
must not wipe client_id / client_secret / redirect_uri from spt_config.json.

Run standalone:  python tests/test_config_save.py
"""
import os, sys, json, tempfile, shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spt_tui import config

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail and not cond else ""))

def read(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

d = tempfile.mkdtemp(prefix="spt_cred_")
old = (config.CONFIG_PATH, config.LOCAL_CFG, config.CLIENT_ID, config.CLIENT_SECRET, config.REDIRECT_URI, config.CONFIG_LOADED)
try:
    config.CONFIG_PATH = os.path.join(d, "spt_config.json")

    # 1. Form first, then a setting (the reported sequence), starting with no file.
    config.LOCAL_CFG = {}
    config.save_credentials("cid", "sec", "http://127.0.0.1:8888/callback")
    config.LOCAL_CFG["playlist_sort"] = "artist"
    config.LOCAL_CFG["playlist_sort_desc"] = False
    config.save_local_config(config.LOCAL_CFG)
    got = read(config.CONFIG_PATH)
    check("credentials survive a later setting save",
          got.get("client_id") == "cid" and got.get("client_secret") == "sec"
          and got.get("redirect_uri") == "http://127.0.0.1:8888/callback" and got.get("playlist_sort") == "artist", str(got))
    check("globals updated", config.CLIENT_ID == "cid" and config.CONFIG_LOADED is True)

    # 2. The form keeps the settings that were already there.
    config.LOCAL_CFG = {"provider": "ytmusic"}
    config.save_credentials("c2", "s2", "r2")
    got = read(config.CONFIG_PATH)
    check("form keeps other settings", got.get("provider") == "ytmusic" and got.get("client_id") == "c2", str(got))

    # 3. Safety net: a stale dict without credentials cannot wipe them on disk.
    config.LOCAL_CFG = {"lyrics_mode": "line"}
    config.save_local_config(config.LOCAL_CFG)
    got = read(config.CONFIG_PATH)
    check("stale dict does not wipe credentials", got.get("client_id") == "c2" and got.get("lyrics_mode") == "line", str(got))
finally:
    (config.CONFIG_PATH, config.LOCAL_CFG, config.CLIENT_ID, config.CLIENT_SECRET, config.REDIRECT_URI, config.CONFIG_LOADED) = old
    shutil.rmtree(d, ignore_errors=True)

ok = sum(1 for _, c, _ in RESULTS if c)
print(f"{ok}/{len(RESULTS)} checks passed")
sys.exit(0 if ok == len(RESULTS) else 1)
