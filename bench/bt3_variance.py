"""BT3 variance hunt: 20 reps x (CDP, Playwright), instrumented splits.

Splits: launch/open, navigate, wait, table-JS-evaluate (CDP) / selector query
(PW), serialization (rows->JSON), verification. Reports median/p95/p99/min/max
per split + per-rep totals to locate the variance source.
"""
from __future__ import annotations

import functools
import statistics
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.safety import SafetyPolicy  # noqa: E402

PORT = 18927
REPS = 20


def qs(xs: list[float]) -> dict:
    s = sorted(xs)
    n = len(s)
    return {"n": n, "median": s[n // 2] if n else 0, "p95": s[min(n - 1, int(n * 0.95))],
            "p99": s[min(n - 1, int(n * 0.99))], "min": s[0] if n else 0,
            "max": s[-1] if n else 0}


def run_cdp(url: str) -> dict:
    import tempfile
    import subprocess
    from engine.browser import CDP, discover_page_target

    t = {}
    t0 = time.perf_counter()
    profile = tempfile.mkdtemp(prefix="bt3-")
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    import socket as _sock

    _s = _sock.socket()
    _s.bind(("127.0.0.1", 0))
    port = _s.getsockname()[1]
    _s.close()
    proc = subprocess.Popen([chrome, f"--remote-debugging-port={port}",
                             f"--user-data-dir={profile}", "--no-first-run",
                             "--headless=new", "about:blank"])
    target = None
    t1 = time.perf_counter()
    while time.perf_counter() - t1 < 20:
        try:
            _, target = discover_page_target([port])
            break
        except LookupError:
            time.sleep(0.3)
    t["attach"] = (time.perf_counter() - t0) * 1000.0
    assert target, "no target"
    cdp = CDP(target["webSocketDebuggerUrl"])
    t1 = time.perf_counter()
    cdp.navigate(url)
    t["navigate"] = (time.perf_counter() - t1) * 1000.0
    t1 = time.perf_counter()
    rows = cdp.extract_table("#users")
    t["js_evaluate"] = (time.perf_counter() - t1) * 1000.0
    t1 = time.perf_counter()
    import json as _j

    blob = _j.dumps(rows)
    t["serialize"] = (time.perf_counter() - t1) * 1000.0
    assert len(rows) == 4, rows
    t["total"] = (time.perf_counter() - t0) * 1000.0
    cdp.close()
    proc.terminate()
    import shutil as _sh

    _sh.rmtree(profile, ignore_errors=True)
    return t


def run_pw(url: str) -> dict:
    # Historical benchmark only — Playwright is NOT a production dependency
    try:
        from tests.archive.browser_pw_legacy import PWSession
    except ImportError:
        return {"skipped": "playwright_not_installed"}

    t = {}
    t0 = time.perf_counter()
    s = PWSession()
    t["launch"] = s.launch_ms
    t1 = time.perf_counter()
    s.navigate(url)
    t["navigate"] = (time.perf_counter() - t1) * 1000.0
    t1 = time.perf_counter()
    rows = s.extract_table("#users")
    t["query_extract"] = (time.perf_counter() - t1) * 1000.0
    t1 = time.perf_counter()
    import json as _j

    _j.dumps(rows)
    t["serialize"] = (time.perf_counter() - t1) * 1000.0
    assert len(rows) == 4, rows
    t["total"] = (time.perf_counter() - t0) * 1000.0
    s.close()
    return t


def main() -> int:
    pre = SafetyPolicy.shell_health()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(
        SimpleHTTPRequestHandler,
        directory=str(Path(__file__).resolve().parent.parent / "tests" / "fixtures")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/tables.html"
    data: dict[str, dict[str, list]] = {"cdp": {}, "playwright": {}}
    for i in range(REPS):
        for be, fn in (("cdp", run_cdp), ("playwright", run_pw)):
            try:
                r = fn(url)
                for k, v in r.items():
                    data[be].setdefault(k, []).append(round(v, 1))
            except Exception as e:  # noqa: BLE001
                print(f"rep {i} {be} FAILED: {type(e).__name__}:{str(e)[:150]}")
            time.sleep(0.3)
    for be in ("cdp", "playwright"):
        print(f"== {be} ==")
        for k, v in data[be].items():
            print(f"  {k}: {qs(v)}")
    post = SafetyPolicy.shell_health()
    print("health changes:", SafetyPolicy.health_diff(pre, post) or "none")
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
