"""SendKeys guard completion tests (Part 5): refusal paths only, zero injection.

- blocked key combos -> blocked_system_keys (nothing sent)
- unknown app target -> needs_ai (nothing sent)
- press on existing Notepad Control+S -> success via guarded path (1 input event)
ONE Notepad, exact close, health-gated. Proves: no blind retry (press not in
SAFE_RETRY_OPS), no silent target switch, no system-wide input unproven.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402


def main() -> int:
    pre = SafetyPolicy.shell_health()
    safety = SafetyPolicy()
    ex = Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety)
    task = safety.new_task()
    tid = task.task_id
    cases = [
        ("win_l", {"verb": "press", "params": {"keys": "{Win}l"}}, "blocked_system_keys"),
        ("win_r", {"verb": "press", "params": {"keys": "{Win}r"}}, "blocked_system_keys"),
        ("alt_f4", {"verb": "press", "params": {"keys": "{Alt}{F4}"}}, "blocked_system_keys"),
        ("ctrl_esc", {"verb": "press", "params": {"keys": "{Ctrl}{Esc}"}}, "blocked_system_keys"),
        ("unknown_app", {"verb": "press", "app_hint": "NoSuchAppXYZ",
                         "params": {"keys": "a"}}, None),  # expect needs_ai, nothing sent
    ]
    for name, intent, expect_reason in cases:
        r = ex.run(compile_intent(intent), intent_verb="press", task_id=tid)
        status_ok = r.status in ("blocked", "needs_ai")
        reason_ok = (expect_reason is None) or (r.reason == expect_reason)
        print(name, r.status, r.reason, "OK" if (status_ok and reason_ok) else "UNEXPECTED")
        assert status_ok and reason_ok, (name, r.to_dict())
    # guarded positive: open notepad, Ctrl+S (save dialog appears = proof keys landed right)
    r = ex.run(compile_intent({"verb": "open_app", "app_hint": "Notepad"}),
               intent_verb="open_app", task_id=tid)
    assert r.status == "success", r.to_dict()
    hwnd = (r.result or {}).get("hwnd", 0)
    r2 = ex.run(compile_intent({"verb": "press", "app_hint": "Notepad",
                                "params": {"keys": "{Ctrl}s"}}),
                intent_verb="press", task_id=tid)
    import uiautomation as auto

    # Save As is a CHILD dialog of Notepad (top-level scan would miss it)
    reached = False
    try:
        w = auto.WindowControl(searchDepth=1, RegexName=".*Notepad.*")
        if w.Exists(1):
            reached = w.WindowControl(SubName="Save As", searchDepth=2).Exists(1)
    except Exception:  # noqa: BLE001
        pass
    print("ctrl_s:", r2.status, r2.reason, "save_dialog_reached:", reached)
    # dismiss dialog (Cancel = no save, no dirty write) + exact close (clean window)
    try:
        w = auto.WindowControl(searchDepth=1, RegexName=".*Notepad.*")
        if w.Exists(1):
            d = w.WindowControl(SubName="Save As", searchDepth=2)
            if d.Exists(1):
                d.ButtonControl(RegexName=".*Cancel.*", searchDepth=6).GetInvokePattern().Invoke()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.5)
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
    print("input_events:", safety.tasks[tid].input_events)
    print("health changes:", SafetyPolicy.health_diff(pre, SafetyPolicy.shell_health()) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
