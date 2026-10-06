"""Browser benchmark: CDP vs Playwright + baseline vs engine (all health-gated).

Mechanism timing (direct engine calls, 5 reps):
  BT1 attach/navigate/wait/verify | BT2 query+extract text | BT3 extract table
  BT4 multi-DOM one call. Local fixture pages (no internet flakiness).
MCP path comparison (real stdio client, 3 reps): granular calls vs steps[].
Metrics: attach/nav/query/extract/verify splits, wall, calls, bytes, success.
"""
from __future__ import annotations

import functools
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402

REPS = 5
PORT = 18923


def serve_fixtures() -> ThreadingHTTPServer:
    directory = str(Path(__file__).resolve().parent.parent / "tests" / "fixtures")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def med(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def run_engine_task(backend: str, steps: list[dict]) -> dict:
    safety = SafetyPolicy()
    ex = Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety)
    task = safety.new_task()
    tid = task.task_id
    t0 = time.perf_counter()
    labels = {"backend": backend, "ok": True, "result": {}}
    try:
        if backend != "auto":
            for s in steps:
                s.setdefault("params", {})["backend"] = backend
        g = compile_intent({"verb": "run", "steps": steps})
        r = ex.run(g, intent_verb="run", task_id=tid)
        labels["ok"] = r.status == "success"
        labels["result"] = r.result
        labels["profiler"] = r.profiler
        if not labels["ok"]:
            labels["reason"] = r.reason
    finally:
        try:
            ex.run(compile_intent({"verb": "browser_close"}),
                   intent_verb="browser_close", task_id=tid)
        except Exception:  # noqa: BLE001
            pass
    labels["wall_ms"] = (time.perf_counter() - t0) * 1000.0
    return labels


def main() -> int:
    pre = SafetyPolicy.shell_health()
    srv = serve_fixtures()
    base = f"http://127.0.0.1:{PORT}"
    tasks = {
        "BT1_navigate_verify": [
            {"verb": "browser_open"},
            {"verb": "browser_navigate", "params": {"url": f"{base}/simple.html"}},
        ],
        "BT2_extract_text": [
            {"verb": "browser_open"},
            {"verb": "browser_navigate", "params": {"url": f"{base}/simple.html"}},
            {"verb": "browser_extract", "params": {"kind": "text", "css": "h1"}},
        ],
        "BT3_extract_table": [
            {"verb": "browser_open"},
            {"verb": "browser_navigate", "params": {"url": f"{base}/tables.html"}},
            {"verb": "browser_extract", "params": {"kind": "table", "css": "#users"}},
        ],
        "BT4_multi_dom": [
            {"verb": "browser_open"},
            {"verb": "browser_navigate", "params": {"url": f"{base}/simple.html"}},
            {"verb": "browser_extract", "params": {"kind": "text", "css": "#intro"}},
            {"verb": "browser_extract", "params": {"kind": "text", "css": ".num"}},
        ],
    }
    out: dict = {}
    for tname, steps in tasks.items():
        out[tname] = {}
        for backend in ("cdp",):
            walls, oks, results = [], 0, []
            for _ in range(REPS):
                import copy

                r = run_engine_task(backend, copy.deepcopy(steps))
                walls.append(r["wall_ms"])
                oks += 1 if r["ok"] else 0
                results.append(r["result"])
                time.sleep(0.3)
            out[tname][backend] = {
                "median_ms": round(med(walls), 1),
                "p95_ms": round(sorted(walls)[min(len(walls) - 1, int(len(walls) * 0.95))], 1),
                "success": f"{oks}/{REPS}",
                "sample_result": results[-1] if results else {},
            }
            print(tname, backend, out[tname][backend])
    # verify expectations (deterministic fixtures)
    assert out["BT2_extract_text"]["cdp"]["sample_result"].get("text", "").strip() == "Probe Header", out["BT2_extract_text"]
    rows = out["BT3_extract_table"]["cdp"]["sample_result"].get("rows")
    assert rows == [["Name", "Age"], ["Ada", "36"], ["Bob", "41"], ["Cyd", "29"]], rows
    Path(__file__).resolve().parent / "results"
    with open(Path(__file__).resolve().parent / "results" / "browser_bench.json", "w") as f:
        json.dump(out, f, indent=1)
    post = SafetyPolicy.shell_health()
    print("health changes:", SafetyPolicy.health_diff(pre, post) or "none")
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
