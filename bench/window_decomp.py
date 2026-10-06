"""UIA window-op decomposition (Part 3): where does ~1.1s go?

Apps: Notepad (open/find/read), Explorer-sandbox (open/find), Settings
(URI open/find), Chrome (READ-ONLY find+title — never drive user's browser).
Modes: cold (first), warm (repeat), stale (close+reopen then find).
Spans from engine profiler: launch/wait/verify/find/read.
Bounded: max 2 windows per app, exact closes, sandbox only, health-gated.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402


def run(ex, verb, intent_extra=None, task_id="T"):
    intent = {"verb": verb}
    intent.update(intent_extra or {})
    t0 = time.perf_counter()
    r = ex.run(compile_intent(intent), intent_verb=verb, task_id=task_id)
    return r, (time.perf_counter() - t0) * 1000.0


def main() -> int:
    pre = SafetyPolicy.shell_health()
    safety = SafetyPolicy()
    ex = Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety)
    out: dict = {}

    def snap(label):
        by_op = {}
        for s in ex.profiler.spans:
            by_op.setdefault(s.operation, []).append(round(s.duration_ms, 1))
        out[label] = {k: {"n": len(v), "med": sorted(v)[len(v) // 2], "max": max(v)}
                       for k, v in by_op.items()}
        print(label, out[label])

    # Notepad cold open + find + read
    r, _ = run(ex, "open_app", {"app_hint": "Notepad"}, "D1")
    hwnd = (r.result or {}).get("hwnd", 0)
    run(ex, "find", {"app_hint": "Notepad", "target": {"control_type": "Edit"}}, "D1")
    run(ex, "find", {"app_hint": "Notepad", "target": {"control_type": "Edit"}}, "D1")
    snap("notepad_cold_warm")
    # stale: close + reopen + find
    import uiautomation as auto

    title = ""
    for c in auto.GetRootControl().GetChildren():
        try:
            if hwnd and int(c.NativeWindowHandle or 0) == int(hwnd):
                title = c.Name
        except Exception:  # noqa: BLE001
            continue
    run(ex, "close_window", {"target": {"title": title}, "params": {"hwnd": hwnd}}, "D1")
    r, _ = run(ex, "open_app", {"app_hint": "Notepad"}, "D2")
    hwnd2 = (r.result or {}).get("hwnd", 0)
    run(ex, "find", {"app_hint": "Notepad", "target": {"control_type": "Edit"}}, "D2")
    snap("notepad_stale_reopen")
    # multi-window: second notepad (max 2), find in ambiguous set by hwnd-owner? find by title
    rB, _ = run(ex, "open_app", {"app_hint": "Notepad"}, "D3")
    run(ex, "find", {"app_hint": "Notepad", "target": {"control_type": "Edit"}}, "D3")
    snap("notepad_multi")
    # Explorer sandbox open + find (allowed path only)
    sb = safety.sandbox
    rE, _ = run(ex, "open_app", {"app_hint": "Explorer", "params": {"path": sb}}, "D4")
    run(ex, "find", {"app_hint": "windows_mcp_test_sandbox",
                     "target": {"control_type": "Text"}}, "D4")
    snap("explorer_sandbox")
    # Settings URI open + find
    rS, _ = run(ex, "open_app", {"app_hint": "Settings"}, "D5")
    snap("settings_open")
    # Chrome READ-ONLY: find window + read title (no input, no focus change)
    t0 = time.perf_counter()
    w = ex.uia.find_window("Chrome", timeout_s=5)
    find_ms = (time.perf_counter() - t0) * 1000.0
    out["chrome_readonly"] = {"find_ms": round(find_ms, 1), "title": (w.Name or "")[:40]}
    print("chrome_readonly", out["chrome_readonly"])

    # cleanup: exact-close everything WE opened (owned hwnds), newest first
    for tid, h, t in (("D3", (rB.result or {}).get("hwnd", 0), None),
                      ("D2", hwnd2, None),
                      ("D4", (rE.result or {}).get("hwnd", 0), None),
                      ("D5", (rS.result or {}).get("hwnd", 0), None)):
        try:
            cur = ""
            for c in auto.GetRootControl().GetChildren():
                try:
                    if h and int(c.NativeWindowHandle or 0) == int(h):
                        cur = c.Name
                except Exception:  # noqa: BLE001
                    continue
            if cur:
                ex.run(compile_intent({"verb": "close_window", "target": {"title": cur},
                                       "params": {"hwnd": h}}),
                       intent_verb="close_window", task_id=tid)
        except Exception:  # noqa: BLE001
            continue
    # (Settings closed via owned hwnd in the loop above; no dangling windows)
    import json

    with open(Path(__file__).resolve().parent / "results" / "window_decomp.json", "w") as f:
        json.dump(out, f, indent=1)
    print("health changes:", SafetyPolicy.health_diff(pre, SafetyPolicy.shell_health()) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
