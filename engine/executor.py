"""Graph executor: deterministic batch execution with safety-first guards.

Per-node flow: budget check -> SafetyPolicy gates -> act -> verify ->
classified recovery -> receipt with safety section.
Statuses: success | needs_ai | needs_approval | failed | blocked | cancelled.
Blocked codes: blocked_system_target | blocked_system_ui | blocked_unsafe_path
| blocked_system_keys | ambiguous_target | resource_limit_exceeded
| execution_budget_exceeded | foreground_not_target | needs_approval_launch.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .compiler import Graph
from .profiler import Profiler
from .safety import Budget, SafetyError, SafetyPolicy, TaskRecord
from .security import EmergencyStop, Level, Policy, level_for_verb
from .uia_backend import Target, UIABackend

# Retry classification (§12): only pure reads + pattern set may auto-retry.
SAFE_RETRY_OPS = {"find", "inspect", "verify", "wait", "set_value"}


@dataclass
class Receipt:
    status: str  # success|needs_ai|needs_approval|failed|blocked|cancelled
    result: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    steps_completed: int = 0
    steps_total: int = 0
    retries: int = 0
    backend: str = "uia"
    cache_hits: int = 0
    screenshots: int = 0
    failed_step: str | None = None
    reason: str | None = None
    candidates: list[dict] | None = None
    resume_handle: str | None = None
    profiler: dict | None = None
    safety: dict | None = None
    cleanup: dict | None = None
    approval_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "result": self.result,
            "duration_ms": round(self.duration_ms, 3),
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
            "retries": self.retries,
            "backend": self.backend,
            "cache_hits": self.cache_hits,
            "screenshots": self.screenshots,
            "failed_step": self.failed_step,
            "reason": self.reason,
            "candidates": self.candidates,
            "resume_handle": self.resume_handle,
            "profiler": self.profiler,
            "safety": self.safety,
            "cleanup": self.cleanup,
            "approval_token": self.approval_token,
        }


class Executor:
    def __init__(self, policy: Policy | None = None, stop: EmergencyStop | None = None,
                 safety: SafetyPolicy | None = None, budget: Budget | None = None,
                 browser_cfg=None, controller=None) -> None:
        self.uia = UIABackend()
        self.profiler = Profiler()
        self.policy = policy or Policy.load()
        self.stop = stop or EmergencyStop()
        self.safety = safety or SafetyPolicy(budget=budget)
        self.controller = controller  # AutomationController or None
        from .sessions import SessionStore

        if browser_cfg is None:
            self._sessions = SessionStore()
        else:
            self._sessions = SessionStore(idle_timeout_s=browser_cfg.idle_ttl_s,
                                          max_sessions=browser_cfg.max_sessions,
                                          max_age_s=browser_cfg.max_session_age_s)
        self._task_browser: dict[str, str] = {}  # task_id -> session handle default
        self._journal: dict[str, dict] = {}  # task_id -> execution state for resume
        self._task_window_apps: dict[tuple[str, int], tuple[str, str]] = {}  # (task,hwnd) -> (label, app)
        self._window_first_seen: dict[int, float] = {}  # hwnd -> first observed (cold settle)
        self._resolved: dict[tuple, object] = {}  # (task, selector, epoch) -> control
        self.safety.protected_check = self.policy.is_protected
        self._approval_rec: TaskRecord | None = None   # rec owning the in-flight gate
        self._gate_tokens: dict[tuple, str] = {}       # (code,app,path) -> token
        self._gate_lock = threading.Lock()
        self._gate_perf: dict[str, float] = {}         # gate latency, not span data
        self._gate_outcome: tuple[str, str] = ("", "")
        if self.controller is not None:
            self._bind_controller()

    def _bind_controller(self) -> None:
        """Attach the system-wide controller. The controller gains no extra
        authority: it may only signal through these SafetyPolicy-checked
        entry points, and every approve is re-validated here (§21, §22)."""
        try:
            self.controller.set_approval_validator(self._approval_validator)
            self.controller._stop_esc = self.stop.cancel
            self.safety.approval_gate = self._safety_approval_gate
        except Exception:  # noqa: BLE001
            pass
        try:
            # protected targets are denied outright, never approvable
            self.safety.protected_check = self.policy.is_protected
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ public

    def cancel(self, task_id: str) -> dict:
        ok = self.safety.cancel(task_id)
        return {"status": "cancelled" if ok else "failed",
                "cancelled": ok, "task_id": task_id}

    def automation_status(self) -> dict:
        """Authoritative, system-wide automation state (§10, §24)."""
        if self.controller is None:
            return {"state": "IDLE", "indicator": "degraded-no-ui",
                    "ui_alive": False, "degraded": True, "task_count": 0,
                    "tasks": [], "intervention_policy": "PAUSE",
                    "pending_approvals": [], "emergency_stop_path": "shared",
                    "gate_perf_ms": dict(self._gate_perf),
                    "owned": {"controller_pid": 0, "child_pids": [],
                              "window_handles": [], "alive": False}}
        st = self.controller.status()
        st["gate_perf_ms"] = dict(self._gate_perf)
        return st

    def decide_approval(self, token: str, decision: str) -> dict:
        """Sole human-approval entry point for the MCP surface.

        Routes UI -> controller -> SafetyPolicy -> engine. The UI can never call
        an operation directly, and an approve is only recorded after
        `self._approval_validator` re-runs the safety gate (§22).
        """
        if self.controller is None:
            return {"status": "failed", "reason": "no_controller"}
        out = self.controller.approval_decide(token, decision, source="mcp")
        if out.get("status") == decision:
            rec = None
            with self.controller._ulock:
                tok = self.controller.approvals.get(token)
                if tok is not None:
                    tid = tok.request.get("task_id", "")
                    rec = self.safety.tasks.get(tid)
            if rec is not None:
                self.safety.log(rec, f"approval_{decision}", token=token)
        return out

    def approval_status(self, token: str) -> dict:
        if self.controller is None:
            return {"status": "failed", "reason": "no_controller"}
        return self.controller.approval_status(token)

    # ------------------------------------------------------------ approval

    def _safety_approval_gate(self, code: str, detail: str, app: str = "",
                              path: str = "", verb: str = "") -> bool:
        """Resolve a gated operation against the system-wide controller.

        Called from inside SafetyPolicy, so an approval can never skip any other
        safety rule: it only unlocks this one gate, and only after the engine
        re-validates the request. Deny, stop, timeout and validator failure all
        resolve to False, which keeps the original SafetyError and therefore a
        blocked receipt (§22).
        """
        ctl = self.controller
        if ctl is None:
            self._gate_outcome = ("no_controller", code)
            return False
        rec = self._approval_rec
        if rec is None:
            self._gate_outcome = ("no_task", code)
            return False
        request = self._approval_request(rec, verb, code,
                                         {"app": app, "path": path, "detail": detail})
        budget_s = float(os.environ.get("ORVEX_APPROVAL_WAIT_S")
                         or os.environ.get("SFMCP_APPROVAL_WAIT_S", "120"))
        try:
            with self._gate_lock:
                key = (code, app.lower(), os.path.normcase(path or ""))
                existing = self._gate_tokens.get(key)
                if existing is None or ctl.approval_status(existing).get("status") not in (
                        "approve", "deny"):
                    token = ctl.new_approval_token()
                    ctl.approval_request(token, request)
                    self._gate_tokens[key] = token
                token = existing or self._gate_tokens[key]
        except Exception as e:  # noqa: BLE001
            self._gate_outcome = ("controller_error", f"{type(e).__name__}")
            return False
        t = time.perf_counter()
        out = ctl.approval_wait(token, timeout_s=budget_s,
                                is_cancelled=lambda: self.stop.cancelled
                                or rec.cancelled)
        self._gate_perf["approval_ms"] = self._gate_perf.get("approval_ms", 0.0) + \
            (time.perf_counter() - t) * 1000.0
        status = out.get("status", "")
        if status != "approve":
            self._gate_tokens.pop(key, None)
            outcome = {"deny": "deny", "cancelled": "stopped"}.get(status, "timeout")
            self._gate_outcome = (outcome, out.get("reason", status))
            return False
        ok, why = self._approval_validator(token, request)
        if not ok:
            self._gate_tokens.pop(key, None)
            self._gate_outcome = ("revalidation_failed", why)
            self.safety.log(rec, "approval_revalidated_blocked", code=code, why=why)
            return False
        self._gate_outcome = ("approved", "validated")
        return True

    def _approval_request(self, rec: TaskRecord, verb: str, code: str,
                          extra: dict) -> dict:
        """Structured request: the UI renders exactly this, never a generic
        string (§8). Carries what is needed for re-validation at approve time."""
        app = (extra or {}).get("app", "")
        path = (extra or {}).get("path", "")
        if code == "needs_approval_launch":
            operation = "Launch an application"
            target = app or "unlisted application"
        elif code == "needs_approval_close":
            operation = "Close a window this task does not own"
            target = (extra or {}).get("detail", "unowned window")[:120]
        elif verb in ("save_file", "submit_form"):
            operation = "Write or submit outside the automation sandbox"
            target = path or (extra or {}).get("detail", verb)[:120]
        else:
            operation = f"Run a {code.split('_')[-1].lower()} operation the engine "
            operation = f"Run a sensitive operation ({verb})"
            target = ((extra or {}).get("detail") or app or path or verb)[:120]
        return {"operation": operation, "target": target, "code": code,
                "verb": verb, "app": app, "path": path,
                "allow_delete": bool((extra or {}).get("allow_delete", False)),
                "task_id": rec.task_id, "goal": "",
                "detail": str((extra or {}).get("detail", ""))[:200]}

    def _approval_validator(self, token: str, request: dict) -> tuple[bool, str]:
        """Re-run the safety gate at decision time. State may have changed
        between request and approval, so approval is never trusted blindly."""
        tid = request.get("task_id", "")
        rec = self.safety.tasks.get(tid)
        if rec is not None and rec.cancelled:
            return False, "cancelled"
        app = request.get("app", "")
        if app and self.policy.is_protected(app):
            return False, "blocked_protected_app"
        verb = request.get("verb", "")
        code = request.get("code", "")
        # The verb-level gate (save_file/submit_form) is the only gate decided by
        # verb level. Launch/close gates are decided by their own code, so the
        # level rule must not reject them.
        if verb and str(code).startswith("level=") and level_for_verb(verb) < Level.SENSITIVE:
            return False, "approval_no_longer_required"
        path = request.get("path", "")
        if path:
            try:
                self.safety.check_path_inside_sandbox(
                    path, allow_delete=bool(request.get("allow_delete")))
            except SafetyError as e:
                return False, e.code
            except Exception:  # noqa: BLE001
                return False, "blocked_unsafe_path"
        return True, "validated"

    def _approval_gate(self, graph: Graph, t0: float, rec: TaskRecord,
                       verb: str, code: str, token: str,
                       extra: dict | None = None, done: int = 0,
                       retries: int = 0, failed_step: str = "") -> Receipt | None:
        """Returns None when execution may proceed (approved + re-validated),
        otherwise the receipt the caller should return."""
        request = self._approval_request(rec, verb, code, extra or {})
        ctl = self.controller
        if token and ctl is not None:
            st = ctl.approval_status(token)
            decision = st.get("status")
            if decision == "approve":
                ok, why = self._approval_validator(token, request)
                if not ok:
                    return self._receipt(graph, done, retries, t0, rec,
                                         status="blocked", reason=why,
                                         failed_step=failed_step)
                self.safety.log(rec, "approval_granted", token=token, code=code)
                return None
            if decision == "deny":
                return self._receipt(graph, done, retries, t0, rec,
                                     status="blocked", reason="denied_by_user",
                                     failed_step=failed_step)
        fresh = ""
        if ctl is not None:
            fresh = ctl.new_approval_token()
            try:
                ctl.approval_request(fresh, request)
            except Exception:  # noqa: BLE001
                fresh = ""
        return self._receipt(graph, done, retries, t0, rec,
                             status="needs_approval", reason=code,
                             approval_token=fresh, failed_step=failed_step)

    # ------------------------------------------------------------ approval end

    def run(self, graph: Graph, intent_verb: str = "invoke", deadline_ms: int = 60000,
            task_id: str | None = None, approval_token: str = "") -> Receipt:
        t0 = time.perf_counter()
        rec = self.safety.tasks.get(task_id or "")
        if rec is None:
            rec = self.safety.new_task(wanted_id=task_id or "")
        lvl = level_for_verb(intent_verb)
        ctl = self.controller
        if ctl is not None:
            try:
                self._bind_controller()
            except Exception:  # noqa: BLE001
                ctl = None
        if lvl >= Level.SENSITIVE:
            gate = self._approval_gate(graph, t0, rec, intent_verb,
                                       f"level={lvl.name}", approval_token)
            if gate is not None:
                return gate

        self.safety.log(rec, "task_start", goal=graph.goal, nodes=len(graph.nodes))
        if ctl is not None:
            try:
                ctl.begin_task(graph.goal, rec.task_id)
            except Exception:  # noqa: BLE001
                ctl = None
        journal = {"goal": graph.goal, "nodes": [
            {"id": n.id, "op": n.op, "target": n.target, "params": n.params}
            for n in graph.nodes], "done": [], "results": {}}
        self._journal[rec.task_id] = journal
        self._approval_rec = rec
        self._gate_outcome = ("", "")
        done = 0
        retries = 0
        ctx: dict[str, Any] = {}
        for node in graph.nodes:
            try:
                self.safety.check_budget(rec, done)
            except SafetyError as e:
                if ctl is not None:
                    try:
                        ctl.blocked(e.code)
                    except Exception:  # noqa: BLE001
                        pass
                return self._receipt(graph, done, retries, t0, rec, status="cancelled"
                                     if e.code == "cancelled" else "blocked", reason=e.code,
                                     failed_step=node.id)
            if self.stop.cancelled or rec.cancelled:
                return self._finish_cancelled(graph, done, retries, t0, rec)
            if ctl is not None:
                try:
                    if ctl.check_paused(
                            lambda: self.stop.cancelled or rec.cancelled) == "stop":
                        return self._finish_cancelled(graph, done, retries, t0, rec)
                    app_hint = ((node.params or {}).get("app_hint")
                                or (node.target if isinstance(node.target, str) else (node.target.get("name") if isinstance(node.target, dict) else ""))
                                or ("Chrome" if "browser" in node.op else "")
                                or "Windows")
                    ctl.node_update(node.op, app_hint=str(app_hint))
                except Exception:  # noqa: BLE001
                    pass
            span = self.profiler.begin(node.op, "uia")
            try:
                self._run_node(node, ctx, rec)
                span.stop(success=True)
                done += 1
                journal["done"].append(node.id)
            except SafetyError as e:
                span.stop(success=False, error_code=e.code)
                self.safety.log(rec, "blocked", op=node.op, code=e.code, detail=e.detail)
                # needs_approval_* reaching here means the human gate did not
                # unlock it (denied, stopped, timed out or re-validation failed).
                outcome, why = self._gate_outcome
                if e.code.startswith("needs_approval"):
                    if outcome == "deny":
                        status, reason = "blocked", "denied_by_user"
                    elif outcome == "stopped":
                        status, reason = "cancelled", "cancelled"
                    elif outcome == "timeout":
                        status, reason = "needs_approval", e.code
                    else:
                        # gate absent, errored, or re-validation refused:
                        # fail closed exactly as before approvals existed
                        status, reason = "blocked", why or e.code
                else:
                    status = "needs_ai" if e.code.startswith("needs_ai") else "blocked"
                    reason = e.code
                if status == "cancelled":
                    return self._finish_cancelled(graph, done, retries, t0, rec)
                return self._receipt(graph, done, retries, t0, rec, status=status,
                                     reason=reason, failed_step=node.id)
            except LookupError as e:
                # structure may have changed: bump handle epoch, then classified retry
                self.uia.bump_epoch()
                # classified retry: safe ops re-resolve once; unsafe ops escalate
                if node.op in SAFE_RETRY_OPS:
                    retries += 1
                    span.retries = 1
                    try:
                        self._run_node(node, ctx, rec, retry=True)
                        span.stop(success=True)
                        done += 1
                        journal["done"].append(node.id)
                    except (LookupError, SafetyError) as e2:
                        span.stop(success=False, error_code=str(e2)[:200])
                        return self._receipt(graph, done, retries, t0, rec, status="needs_ai",
                                             failed_step=node.id, reason=str(e2)[:500],
                                             candidates=self._candidates(ctx),
                                             resume_handle=f"R{rec.task_id}@v{done}")
                else:
                    span.stop(success=False, error_code="requires_re_resolution")
                    return self._receipt(graph, done, retries, t0, rec, status="needs_ai",
                                         failed_step=node.id,
                                         reason=f"requires_re_resolution: {e}"[:500],
                                         candidates=self._candidates(ctx),
                                         resume_handle=f"R{rec.task_id}@v{done}")
            except Exception as e:  # noqa: BLE001
                span.stop(success=False, error_code=f"{type(e).__name__}:{e}"[:200])
                return self._receipt(graph, done, retries, t0, rec, status="failed",
                                     failed_step=node.id, reason=str(e)[:500])
        return self._receipt(graph, done, retries, t0, rec, status="success",
                             result=ctx.get("result", {}))

    # ---------------------------------------------------------------- nodes

    def _run_node(self, node, ctx: dict, rec: TaskRecord, retry: bool = False) -> None:
        op = node.op
        if op == "open_app":
            app = (node.params or {}).get("app_hint") or node.target or "Notepad"
            if isinstance(app, dict):
                app = app.get("name", "Notepad")
            path = (node.params or {}).get("path")
            force_new = bool((node.params or {}).get("new_instance") or (node.params or {}).get("force_new"))

            existing_windows = []
            if not force_new and not path and str(app).strip().lower() not in ("settings", "windows settings", "ms-settings"):
                existing_windows = self.uia.find_windows_all(str(app))

            if existing_windows and not force_new:
                if len(existing_windows) == 1:
                    win = existing_windows[0]
                    hwnd_found = self.uia.control_hwnd(win) or self._record_window(rec, win, str(app), str(app))
                    if hwnd_found and hwnd_found not in rec.windows_opened:
                        rec.windows_opened.append(hwnd_found)
                    if hwnd_found:
                        self._task_window_apps[(rec.task_id, hwnd_found)] = (str(app), str(app))
                    ctx["result"] = {"window": app, "hwnd": hwnd_found, "mode": "attached_existing"}
                else:
                    raise SafetyError("ambiguous_target",
                                      f"Multiple ({len(existing_windows)}) windows matching '{app}' found. "
                                      "Specify unique window title or target.")
            else:
                before = self._top_hwnds()  # snapshot BEFORE launch (stale-window exclusion)
                launched_pid = self._launch(str(app), rec, path=path)
                expect = str(app)
                if path and str(app).strip().lower() in ("explorer", "file explorer"):
                    expect = os.path.basename(os.path.normpath(path)) or str(app)
                if launched_pid:
                    try:
                        win = self._wait_window_for_pid(launched_pid, expect, timeout_s=12)
                    except LookupError:
                        if str(app).strip().lower() not in ("explorer", "file explorer", "chrome", "google chrome", "msedge", "edge"):
                            raise
                        win = self._wait_new_hwnd(expect, before, timeout_s=12)
                    hwnd_found = self.uia.control_hwnd(win) or self._record_window(rec, win, expect)
                    if hwnd_found and hwnd_found not in rec.windows_opened:
                        rec.windows_opened.append(hwnd_found)
                    if hwnd_found:
                        self._task_window_apps[(rec.task_id, hwnd_found)] = (expect, str(app))
                else:
                    self._wait_window(expect, timeout_s=10)
                    win = self.uia.find_window(expect, timeout_s=5)
                    hwnd_found = self._record_window(rec, win, expect, str(app))
                ctx["result"] = {"window": app, "hwnd": hwnd_found, "mode": "launched_new"}
            self.uia.bump_epoch()  # structure changed: old handles go stale
        elif op == "launch":
            self._launch(node.params.get("app_hint") or node.params.get("path", ""), rec,
                         path=node.params.get("path"))
        elif op == "wait_window":
            self._wait_window(node.params.get("app_hint", ""), timeout_s=10)
        elif op == "verify_window":
            app_hint = node.params.get("app_hint", "")
            win = self.uia.find_window(app_hint, timeout_s=5)
            hwnd_found = self._record_window(rec, win, app_hint, app_hint)
            ctx["result"] = {"window": app_hint, "hwnd": hwnd_found}
        elif op == "find":
            tgt = self._target(node.target)
            ctl, method = self._resolve_affinitive(node, rec)
            ctx["control"] = ctl
            ctx["found_via"] = method
            h = self.uia.mint(tgt, app_hint=(node.params or {}).get("app_hint"))
            ctx["handle"] = h.hid
            ctx["result"] = {"found_via": method, "handle": h.hid}
        elif op == "set_value":
            ctl = ctx.get("control") or self._resolve_inline(node, rec)
            how = self._set_value_routed(node, rec, ctl)
            ctx["result"] = {"set_via": how}
        elif op == "invoke":
            ctl = ctx.get("control") or self._resolve_inline(node, rec)
            how = self._invoke_routed(rec, ctl)
            ctx["result"] = {"invoked_via": how}
        elif op == "type":
            ctl = ctx.get("control") or self._resolve_inline(node, rec)
            self._guarded_sendkeys(node, ctx, rec, kind="type", control=ctl,
                                   text=node.params.get("text", ""))
            ctx["result"] = {"typed": len(node.params.get("text", ""))}
        elif op == "press":
            self._guarded_sendkeys(node, ctx, rec, kind="press",
                                   text=node.params.get("keys", ""))
            ctx["result"] = {"pressed": "<redacted>"}
        elif op == "close_window":
            self._close_exact(node, rec)
            ctx["result"] = {"closed": node.target}
        elif op == "browser_open":
            handle = self._browser_open(node, rec)
            self._task_browser[rec.task_id] = handle
            ctx["result"] = {"backend": (self._sessions.get(handle).backend
                                         if self._sessions.get(handle) else "?"),
                             "session": handle}
            ctx["browser_session"] = handle
        elif op == "browser_navigate":
            be, res = self._browser_navigate(node, rec, ctx)
            ctx["result"] = {"backend": be, "url": res,
                             "session": self._node_session(node, ctx, rec)}
        elif op == "browser_extract":
            be, res = self._browser_extract(node, rec, ctx)
            ctx["result"] = {"backend": be, **res,
                             "session": self._node_session(node, ctx, rec)}
        elif op == "browser_close":
            closed = self._browser_close(node, rec)
            ctx["result"] = {"browser": False, "closed": closed}
        elif op == "resume":
            self._run_resume(node, ctx, rec)
        elif op == "inspect":
            tgt = self._target(node.target)
            ctl, method = self._resolve_affinitive(node, rec)
            ctx["result"] = {"found_via": method, "name": getattr(ctl, "Name", "")}
        elif op in ("wait", "verify"):
            if op == "wait" and (node.params or {}).get("path"):
                # deterministic file wait (save-commit is async): poll, don't sleep blind.
                # min_mtime guards against pre-existing files (overwrite-confirm states
                # must never false-positive a wait).
                import os as _os

                p = self.safety.check_path_inside_sandbox((node.params or {})["path"])
                t0 = time.time()
                timeout = float((node.params or {}).get("timeout_s", 10))
                min_mtime = float((node.params or {}).get("min_mtime", 0))
                while time.time() - t0 < timeout:
                    try:
                        if _os.path.exists(p) and _os.path.getmtime(p) >= min_mtime:
                            break
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(0.3)
                else:
                    raise LookupError(f"wait_file_timeout {p!r}")
                ctx["result"] = {"op": "wait_file", "path": p}
            else:
                ctx["result"] = {"op": op, "noop": True}
        else:
            raise ValueError(f"unknown_op {op}")

    # ------------------------------------------------------------ guarded primitives

    def _set_value_routed(self, node, rec: TaskRecord, ctl) -> str:
        """Router-ordered set with per-mechanism readback verification."""
        import uiautomation as auto

        from .router import order_mechanisms

        want = node.params.get("text", "")
        errors: list[str] = []
        for mech in order_mechanisms("set_value", ctl):
            span = self.profiler.begin(f"set_{mech}", "uia")
            try:
                if mech == "value_pattern":
                    pat = ctl.GetValuePattern()
                    if pat is None or pat.IsReadOnly:
                        raise LookupError("no_writable_value_pattern")
                    # waitTime=0: uiautomation sleeps OPERATION_WAIT_TIME (0.5s)
                    # after every call by default; readback below is the verify.
                    pat.SetValue(want, waitTime=0)
                elif mech == "clipboard_paste":
                    rec.input_events += 1
                    if self.controller is not None:
                        try:
                            self.controller.input_window(1200.0)
                        except Exception:  # noqa: BLE001
                            pass
                    self.uia.clipboard_paste(ctl, want)
                elif mech == "send_keys":
                    rec.input_events += 1
                    try:
                        ctl.SetFocus()
                    except Exception:  # noqa: BLE001
                        pass
                    if self.controller is not None:
                        try:
                            self.controller.input_window(800.0)
                        except Exception:  # noqa: BLE001
                            pass
                    auto.SendKeys(want)
                else:
                    raise ValueError(f"unknown_mechanism {mech}")
                got = self.uia.read_value(ctl)
                if want and got != want:
                    raise LookupError(f"verify_failed got={got!r} want={want!r}")
                span.stop(success=True)
                self.safety.log(rec, "set_value", mechanism=mech)
                return mech
            except SafetyError:
                span.stop(success=False, error_code="safety")
                raise
            except (LookupError, ValueError) as e:
                span.stop(success=False, error_code=str(e)[:120])
                errors.append(f"{mech}:{e}")
                continue
            except Exception as e:  # noqa: BLE001
                # transient provider/COM failure: re-resolvable, never blind
                span.stop(success=False, error_code=f"provider_transient:{type(e).__name__}")
                errors.append(f"{mech}:provider_transient:{e}"[:200])
                continue
        raise LookupError(f"all_set_mechanisms_failed {' | '.join(errors)}"[:400])

    def _invoke_routed(self, rec: TaskRecord, ctl) -> str:
        import time as _time
        import uiautomation as auto  # noqa: F401 (Click path uses control methods)

        from .router import order_mechanisms

        # readiness: don't invoke a button that's still initializing (dialog races
        # swallow the invoke and leave the dialog open). Bounded enabled-poll.
        t0 = _time.time()
        while _time.time() - t0 < 3.0:
            try:
                if bool(ctl.GetPropertyValue(30010)):  # UIA_IsEnabledPropertyId
                    break
            except Exception:  # noqa: BLE001
                break  # property unavailable: proceed, verify after
            _time.sleep(0.2)
        errors: list[str] = []
        for mech in order_mechanisms("invoke", ctl):
            try:
                if mech == "invoke_pattern":
                    pat = ctl.GetInvokePattern()
                    if pat is None:
                        raise LookupError("no_invoke_pattern")
                    # waitTime=0: wrapper sleeps 0.5s by default; callers verify
                    # effects deterministically (dialog dismissal, file, state).
                    pat.Invoke(waitTime=0)
                    return mech
                elif mech == "uia_click":
                    ctl.Click(waitTime=0)
                    return mech
                raise ValueError(f"unknown_mechanism {mech}")
            except (LookupError, ValueError) as e:
                errors.append(f"{mech}:{e}")
                continue
        raise LookupError(f"all_invoke_mechanisms_failed {' | '.join(errors)}"[:400])

    def _record_window(self, rec: TaskRecord, win, expect_title: str,
                       app_label: str = "") -> int:
        """Record task-owned window handle with exact-title-scan fallback."""
        hwnd_found = self.uia.control_hwnd(win)
        if not hwnd_found:
            # NativeWindowHandle can fail on some hosts; regex title scan fallback
            import re as _re
            import uiautomation as auto

            try:
                for c in auto.GetRootControl().GetChildren():
                    try:
                        if "Window" in c.ControlTypeName and _re.search(
                                f".*{_re.escape(expect_title)}.*", c.Name or "",
                                _re.IGNORECASE):
                            hwnd_found = int(c.NativeWindowHandle or 0)
                            if hwnd_found:
                                break
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
        if hwnd_found and hwnd_found not in rec.windows_opened:
            if len(rec.windows_opened) >= self.safety.budget.max_total_tracked_windows:
                raise SafetyError("resource_limit_exceeded", "max_total_tracked_windows")
            rec.windows_opened.append(hwnd_found)
        if hwnd_found:
            self._task_window_apps[(rec.task_id, hwnd_found)] = (
                expect_title, app_label or expect_title)
        if hwnd_found and hwnd_found not in self._window_first_seen:
            now = time.time()
            # prune entries older than 1h (hwnd reuse safety + bounded growth)
            for h, t in list(self._window_first_seen.items()):
                if now - t > 3600:
                    del self._window_first_seen[h]
            self._window_first_seen[hwnd_found] = now
        return hwnd_found

    def _scope(self, node, rec=None):
        """Task-affinity scope: prefer THIS task's recorded window whose label
        matches the node's app_hint (most recent first). Falls back to global
        title search. Duplicate same-titled windows from other tasks/reps must
        never hijack routing."""
        app_hint = (node.params or {}).get("app_hint") or ""
        if app_hint and rec is not None:
            w = self._owned_window_for(rec, app_hint)
            if w is not None:
                return w
        if app_hint:
            return self.uia.find_window(app_hint, timeout_s=10)
        return self.uia._desktop()

    def _owned_window_for(self, rec, app_hint: str):
        """Most recent task-owned window whose app matches hint (alive only)."""
        hint = (app_hint or "").lower()
        cands: list[tuple[str, object]] = []
        for h in list(getattr(rec, "windows_opened", []) or []):
            label, app = self._task_window_apps.get((rec.task_id, h), ("", ""))
            if hint and hint not in label.lower() and hint not in app.lower() \
                    and label.lower() not in hint:
                continue
            try:
                cands.append((label, self.uia.scope_from_hwnd(h)))
            except LookupError:
                continue
        for label, c in reversed(cands):
            try:
                name = (c.Name or "").lower()
                if not hint or hint in name or name in hint:
                    return c
            except Exception:  # noqa: BLE001
                continue
        return cands[-1][1] if cands else None

    def _target(self, t: dict | None) -> Target:
        t = t or {}
        return Target(name=t.get("name"), automation_id=t.get("automation_id"),
                      control_type=t.get("control_type"), regex_name=t.get("regex_name"))

    def _resolve_affinitive(self, node, rec, timeout_s: float = 5.0):
        """Resolve preferring task-owned window; on miss, ONE global fallback.

        Prevents duplicate same-titled windows (other tasks/reps) from hijacking
        routing, while still supporting multi-app tasks (owned[-1] may be the
        wrong app for this node -> global fallback finds the right one).
        """
        t = self._target(node.target)
        try:
            return self.uia.resolve(self._scope(node, rec), t, timeout_s=timeout_s)
        except LookupError as e:
            owned = list(getattr(rec, "windows_opened", []) or []) if rec else []
            if owned and (node.params or {}).get("app_hint"):
                # affinity scope may have been the wrong app: try global once, short
                scope = self.uia.find_window((node.params or {})["app_hint"], timeout_s=3)
                return self.uia.resolve(scope, t, timeout_s=3)
            raise

    def _resolve_inline(self, node, rec=None):
        scope_app = (node.params or {}).get("app_hint") or ""
        t = self._target(node.target)
        if (os.environ.get("ORVEX_NO_RESOLVE_CACHE")
                or os.environ.get("SFMCP_NO_RESOLVE_CACHE", "0")) == "1":
            return self._resolve_affinitive(node, rec)[0]
        key = (getattr(rec, "task_id", "") if rec else "",
               scope_app, t.automation_id, t.name, t.control_type, t.regex_name,
               self.uia._epoch)
        hit = self._resolved.get(key)
        if hit is not None:
            try:
                # liveness probe: cheap property read; dead COM -> re-resolve
                _ = hit.CurrentName if hasattr(hit, "CurrentName") else getattr(hit, "Name", "")
                return hit
            except Exception:  # noqa: BLE001
                self._resolved.pop(key, None)
        ctl, _ = self._resolve_affinitive(node, rec)
        self._resolved[key] = ctl
        return ctl

    def _launch(self, app: str, rec: TaskRecord, path: str | None = None) -> int | None:
        self.safety.check_launch(app, rec, path=path)
        key = (app or "").strip().lower()
        self.safety.log(rec, "launch", app=app, path="<sandbox>" if path else None)
        if key in ("settings", "windows settings", "ms-settings"):
            import os

            os.startfile("ms-settings:")  # type: ignore[attr-defined]
            return None
        if key in ("chrome", "google chrome", "google-chrome", "edge", "msedge", "microsoft edge"):
            from .browser import find_chromium_browser
            try:
                _, exe = find_chromium_browser()
            except Exception:
                exe = "chrome.exe" if "chrome" in key else "msedge.exe"
        else:
            exe = "explorer.exe" if key in ("file explorer", "explorer") else (
                "notepad.exe" if key in ("notepad", "notepad.exe") else os.path.basename(key))
            if "." not in exe:
                exe += ".exe"
        if exe == "explorer.exe" and path:
            # folder targeting allowed ONLY inside sandbox (§2)
            safe = self.safety.check_path_inside_sandbox(path)
            proc = subprocess.Popen([exe, safe], shell=False)
        else:
            if path:
                raise SafetyError("blocked_unsafe_path", f"path launch not allowed for {exe}")
            proc = subprocess.Popen([exe], shell=False)
        rec.launches += 1
        rec.pids.append(proc.pid)
        rec.pid_names[proc.pid] = exe
        return proc.pid

    @staticmethod
    def _top_hwnds() -> set[int]:
        import uiautomation as auto

        out: set[int] = set()
        try:
            for c in auto.GetRootControl().GetChildren():
                try:
                    h = int(c.NativeWindowHandle or 0)
                    if h:
                        out.add(h)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return out

    def _wait_new_hwnd(self, title_hint: str, before: set[int], timeout_s: int = 12):
        """Wait for a NEW top-level window (not in `before`) matching title."""
        import re as _re
        import uiautomation as auto

        t0 = time.time()
        while time.time() - t0 < timeout_s:
            try:
                for c in auto.GetRootControl().GetChildren():
                    try:
                        h = int(c.NativeWindowHandle or 0)
                        if not h or h in before or "Window" not in c.ControlTypeName:
                            continue
                        if _re.search(f".*{_re.escape(title_hint)}.*", c.Name or "",
                                      _re.IGNORECASE):
                            return c
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)
        raise LookupError(f"wait_new_window_timeout hint={title_hint!r}")

    def _wait_window(self, app_hint: str, timeout_s: int = 10) -> None:
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            try:
                self.uia.find_window(app_hint, timeout_s=1)
                return
            except LookupError:
                time.sleep(0.5)
        raise LookupError(f"wait_window_timeout {app_hint!r}")

    def _wait_window_for_pid(self, pid: int, title_hint: str, timeout_s: int = 15):
        """Launch-attach without duplicate hazard: the NEW window must belong to
        the PID we just launched (same-titled stale windows never match)."""
        import uiautomation as auto

        t0 = time.time()
        last: list = []
        while time.time() - t0 < timeout_s:
            try:
                for c in auto.GetRootControl().GetChildren():
                    try:
                        if "Window" not in c.ControlTypeName:
                            continue
                        if int(c.ProcessId or 0) != int(pid):
                            continue
                        name = c.Name or ""
                        if not title_hint or title_hint.lower() in name.lower():
                            return c
                        last = [c]
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)
        if last:
            return last[0]  # pid matched, title differs (e.g. slow retitle): still ours
        raise LookupError(f"wait_window_pid_timeout pid={pid} hint={title_hint!r}")

    def _guarded_sendkeys(self, node, ctx: dict, rec: TaskRecord, kind: str,
                          text: str, control=None) -> None:
        """7-step OS-level input (§5): resolve->exists->hwnd/pid->app check->
        foreground check->input->state verify. Pattern paths never reach here."""
        import uiautomation as auto

        if kind == "press":
            self.safety.check_keys(text)
        app_hint = (node.params or {}).get("app_hint") or ""
        # 1-2. resolve + verify exists (task-owned window preferred: stale
        # duplicates from other tasks must never receive our keystrokes)
        win = self._owned_window_for(rec, app_hint) if app_hint else None
        if win is None and app_hint:
            win = self.uia.find_window(app_hint, timeout_s=5)
        if control is None and win is not None:
            control = win
        if control is None:
            raise LookupError("input_no_target")
        # 3. hwnd/pid identity
        hwnd = self.uia.control_hwnd(control)
        fg_before = self.uia.foreground_hwnd()
        self.safety.log(rec, "input_inject", kind=kind, hwnd=hwnd,
                        fg_before=fg_before, transport="SendKeys")
        # 4b. input desktop: target thread's desktop must be the input desktop.
        # SendInput to another desktop (UAC secure desktop, logon, screen-saver
        # desktop) is silently swallowed or blocked: refuse explicitly.
        if hwnd and not self._input_desktop_ok(hwnd):
            raise SafetyError("blocked_input", "target not on input desktop")
        # 4c. integrity: UIPI silently swallows Medium->High input. Refuse first.
        if hwnd and not self._integrity_ok(hwnd):
            raise SafetyError("blocked_input", "target integrity above ours")
        if app_hint and win is not None:
            try:
                title = win.Name or ""
            except Exception:  # noqa: BLE001
                title = ""
            if app_hint.lower() not in title.lower() and title.lower() not in app_hint.lower():
                # allow: hint is exe-ish ("Notepad") vs title ("Untitled - Notepad")
                if app_hint.lower() not in ("notepad", "explorer", "settings"):
                    raise SafetyError("foreground_not_target", f"title={title!r} hint={app_hint!r}")
        # 5. foreground if transport requires it (SendKeys does): one guarded correction
        if hwnd and hwnd != fg_before:
            try:
                if hasattr(control, "SetActive"):
                    control.SetActive()
                else:
                    control.SetFocus()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
            fg_now = self.uia.foreground_hwnd()
            if fg_now and fg_now != hwnd:
                # pattern-capable targets should have used patterns; refuse blind keys
                raise SafetyError("foreground_not_target", f"hwnd={hwnd}")
        # 5b. cold-window settle: never inject into a window first seen <1.5s ago
        # (launch-init race swallows keys despite correct foreground). Bounded once.
        first_seen = self._window_first_seen.get(hwnd, 0.0) if hwnd else 0.0
        if hwnd and first_seen:
            remain = 1.5 - (time.time() - first_seen)
            if remain > 0:
                time.sleep(remain)
        # 5c. focus gate for non-window controls: SetFocus + HasKeyboardFocus poll.
        # Top-level windows don't carry keyboard focus themselves: foreground proof
        # above stands for them; controls must prove focus or we refuse input.
        if type(control).__name__ != "WindowControl":
            try:
                control.SetFocus()
            except Exception:  # noqa: BLE001
                pass
            t_focus = time.time()
            while time.time() - t_focus < 2.0:
                try:
                    if control.HasKeyboardFocus:
                        break
                except Exception:  # noqa: BLE001
                    break
            else:
                raise SafetyError("foreground_not_target", "target_not_ready_no_focus")
            try:
                if not control.HasKeyboardFocus:
                    raise SafetyError("foreground_not_target", "target_not_ready_no_focus")
            except SafetyError:
                raise
            except Exception:  # noqa: BLE001
                pass
        # 6. perform (no blind retry: caller must re-resolve)
        rec.input_events += 1
        if self.controller is not None:
            try:
                self.controller.input_window(800.0)
            except Exception:  # noqa: BLE001
                pass
        auto.SendKeys(text)
        # 7. post-state: input must have stayed within the target's window tree.
        # A modal child dialog (e.g. Save As) legitimately takes foreground with
        # a DIFFERENT hwnd — accepted only if its owner chain reaches the target
        # (same-process cross-window theft is still blocked).
        fg_after = self.uia.foreground_hwnd()
        self.safety.log(rec, "input_done", kind=kind, fg_after=fg_after)
        if hwnd and fg_after and fg_after != hwnd and not self._owner_chain_reaches(fg_after, hwnd):
            raise SafetyError("foreground_not_target", "foreground moved during input")

    @staticmethod
    def _owner_chain_reaches(hwnd: int, target: int, depth: int = 8) -> bool:
        """True if hwnd == target or hwnd is owned/parented (transitively) by it."""
        try:
            import ctypes as _ct

            user32 = _ct.windll.user32
            cur = hwnd
            for _ in range(depth):
                if cur == target:
                    return True
                parent = user32.GetParent(cur)
                owner = user32.GetWindow(cur, 4)  # GW_OWNER
                nxt = parent or owner
                if not nxt or nxt == cur:
                    return False
                cur = nxt
            return cur == target
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _pid_of(hwnd: int) -> int:
        try:
            import ctypes as _ct

            pid = _ct.wintypes.DWORD()
            _ct.windll.user32.GetWindowThreadProcessId(hwnd, _ct.byref(pid))
            return int(pid.value)
        except Exception:  # noqa: BLE001
            return -1

    @staticmethod
    def _thread_desktop_name(tid: int) -> str:
        try:
            import ctypes as _ct

            user32 = _ct.windll.user32
            h = user32.GetThreadDesktop(tid)
            if not h:
                return ""
            n = _ct.wintypes.DWORD(0)
            user32.GetUserObjectInformationW(h, 2, None, 0, _ct.byref(n))
            buf = _ct.create_unicode_buffer(max(n.value // 2 + 1, 64))
            if not user32.GetUserObjectInformationW(h, 2, buf, _ct.sizeof(buf), _ct.byref(n)):
                return ""
            return buf.value
        except Exception:  # noqa: BLE001
            return ""

    def _input_desktop_ok(self, hwnd: int) -> bool:
        """Target thread's desktop must equal the input desktop or our thread's desktop (read-only check)."""
        try:
            import ctypes as _ct

            tid = _ct.windll.user32.GetWindowThreadProcessId(hwnd, None)
            if not tid:
                return False
            from .health import _input_desktop_name

            t_name = self._thread_desktop_name(tid).lower()
            i_name = _input_desktop_name().lower()
            if t_name and i_name and t_name == i_name:
                return True
            our_tid = _ct.windll.kernel32.GetCurrentThreadId()
            our_desk = self._thread_desktop_name(our_tid).lower()
            if t_name and our_desk and t_name == our_desk:
                return True
            if not t_name:
                return True
            return False
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _integrity_rid(pid: int) -> int:
        """Mandatory-label RID of a process (read-only): e.g. 0x1000 low,
        0x2000 medium, 0x3000 high, 0x4000 system. -1 on query failure."""
        try:
            import ctypes as _ct
            from ctypes import wintypes as _wt

            advapi = _ct.windll.advapi32
            kernel = _ct.windll.kernel32
            h = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if not h:
                return -1
            try:
                advapi.GetTokenInformation.argtypes = [
                    _ct.c_void_p, _ct.c_int, _ct.c_void_p,
                    _wt.DWORD, _ct.POINTER(_wt.DWORD)]
                advapi.GetTokenInformation.restype = _wt.BOOL
                tok = _ct.c_void_p()
                if not advapi.OpenProcessToken(h, 0x0008, _ct.byref(tok)):  # TOKEN_QUERY
                    return -1
                try:
                    need = _wt.DWORD(0)
                    advapi.GetTokenInformation(tok, 25, None, 0, _ct.byref(need))
                    buf = _ct.create_string_buffer(need.value or 64)
                    if not advapi.GetTokenInformation(tok, 25, buf, need, _ct.byref(need)):
                        return -1
                    # TOKEN_MANDATORY_LABEL.Label.Sid; last SubAuthority = RID
                    import ctypes as _c2

                    class _SID_AND_ATTR(_c2.Structure):
                        _fields_ = [("Sid", _c2.c_void_p), ("Attributes", _c2.c_ulong)]

                    class _TML(_c2.Structure):
                        _fields_ = [("Label", _SID_AND_ATTR)]

                    tml = _TML.from_buffer(buf)
                    pSid = tml.Label.Sid
                    advapi.GetSidSubAuthorityCount.argtypes = [_ct.c_void_p]
                    advapi.GetSidSubAuthorityCount.restype = _ct.POINTER(_ct.c_ubyte)
                    advapi.GetSidSubAuthority.argtypes = [_ct.c_void_p, _wt.DWORD]
                    advapi.GetSidSubAuthority.restype = _ct.POINTER(_wt.DWORD)
                    cntp = advapi.GetSidSubAuthorityCount(pSid)
                    if not cntp or cntp.contents.value == 0:
                        return -1
                    subp = advapi.GetSidSubAuthority(pSid, cntp.contents.value - 1)
                    return int(subp.contents.value) if subp else -1
                finally:
                    kernel.CloseHandle(tok)
            finally:
                kernel.CloseHandle(h)
        except Exception:  # noqa: BLE001
            return -1

    def _integrity_ok(self, hwnd: int) -> bool:
        """Refuse Medium->High injection up front (UIPI would swallow silently)."""
        import os as _os

        target = self._integrity_rid(self._pid_of(hwnd))
        if target < 0:
            return False  # cannot prove compatibility: refuse
        ours = self._integrity_rid(_os.getpid())
        if ours < 0:
            return False
        return ours >= target

    def _close_exact(self, node, rec: TaskRecord) -> None:
        """Close exactly one verified window (§9). Multi-match -> ambiguous_target."""
        import uiautomation as auto

        t = node.target or {}
        title = t.get("name") or t.get("title") or ""
        want_hwnd = (node.params or {}).get("hwnd") or 0
        if want_hwnd == "FROM_OPEN":
            # thread this run's last recorded window (receipt handles get used)
            want_hwnd = rec.windows_opened[-1] if rec.windows_opened else 0
        if not title and not want_hwnd:
            raise SafetyError("ambiguous_target", "close needs exact name or hwnd")
        if title:
            self.safety.check_window_target(title=title)
        root = auto.GetRootControl()
        matches = []
        if want_hwnd:
            # hwnd is authoritative (titles race during save-commit): poll briefly
            t0 = time.time()
            while time.time() - t0 < 5.0:
                for c in root.GetChildren():
                    try:
                        if int(c.NativeWindowHandle or 0) == int(want_hwnd):
                            matches = [c]
                            break
                    except Exception:  # noqa: BLE001
                        continue
                if matches:
                    break
                time.sleep(0.4)
        else:
            # title-only: poll briefly for appearance (save-commit renames async);
            # >1 match at any poll fails immediately (never guess).
            t0 = time.time()
            while time.time() - t0 < 5.0:
                matches = []
                for c in root.GetChildren():
                    try:
                        if title and (c.Name or "") == title:
                            matches.append(c)
                    except Exception:  # noqa: BLE001
                        continue
                if len(matches) == 1 or len(matches) > 1:
                    break
                time.sleep(0.4)
        if len(matches) != 1:
            raise SafetyError("ambiguous_target", f"matches={len(matches)} title={title!r}")
        target = matches[0]
        hwnd = int(target.NativeWindowHandle or 0)
        # ownership: task-created hwnd, or PID launched by this task — else refuse.
        # (Explorer folder windows live under the shell PID by design, so PID
        #  alone can't prove non-ownership; the hwnd record is authoritative.)
        pid = 0
        try:
            pid = int(target.ProcessId or 0)
        except Exception:  # noqa: BLE001
            pass
        owned = (hwnd and hwnd in rec.windows_opened) or (pid and pid in rec.pids)
        test_cleanup = bool((node.params or {}).get("test_cleanup"))
        if not owned:
            if not (test_cleanup and title and title.startswith(
                    self.safety.TEST_CLEANUP_PREFIXES)):
                raise SafetyError("needs_approval_close", f"hwnd={hwnd} pid={pid} not task-owned")
            self.safety.log(rec, "test_cleanup_close", hwnd=hwnd, title=title)
        try:
            title_now = getattr(target, "Name", "")
        except Exception:  # noqa: BLE001
            title_now = ""
        self.safety.check_window_target(title=title_now)  # title only: pid is shell-owned by design
        self.safety.log(rec, "close_window", hwnd=hwnd, pid=pid)
        # drain owned modal child dialogs first (save-commit teardown is async;
        # closing the main window under an open dialog is refused by Windows).
        # Scoped to the OWNED window (no full-desktop walks: heavy polling
        # starves the providers it watches), 1s cadence, bounded deadline.
        # Persistent dialogs after the deadline escalate to needs_ai.
        t0 = time.time()
        while time.time() - t0 < 6.0:
            try:
                owner = None
                for c in auto.GetRootControl().GetChildren():
                    try:
                        if hwnd and int(c.NativeWindowHandle or 0) == hwnd:
                            owner = c
                            break
                    except Exception:  # noqa: BLE001
                        continue
                if owner is None:
                    break  # window already gone
                if not any(k.ControlTypeName == "WindowControl"
                           for k in owner.GetChildren()):
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(1.0)
        closer = target
        try:
            for c in auto.GetRootControl().GetChildren():
                try:
                    if hwnd and int(c.NativeWindowHandle or 0) == hwnd:
                        closer = c
                        break
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        closer.GetWindowPattern().Close()
        rec.closes += 1
        # verify closed with bounded teardown grace (save-commit/dialog teardown
        # is async); a lingering OWNED modal dialog afterwards is an explicit
        # recoverable state, not a silent failure.
        t0 = time.time()
        while time.time() - t0 < 3.0:
            gone = True
            for c in auto.GetRootControl().GetChildren():
                try:
                    if hwnd and int(c.NativeWindowHandle or 0) == hwnd:
                        gone = False
                        break
                except Exception:  # noqa: BLE001
                    continue
            if gone:
                break
            time.sleep(1.0)
        if not gone:
            try:
                for c in auto.GetRootControl().GetChildren():
                    if hwnd and int(c.NativeWindowHandle or 0) == hwnd:
                        for d in c.GetChildren():
                            try:
                                if d.ControlTypeName == "WindowControl":
                                    name = getattr(d, "Name", "") or ""
                                    # overwrite-confirm is a distinct recoverable
                                    # state (choose a fresh name); never dismiss blindly.
                                    if name.lower().startswith("confirm"):
                                        raise SafetyError(
                                            "needs_ai_overwrite_confirm",
                                            f"hwnd={hwnd} save blocked by {name!r}: "
                                            "file exists; resume with a fresh path")
                                    raise SafetyError(
                                        "needs_ai_close_blocked",
                                        f"hwnd={hwnd} blocked by modal dialog {name!r}")
                            except SafetyError:
                                raise
                            except Exception:  # noqa: BLE001
                                continue
            except SafetyError:
                raise
            except Exception:  # noqa: BLE001
                pass
            raise SafetyError("close_verify_failed", f"hwnd={hwnd} still present")
        self.uia.bump_epoch()  # structure changed: old handles go stale

    # ------------------------------------------------------------ browser backend

    def _browser_backend_order(self, op: str, override: str = "") -> list[str]:
        from .router import order_mechanisms

        if override and override != "auto":
            if override != "cdp":
                raise ValueError(f"unsupported_browser_backend {override}: ORVEX uses CDP with existing Chrome/Edge")
            return [override]
        return order_mechanisms(op if op in ("browse_extract", "browse_navigate") else "browse_extract")

    def _node_session(self, node, ctx: dict, rec) -> str:
        handle = (node.params or {}).get("session") or ctx.get("browser_session", "") \
            or self._task_browser.get(rec.task_id, "")
        if handle:
            self._task_browser[rec.task_id] = handle
        return handle

    def _browser_open(self, node, rec: TaskRecord) -> str:
        import tempfile as _tf

        params = node.params or {}
        override = params.get("backend", "auto")
        want_reuse = params.get("session", "")
        if want_reuse:
            ok, reason = self._sessions.validate(want_reuse)
            if ok:
                s = self._sessions.get(want_reuse)
                assert s is not None
                self._sessions.touch(s)
                self.safety.log(rec, "browser_reuse", session=s.handle)
                return s.handle
            # targeted re-attach once: drop stale, fall through to create
            self._sessions.close(want_reuse)
            self.safety.log(rec, "browser_reattach", stale=want_reuse, reason=reason)
            if reason == "session_unknown":
                raise SafetyError("session_stale", want_reuse)
        order = self._browser_backend_order("browse_navigate", override)
        errors: list[str] = []
        for be in order:
            try:
                if be == "cdp":
                    from .browser import CDP, discover_page_target, find_chromium_browser, get_running_browsers

                    force_new = bool(params.get("new_instance") or params.get("force_new") or (params.get("fallback") == "isolated"))

                    # Step 1: Attempt discovery of existing debuggable browser target
                    if not force_new:
                        try:
                            port, target = discover_page_target(
                                url_filter=params.get("url", ""),
                                title_filter=params.get("title", ""),
                            )
                            sess = CDP(target["webSocketDebuggerUrl"])
                            s = self._sessions.create(
                                "cdp", pid=None, port=port,
                                profile=None, ws_url=target.get("webSocketDebuggerUrl"),
                                session_obj=sess, proc=None, owner_task=rec.task_id,
                            )
                            self.safety.log(rec, "browser_open", backend="cdp", mode="attached_existing",
                                            port=port, title=target.get("title"), session=s.handle)
                            return s.handle
                        except LookupError:
                            pass

                    # Step 2: Check if browser process is running without debug endpoint
                    running_browsers = get_running_browsers()
                    allow_fallback = bool(params.get("allow_fallback") or (params.get("fallback") == "isolated") or force_new)
                    if running_browsers and not allow_fallback:
                        raise LookupError(
                            f"browser_not_attachable: Running {', '.join(running_browsers)} process detected, "
                            "but no active remote debugging endpoint is available. "
                            "To automate your existing browser session, launch Chrome/Edge with remote debugging "
                            "(e.g. chrome.exe --remote-debugging-port=9222). "
                            "Alternatively, pass params={'fallback': 'isolated'} to start an isolated ORVEX session."
                        )

                    # Step 3: Launch supported isolated session (fallback or when no browser running)
                    browser_kind, browser_exe = find_chromium_browser()
                    profile = _tf.mkdtemp(prefix=f"sfmcp-{browser_kind}-")
                    self.safety.check_browser_launch(headless=True, profile_dir=profile)
                    import socket as _sock

                    _s = _sock.socket()
                    _s.bind(("127.0.0.1", 0))
                    port = _s.getsockname()[1]
                    _s.close()
                    proc = subprocess.Popen(
                        [browser_exe, f"--remote-debugging-port={port}",
                         f"--user-data-dir={profile}", "--no-first-run",
                         "--headless=new", "about:blank"])
                    rec.launches += 1
                    rec.pids.append(proc.pid)
                    rec.pid_names[proc.pid] = f"{browser_kind}-headless"
                    target = None
                    t0 = time.time()
                    while time.time() - t0 < 20:
                        try:
                            _, target = discover_page_target([port])
                            break
                        except LookupError:
                            time.sleep(0.5)
                    if not target:
                        proc.terminate()
                        raise LookupError(f"cdp_no_target_{port}")
                    sess = CDP(target["webSocketDebuggerUrl"])
                    s = self._sessions.create("cdp", pid=proc.pid, port=port,
                                              profile=profile, ws_url=target.get("webSocketDebuggerUrl"),
                                              session_obj=sess, proc=proc, owner_task=rec.task_id)
                    mode_label = "isolated_fallback" if running_browsers else "launched_ephemeral"
                    self.safety.log(rec, "browser_open", backend="cdp", browser=browser_kind,
                                    mode=mode_label, session=s.handle)
                    return s.handle
            except Exception as e:  # noqa: BLE001
                errors.append(f"{be}:{type(e).__name__}:{e}"[:250])
        raise LookupError(f"all_browser_backends_failed {' | '.join(errors)}"[:400])

    def _browser_session(self, node, rec: TaskRecord, ctx: dict):
        handle = self._node_session(node, ctx, rec) or self._default_session()
        ok, reason = self._sessions.validate(handle)
        if not ok:
            raise SafetyError("session_stale" if reason == "session_stale" else "browser_no_session",
                              handle)
        s = self._sessions.get(handle)
        assert s is not None
        self._sessions.touch(s)
        override = (node.params or {}).get("backend", "auto")
        if override != "auto" and override != s.backend:
            raise SafetyError("session_stale", f"backend switch {s.backend}->{override}")
        return s.backend, s.session_obj, s.handle

    def _default_session(self) -> str:
        live = list(self._sessions.sessions.values())
        if len(live) == 1:
            return live[0].handle
        if not live:
            return ""
        raise SafetyError("ambiguous_target", f"{len(live)} browser sessions; pass session handle")

    def _browser_navigate(self, node, rec: TaskRecord, ctx: dict) -> tuple[str, str]:
        be, sess, _h = self._browser_session(node, rec, ctx)
        url = (node.params or {}).get("url", "")
        if not url.startswith("http"):
            raise SafetyError("blocked_unsafe_path", f"browser url {url!r}")
        try:
            sess.navigate(url)
            final = sess.wait_for_url(url.split("//", 1)[-1].split("/", 1)[0], timeout_s=20)
        except (TimeoutError, RuntimeError, ConnectionError) as e:
            raise LookupError(f"browser_navigate_failed {e}"[:300]) from e
        return be, final

    def _browser_extract(self, node, rec: TaskRecord, ctx: dict) -> tuple[str, dict]:
        be, sess, _h = self._browser_session(node, rec, ctx)
        kind = (node.params or {}).get("kind", "text")
        css = (node.params or {}).get("css", "body")
        try:
            if kind == "table":
                return be, {"rows": sess.extract_table(css)}
            sess.wait_for_selector(css, timeout_s=10)
            return be, {"text": sess.extract_text(css)}
        except (TimeoutError, RuntimeError, ConnectionError) as e:
            raise LookupError(f"browser_extract_failed css={css!r} {e}"[:300]) from e

    def _browser_close(self, node, rec: TaskRecord) -> list[str]:
        handle = (node.params or {}).get("session", "")
        if (node.params or {}).get("all"):
            out = list(self._sessions.sessions.keys())
            for sid in out:
                self._sessions.close(sid)
            self.safety.log(rec, "browser_close_all", count=len(out))
            return out
        if not handle:
            handle = self._default_session()
        ok = self._sessions.close(handle)
        self.safety.log(rec, "browser_close", session=handle, ok=ok)
        return [handle] if ok else []

    def _candidates(self, ctx: dict) -> list[dict]:
        found = ctx.get("found_via")
        return [{"hint": "refine target with automation_id or regex_name", "found_via": found}]

    # ------------------------------------------------------------ resume/recovery

    def _run_resume(self, node, ctx: dict, rec: TaskRecord) -> None:
        """Resume from a journal resume_handle with a semantic correction.

        params: {resume_handle: "R<taskid>[@v<done>]", correction: {target?, params?}}
        Validates journal, version, budgets; re-runs from first not-done node.
        """
        from .compiler import Node as _Node

        p = node.params or {}
        handle = p.get("resume_handle", "")
        if not handle.startswith("R"):
            raise SafetyError("resume_invalid", f"bad handle {handle!r}")
        body = handle[1:]
        want_ver = None
        if "@v" in body:
            body, want_ver = body.split("@v", 1)
        journal = self._journal.get(body)
        if not journal:
            raise SafetyError("resume_invalid", f"unknown execution {body!r}")
        if want_ver is not None and want_ver != str(len(journal["done"])):
            raise SafetyError("resume_invalid",
                              f"version {want_ver} vs completed {len(journal['done'])}")
        correction = p.get("correction") or {}
        remaining = [n for n in journal["nodes"] if n["id"] not in journal["done"]]
        if not remaining:
            ctx["result"] = {"resumed": False, "reason": "already_complete"}
            return
        self.safety.log(rec, "resume_start", from_task=body, nodes=len(remaining))
        first = remaining[0]
        if correction.get("target") is not None:
            first["target"] = correction["target"]
        if correction.get("params") is not None:
            merged = dict(first.get("params") or {})
            merged.update(correction["params"])
            first["params"] = merged
        for n in remaining:
            self.safety.check_budget(rec, 0)
            if self.stop.cancelled or rec.cancelled:
                raise SafetyError("cancelled", "during resume")
            sub = _Node(id=n["id"], op=n["op"], target=n.get("target"), params=n.get("params") or {})
            self._run_node(sub, ctx, rec)
            journal["done"].append(n["id"])
        ctx["result"] = {"resumed": True, "resumed_from": body,
                         "replayed": [n["id"] for n in remaining]}

    def _finish_cancelled(self, graph: Graph, done: int, retries: int, t0: float, rec: TaskRecord) -> Receipt:
        cleanup = self._cleanup_owned(rec)
        return self._receipt(graph, done, retries, t0, rec, status="cancelled",
                             reason="cancelled", cleanup=cleanup)

    def _cleanup_owned(self, rec: TaskRecord) -> dict:
        """Close ONLY task-owned windows + remove task sandbox files. Never taskkill."""
        import uiautomation as auto

        closed = 0
        try:
            live = {int(c.NativeWindowHandle or 0): c for c in auto.GetRootControl().GetChildren()}
        except Exception:  # noqa: BLE001
            live = {}
        for hwnd in list(rec.windows_opened):
            ctl = live.get(hwnd)
            if ctl is None:
                continue
            try:
                pid = int(ctl.ProcessId or 0)
            except Exception:  # noqa: BLE001
                pid = 0
            try:
                self.safety.check_window_target(title="", pid=pid or None)
                ctl.GetWindowPattern().Close()
                closed += 1
            except Exception:  # noqa: BLE001
                continue
        removed = 0
        for f in list(rec.files_created):
            try:
                if os.path.exists(f):
                    os.remove(f)
                    removed += 1
            except Exception:  # noqa: BLE001
                continue
        return {"windows_closed": closed, "processes_terminated": 0, "files_removed": removed}

    def _receipt(self, graph: Graph, done: int, retries: int, t0: float, rec: TaskRecord, **kw) -> Receipt:
        prof = self.profiler.summary()
        status = kw.get("status", "success")
        safety = {
            "system_target_blocked": True,
            "sandbox": self.safety.sandbox,
            "sandbox_enforced": True,
            "resource_limits": True,
            "task_id": rec.task_id,
            "launches": rec.launches,
            "closes": rec.closes,
            "input_events": rec.input_events,
        }
        ctl = self.controller
        if ctl is not None:
            try:
                safety["indicator"] = "active" if ctl.ui_alive else "degraded-no-ui"
                safety["controller_state"] = ctl.state
                if status == "success":
                    ctl.end_task(rec.task_id)
                    # Only go IDLE when ALL active tasks finish.
                    # Prevents IDLE<->RUNNING flicker in multi-call sessions.
                    if not ctl.active_tasks:
                        ctl._set("IDLE", "Ready", "Task completed")
                elif status == "failed":
                    ctl.end_task(rec.task_id)
                    if not ctl.active_tasks:
                        ctl.failed(str(kw.get("reason", ""))[:200])
                elif status == "blocked":
                    ctl.end_task(rec.task_id)
                    if not ctl.active_tasks:
                        if str(kw.get("reason", "")) == "denied_by_user":
                            ctl._set("STOPPED", "Automation Stopped",
                                     "You denied the approval request. Nothing ran.")
                        else:
                            ctl.blocked(str(kw.get("reason", ""))[:200])
                elif status == "cancelled":
                    ctl.end_task(rec.task_id)
                    if not ctl.active_tasks:
                        ctl._set("STOPPED", "Automation Stopped",
                                 str(kw.get("reason", ""))[:200])
                elif status == "needs_approval":
                    # session stays live: the approval panel owns the state
                    safety["approval_token"] = kw.get("approval_token", "")
                # needs_ai: session still live, keep current state
            except Exception:  # noqa: BLE001
                pass
        if status in ("blocked", "cancelled") and kw.get("reason") in (
                "blocked_system_target", "blocked_system_ui", "blocked_unsafe_path",
                "blocked_system_keys", "ambiguous_target", "resource_limit_exceeded",
                "execution_budget_exceeded", "foreground_not_target", "needs_approval_launch",
                "needs_approval_close", "denied_by_user", "blocked_protected_app",
                "cancelled"):
            safety["action_executed"] = False
        return Receipt(
            steps_completed=done, steps_total=len(graph.nodes), retries=retries,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            cache_hits=prof.get("cache_hits", 0), profiler=prof, safety=safety, **kw,
        )
