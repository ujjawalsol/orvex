"""SetValue before/after: library-default sleep vs waitTime=0 (same target/text/verify).

Proves the ~500ms was client-library sleep, not provider cost.
ONE Notepad, exact close, health-gated.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402

TEXT = "setvalue-probe-0123456789"


def set_raw(control, text, wait):
    pat = control.GetValuePattern()
    t0 = time.perf_counter()
    pat.SetValue(text, waitTime=wait)
    dt = (time.perf_counter() - t0) * 1000.0
    t1 = time.perf_counter()
    got = pat.Value
    vms = (time.perf_counter() - t1) * 1000.0
    return dt, vms, got == text


def main() -> int:
    pre = SafetyPolicy.shell_health()
    safety = SafetyPolicy()
    ex = Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety)
    task = safety.new_task()
    tid = task.task_id
    r = ex.run(compile_intent({"verb": "open_app", "app_hint": "Notepad"}),
               intent_verb="open_app", task_id=tid)
    assert r.status == "success", r.to_dict()
    hwnd = (r.result or {}).get("hwnd", 0)
    import uiautomation as auto

    win = ex.uia.find_window("Notepad", timeout_s=5)
    ctl = win.EditControl(searchDepth=8)
    assert ctl.Exists(2)
    rows = []
    for wait, label in ((0.5, "before_default_sleep"), (0.0, "after_wait0")):
        for _ in range(5):
            dt, vms, ok = set_raw(ctl, TEXT, wait)
            rows.append((label, round(dt, 1), round(vms, 1), ok))
    for row in rows:
        print(row)
    # engine path (routed, waitTime=0) end-to-end with verify
    t0 = time.perf_counter()
    r2 = ex.run(compile_intent({"verb": "set_value", "app_hint": "Notepad",
                                "target": {"control_type": "Edit"},
                                "params": {"text": TEXT}}),
                intent_verb="set_value", task_id=tid)
    print("engine_set_value:", r2.status, round((time.perf_counter() - t0) * 1000.0, 1),
          r2.result)
    # exact close (clean: save to sandbox first to avoid dialog)
    sb = safety.sandbox
    import os as _os

    tgt = safety.check_path_inside_sandbox(_os.path.join(sb, "sv_probe.txt"))
    ex.run(compile_intent({"verb": "press", "app_hint": "Notepad",
                           "params": {"keys": "{Ctrl}s"}}),
           intent_verb="press", task_id=tid)
    ex.run(compile_intent({"verb": "set_value", "app_hint": "Notepad",
                           "target": {"control_type": "Edit", "name": "File name:"},
                           "params": {"text": tgt}}), intent_verb="set_value", task_id=tid)
    ex.run(compile_intent({"verb": "invoke", "app_hint": "Notepad",
                           "target": {"control_type": "Button", "name": "Save"}}),
           intent_verb="invoke", task_id=tid)
    title = ""
    for c in auto.GetRootControl().GetChildren():
        try:
            if hwnd and int(c.NativeWindowHandle or 0) == int(hwnd):
                title = c.Name
        except Exception:  # noqa: BLE001
            continue
    rc = ex.run(compile_intent({"verb": "close_window", "target": {"title": title},
                                "params": {"hwnd": hwnd}}),
                intent_verb="close_window", task_id=tid)
    print("close:", rc.status, rc.reason)
    try:
        if _os.path.exists(tgt):
            _os.remove(tgt)
    except Exception:  # noqa: BLE001
        pass
    print("health changes:", SafetyPolicy.health_diff(pre, SafetyPolicy.shell_health()) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
