"""Session reuse benchmark (Phase 3 Part 1): fresh browser per task vs reuse.

N sequential BT2-equivalent tasks (navigate fixture + extract h1) on ONE
Executor. Fresh: open/navigate/extract/close per task. Reuse: open once,
tasks pass session handle, close at end. Reports launch_tax_removed and
speedup = fresh_total / reused_total (successful verified tasks only).
Health-gated. Local fixtures. No downloads.
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

from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402

PORT = 18925


def main() -> int:
    pre = SafetyPolicy.shell_health()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(
        SimpleHTTPRequestHandler,
        directory=str(Path(__file__).resolve().parent.parent / "tests" / "fixtures")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/simple.html"
    out: dict = {}
    for mode in ("fresh", "reuse"):
        for n in (1, 2, 5):
            safety = SafetyPolicy()
            ex = Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety)
            t0 = time.perf_counter()
            ok = True
            handle = ""
            for i in range(n):
                task = safety.new_task()
                tid = task.task_id
                if mode == "fresh" or not handle:
                    r = ex.run(compile_intent({"verb": "browser_open"}),
                               intent_verb="browser_open", task_id=tid)
                    if r.status != "success":
                        ok = False
                        break
                    handle = (r.result or {}).get("session", "")
                    if mode == "fresh":
                        pass
                g = compile_intent({"verb": "run", "steps": [
                    {"verb": "browser_navigate",
                     "params": {"url": url, "session": handle if mode == "reuse" else ""}},
                    {"verb": "browser_extract",
                     "params": {"kind": "text", "css": "h1",
                                "session": handle if mode == "reuse" else ""}},
                ]})
                # thread session via task default for fresh mode
                if mode == "fresh":
                    ex._task_browser[tid] = handle
                r = ex.run(g, intent_verb="run", task_id=tid)
                if r.status != "success" or (r.result or {}).get("text", "").strip() != "Probe Header":
                    ok = False
                    print("FAIL", mode, n, r.to_dict())
                    break
                if mode == "fresh":
                    ex.run(compile_intent({"verb": "browser_close",
                                           "params": {"session": handle}}),
                           intent_verb="browser_close", task_id=tid)
                    handle = ""
            if mode == "reuse" and handle:
                task = safety.new_task()
                ex.run(compile_intent({"verb": "browser_close",
                                       "params": {"session": handle}}),
                       intent_verb="browser_close", task_id=task.task_id)
            dt = (time.perf_counter() - t0) * 1000.0
            out[f"{mode}x{n}"] = {"total_ms": round(dt, 1), "ok": ok,
                                  "sessions": ex._sessions.stats()}
            print(mode, f"x{n}", out[f"{mode}x{n}"])
            time.sleep(0.5)
    for n in (1, 2, 5):
        f, r_ = out[f"freshx{n}"]["total_ms"], out[f"reusex{n}"]["total_ms"]
        print(f"x{n}: fresh={f} reuse={r_} tax_removed={round(f - r_, 1)} "
              f"speedup={round(f / max(r_, 0.01), 2)}")
    print("health changes:", SafetyPolicy.health_diff(pre, SafetyPolicy.shell_health()) or "none")
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
