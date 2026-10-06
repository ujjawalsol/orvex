"""Automation controller (engine side): system-wide session state machine.

Connects to the system-wide singleton Controller UI over Windows Named Pipe IPC.
Decoupled from individual MCP process lifetimes:
  * Exactly ONE controller UI per Windows desktop session
  * Multiple MCP clients and engine processes share the same control surface
  * States: IDLE, RUNNING, PAUSED, USER_CONTROL, WAITING_APPROVAL, STOPPING, STOPPED
  * Real user controls: Take Control, Resume, Stop
  * Zero focus stealing, zero shell / HID interference
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from multiprocessing.connection import Client

user32 = ctypes.windll.user32
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

STATES = ("IDLE", "RUNNING", "PAUSED", "USER_CONTROL", "WAITING_APPROVAL",
          "STOPPING", "STOPPED", "FAILED", "BLOCKED")

INTERVENTION_MODES = ("BLOCK", "PAUSE", "TAKE_CONTROL", "ALLOW")

APPROVAL_TTL_S = 300.0
_TOKENS = itertools.count(1)

PIPE_NAME = r"\\.\pipe\orvex_controller_ipc"
MUTEX_NAME = r"Local\ORVEX_ControllerUI_Singleton_Mutex"
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
ORVEX_DIR = Path(LOCALAPPDATA) / "ORVEX" if LOCALAPPDATA else Path.home() / ".orvex"
ORVEX_DIR.mkdir(parents=True, exist_ok=True)
KEY_FILE = ORVEX_DIR / ".controller_ipc_key"
STATE_FILE = ORVEX_DIR / ".controller_state.json"


def attach_to_input_desktop() -> None:
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass


def _get_auth_key() -> bytes:
    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_bytes()
        except Exception:
            pass
    # Generate fallback
    key = os.urandom(32)
    try:
        KEY_FILE.write_bytes(key)
    except Exception:
        pass
    return key


class ApprovalToken:
    """Structured, single-use approval record."""

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
        return {
            "operation": self.request.get("operation", "?"),
            "target": self.request.get("target", "?"),
            "reason": self.request.get("reason", ""),
            "code": self.request.get("code", ""),
        }


class AutomationController:
    def __init__(self, intervention: str = "PAUSE") -> None:
        if intervention not in INTERVENTION_MODES:
            raise ValueError(f"intervention must be one of {INTERVENTION_MODES}")
        self.intervention = intervention
        self.state = "IDLE"
        self.paused = threading.Event()
        self.user_control = threading.Event()

        self.client_id = f"mcp_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self.conn: any = None
        self.proc: subprocess.Popen | None = None
        self._wlock = threading.Lock()
        self._last_alive = 0.0
        self._watchdog: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._stop_esc: callable | None = None
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

        self._approval_validator: callable | None = None
        self._ulock = threading.Lock()
        self._pre_approval = "IDLE"
        self._ui_hwnd = 0
        self._ui_pid = 0

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        """Connect to or spawn the singleton UI companion service."""
        attach_to_input_desktop()
        if (os.environ.get("ORVEX_NO_UI", "0") in ("1", "true", "yes") or
                os.environ.get("SFMCP_NO_UI", "0") in ("1", "true", "yes") or
                os.environ.get("ORVEX_HEADLESS", "0") in ("1", "true", "yes")):
            self.degraded = True
            return False

        if self.conn is not None:
            return True

        auth_key = _get_auth_key()

        # 1. Try connecting to an already running UI service
        connected = self._try_connect(auth_key)

        # 2. If not running, spawn the UI companion process
        if not connected:
            self._spawn_ui()
            # Wait up to 3 seconds for pipe availability
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if self._try_connect(auth_key):
                    connected = True
                    break
                time.sleep(0.06)

        if not connected:
            self.degraded = True
            return False

        self._last_alive = time.time()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._watchdog = threading.Thread(target=self._watch_loop, daemon=True)
        self._watchdog.start()

        # Initial handshake register
        self._send({"cmd": "register_client", "client_id": self.client_id, "pid": os.getpid()})
        return True

    def _try_connect(self, auth_key: bytes) -> bool:
        try:
            self.conn = Client(PIPE_NAME, "AF_PIPE", authkey=auth_key)
            return True
        except Exception:
            self.conn = None
            return False

    def _spawn_ui(self) -> None:
        try:
            ui_path = str(Path(__file__).resolve().parent / "controller_ui.py")
            python_bin = sys.executable
            w_cand = Path(sys.executable).with_name("pythonw.exe")
            if w_cand.exists():
                python_bin = str(w_cand)

            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(
                [python_bin, "-u", ui_path],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags
            )
        except Exception:
            self.proc = None

    @property
    def ui_alive(self) -> bool:
        if self.conn is None:
            return False
        # Consider alive if recent heartbeat
        return (time.time() - self._last_alive) < 10.0

    def owned(self) -> dict:
        """Strict ownership record for shutdown auditing."""
        attach_to_input_desktop()
        pid = self._ui_pid
        hwnd = self._ui_hwnd

        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                file_hwnd = data.get("hwnd", 0)
                file_wid = data.get("wid", 0)
                file_pid = data.get("pid", 0)
                if file_pid:
                    pid = file_pid
                    self._ui_pid = file_pid
                if file_hwnd and user32.IsWindow(file_hwnd):
                    hwnd = file_hwnd
                    self._ui_hwnd = file_hwnd
                elif file_wid and user32.IsWindow(file_wid):
                    hwnd = file_wid
                    self._ui_hwnd = file_wid
            except Exception:
                pass

        pids = [pid] if pid > 0 else []
        hwnds = [hwnd] if (hwnd > 0 and user32.IsWindow(hwnd)) else []
        if not hwnds and pid > 0:
            def cb(h, _l):
                wpid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(h, ctypes.byref(wpid))
                if wpid.value == pid and user32.IsWindow(h):
                    hwnds.append(h)
                return 1
            CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_size_t, ctypes.c_size_t)
            user32.EnumWindows(CB(cb), 0)
        return {
            "controller_pid": pid,
            "child_pids": pids,
            "window_handles": hwnds,
            "alive": self.ui_alive,
        }

    def shutdown(self) -> dict:
        rec = self.owned()
        if self.conn is not None:
            try:
                self._send({"cmd": "disconnect", "client_id": self.client_id})
                self.conn.close()
            except Exception:
                pass
            self.conn = None

        self.active_tasks.clear()
        self.approvals.clear()
        rec["controller_exited"] = True
        rec["processes_terminated"] = 0
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
        self._send({
            "cmd": "set_state",
            "client_id": self.client_id,
            "state": state,
            "text": text,
            "detail": detail,
            "token": token,
            "task_count": len(self.active_tasks),
        })
        self.perf["last_transition_ms"] = (time.perf_counter() - t) * 1000.0

    def begin_task(self, goal: str, task_id: str = "") -> None:
        self._t0 = time.time()
        self.task_info = {"goal": goal, "task_id": task_id}
        if task_id:
            self.active_tasks.add(task_id)
        self.paused.clear()
        self.user_control.clear()
        self.state = "RUNNING"
        self._send({
            "cmd": "task_begin",
            "client_id": self.client_id,
            "task_id": task_id,
            "goal": goal,
        })

    def end_task(self, task_id: str = "", status: str = "success") -> None:
        if task_id:
            self.active_tasks.discard(task_id)
        self._send({
            "cmd": "task_end",
            "client_id": self.client_id,
            "task_id": task_id,
            "status": status,
        })
        if not self.active_tasks and self.state == "RUNNING":
            if status == "success":
                self.state = "IDLE"
            elif status in ("stopped", "cancelled"):
                self.state = "STOPPED"
            elif status == "failed":
                self.state = "FAILED"

    def node_update(self, op: str, app_hint: str = "") -> None:
        if self.state != "RUNNING":
            return
        self._send({
            "cmd": "node_update",
            "client_id": self.client_id,
            "op": op,
            "app_hint": app_hint,
        })

    def check_paused(self, is_cancelled) -> str:
        try:
            if is_cancelled():
                return "stop"
        except Exception:
            pass
        while self.paused.is_set():
            try:
                if is_cancelled():
                    return "stop"
            except Exception:
                pass
            time.sleep(0.05)
        return "go"

    def pause(self, why: str = "user") -> None:
        t = time.perf_counter()
        self.paused.set()
        self._set("PAUSED", "Automation Paused", f"{why}. State preserved.", expand=True)
        self.perf["pause_ms"] = (time.perf_counter() - t) * 1000.0

    def take_control(self) -> None:
        t = time.perf_counter()
        self.paused.set()
        self.user_control.set()
        self._set("USER_CONTROL", "Manual Control",
                  "Automation paused. You have control.\nClick Resume to continue.",
                  expand=True)
        self.perf["take_control_ms"] = (time.perf_counter() - t) * 1000.0

    def resume(self) -> bool:
        if self.state not in ("PAUSED", "USER_CONTROL"):
            return False
        t = time.perf_counter()
        self.paused.clear()
        self.user_control.clear()
        self._set("RUNNING", "Automating",
                  f"AI is controlling this PC. Task: {self.task_info.get('goal', '')}",
                  expand=True)
        self.perf["resume_ms"] = (time.perf_counter() - t) * 1000.0
        return True

    def stop(self, why: str = "user") -> dict:
        t = time.perf_counter()
        self.state = "STOPPING"
        stopped = False
        if self._stop_esc is not None:
            try:
                self._stop_esc()
                stopped = True
            except Exception:
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
        self._set("WAITING_APPROVAL", "Action Required",
                  f"{s['operation']} {s['target']}".strip()[:200],
                  expand=True, token=token)
        return rec.summary()

    def approval_decide(self, token: str, decision: str,
                        source: str = "ui") -> dict:
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
                except Exception as e:
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
            self._set(back, "Automating" if back == "RUNNING"
                      else ("Manual Control" if back == "USER_CONTROL" else "Automation Paused"),
                      "Approval granted. Resuming the same session.")
        return {"status": decision, "token": token, "source": source}

    def approval_wait(self, token: str, timeout_s: float = 300.0,
                      is_cancelled=None) -> dict:
        deadline = time.time() + timeout_s
        while True:
            if is_cancelled is not None:
                try:
                    if is_cancelled():
                        return {"status": "cancelled", "reason": "cancelled"}
                except Exception:
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
        if self.conn is None:
            return
        try:
            with self._wlock:
                self.conn.send(msg)
        except Exception:
            self.conn = None
            self.degraded = True

    def input_window(self, ms: float = 600.0) -> None:
        self._send({"cmd": "input_window", "ms": ms})

    def _read_loop(self) -> None:
        try:
            while self.conn is not None:
                try:
                    msg = self.conn.recv()
                except (EOFError, BrokenPipeError, ConnectionResetError):
                    break
                except Exception:
                    break

                ev = msg.get("event", "")
                if ev == "alive":
                    self._last_alive = time.time()
                    if "pid" in msg:
                        self._ui_pid = int(msg["pid"])
                    if "hwnd" in msg:
                        self._ui_hwnd = int(msg["hwnd"])
                elif ev == "take_control":
                    self.take_control()
                    self._fire_pause_cb("take_control")
                elif ev == "resume":
                    self.resume()
                elif ev == "stop":
                    self.stop("controller button")
                elif ev in ("approve", "deny"):
                    tok = msg.get("token") or (self._pending_token() or "")
                    if tok:
                        self.approval_decide(tok, ev, source="controller_ui")
                elif ev == "user_intervention":
                    self._on_intervention()
        except Exception:
            pass
        finally:
            self.conn = None
            self.degraded = True

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
            except Exception:
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
        else:
            self.pause("user_intervention_detected")

    def _watch_loop(self) -> None:
        while self.conn is not None:
            time.sleep(1.0)
            if time.time() - self._last_alive > 8.0 and self.state == "RUNNING":
                self.pause("indicator heartbeat lost (fail-safe)")

    def status(self) -> dict:
        with self._ulock:
            pending = [t for t, r in self.approvals.items() if r.decision is None]
        return {
            "state": self.state,
            "indicator": "active" if self.ui_alive else "degraded-no-ui",
            "ui_alive": self.ui_alive,
            "degraded": self.degraded,
            "task_count": len(self.active_tasks),
            "tasks": sorted(self.active_tasks),
            "intervention_policy": self.intervention,
            "pending_approvals": pending,
            "emergency_stop_path": "shared",
            "owned": self.owned(),
            "perf_ms": dict(self.perf),
        }