"""Safety Smoke Test (§17): the ONLY test allowed in Phase 2 validation.

SAFE class only (§16): inspect/get/set_value/invoke/read, ONE Notepad, ONE
Explorer pointed at the sandbox, exact-handle close, shell health before/after.
Max 3 reps. No parallel windows. No system-wide injection beyond dialog
Confirm (pattern invoke). Abort on shell_health_changed — no auto-recovery.

Usage: python tests/test_safety_smoke.py [--reps 1]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402

SAFE = "SAFE"

counters = {"windows_created": 0, "windows_closed": 0, "processes_launched": 0,
            "files_created": 0, "files_deleted": 0, "system_inputs": 0}


def step(name: str, ok: bool, detail: object = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return ok


def run_once(rep: int, safety: SafetyPolicy) -> bool:
    ex = Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety)
    task = safety.new_task()
    tid = task.task_id
    ok_all = True
    health_before = SafetyPolicy.shell_health()
    print(f"PRE health: {health_before} task={tid}")

    # 1. sandbox (auto-created by SafetyPolicy)
    sb = safety.sandbox
    ok_all &= step("sandbox", os.path.isdir(sb), sb)

    # 2-3. ONE Notepad, exact window (ownership tracked on this task record)
    r = ex.run(compile_intent({"verb": "open_app", "app_hint": "Notepad"}), intent_verb="open_app",
               task_id=tid)
    ok_all &= step("open-notepad", r.status == "success", r.to_dict())
    if r.status != "success":
        return False
    counters["windows_created"] += 1
    counters["processes_launched"] += 1
    hwnd = (r.result or {}).get("hwnd", 0)

    # 4-5. UIA set + verify (pattern path, no injection)
    target_file = safety.check_path_inside_sandbox(os.path.join(sb, f"smoke_{rep}.txt"))
    r2 = ex.run(compile_intent({
        "verb": "set_value", "app_hint": "Notepad",
        "target": {"control_type": "Edit"}, "params": {"text": "safety-smoke"}}),
        intent_verb="set_value", task_id=tid)
    ok_all &= step("set-verify", r2.status == "success" and (r2.result or {}).get("set_via") == "value_pattern",
                   r2.to_dict())

    # 6-7. save inside sandbox ONLY (dialog filename + Save invoke, no blind Enter)
    ex.run(compile_intent({"verb": "press", "app_hint": "Notepad", "params": {"keys": "{Ctrl}s"}}),
           intent_verb="press", task_id=tid)
    counters["system_inputs"] += 1
    r3 = ex.run(compile_intent({
        "verb": "set_value", "app_hint": "Notepad",
        "target": {"control_type": "Edit", "name": "File name:"}, "params": {"text": target_file}}),
        intent_verb="set_value", task_id=tid)
    ok_all &= step("save-dialog", r3.status == "success", (r3.status, r3.reason))
    r4 = ex.run(compile_intent({
        "verb": "invoke", "app_hint": "Notepad",
        "target": {"control_type": "Button", "name": "Save"}}), intent_verb="invoke", task_id=tid)
    ok_all &= step("save-confirm", r4.status == "success", (r4.status, r4.reason))
    time.sleep(1.0)
    exists = os.path.exists(target_file)
    ok_all &= step("file-verify", exists, target_file)
    if exists:
        counters["files_created"] += 1

    # 8-9. close EXACT notepad. Negative probe first (must refuse), then real close.
    # Ownership via task-recorded PID works even if HWND capture failed.
    rNeg = ex.run(compile_intent({
        "verb": "close_window", "target": {"title": "no-such-window-xyz"}}),
        intent_verb="close_window", task_id=tid)
    ok_all &= step("close-refuses-unknown", rNeg.status in ("blocked", "needs_ai", "failed"),
                   (rNeg.status, rNeg.reason))
    exact_title = f"smoke_{rep} - Notepad"
    r5 = ex.run(compile_intent({
        "verb": "close_window", "target": {"title": exact_title},
        "params": {"hwnd": hwnd}}), intent_verb="close_window", task_id=tid)
    ok_all &= step("close-exact", r5.status == "success", r5.to_dict())
    if r5.status == "success":
        counters["windows_closed"] += 1

    # 10-12. ONE Explorer at sandbox via ENGINE (task-owned -> engine close works)
    rE = ex.run(compile_intent({
        "verb": "open_app", "app_hint": "Explorer", "params": {"path": sb}}),
        intent_verb="open_app", task_id=tid)
    ok_all &= step("explorer-sandbox", rE.status == "success", rE.to_dict())
    exp_hwnd = (rE.result or {}).get("hwnd", 0) if rE.status == "success" else 0
    if not exp_hwnd:
        # fallback: locate sandbox-titled window (engine still owns via pid record)
        import uiautomation as auto

        sb_title = os.path.basename(sb)
        for c in auto.GetRootControl().GetChildren():
            try:
                if (c.Name or "").endswith(sb_title) and "Window" in c.ControlTypeName:
                    exp_hwnd = int(c.NativeWindowHandle or 0)
            except Exception:  # noqa: BLE001
                continue
    counters["windows_created"] += 1
    if exp_hwnd:
        sb_title = os.path.basename(sb)
        r6 = ex.run(compile_intent({
            "verb": "close_window", "target": {"title": sb_title},
            "params": {"hwnd": exp_hwnd}}), intent_verb="close_window", task_id=tid)
        # engine-owned close must succeed; refusal means ownership tracking broke
        ok_all &= step("close-explorer-exact", r6.status == "success", r6.to_dict())
        if r6.status == "success":
            counters["windows_closed"] += 1

    # 13-16. cleanup sandbox file + health checks
    try:
        from _cleanup import cleanup_test_windows

        cleanup_test_windows()
    except Exception as e:  # noqa: BLE001
        print("window cleanup skipped:", e)
    try:
        if os.path.exists(target_file):
            os.remove(target_file)
            counters["files_deleted"] += 1
    except Exception:  # noqa: BLE001
        pass
    health_after = SafetyPolicy.shell_health()
    print(f"POST health: {health_after}")
    changes = SafetyPolicy.health_diff(health_before, health_after)
    ok_all &= step("shell-health", not changes, changes or "unchanged")
    if changes:
        print("shell_health_changed — STOPPING, no recovery attempted")
        return False
    return ok_all


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1)
    args = ap.parse_args()
    if args.reps > 3:
        print("refused: max 3 reps in Phase 2")
        return 2
    safety = SafetyPolicy()
    print("policy:", safety.dump())
    results = []
    for rep in range(args.reps):
        before = SafetyPolicy.shell_health()
        ok = run_once(rep, safety)
        after = SafetyPolicy.shell_health()
        if SafetyPolicy.health_diff(before, after):
            print("ABORT: shell health changed across rep — stopping")
            return 1
        results.append(ok)
    print("counters:", counters)
    print(f"SMOKE passed={sum(results)}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
