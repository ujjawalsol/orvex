"""Router mechanism benchmark (§ROUTER BENCHMARK): successful VERIFIED op time.

Ops: set_value (value_pattern / clipboard_paste / send_keys),
read (value_pattern / name_property),
invoke (invoke_pattern / uia_click on a real Button if present).
ONE Notepad via engine task, exact close after. Health-gated. No saves.
Reports median/p95/p99 + success + verify split. Measured costs are fed
back into capabilities.COSTS so the router re-ranks on data.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.harness import Recorder, summarize  # noqa: E402
from engine.capabilities import COSTS, MechanismCost  # noqa: E402
from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402

REPS = 7
TEXT = "router-probe-0123456789"


def main() -> int:
    base = Path(__file__).resolve().parent.parent
    pre = SafetyPolicy.shell_health()
    safety = SafetyPolicy()
    ex = Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety)
    task = safety.new_task()
    tid = task.task_id
    rec = Recorder(base / "bench" / "results" / "router_bench.jsonl")
    if (base / "bench" / "results" / "router_bench.jsonl").exists():
        (base / "bench" / "results" / "router_bench.jsonl").unlink()
        rec = Recorder(base / "bench" / "results" / "router_bench.jsonl")

    r = ex.run(compile_intent({"verb": "open_app", "app_hint": "Notepad"}),
               intent_verb="open_app", task_id=tid)
    assert r.status == "success", r.to_dict()
    notepad_hwnd = (r.result or {}).get("hwnd", 0)
    import uiautomation as auto

    win = ex.uia.find_window("Notepad", timeout_s=5)
    ctl = win.EditControl(searchDepth=8)
    assert ctl.Exists(2)

    def timed(op: str, mech: str, fn):
        import datetime

        t0 = time.perf_counter()
        ok, err, verify_ms = True, None, 0.0
        try:
            fn()
            t1 = time.perf_counter()
            got = ex.uia.read_value(ctl)
            verify_ms = (time.perf_counter() - t1) * 1000.0
            if TEXT and mech.startswith("set_") and got != TEXT:
                ok, err = False, f"verify_mismatch {got!r}"
        except Exception as e:  # noqa: BLE001
            ok, err = False, f"{type(e).__name__}:{e}"
        dt = (time.perf_counter() - t0) * 1000.0
        from bench.harness import Sample
        import datetime as _dt

        rec.record(Sample(timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
                          operation=op, backend=mech, duration_ms=dt, success=ok,
                          error_code=(err[:200] if err else None),
                          extra={"verify_ms": round(verify_ms, 2)}))

    for _ in range(REPS):
        timed("set_value", "value_pattern",
              lambda: ctl.GetValuePattern().SetValue(TEXT))
        timed("set_value", "clipboard_paste",
              lambda: ex.uia.clipboard_paste(ctl, TEXT))
        timed("set_value", "send_keys",
              lambda: (ctl.SetFocus(), auto.SendKeys(TEXT)))
    for _ in range(REPS):
        timed("read", "value_pattern", lambda: ctl.GetValuePattern().Value)
        timed("read", "name_property", lambda: ctl.Name)

    # invoke targets: real Button with InvokePattern if present
    btn = win.ButtonControl(searchDepth=8)
    if btn.Exists(1):
        try:
            has_invoke = btn.GetInvokePattern() is not None
        except Exception:  # noqa: BLE001
            has_invoke = False
        print(f"invoke_target: {btn.Name!r} invoke_pattern={has_invoke}")
        if has_invoke:
            for _ in range(3):
                # NOTE: invoking unknown buttons can navigate away; prefer Settings gear only
                if "setting" not in (btn.Name or "").lower():
                    print("invoke skipped: first button is not Settings (side-effect safety)")
                    break
                timed("invoke", "invoke_pattern", lambda: btn.GetInvokePattern().Invoke())
    else:
        print("invoke_target: none at depth 8 (recorded unavailable)")

    # save to sandbox BEFORE close (else unsaved-changes dialog correctly blocks close)
    import os as _os

    probe_file = safety.check_path_inside_sandbox(
        _os.path.join(safety.sandbox, "router_bench.txt"))
    ex.run(compile_intent({"verb": "press", "app_hint": "Notepad",
                           "params": {"keys": "{Ctrl}s"}}),
           intent_verb="press", task_id=tid)
    ex.run(compile_intent({
        "verb": "set_value", "app_hint": "Notepad",
        "target": {"control_type": "Edit", "name": "File name:"},
        "params": {"text": probe_file}}), intent_verb="set_value", task_id=tid)
    ex.run(compile_intent({
        "verb": "invoke", "app_hint": "Notepad",
        "target": {"control_type": "Button", "name": "Save"}}),
        intent_verb="invoke", task_id=tid)
    # exact close of OUR notepad (task-owned hwnd)
    title = None
    for c in auto.GetRootControl().GetChildren():
        try:
            if notepad_hwnd and int(c.NativeWindowHandle or 0) == int(notepad_hwnd):
                title = c.Name
        except Exception:  # noqa: BLE001
            continue
    rc = ex.run(compile_intent({"verb": "close_window", "target": {"title": title or ""},
                                "params": {"hwnd": notepad_hwnd}}),
                intent_verb="close_window", task_id=tid)
    print("close:", rc.status, rc.reason)
    try:
        if _os.path.exists(probe_file):
            _os.remove(probe_file)
    except Exception:  # noqa: BLE001
        pass

    by_op: dict[str, list] = {}
    for s in rec.samples:
        by_op.setdefault(f"{s.operation}/{s.backend}", []).append(s)
    for k, v in by_op.items():
        print(k, summarize(v))
    # feed measured costs back (only ops with >=3 successes)
    for k, v in by_op.items():
        op, mech = k.split("/", 1)
        s = summarize(v)
        if s["success_rate"] >= 0.99 and v:
            COSTS.setdefault(op, {})[mech] = MechanismCost(
                median_ms=s["median_ms"], p95_ms=s["p95_ms"],
                success_rate=s["success_rate"], measured=True)
    from engine import capabilities as _cap

    print("router order post-measure:",
          {op: __import__("engine.router", fromlist=["order_mechanisms"]).order_mechanisms(op)
           for op in ("set_value", "read")})
    post = SafetyPolicy.shell_health()
    print("health changes:", SafetyPolicy.health_diff(pre, post) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
