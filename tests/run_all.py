"""Run every suite in parallel.

Each suite drives a real Textual app, so running them one after another takes
over ten minutes. They are independent processes, so they can run at once — the
only thing they shared was the cache directory (token, playlist cache, config,
log), which every process now gets its own copy of via SPT_TUI_CACHE_DIR. That
also stops the suites reading and writing the user's real files, which they did
before.

    python tests/run_all.py               # all suites
    python tests/run_all.py -j 4          # cap the workers
    python tests/run_all.py -k playlist   # only suites matching a substring
    python tests/run_all.py -v            # print each failure's detail lines

Exit code is non-zero if any suite failed, so it works as a pre-commit gate.
"""
from __future__ import annotations

import os
import re
import sys
import time
import shutil
import argparse
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)

SUMMARY_RE = re.compile(r"(\d+)/(\d+) checks passed")
# Suites that take the longest, so they start first and do not become the tail
# every worker waits on. Unknown suites sort after these.
SLOWEST_FIRST = ("test_library_load.py", "test_search_grid.py", "test_table_fill.py",
                 "test_grid_panel_fill.py", "test_lyrics_cache_size.py",
                 "test_playlist_streaming.py", "test_tui_flows.py")


def discover(pattern: str | None):
    names = sorted(f for f in os.listdir(TESTS_DIR)
                   if f.startswith("test_") and f.endswith(".py"))
    if pattern:
        names = [n for n in names if pattern in n]

    def rank(n):
        return (SLOWEST_FIRST.index(n) if n in SLOWEST_FIRST else len(SLOWEST_FIRST), n)
    return sorted(names, key=rank)


def run_one(name: str, timeout: int):
    """Run one suite in its own process with its own cache directory."""
    path = os.path.join(TESTS_DIR, name)
    env = dict(os.environ)
    # Hermetic: never touch the user's real token / caches / log, and never race
    # another worker over them.
    tmp = tempfile.mkdtemp(prefix="spt_test_")
    env["SPT_TUI_CACHE_DIR"] = tmp
    # The suites print ♥ in their check names; the Windows console default
    # (cp1252) cannot encode it and the run dies on its own summary.
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    try:
        p = subprocess.run([sys.executable, path], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        code = p.returncode
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or "") if isinstance(e.stdout, str) else "") + \
              f"\n[runner] timed out after {timeout}s"
        code = -1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    m = SUMMARY_RE.search(out)
    passed, total = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    fails = [l.strip() for l in out.splitlines() if l.strip().startswith("FAIL:")]
    if not m and code != 0:
        fails = fails or ["suite did not report a summary (crashed on import or startup)"]
    return {"name": name, "passed": passed, "total": total, "ok": code == 0 and m is not None,
            "fails": fails, "secs": time.time() - started, "output": out}


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the suites in parallel.")
    ap.add_argument("-j", "--jobs", type=int, default=0,
                    help="worker processes (default: CPU count, capped at 8)")
    ap.add_argument("-k", "--filter", default=None, help="only suites whose name contains this")
    ap.add_argument("-t", "--timeout", type=int, default=600, help="per-suite timeout in seconds")
    ap.add_argument("-v", "--verbose", action="store_true", help="print full output of failures")
    args = ap.parse_args()

    names = discover(args.filter)
    if not names:
        print("no suites matched")
        return 1
    jobs = args.jobs or min(8, (os.cpu_count() or 4))
    print(f"running {len(names)} suites, {jobs} at a time\n")

    started = time.time()
    results = []
    # Threads, not processes: each worker only waits on a subprocess.
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_one, n, args.timeout): n for n in names}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            mark = "PASS" if r["ok"] else "FAIL"
            print(f"  {mark}  {r['name']:<38} {r['passed']}/{r['total']:<5} {r['secs']:5.1f}s")

    elapsed = time.time() - started
    results.sort(key=lambda r: r["name"])
    failed = [r for r in results if not r["ok"]]
    checks = sum(r["passed"] for r in results)
    checks_total = sum(r["total"] for r in results)
    serial = sum(r["secs"] for r in results)

    print("\n==== SUMMARY ====")
    print(f"{len(results) - len(failed)}/{len(results)} suites, "
          f"{checks}/{checks_total} checks passed")
    print(f"{elapsed:.1f}s wall clock ({serial:.1f}s of work, {serial / max(elapsed, 0.1):.1f}x)")

    if failed:
        print(f"\n{len(failed)} suite(s) failed:")
        for r in failed:
            print(f"\n  {r['name']}  ({r['passed']}/{r['total']})")
            for f in r["fails"][:10]:
                print(f"    {f}")
            if args.verbose:
                print("    --- full output ---")
                for line in r["output"].splitlines():
                    print(f"    {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
