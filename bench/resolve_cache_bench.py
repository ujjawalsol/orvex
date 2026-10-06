"""Resolve-cache before/after: open + 3x set same Edit (same text/verify).

Compares SFMCP_NO_RESOLVE_CACHE=1 (before) vs cache on (after).
ONE Notepad, sandbox save+exact close, health-gated.
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


def flow(ex, tid, label):
    steps = [{"verb": "open_app", "app_hint": "Notepad"}] + [
        {"verb": "set_value", "app_hint": "Notepad",
         "target": {"control_type": "Edit"}, "params": {"text": f"cache-{label}-{i}"}}
        for i in range(3)
    ]
    t0 = time.perf_counter()
    r = ex.run(compile_intent({"verb": "run", "steps": steps}),
               intent_verb="run", task_id=tid)
    dt = (time.perf_counter() - t0) * 1000.0
    finds = [s for s in ex.profiler.spans if s.operation == "find"]
    print(label, r.status, f"{dt:.0f}ms", r.result)
    return dt, r.status == "success"


def main() -> int:
    pre = SafetyPolicy.shell_health()
    for mode in ("off", "on"):
        os.environ["SFMCP_NO_RESOLVE_CACHE"] = "1" if mode == "off" else "0"
        safety = SafetyPolicy()
        ex = Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety)
        task = safety.new_task()
        dt, ok = flow(ex, task.task_id, mode)
        # cleanup: save to sandbox + exact close
        sb = safety.sandbox
        tgt = safety.check_path_inside_sandbox(os.path.join(sb, f"rc_{mode}.txt"))
        ex.run(compile_intent({"verb": "press", "app_hint": "Notepad",
                               "params": {"keys": "{Ctrl}s"}}),
               intent_verb="press", task_id=task.task_id)
        ex.run(compile_intent({"verb": "set_value", "app_hint": "Notepad",
                               "target": {"control_type": "Edit", "name": "File name:"},
                               "params": {"text": tgt}}),
               intent_verb="set_value", task_id=task.task_id)
        ex.run(compile_intent({"verb": "invoke", "app_hint": "Notepad",
                               "target": {"control_type": "Button", "name": "Save"}}),
               intent_verb="invoke", task_id=task.task_id)
        import uiautomation as auto

        hwnd = title = 0
        for c in auto.GetRootControl().GetChildren():
            try:
                if "Notepad" in (c.Name or "") and "Window" in c.ControlTypeName:
                    hwnd, title = int(c.NativeWindowHandle or 0), c.Name
                    break
            except Exception:  # noqa: BLE001
                continue
        rc = ex.run(compile_intent({"verb": "close_window", "target": {"title": title},
                                    "params": {"hwnd": hwnd}}),
                    intent_verb="close_window", task_id=task.task_id)
        print(mode, "close:", rc.status, rc.reason)
        try:
            if os.path.exists(tgt):
                os.remove(tgt)
        except Exception:  # noqa: BLE001
            pass
    print("health changes:", SafetyPolicy.health_diff(pre, SafetyPolicy.shell_health()) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
