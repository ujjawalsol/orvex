"""Automation controller (engine side): system-wide session state machine.

ONE global controller per engine process is the source of truth for automation
state (§10). It owns:
  * controller-UI subprocess lifecycle (topmost companion window + tray-less)
  * states: IDLE/RUNNING/PAUSED/USER_CONTROL/WAITING_APPROVAL/STOPPING/
    STOPPED/FAILED/BLOCKED
  * pause / resume / take-control / stop, ALL converging on the single
    EmergencyStop cancellation path (§15)
  * approval request + decision store, validated by the engine's SafetyPolicy
    before an approve can ever take effect (§22)
  * heartbeat watchdog: UI death or heartbeat loss -> safe pause (§14)
  * user-intervention policy: BLOCK/PAUSE/TAKE_CONTROL/ALLOW, default PAUSE
  * strict resource ownership: controller PID + child PIDs + HWND (§27)

The controller NEVER executes automation primitives. It only signals through
SafetyPolicy-checked pathways; it holds no capability the executor lacks.
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

STATES = ("IDLE", "RUNNING", "PAUSED", "USER_CONTROL", "WAITING_APPROVAL",
          "STOPPING", "STOPPED", "FAILED", "BLOCKED")

INTERVENTION_MODES = ("BLOCK", "PAUSE", "TAKE_CONTROL", "ALLOW")

APPROVAL_TTL_S = 300.0
_TOKENS = itertools.count(1)


class ApprovalToken:
    """Structured, single-use approval record.

    `request` is engine-supplied structured data; the UI renders it verbatim so
    the prompt can never be generic or misleading (§8).
    """

    __slots__ = ("token", "request", "created", "decision", "decided_by")

    def __init__(self, token: str, request: dict) -> None:
        self.token = token
        self.request = dict(request or {})
        self.created = time.time()
        self.decision: str | None = None
        self.decided_by = ""

    @property
    def expired(self) -> bool:
        return (time.time() - self.created) > APPROVAL_TTL_S

    def summary(self) -> dict:
        return {"operation": self.request.get("operation", "?"),
                "target": self.request.get("target", "?"),
                "reason": self.request.get("reason", ""),
                "code": self.request.get("code", "")}


class AutomationController:
    def __init__(self, intervention: str = "PAUSE") -> None:
        if intervention not in INTERVENTION_MODES:
            raise ValueError(f"intervention must be one of {INTERVENTION_MODES}")
        self.intervention = intervention
        self.state = "IDLE"
        self.paused = threading.Event()   # set = paused, polled at node boundaries
        self.user_control = threading.Event()
        self.proc: subprocess.Popen | None = None
        self._wlock = threading.Lock()
        self._last_alive = 0.0
        self._watchdog: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._stop_esc: callable | None = None   # wired to EmergencyStop.cancel
        self._on_pause_cb: callable | None = None
        self.approvals: dict[str, ApprovalToken] = {}
        self.transitions: list[tuple[str, str, float]] = []
        self.task_info: dict = {}
        self.last_text = ""
        self.last_detail = ""
        self.active_tasks: set[str] = set()
        self._t0 = 0.0
        self.perf: dict[str, float] = {}
        self.degraded = False
        # validator: (token, request) -> (ok, reason). MUST be injected by the
        # engine; without it an approve is refused, never assumed (§22).
        self._approval_validator: callable | None = None
        self._ulock = threading.Lock()
        self._pre_approval = "IDLE"

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        """Launch the companion UI. False => degraded mode: automation may only
        proceed because ESC + MCP cancel remain independent always-available
        emergency stops (§14)."""
        if os.environ.get("ORVEX_NO_UI", "0") in ("1", "true", "yes") or \
           os.environ.get("SFMCP_NO_UI", "0") in ("1", "true", "yes") or \
           os.environ.get("ORVEX_HEADLESS", "0") in ("1", "true", "yes"):
            self.proc = None
            self.degraded = True
            return False
        if self.proc is not None and self.proc.poll() is None:
            return True
        try:
            ui = str(Path(__file__).resolve().parent / "controller_ui.py")
            self.proc = subprocess.Popen(
                [sys.executable, "-u", ui],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:  # noqa: BLE001
            self.proc = None
            self.degraded = True
            return False
        self._last_alive = time.time()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._watchdog = threading.Thread(target=self._watch_loop, daemon=True)
        self._watchdog.start()
        return True

    @property
    def ui_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def owned(self) -> dict:
        """Strict ownership record for shutdown auditing (§27)."""
        pids: list[int] = []
        if self.proc is not None:
            pids.append(self.proc.pid)
        hwnds: list[int] = []
        if self.ui_alive:
            hwnds = self._ui_windows()
        return {"controller_pid": self.proc.pid if self.proc is not None else 0,
                "child_pids": pids, "window_handles": hwnds,
                "alive": self.ui_alive}

    def _ui_windows(self) -> list[int]:
        try:
            import ctypes

            u = ctypes.windll.user32
            found: list[int] = []
            pid = self.proc.pid if self.proc is not None else 0

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)  # type: ignore[attr-defined]
            def cb(hwnd, _l):
                wpid = ctypes.c_ulong()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                if wpid.value == pid and u.IsWindowVisible(hwnd):
                    found.append(int(hwnd))
                return True

            u.EnumWindows(cb, 0)
            return found
        except Exception:  # noqa: BLE001
            return []

    def shutdown(self) -> dict:
        rec = self.owned()
        proc = self.proc
        # send the clean exit request while the handle is still current,
        # otherwise _send() short-circuits and the UI is only terminated
        if proc is not None:
            try:
                self._send({"cmd": "exit"})
            except Exception:  # noqa: BLE001
                pass
        self.proc = None
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.terminate()  # owned child only
                except Exception:  # noqa: BLE001
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass
            rec["controller_exited"] = proc.poll() is not None
            rec["processes_terminated"] = 1 if rec["controller_exited"] else 0
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:  # noqa: BLE001
                    pass
        else:
            rec["controller_exited"] = True
            rec["processes_terminated"] = 0
        self.active_tasks.clear()
        self.approvals.clear()
        return rec

    # ------------------------------------------------------------ states

    def _set(self, state: str, text: str = "", detail: str = "",
             expand: bool = False, token: str = "") -> None:
        if state not in STATES:
            raise ValueError(f"bad state {state}")
        t = time.perf_counter()
        self.transitions.append((self.state, state, time.time()))
        self.state = state
        self.last_text = text
        self.last_detail = detail
        self._send({"cmd": "state", "state": state, "text": text,
                    "detail": detail, "expand": expand, "token": token,
                    "task_count": len(self.active_tasks)})
        self.perf["last_transition_ms"] = (time.perf_counter() - t) * 1000.0

    def begin_task(self, goal: str, task_id: str = "") -> None:
        self._t0 = time.time()
        self.task_info = {"goal": goal, "task_id": task_id}
        if task_id:
            self.active_tasks.add(task_id)
        self.paused.clear()
        self.user_control.clear()
        self._set("RUNNING", "Automation Active",
                  f"AI is controlling this PC. Task: {goal}")

    def end_task(self, task_id: str = "") -> None:
        if task_id:
            self.active_tasks.discard(task_id)

    def node_update(self, op: str, app_hint: str = "") -> None:
        if self.state != "RUNNING":
            return
        el = time.time() - self._t0
        app_part = f"Controlling: {app_hint} | " if app_hint else ""
        self._send({"cmd": "state", "state": "RUNNING",
                    "text": "Automation Active",
                    "detail": f"{app_part}Step: {op} | Elapsed: {el:.1f} s",
                    "task_count": len(self.active_tasks)})

    def check_paused(self, is_cancelled) -> str:
        """Polled at node boundaries. is_cancelled: zero-arg callable.
        Returns 'go' | 'stop'. Pause waits for the user; cancel always wins."""
        try:
            if is_cancelled():
                return "stop"
        except Exception:  # noqa: BLE001
            pass
        while self.paused.is_set():
            try:
                if is_cancelled():
                    return "stop"
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.05)
        return "go"

    def pause(self, why: str = "user") -> None:
        t = time.perf_counter()
        self.paused.set()
        self._set("PAUSED", "Automation Paused", f"{why}. State preserved.")
        self.perf["pause_ms"] = (time.perf_counter() - t) * 1000.0

    def take_control(self) -> None:
        t = time.perf_counter()
        self.paused.set()
        self.user_control.set()
        self._set("USER_CONTROL", "You have control",
                  "Automation paused. Your input is yours; nothing is recorded "
                  "as automation input.")
        self.perf["take_control_ms"] = (time.perf_counter() - t) * 1000.0

    def resume(self) -> bool:
        if self.state not in ("PAUSED", "USER_CONTROL"):
            return False
        t = time.perf_counter()
        self.paused.clear()
        self.user_control.clear()
        self._set("RUNNING", "Automation Active",
                  f"AI is controlling this PC. Task: {self.task_info.get('goal', '')}")
        self.perf["resume_ms"] = (time.perf_counter() - t) * 1000.0
        return True

    def stop(self, why: str = "user") -> dict:
        """Strongest user control: stop the AUTOMATION, not the resources.

        Converges on the one EmergencyStop.cancel path shared by ESC, the
        controller button and the MCP cancel tool (§7, §15). No taskkill, no
        process kill, no driver or HID reset.
        """
        t = time.perf_counter()
        self._set("STOPPING", "Stopping", why)
        stopped = False
        if self._stop_esc is not None:
            try:
                self._stop_esc()
                stopped = True
            except Exception:  # noqa: BLE001
                pass
        self.paused.clear()
        self.user_control.set()
        self._set("STOPPED", "Automation Stopped", why)
        self.perf["stop_ms"] = (time.perf_counter() - t) * 1000.0
        return {"status": "cancelled", "reason": why, "stop_path_invoked": stopped}

    def failed(self, reason: str) -> None:
        self._set("FAILED", "Automation Failed", reason[:200], expand=True)

    def blocked(self, reason: str) -> None:
        self._set("BLOCKED", "Automation Blocked", reason[:200], expand=True)

    # ------------------------------------------------------------ approval

    def set_approval_validator(self, fn) -> None:
        self._approval_validator = fn

    def new_approval_token(self) -> str:
        return f"APR-{next(_TOKENS):04d}"

    def approval_request(self, token: str, request: dict) -> dict:
        rec = ApprovalToken(token, request)
        with self._ulock:
            self.approvals[token] = rec
        s = rec.summary()
        self._pre_approval = self.state
        self._set("WAITING_APPROVAL", "Approval Required",
                  f"Automation wants to: {s['operation']} {s['target']}".strip()[:300],
                  expand=True, token=token)
        return rec.summary()

    def approval_decide(self, token: str, decision: str,
                        source: str = "ui") -> dict:
        """Sole approval funnel. UI buttons, MCP tools and tests all land here.

        Approve is REFUSED unless the engine-injected validator re-checks the
        original safety gate and returns ok (§22).
        """
        if decision not in ("approve", "deny"):
            return {"status": "failed", "reason": "bad_decision"}
        with self._ulock:
            rec = self.approvals.get(token)
            if rec is None:
                return {"status": "failed", "reason": "unknown_token"}
            if rec.decision is not None:
                return {"status": "failed", "reason": "already_decided",
                        "decision": rec.decision}
            if rec.expired:
                return {"status": "failed", "reason": "approval_expired"}
            if decision == "approve":
                if self._approval_validator is None:
                    return {"status": "failed",
                            "reason": "approval_validator_missing"}
                try:
                    ok, why = self._approval_validator(token, dict(rec.request))
                except Exception as e:  # noqa: BLE001
                    return {"status": "failed",
                            "reason": f"validator_error:{type(e).__name__}"}
                if not ok:
                    self._set("BLOCKED", "Automation Blocked", why[:200])
                    return {"status": "blocked", "reason": why}
            rec.decision = decision
            rec.decided_by = source
        self.perf.setdefault("approval_ms", 0.0)
        if decision == "deny":
            self._set("STOPPED", "Automation Stopped",
                      "You denied the approval request. Nothing was executed.")
        else:
            back = self._pre_approval if self._pre_approval in (
                "RUNNING", "PAUSED", "USER_CONTROL") else "RUNNING"
            self._set(back, "Automation Active" if back == "RUNNING"
                      else ("Automation Paused" if back == "PAUSED" else "You have control"),
                      "Approval granted. Resuming the same session.")
        return {"status": decision, "token": token, "source": source}

    def approval_wait(self, token: str, timeout_s: float = 300.0,
                      is_cancelled=None) -> dict:
        """Engine-side wait for a human decision. Stop/cancel always wins."""
        deadline = time.time() + timeout_s
        while True:
            if is_cancelled is not None:
                try:
                    if is_cancelled():
                        return {"status": "cancelled", "reason": "cancelled"}
                except Exception:  # noqa: BLE001
                    pass
            with self._ulock:
                rec = self.approvals.get(token)
                if rec is not None and rec.decision is not None:
                    return {"status": rec.decision, "token": token,
                            "decided_by": rec.decided_by}
            if rec is not None and rec.expired:
                return {"status": "failed", "reason": "approval_expired"}
            if time.time() > deadline:
                return {"status": "failed", "reason": "approval_timeout"}
            time.sleep(0.05)

    def approval_status(self, token: str) -> dict:
        with self._ulock:
            rec = self.approvals.get(token)
            if rec is None:
                return {"status": "failed", "reason": "unknown_token"}
            return {"status": rec.decision or "waiting", "token": token,
                    "request": rec.summary(), "decided_by": rec.decided_by}

    # ------------------------------------------------------------ wire

    def _send(self, msg: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        try:
            with self._wlock:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
        except Exception:  # noqa: BLE001
            pass  # broken pipe -> watchdog safe-pause (§14)

    def input_window(self, ms: float = 600.0) -> None:
        """Tell the UI which interval contains our own injected input, so
        physical user input is never confused with automation input (§12)."""
        self._send({"cmd": "input_window", "ms": ms})

    def _read_loop(self) -> None:
        try:
            assert self.proc is not None and self.proc.stdout is not None
            for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                self._on_ui_event(msg.get("event", ""), msg.get("token", ""))
        except Exception:  # noqa: BLE001
            pass

    def _on_ui_event(self, ev: str, token: str = "") -> None:
        if ev == "alive":
            self._last_alive = time.time()
        elif ev == "pause":
            self.pause("controller button")
            self._fire_pause_cb("pause")
        elif ev == "take_control":
            self.take_control()
            self._fire_pause_cb("take_control")
        elif ev == "resume":
            self.resume()
        elif ev == "stop":
            self.stop("controller button")
        elif ev in ("approve", "deny"):
            tok = token or (self._pending_token() or "")
            if tok:
                self.approval_decide(tok, ev, source="controller_ui")
        elif ev == "user_intervention":
            self._on_intervention()
        elif ev == "ui_closed_by_user":
            # User dismissed the panel: treat as an unavailable indicator.
            if self.state in ("RUNNING", "PAUSED", "USER_CONTROL", "WAITING_APPROVAL"):
                self.pause("indicator closed by user (fail-safe)")

    def _pending_token(self) -> str | None:
        with self._ulock:
            for token, rec in self.approvals.items():
                if rec.decision is None and not rec.expired:
                    return token
        return None

    def _fire_pause_cb(self, why: str) -> None:
        if self._on_pause_cb is not None:
            try:
                self._on_pause_cb(why)
            except Exception:  # noqa: BLE001
                pass

    def _on_intervention(self) -> None:
        if self.state != "RUNNING":
            return
        if self.intervention == "ALLOW":
            return
        if self.intervention == "BLOCK":
            self.stop("user intervention (BLOCK policy)")
        elif self.intervention == "TAKE_CONTROL":
            self.take_control()
        else:  # PAUSE is the production default
            self.pause("user_intervention_detected")

    def _watch_loop(self) -> None:
        while True:
            time.sleep(1.0)
            if self.proc is None:
                return
            if self.proc.poll() is not None:
                self.degraded = True
                # UI died on its own: safe-pause if work is in flight (§14).
                if self.state in ("RUNNING", "PAUSED", "USER_CONTROL", "WAITING_APPROVAL"):
                    self.pause("indicator unavailable (fail-safe)")
                return
            if time.time() - self._last_alive > 8.0 and self.state == "RUNNING":
                self.pause("indicator heartbeat lost (fail-safe)")

    def status(self) -> dict:
        with self._ulock:
            pending = [t for t, r in self.approvals.items() if r.decision is None]
        return {"state": self.state,
                "indicator": "active" if self.ui_alive else "degraded-no-ui",
                "ui_alive": self.ui_alive, "degraded": self.degraded,
                "task_count": len(self.active_tasks),
                "tasks": sorted(self.active_tasks),
                "intervention_policy": self.intervention,
                "pending_approvals": pending,
                "emergency_stop_path": "shared",
                "owned": self.owned(),
                "perf_ms": dict(self.perf)}