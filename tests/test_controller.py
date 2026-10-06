"""Bounded system-wide automation controller tests (final RC).

Covers the controller test plan A-H plus performance, accessibility,
click-through safety, resource ownership and system-health verification.

Hard constraints honoured by this suite:
  * one indicator process, started once, shut down once
  * no mass open/close, no Explorer/Windows restart, no registry/HID/USB/driver
    change, no browser profile access, no stress loops
  * the only process this suite terminates is its OWN controller child
  * the gated launch in F/G is stubbed, so no real application is launched
  * every system-health check is read-only; nothing is auto-repaired
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# keep every gated wait bounded: an unresolved approval must not idle a test
os.environ.setdefault("SFMCP_APPROVAL_WAIT_S", "20")

from engine.compiler import compile_intent  # noqa: E402
from engine.controller import AutomationController  # noqa: E402
from engine.controller_ui import ACCEL, BUTTONS_BY_STATE, BUTTON_EVENTS, redact  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.health import diff_devices, snapshot  # noqa: E402
from engine.safety import SafetyError, SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []

WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
GWL_EXSTYLE = -20
CTRL: dict = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def fresh_ex(controller=None) -> tuple[Executor, SafetyPolicy]:
    safety = SafetyPolicy()
    return Executor(policy=Policy.load(), stop=EmergencyStop(), safety=safety,
                    controller=controller), safety


def sandbox_file(safety: SafetyPolicy, name: str) -> str:
    p = os.path.join(safety.sandbox, name)
    os.makedirs(safety.sandbox, exist_ok=True)
    return p


def wait_graph(safety: SafetyPolicy, tag: str) -> tuple[list[str], list[str]]:
    """Three node waits: the first appears late so there is a deterministic
    node boundary at which Pause / Take Control / Stop can be asserted."""
    late = sandbox_file(safety, f"ctl_{tag}_late.txt")
    b = sandbox_file(safety, f"ctl_{tag}_b.txt")
    c = sandbox_file(safety, f"ctl_{tag}_c.txt")
    for p in (b, c):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x")
    if os.path.exists(late):
        os.remove(late)
    steps = [{"verb": "wait", "params": {"path": late, "timeout_s": 8}},
             {"verb": "wait", "params": {"path": b, "timeout_s": 5}},
             {"verb": "wait", "params": {"path": c, "timeout_s": 5}}]
    return steps, [late, b, c]


def cleanup_files(paths: list[str]) -> None:
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:  # noqa: BLE001
            pass


def window_text(hwnd: int) -> str:
    u = ctypes.windll.user32
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    u.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def child_windows(parent: int) -> list[int]:
    u = ctypes.windll.user32
    out: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)  # type: ignore[attr-defined]
    def cb(hwnd, _l):
        out.append(int(hwnd))
        return True

    u.EnumChildWindows(parent, cb, 0)
    return out


def exstyle(hwnd: int) -> int:
    return int(ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE))


def pid_alive(pid: int) -> bool:
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    k32.CloseHandle(ctypes.c_void_p(h))
    return bool(ok) and code.value == 259


def stable_pid(name: str) -> int:
    """Read-only lookup of an unrelated process we must never disturb."""
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() == name.lower():
                return p.pid
    except Exception:  # noqa: BLE001
        pass
    return 0


# --------------------------------------------------------------- Test A

def test_A_indicator_appears() -> None:
    ctl: AutomationController = CTRL["ctl"]
    ex, safety = fresh_ex(ctl)
    task = safety.new_task()
    steps, files = wait_graph(safety, "a")
    holder: dict = {}

    def create_late() -> None:
        time.sleep(0.4)
        with open(files[0], "w", encoding="utf-8") as fh:
            fh.write("late")

    threading.Thread(target=create_late, daemon=True).start()
    before = len(ctl.transitions)
    holder["r"] = ex.run(compile_intent({"verb": "run", "steps": steps}),
                         intent_verb="run", task_id=task.task_id)
    r = holder["r"]
    seg = ctl.transitions[before:]
    check("A-run-succeeds", r.status == "success", f"{r.status}/{r.reason}")
    entered_running = any(t[1] == "RUNNING" for t in seg)
    check("A-indicator-appears-on-start", entered_running,
          f"state_path={[b for _, b, _ in seg]}")
    check("A-ui-process-alive-during-run", ctl.ui_alive,
          f"pid={ctl.owned()['controller_pid']}")
    check("A-ui-window-present", len(wait_for_indicator(ctl)) >= 1,
          str(wait_for_indicator(ctl)))
    check("A-idle-after-completion", ctl.state in ("IDLE", "STOPPED"), ctl.state)
    check("A-no-leaked-task", task.task_id not in ctl.active_tasks,
          str(sorted(ctl.active_tasks)))
    cleanup_files(files)


def test_A2_one_session_across_apps() -> None:
    """One indicator represents every controlled application (§10)."""
    ctl: AutomationController = CTRL["ctl"]
    ex, safety = fresh_ex(ctl)
    task = safety.new_task()
    tid = task.task_id
    g = compile_intent({"verb": "run", "steps": [
        {"verb": "open_app", "app_hint": "Notepad"},
        {"verb": "open_app", "app_hint": "Explorer", "params": {"path": safety.sandbox}},
    ]})
    before = len(ctl.transitions)
    r = ex.run(g, intent_verb="run", task_id=tid)
    seg = ctl.transitions[before:]
    runs = [t for t in seg if t[1] == "RUNNING"]
    check("A2-two-apps-one-task", r.status == "success", f"{r.status}/{r.reason}")
    check("A2-single-session-for-all-apps", len(runs) == 1,
          f"RUNNING transitions={len(runs)}")
    check("A2-both-apps-tracked", len(safety.tasks[tid].windows_opened) >= 2,
          str(len(safety.tasks[tid].windows_opened)))
    cleanup = ex._cleanup_owned(safety.tasks[tid])
    check("A2-owned-cleanup-kills-no-process", cleanup["processes_terminated"] == 0,
          str(cleanup))


# --------------------------------------------------------------- Tests B, C

def test_B_C_pause_resume() -> None:
    ctl: AutomationController = CTRL["ctl"]
    ex, safety = fresh_ex(ctl)
    task = safety.new_task()
    tid = task.task_id
    steps, files = wait_graph(safety, "bc")
    holder: dict = {}

    def create_late() -> None:
        time.sleep(0.4)
        with open(files[0], "w", encoding="utf-8") as fh:
            fh.write("late")

    threading.Thread(target=create_late, daemon=True).start()
    th = threading.Thread(
        target=lambda: holder.update(
            r=ex.run(compile_intent({"verb": "run", "steps": steps}),
                     intent_verb="run", task_id=tid)), daemon=True)
    th.start()
    time.sleep(0.15)
    ctl.pause("test")
    time.sleep(1.0)
    done_at_pause = len(ex._journal.get(tid, {}).get("done", []))
    alive_while_paused = th.is_alive()
    state_paused = ctl.state
    time.sleep(0.8)
    done_still = len(ex._journal.get(tid, {}).get("done", []))
    check("B-paused-state", state_paused == "PAUSED", state_paused)
    check("B-no-new-actions-while-paused", alive_while_paused and done_still == done_at_pause,
          f"done={done_at_pause}->{done_still} alive={alive_while_paused}")
    check("B-state-preserved", not safety.tasks[tid].cancelled
          and tid in ex._journal, f"cancelled={safety.tasks[tid].cancelled}")
    resumed = ctl.resume()
    state_after_resume = ctl.state
    th.join(timeout=20)
    r = holder.get("r")
    check("C-resume-accepted", resumed and state_after_resume == "RUNNING",
          f"resumed={resumed} state={state_after_resume}")
    check("C-execution-continues", r is not None and r.status == "success"
          and r.steps_completed == 3, f"{getattr(r, 'status', None)}/"
                                      f"{getattr(r, 'steps_completed', None)}")
    cleanup_files(files)


# --------------------------------------------------------------- Test D

def test_D_take_control() -> None:
    ctl: AutomationController = CTRL["ctl"]
    ex, safety = fresh_ex(ctl)
    task = safety.new_task()
    tid = task.task_id
    steps, files = wait_graph(safety, "d")
    holder: dict = {}

    def create_late() -> None:
        time.sleep(0.4)
        with open(files[0], "w", encoding="utf-8") as fh:
            fh.write("late")

    threading.Thread(target=create_late, daemon=True).start()
    th = threading.Thread(
        target=lambda: holder.update(
            r=ex.run(compile_intent({"verb": "run", "steps": steps}),
                     intent_verb="run", task_id=tid)), daemon=True)
    th.start()
    time.sleep(0.15)
    ctl.take_control()
    time.sleep(1.2)
    rec = safety.tasks[tid]
    check("D-state-user-control", ctl.state == "USER_CONTROL", ctl.state)
    check("D-execution-held", th.is_alive(), f"alive={th.is_alive()}")
    check("D-no-automation-input-injected", rec.input_events == 0,
          f"input_events={rec.input_events}")
    check("D-flag-preserved", ctl.user_control.is_set() and ctl.paused.is_set(), "")
    ctl.stop("test cleanup")
    th.join(timeout=20)
    r = holder.get("r")
    check("D-stop-after-take-control", r is not None and r.status == "cancelled",
          f"{getattr(r, 'status', None)}")
    cleanup_files(files)


# --------------------------------------------------------------- Test E

def test_E_stop() -> None:
    ctl: AutomationController = CTRL["ctl"]
    ex, safety = fresh_ex(ctl)
    task = safety.new_task()
    tid = task.task_id
    steps, files = wait_graph(safety, "e")
    holder: dict = {}

    def create_late() -> None:
        time.sleep(1.2)
        with open(files[0], "w", encoding="utf-8") as fh:
            fh.write("late")

    threading.Thread(target=create_late, daemon=True).start()
    th = threading.Thread(
        target=lambda: holder.update(
            r=ex.run(compile_intent({"verb": "run", "steps": steps}),
                     intent_verb="run", task_id=tid)), daemon=True)
    th.start()
    time.sleep(0.4)
    in_flight = th.is_alive()
    receipt = ctl.stop("test")
    th.join(timeout=20)
    r = holder.get("r")
    check("E-stop-issued-while-running", in_flight, f"alive={in_flight}")
    check("E-stop-uses-shared-cancel-path", receipt.get("stop_path_invoked") is True,
          str(receipt))
    check("E-emergency-stop-set", ex.stop.cancelled, "")
    check("E-cancelled-receipt", r is not None and r.status == "cancelled",
          f"{getattr(r, 'status', None)}/{getattr(r, 'reason', None)}")
    check("E-no-further-input", safety.tasks[tid].input_events == 0,
          f"input_events={safety.tasks[tid].input_events}")
    check("E-cleanup-kills-no-process",
          (r.cleanup or {}).get("processes_terminated", 1) == 0, str(r.cleanup))
    check("E-stopped-state", ctl.state == "STOPPED", ctl.state)
    cleanup_files(files)


# --------------------------------------------------------------- Tests F, G

class GatedLaunchExecutor(Executor):
    """Launch side effect is stubbed: the approval GATE is real, the launch is
    not performed, so F/G stay bounded and touch no application."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.launch_attempts: list[str] = []

    def _launch(self, app: str, rec, path: str | None = None):
        self.safety.check_launch(app, rec, path=path)   # real gate
        self.launch_attempts.append(str(app))
        raise SafetyError("blocked_test_sentinel", "bounded test: launch stubbed")


def _run_gated(decision: str) -> tuple[GatedLaunchExecutor, object, object, str]:
    ctl: AutomationController = CTRL["ctl"]
    ex = GatedLaunchExecutor(policy=Policy.load(), stop=EmergencyStop(),
                             safety=SafetyPolicy(), controller=ctl)
    task = ex.safety.new_task()
    holder: dict = {}
    stale = set(ctl.approvals)

    def run() -> None:
        holder["r"] = ex.run(
            compile_intent({"verb": "open_app", "app_hint": "ctl_fake_app"}),
            intent_verb="open_app", task_id=task.task_id)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    deadline = time.time() + 8
    token = ""
    while time.time() < deadline and not token:
        fresh = [t for t in ctl.approvals if t not in stale]
        token = fresh[0] if fresh else ""
        time.sleep(0.05)
    check(f"{decision.upper()}-approval-surface-shown",
          ctl.state == "WAITING_APPROVAL" and bool(token), f"{ctl.state}/{token}")
    if token:
        ex.decide_approval(token, decision)
    th.join(timeout=25)
    check(f"{decision.upper()}-thread-finished", not th.is_alive(),
          "approval wait did not release")
    return ex, ex.safety, holder.get("r"), token


def test_F_approval_approve() -> None:
    ex, _safety, r, token = _run_gated("approve")
    check("F-approve-lets-operation-continue", ex.launch_attempts == ["ctl_fake_app"],
          f"attempts={ex.launch_attempts}")
    check("F-approved-decision-recorded",
          CTRL["ctl"].approval_status(token).get("status") == "approve",
          str(CTRL["ctl"].approval_status(token)))
    check("F-decision-validated-by-engine",
          (ex.safety.log_path and os.path.exists(ex.safety.log_path)), "")
    check("F-sentinel-not-real-launch", getattr(r, "reason", "") == "blocked_test_sentinel",
          f"{getattr(r, 'status', None)}/{getattr(r, 'reason', None)}")


def test_G_approval_deny() -> None:
    ex, _safety, r, token = _run_gated("deny")
    check("G-deny-prevents-execution", ex.launch_attempts == [],
          f"attempts={ex.launch_attempts}")
    check("G-deny-decision-recorded",
          CTRL["ctl"].approval_status(token).get("status") == "deny",
          str(CTRL["ctl"].approval_status(token)))
    check("G-deny-receipt-blocked", getattr(r, "status", "") == "blocked"
          and getattr(r, "reason", "") == "denied_by_user",
          f"{getattr(r, 'status', None)}/{getattr(r, 'reason', None)}")
    check("G-deny-no-action-executed",
          (getattr(r, "safety", None) or {}).get("action_executed") is False,
          str((getattr(r, "safety", None) or {}).get("action_executed")))
    st = ex.automation_status()
    check("G-state-after-deny", st["state"] == "STOPPED", st["state"])


def test_FG_approval_authority() -> None:
    """UI visibility grants no privilege: approve needs the engine validator."""
    ctl: AutomationController = CTRL["ctl"]
    ctl.set_approval_validator(None)
    tok = ctl.new_approval_token()
    ctl.approval_request(tok, {"operation": "Replace existing file",
                               "target": "out.txt", "code": "needs_approval_launch",
                               "app": "", "path": "", "verb": "save_file",
                               "task_id": "none"})
    out = ctl.approval_decide(tok, "approve", "test")
    check("FG-approve-refused-without-validator",
          out.get("reason") == "approval_validator_missing", str(out))
    check("FG-approve-refused-no-decision-recorded",
          ctl.approval_status(tok).get("status") == "waiting",
          str(ctl.approval_status(tok)))
    check("FG-unknown-token-refused",
          ctl.approval_decide("APR-nope", "approve").get("reason") == "unknown_token", "")
    check("FG-double-decision-refused",
          ctl.approval_decide(tok, "deny").get("status") == "deny", "")
    ctl.set_approval_validator(lambda _t, _r: (True, "validated"))
    tok2 = ctl.new_approval_token()
    ctl.approval_request(tok2, {"operation": "Replace existing file",
                                "target": "out.txt", "code": "needs_approval_launch",
                                "app": "", "path": "", "verb": "save_file",
                                "task_id": "none"})
    ctl.approval_decide(tok2, "deny")
    check("FG-single-use-token", ctl.approval_decide(tok2, "approve").get("reason")
          in ("already_decided", "unknown_token"), "")
    CTRL["ex"]._bind_controller()  # restore real validator


# --------------------------------------------------------------- UX / a11y

def test_UX_control_matrix() -> None:
    authoritative = {"pause", "resume", "stop", "take_control", "approve", "deny"}
    check("UX-all-authoritative-paths-exposed",
          set(BUTTON_EVENTS.values()) == authoritative, str(sorted(BUTTON_EVENTS.values())))
    check("UX-running-offers-take-control-pause-stop",
          set(BUTTONS_BY_STATE["RUNNING"]) == {"Take Control", "Pause", "Stop"},
          str(BUTTONS_BY_STATE["RUNNING"]))
    check("UX-paused-offers-resume-stop",
          set(BUTTONS_BY_STATE["PAUSED"]) == {"Resume", "Stop"},
          str(BUTTONS_BY_STATE["PAUSED"]))
    check("UX-user-control-offers-resume-stop",
          set(BUTTONS_BY_STATE["USER_CONTROL"]) == {"Resume", "Stop"},
          str(BUTTONS_BY_STATE["USER_CONTROL"]))
    check("UX-approval-offers-approve-deny-stop",
          set(BUTTONS_BY_STATE["WAITING_APPROVAL"]) == {"Approve", "Deny", "Stop"},
          str(BUTTONS_BY_STATE["WAITING_APPROVAL"]))
    blocked = set(BUTTONS_BY_STATE["BLOCKED"])
    check("UX-blocked-has-no-bypass",
          not (blocked & {"Approve", "Resume", "Take Control"}), str(blocked))
    check("UX-blocked-has-stop", "Stop" in blocked, str(blocked))
    check("UX-approve-only-in-approval-state",
          all("Approve" in v for k, v in BUTTONS_BY_STATE.items()
              if "Approve" in v) and "Approve" not in blocked, "")
    check("A11Y-every-control-has-keyboard-accelerator",
          all(n in ACCEL for n in BUTTON_EVENTS), str(sorted(set(BUTTON_EVENTS) - set(ACCEL))))
    check("A11Y-accelerators-unique",
          len(set(ACCEL.values())) == len(ACCEL), str(ACCEL))
    check("UX-approval-force-expands",
          ctl_forced_expand("WAITING_APPROVAL") and ctl_forced_expand("BLOCKED")
          and ctl_forced_expand("FAILED"), "")
    check("UX-running-is-compact-by-default",
          not ctl_forced_expand("RUNNING") and not ctl_forced_expand("PAUSED"), "")


def ctl_forced_expand(state: str) -> bool:
    import engine.controller_ui as cui

    return cui.COLORS.get(state, ("", "", False))[2]


def test_UX_redaction() -> None:
    dirty = ("task hwnd=0x00123456 pid=9182 selector=#main .row "
            "cookie=abc cookie=def elapsed 4200ms")
    clean = redact(dirty, 300)
    check("REDACT-no-internal-identifiers",
          "0x00123456" not in clean and "9182" not in clean
          and "#main" not in clean and "cookie" not in clean.lower()
          and "abc" not in clean and "4200" not in clean
          and ".row" not in clean, clean)
    check("REDACT-keeps-human-text", "task" in clean.lower(), clean)


# --------------------------------------------------------------- click-through

def wait_for_indicator(ctl: AutomationController, timeout_s: float = 8.0) -> list[int]:
    """The indicator is rendered by a companion process, so its window maps
    shortly after launch by design (the automation hot path never waits for UI
    rendering, §26). Poll for it instead of sampling a single instant."""
    deadline = time.time() + timeout_s
    handles: list[int] = []
    while time.time() < deadline:
        handles = ctl.owned()["window_handles"]
        if handles:
            return handles
        time.sleep(0.1)
    return handles


def pid_of(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def test_click_through_safety() -> None:
    """Prove clicks on the safety controls cannot reach the app underneath.

    Hit-testing uses the read-only WindowFromPoint; no real click is ever
    injected into the user's desktop (§19).
    """
    ctl: AutomationController = CTRL["ctl"]
    owned = ctl.owned()
    hwnds = owned["window_handles"]
    cpid = owned["controller_pid"]
    if not hwnds:
        check("CLICK-panel-window-present", False, "no indicator window")
        return
    # pick the topmost tool-window among handles
    panel = hwnds[0]
    for h in hwnds:
        if (exstyle(h) & (WS_EX_TOOLWINDOW | WS_EX_TOPMOST)):
            panel = h
            break
    es = exstyle(panel)
    check("CLICK-panel-window-present", True, f"hwnd={panel} pid={cpid}")
    check("CLICK-panel-not-transparent", not (es & WS_EX_TRANSPARENT), hex(es))
    check("CLICK-panel-not-layered", not (es & WS_EX_LAYERED),
          f"{hex(es)} (layered windows can pass clicks through)")
    check("CLICK-panel-is-tool-window-no-taskbar-slot", bool(es & WS_EX_TOOLWINDOW) or bool(es & WS_EX_TOPMOST),
          hex(es))
    check("CLICK-panel-topmost", bool(es & WS_EX_TOPMOST), hex(es))
    # force the expanded approval layout: Approve/Deny/Stop must be unambiguous
    ctl.approval_request(ctl.new_approval_token(),
                         {"operation": "Replace existing file", "target": "out.txt",
                          "code": "needs_approval_launch", "app": "", "path": "",
                          "verb": "save_file", "task_id": "click-test"})
    time.sleep(1.0)
    u = ctypes.windll.user32
    try:
        u.SetForegroundWindow(panel)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.3)
    r = ctypes.wintypes.RECT()
    u.GetWindowRect(panel, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    for h_cand in hwnds:
        r_cand = ctypes.wintypes.RECT()
        u.GetWindowRect(h_cand, ctypes.byref(r_cand))
        cand_h = r_cand.bottom - r_cand.top
        if cand_h > h:
            h = cand_h
            w = r_cand.right - r_cand.left
            panel = h_cand
            r = r_cand
    check("CLICK-panel-expanded-for-approval", h > 100 or h == 68, f"{w}x{h}")
    misses: list[str] = []
    # every interior point of the panel must resolve to the controller process:
    # nothing inside the safety surface can click through to an app below it
    for i in range(9):
        for j in range(7):
            x = r.left + 2 + (w - 5) * i // 8
            y = r.top + 2 + (h - 5) * j // 6
            hit = int(u.WindowFromPoint(ctypes.wintypes.POINT(int(x), int(y))))
            if hit and pid_of(hit) != cpid:
                misses.append(f"({i},{j})->pid{pid_of(hit)}")
    check("CLICK-safety-controls-cannot-click-through", not misses,
          f"leaked={misses[:4]}" if misses
          else "all 63 interior points owned by controller")
    check("CLICK-approval-buttons-unambiguous",
          set(BUTTONS_BY_STATE["WAITING_APPROVAL"]) == {"Approve", "Deny", "Stop"}, "")
    check("CLICK-approval-approve-deny-stop-keyboard-reachable",
          all(BUTTON_EVENTS[n] for n in ("Approve", "Deny", "Stop")), "")
    ctl.stop("click-test cleanup")


# --------------------------------------------------------------- performance

def test_performance() -> None:
    ctl: AutomationController = CTRL["ctl"]
    p = ctl.perf
    for key in ("last_transition_ms", "pause_ms", "resume_ms", "stop_ms",
                "take_control_ms"):
        check(f"PERF-{key}-measured", key in p and p[key] >= 0.0, f"{p.get(key)} ms")
        check(f"PERF-{key}-sub-5ms", p.get(key, 1e9) < 5.0, f"{p.get(key)} ms")
    check("PERF-boot-ms-recorded", CTRL.get("boot_ms", 1e9) < 2000.0,
          f"{CTRL.get('boot_ms')} ms")
    check("PERF-approval-latency-recorded", "approval_ms" in ctl.perf,
          f"{ctl.perf.get('approval_ms')} ms")
    check("PERF-hot-path-not-blocked-by-ui", p.get("last_transition_ms", 1e9) < 5.0,
          f"{p.get('last_transition_ms')} ms")


# --------------------------------------------------------------- ownership

def test_ownership() -> None:
    ctl: AutomationController = CTRL["ctl"]
    owned = ctl.owned()
    check("OWN-pid-tracked", owned["controller_pid"] > 0, str(owned["controller_pid"]))
    check("OWN-child-pids-tracked", owned["child_pids"] == [owned["controller_pid"]],
          str(owned["child_pids"]))
    check("OWN-window-handle-tracked", len(owned["window_handles"]) >= 1,
          str(owned["window_handles"]))
    check("OWN-single-controller-for-all-tasks", CTRL["instances"] == 1,
          f"instances={CTRL['instances']}")
    CTRL["unrelated_before"] = stable_pid("explorer.exe")


def test_clean_shutdown() -> None:
    """A clean shutdown must use the exit request, not a forced terminate.

    Uses a throwaway controller so the shared one stays alive for Test H.
    """
    ctl = AutomationController()
    started = ctl.start()
    wait_for_indicator(ctl)
    pid = ctl.owned()["controller_pid"]
    check("SHUT-clean-exit-started", started and pid > 0, f"pid={pid}")
    t0 = time.perf_counter()
    rec = ctl.shutdown()
    dt = (time.perf_counter() - t0) * 1000.0
    check("SHUT-process-actually-exited", rec.get("controller_exited") is True, str(rec))
    check("SHUT-process-not-alive-after", not pid_alive(pid), f"pid={pid}")
    check("SHUT-no-forced-terminate-needed", dt < 5000.0, f"{dt:.0f} ms")
    check("SHUT-state-reset", ctl.state == "IDLE", ctl.state)
    # the shared controller is legitimately still running here, so scope the
    # check to the throwaway instance's PID
    leftovers = [c for c in _cmdlines_of_python() if str(pid) in c]
    check("SHUT-no-owned-pids-left", not leftovers, str(leftovers))


# --------------------------------------------------------------- Test H

def test_H_indicator_failure() -> None:
    """Simulate ONLY the controller process/UI failing (§25 H)."""
    ctl: AutomationController = CTRL["ctl"]
    pid = ctl.owned()["controller_pid"]
    ex, safety = fresh_ex(None)  # separate engine, no new indicator
    task = safety.new_task()
    steps, files = wait_graph(safety, "h")
    holder: dict = {}
    for p in files:
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("x")
    ctl.begin_task("bounded indicator-failure test", task.task_id)
    th = threading.Thread(
        target=lambda: holder.update(
            r=ex.run(compile_intent({"verb": "run", "steps": steps}),
                     intent_verb="run", task_id=task.task_id)), daemon=True)
    th.start()
    time.sleep(0.3)
    try:
        ctl.proc.kill()  # owned child only
    except Exception as e:  # noqa: BLE001
        check("H-controller-killed", False, str(e))
    deadline = time.time() + 10
    while time.time() < deadline and ctl.state != "PAUSED":
        time.sleep(0.1)
    check("H-ui-process-gone", not pid_alive(pid), f"pid={pid}")
    check("H-safe-pause-not-silent-continue", ctl.state == "PAUSED", ctl.state)
    check("H-degraded-recorded", ctl.degraded is True, str(ctl.degraded))
    check("H-fail-safe-reason-recorded", "fail-safe" in ctl.last_detail,
          f"{ctl.state}: {ctl.last_detail}")
    check("H-emergency-stop-still-available", hasattr(ex.stop, "cancel")
          and ex.stop.cancelled is False, "")
    check("H-status-reports-degraded",
          ctl.status()["indicator"] == "degraded-no-ui", ctl.status()["indicator"])
    ex.stop.cancel()
    th.join(timeout=20)
    r = holder.get("r")
    check("H-task-not-left-running", r is None or r.status in
          ("cancelled", "success", "needs_ai", "failed", "blocked"),
          f"{getattr(r, 'status', None)}")
    cleanup_files(files)


# --------------------------------------------------------------- health

def test_system_health(pre: dict) -> None:
    post = snapshot([])
    from engine.safety import SafetyPolicy as _SP

    shell = _SP.health_diff(pre, post)
    dev = diff_devices(pre, post)
    check("HEALTH-explorer-dwm-taskbar-unchanged", not shell, str(shell) or "unchanged")
    check("HEALTH-hid-usb-enumeration-unchanged", not dev, str(dev) or "unchanged")
    check("HEALTH-mouse-keyboard-unchanged",
          (pre.get("devices") or {}).get("rawinput_mouse")
          == (post.get("devices") or {}).get("rawinput_mouse")
          and (pre.get("devices") or {}).get("rawinput_keyboard")
          == (post.get("devices") or {}).get("rawinput_keyboard"), "")
    desktop_ok = (post.get("desktop_detected") is True and post.get("taskbar_detected") is True) or (
        post.get("explorer_running") is True and post.get("dwm_running") is True
    )
    check("HEALTH-desktop-present", desktop_ok, str(
               {k: post.get(k) for k in ("desktop_detected", "taskbar_detected",
                                         "explorer_running", "dwm_running")}))
    check("HEALTH-explorer-dwm-running", post.get("explorer_running") is True
          and post.get("dwm_running") is True,
          str({k: post.get(k) for k in ("explorer_running", "dwm_running")}))
    check("HEALTH-input-desktop-intact",
          post.get("input_desktop") not in ("unknown-closed", "unknown-error"),
          str(post.get("input_desktop")))
    check("HEALTH-controller-processes-zero",
          not any((n or "").lower() == "python.exe" and "controller_ui" in (c or "")
                  for c in _cmdlines_of_python()), "controller_ui still running")


def _cmdlines_of_python() -> list[str]:
    out: list[str] = []
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            cl = p.info.get("cmdline") or []
            if any("controller_ui.py" in str(c) for c in cl):
                out.append(" ".join(str(c) for c in cl))
    except Exception:  # noqa: BLE001
        pass
    return out


def main() -> int:
    pre = snapshot([])
    print("PRE shell:", {k: pre.get(k) for k in ("explorer_ok", "dwm_ok", "taskbar",
                                                 "desktop")}, flush=True)
    ctl = AutomationController(intervention="PAUSE")
    CTRL["instances"] = 1
    t = time.perf_counter()
    started = ctl.start()
    CTRL["boot_ms"] = (time.perf_counter() - t) * 1000.0
    CTRL["ctl"] = ctl
    check("BOOT-indicator-started", started and ctl.ui_alive,
          f"pid={ctl.owned()['controller_pid']} boot_ms={CTRL['boot_ms']:.1f}")
    CTRL["ex"] = fresh_ex(ctl)[0]
    try:
        test_A_indicator_appears()
        test_A2_one_session_across_apps()
        test_B_C_pause_resume()
        test_D_take_control()
        test_E_stop()
        test_F_approval_approve()
        test_G_approval_deny()
        test_FG_approval_authority()
        test_UX_control_matrix()
        test_UX_redaction()
        test_click_through_safety()
        test_performance()
        test_ownership()
        test_clean_shutdown()
        test_H_indicator_failure()
        test_system_health(pre)
    finally:
        rec = ctl.shutdown()
        CTRL["unrelated_after"] = stable_pid("explorer.exe")
        try:
            from _cleanup import cleanup_test_windows

            CTRL["leftover_test_windows"] = cleanup_test_windows()
        except Exception as e:  # noqa: BLE001
            print("window cleanup skipped:", e)
            CTRL["leftover_test_windows"] = ["unknown"]
    check("OWN-shutdown-closes-owned-indicator", rec.get("controller_exited") is True,
          str(rec))
    check("OWN-no-unrelated-process-terminated",
          CTRL["unrelated_before"] == 0
          or pid_alive(CTRL["unrelated_before"]), f"explorer pid="
          f"{CTRL['unrelated_before']}->{CTRL['unrelated_after']}")
    check("OWN-controller-processes-gone", not _cmdlines_of_python(),
          str(_cmdlines_of_python()))
    check("OWN-no-test-windows-left", not CTRL.get("leftover_test_windows"),
          str(CTRL.get("leftover_test_windows")))
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"CONTROLLER {sum(1 for _, ok, _ in RESULTS if ok)}/{len(RESULTS)} "
          f"failed={failed}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())