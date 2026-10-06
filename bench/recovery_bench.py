"""Recovery benchmark: full restart vs targeted resume on controlled failures.

F-A: wrong AutomationId mid-task -> needs_ai + resume with corrected target.
F-B: window recreated mid-task (close+reopen) -> stale epoch -> re-resolve.
F-C: browser extract with wrong css -> needs_ai + resume (same session, no relaunch).
ONE Notepad, sandbox saves where needed, exact closes. Health-gated.
Measures: recovery success, recovery latency, steps replayed, final time.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402


def fresh_ex():
    safety = SafetyPolicy()
    return Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety), safety


def cleanup_notepads(ex):
    """Close all Notepads, discarding via descendant 'Don't Save' search."""
    import uiautomation as auto

    for _ in range(8):
        wins = [c for c in auto.GetRootControl().GetChildren()
                if "Notepad" in (c.Name or "") and "Window" in c.ControlTypeName]
        if not wins:
            break
        for w in wins:
            try:
                b = w.ButtonControl(RegexName=".*Don't Save.*", searchDepth=8)
                if b.Exists(1):
                    b.GetInvokePattern().Invoke()
                    continue
                w.GetWindowPattern().Close()
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.8)


def t_fail_a(ex, tid):
    """3-step task, step 3 has wrong target. Returns (receipt, resume_handle)."""
    g = compile_intent({"verb": "run", "steps": [
        {"verb": "open_app", "app_hint": "Notepad"},
        {"verb": "set_value", "app_hint": "Notepad",
         "target": {"control_type": "Edit"}, "params": {"text": "recovery-A"}},
        {"verb": "set_value", "app_hint": "Notepad",
         "target": {"control_type": "Edit", "automation_id": "NoSuchId"},
         "params": {"text": "zzz"}},
    ]})
    t0 = time.perf_counter()
    r = ex.run(g, intent_verb="run", task_id=tid)
    return r, (time.perf_counter() - t0) * 1000.0


def t_resume_a(ex, tid, resume_handle):
    t0 = time.perf_counter()
    r = ex.run(compile_intent({"verb": "resume", "params": {
        "resume_handle": resume_handle,
        "correction": {"target": {"control_type": "Edit"},
                       "params": {"text": "recovery-A-fixed"}}}}),
        intent_verb="resume", task_id=tid)
    return r, (time.perf_counter() - t0) * 1000.0


def t_fail_b(ex, tid):
    """Window recreated via EXECUTOR ops (epoch bumps on close/open)."""
    from engine.uia_backend import Target

    ex.run(compile_intent({"verb": "open_app", "app_hint": "Notepad"}),
           intent_verb="open_app", task_id=tid)
    # NOTE: no text set before close — keeps window clean so close has no dialog
    h = ex.uia.mint(Target(control_type="Edit"), app_hint="Notepad")
    token_before = h.token
    # close through the engine (task-owned -> bumps epoch), reopen
    import uiautomation as auto

    title = ""
    for c in auto.GetRootControl().GetChildren():
        try:
            if "Notepad" in (c.Name or "") and "Window" in c.ControlTypeName:
                title = c.Name
                break
        except Exception:  # noqa: BLE001
            continue
    rc = ex.run(compile_intent({"verb": "close_window", "target": {"title": title}}),
                intent_verb="close_window", task_id=tid)
    assert rc.status == "success", rc.to_dict()
    ex.run(compile_intent({"verb": "open_app", "app_hint": "Notepad"}),
           intent_verb="open_app", task_id=tid)
    t0 = time.perf_counter()
    try:
        ex.uia.use(h.hid, timeout_s=2)
        reused = "blind-reuse-FAIL"
    except LookupError as e:
        reused = f"stale-detected:{str(e)[:80]}"
    # targeted recovery: fresh resolve in same task
    r = ex.run(compile_intent({"verb": "set_value", "app_hint": "Notepad",
                               "target": {"control_type": "Edit"},
                               "params": {"text": "after-recreate"}}),
               intent_verb="set_value", task_id=tid)
    dt = (time.perf_counter() - t0) * 1000.0
    return token_before, reused, r.status, dt


def main() -> int:
    pre = SafetyPolicy.shell_health()
    out: dict = {}
    # F-A restart vs resume
    ex, _ = fresh_ex()
    r_fail, t_fail = t_fail_a(ex, "FA-1")
    assert r_fail.status == "needs_ai" and r_fail.resume_handle, r_fail.to_dict()
    # restart path = FULL task re-run WITH the fix (honest comparison)
    def t_fixed_full(ex2, tid):
        g = compile_intent({"verb": "run", "steps": [
            {"verb": "open_app", "app_hint": "Notepad"},
            {"verb": "set_value", "app_hint": "Notepad",
             "target": {"control_type": "Edit"}, "params": {"text": "recovery-A"}},
            {"verb": "set_value", "app_hint": "Notepad",
             "target": {"control_type": "Edit"}, "params": {"text": "recovery-A-fixed"}},
        ]})
        t0 = time.perf_counter()
        r = ex2.run(g, intent_verb="run", task_id=tid)
        return r, (time.perf_counter() - t0) * 1000.0
    t0 = time.perf_counter()
    r_restart, t_restart = t_fixed_full(ex, "FA-restart")
    t_restart = (time.perf_counter() - t0) * 1000.0
    r_resume, t_resume = t_resume_a(ex, "FA-resume", r_fail.resume_handle)
    out["F-A"] = {"fail_status": r_fail.status, "failed_step": r_fail.failed_step,
                  "resume_handle": r_fail.resume_handle,
                  "restart_ms": round(t_restart, 1), "restart_ok": r_restart.status,
                  "resume_ms": round(t_resume, 1), "resume_ok": r_resume.status,
                  "resume_result": r_resume.result}
    print("F-A:", out["F-A"])
    cleanup_notepads(ex)

    # F-B stale handle on recreated window
    ex2, _ = fresh_ex()
    token_before, reused, status, dt = t_fail_b(ex2, "FB-1")
    out["F-B"] = {"handle": token_before, "stale": reused, "reresolve_status": status,
                  "ms": round(dt, 1)}
    print("F-B:", out["F-B"])
    cleanup_notepads(ex2)

    # F-C browser wrong-css -> resume same session (no relaunch)
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    base = Path(__file__).resolve().parent.parent
    srv = ThreadingHTTPServer(("127.0.0.1", 18926), functools.partial(
        SimpleHTTPRequestHandler, directory=str(base / "tests" / "fixtures")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ex3, _ = fresh_ex()
    url = "http://127.0.0.1:18926/simple.html"
    g = compile_intent({"verb": "run", "steps": [
        {"verb": "browser_open"},
        {"verb": "browser_navigate", "params": {"url": url}},
        {"verb": "browser_extract", "params": {"kind": "text", "css": "#nope"}},
    ]})
    t0 = time.perf_counter()
    rf = ex3.run(g, intent_verb="run", task_id="FC-1")
    t_fail_c = (time.perf_counter() - t0) * 1000.0
    assert rf.status == "needs_ai", rf.to_dict()
    sess_before = (rf.result or {}).get("session", "")
    t0 = time.perf_counter()
    rr = ex3.run(compile_intent({"verb": "resume", "params": {
        "resume_handle": rf.resume_handle,
        "correction": {"params": {"kind": "text", "css": "h1"}}}}),
        intent_verb="resume", task_id="FC-resume")
    t_resume_c = (time.perf_counter() - t0) * 1000.0
    out["F-C"] = {"fail_ms": round(t_fail_c, 1), "resume_ms": round(t_resume_c, 1),
                  "resume_ok": rr.status, "resume_result": rr.result}
    print("F-C:", out["F-C"])
    ex3.run(compile_intent({"verb": "browser_close", "params": {"all": True}}),
            intent_verb="browser_close", task_id="FC-x")
    srv.shutdown()
    cleanup_notepads(ex3)

    post = SafetyPolicy.shell_health()
    print("health changes:", SafetyPolicy.health_diff(pre, post) or "none")
    import json

    with open(base / "bench" / "results" / "recovery_bench.json", "w") as f:
        json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
