"""Check the API assumptions the offline suites can only fake. READ ONLY.

Every suite drives fakes written from the documentation, so they all agree with
each other by construction and would miss a wrong assumption about the real API.
This script asks Spotify directly, using the token already cached by the app,
and never writes anything: no playlist is created, modified or deleted, nothing
is saved or unsaved, playback is not touched.

    python tests/check_live_api.py            # uses your largest playlist
    python tests/check_live_api.py <id/uri>   # or a specific one

It is not part of run_all.py: it needs credentials and a network, so it is a
thing you run on purpose.
"""
from __future__ import annotations

import os
import sys

# Playlist names carry emoji; the Windows console default (cp1252) cannot encode
# them and the script would die printing its own header.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spt_tui.spotify_client import SpotifyClient

# The exact field mask the playlist loader uses (library.py). If Spotify ever
# stops returning `total` under it, the progressive path silently never engages.
FIELDS = ("items(added_at,track(id,uri,name,type,duration_ms,"
          "artists(name),album(name))),total")

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, (("  :: " + detail) if detail else ""))


def pick_playlist(c, arg=None):
    if arg:
        return arg.split(":")[-1].split("/")[-1].split("?")[0], "(given)"
    best, best_n, offset = None, -1, 0
    while True:
        page = c.user_playlists(limit=50, offset=offset) or {}
        items = page.get("items") or []
        for p in items:
            n = int(((p or {}).get("tracks") or {}).get("total") or 0)
            if n > best_n:
                best, best_n = p, n
        if not page.get("next"):
            break
        offset += 50
    if not best:
        return None, None
    return best.get("id"), f"{best.get('name')} ({best_n} tracks)"


def main() -> int:
    c = SpotifyClient()
    if not c.has_cached_token():
        print("No cached token. Run the app once and log in first.")
        return 2

    pl_id, label = pick_playlist(c, sys.argv[1] if len(sys.argv) > 1 else None)
    if not pl_id:
        print("No playlists on this account.")
        return 2
    print(f"checking against playlist: {label}\n")

    # --- the field mask still returns `total` ------------------------------ #
    first = c.playlist_items(pl_id, limit=100, offset=0, fields=FIELDS) or {}
    total = first.get("total")
    check("the restricted `fields=` mask still returns `total`", total is not None,
          f"keys={sorted(first.keys())}")
    n_first = len(first.get("items") or [])
    check("the first page comes back", n_first > 0, f"items={n_first}")

    items = first.get("items") or []
    sample = next((it for it in items if (it.get("track") or {}).get("id")), None)
    if sample:
        tr = sample.get("track") or {}
        check("every rendered field is present under the mask",
              all(k in tr for k in ("id", "uri", "name", "type", "duration_ms",
                                    "artists", "album")),
              f"got={sorted(tr.keys())}")
        check("added_at is present under the mask", "added_at" in sample,
              f"got={sorted(sample.keys())}")

    # --- paging really advances ------------------------------------------- #
    if total and int(total) > 100:
        second = c.playlist_items(pl_id, limit=100, offset=100, fields=FIELDS) or {}
        s_items = second.get("items") or []
        check("offset=100 returns a different page", bool(s_items) and
              (s_items[0].get("track") or {}).get("id") != (items[0].get("track") or {}).get("id"),
              f"second page items={len(s_items)}")
    else:
        print("SKIP - paging check (playlist is a single page)")

    # --- the liked lookup lines up with the ids we send -------------------- #
    ids = [(it.get("track") or {}).get("id") for it in items
           if (it.get("track") or {}).get("id")
           and ((it.get("track") or {}).get("type") or "track") == "track"]
    ids = ids[:60]                      # spans two batches (50 + rest)
    if ids:
        batches = []
        vals = c.check_saved_tracks(ids, on_batch=lambda s, v: batches.append((s, len(v))))
        check("check_saved_tracks returns one value per id", len(vals) == len(ids),
              f"sent={len(ids)} got={len(vals)}")
        size = SpotifyClient.SAVED_BATCH
        expected = [(i, len(ids[i:i + size])) for i in range(0, len(ids), size)]
        check(f"on_batch reports the batches in order (size {size})", batches == expected,
              f"batches={batches} expected={expected}")
        check("no batch exceeded what the endpoint accepts",
              all(n <= size for _, n in batches), f"batches={batches}")
        # Cross-check a single id against its batched answer: this is the
        # alignment the hearts depend on.
        probe = ids[-1]
        one = c.check_saved_tracks([probe])
        check("a single-id lookup agrees with the batched one",
              bool(one) and bool(one[0]) == bool(vals[-1]),
              f"single={one} batched={vals[-1]}")
    else:
        print("SKIP - liked lookup check (no track ids on the first page)")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n==== SUMMARY (live API) ====\n{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL:", name, "::", detail)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
