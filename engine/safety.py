"""Central SafetyPolicy: every execution path passes through here.

Covers: protected processes, system-UI targets, dangerous key combos,
sandbox path validation, budgets/limits, task resource tracking,
structured danger-op logging (no secrets), shell health snapshots.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import tempfile
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------- constants

SYSTEM_PROCESSES = {
    "explorer.exe", "dwm.exe", "winlogon.exe", "csrss.exe", "services.exe",
    "lsass.exe", "smss.exe", "wininit.exe", "system", "registry",
    "fontdrvhost.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe",
    "applicationframehost.exe", "shellexperiencehost.exe",
}

# Window classes / names owned by the shell — never drive these.
SYSTEM_UI_PATTERNS = [
    r"(?i)\btaskbar\b", r"Shell_TrayWnd", r"Shell_SecondaryTrayWnd",
    r"(?i)start menu", r"(?i)system tray", r"(?i)notification area",
    r"(?i)action center", r"Progman", r"WorkerW", r"(?i)lock screen",
    r"(?i)secure desktop", r"CtrlAltDel", r"(?i)winlogon",
]

# Key combos that act on the OS shell rather than the target app.
BLOCKED_KEY_PATTERNS = [
    r"(?i)\{ *(LWIN|RWIN|WIN) *\} *\{ *L *\}",          # Win+L
    r"(?i)\{ *CTRL *\} *\{ *ALT *\} *\{ *(DEL|DELETE) *\}",  # CAD
    r"(?i)\{ *ALT *\} *\{ *F4 *\}",                      # Alt+F4
    r"(?i)\{ *CTRL *\} *\{ *SHIFT *\} *\{ *ESC",         # Ctrl+Shift+Esc
    r"(?i)\{ *(LWIN|RWIN|WIN) *\} *\{ *R *\}",           # Win+R
    r"(?i)\{ *(LWIN|RWIN|WIN) *\} *\{ *X *\}",           # Win+X
    r"(?i)\{ *CTRL *\} *\{ *ESC",                        # Ctrl+Esc
    r"(?i)\{ *ALT *\} *\{ *TAB",                         # Alt+Tab
    r"(?i)\{(LWIN|RWIN|WIN)\}",                          # any bare Win key use
]

# Apps that may be launched without approval (case-insensitive basename).
# Extend via ORVEX_ALLOWED_APPS=app1.exe;app2.exe (semicolon-separated).
_BUILTIN_ALLOWED_LAUNCHES: set[str] = {
    "notepad.exe",
    "notepad++.exe",
    "explorer.exe",
    "calc.exe",
    "mspaint.exe",
    "wordpad.exe",
    "write.exe",
    "cmd.exe",         # command prompt (not PowerShell - different risk profile)
    "powershell.exe",
}

def _load_allowed_launches() -> set[str]:
    extra_raw = (os.environ.get("ORVEX_ALLOWED_APPS")
                 or os.environ.get("SFMCP_ALLOWED_APPS", ""))
    extra = {p.strip().lower() for p in extra_raw.split(";") if p.strip()}
    return _BUILTIN_ALLOWED_LAUNCHES | extra

ALLOWED_LAUNCHES: set[str] = _load_allowed_launches()

SHELL_FOLDERS_BLOCKED = [
    "desktop", "documents", "downloads", "pictures", "onedrive",
]

# Resolve a safe, writable log directory for ORVEX at module load time.
# Priority: ORVEX_LOG_DIR > %APPDATA%\ORVEX\logs > bench/results (dev fallback)
def _resolve_log_dir() -> str:
    custom = os.environ.get("ORVEX_LOG_DIR", "")
    if custom:
        try:
            os.makedirs(custom, exist_ok=True)
            return custom
        except Exception:  # noqa: BLE001
            pass
    # Try %APPDATA%\ORVEX\logs — production path on a fresh machine
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        p = os.path.join(appdata, "ORVEX", "logs")
        try:
            os.makedirs(p, exist_ok=True)
            return p
        except Exception:  # noqa: BLE001
            pass
    # Fallback: bench/results relative to this file (development)
    base = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(base, "..", "bench", "results")
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return p


LOG_DIR = _resolve_log_dir()


@dataclass
class Budget:
    max_steps: int = 20
    max_duration_s: float = 30.0
    max_process_launches: int = 5
    max_window_creations: int = 5
    max_window_closes: int = 10
    max_system_input_events: int = 20
    max_new_windows_per_task: int = 5
    max_total_tracked_windows: int = 20
    max_same_app_windows: int = 5


@dataclass
class TaskRecord:
    task_id: str
    started: float
    pids: list[int] = field(default_factory=list)
    pid_names: dict[int, str] = field(default_factory=dict)
    windows_opened: list[int] = field(default_factory=list)  # hwnds created by task
    files_created: list[str] = field(default_factory=list)
    input_events: int = 0
    launches: int = 0
    closes: int = 0
    cancelled: bool = False


class SafetyError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code} {detail}".strip())
        self.code = code
        self.detail = detail


class SafetyPolicy:
    """Single choke point. Construct per task (budgets + sandbox + log)."""

    # Test-artifact title prefixes eligible for operator cleanup (env-configurable).
    TEST_CLEANUP_PREFIXES = tuple(
        p for p in (
            os.environ.get("ORVEX_TEST_PREFIX")
            or os.environ.get("SFMCP_TEST_PREFIX", "wa_,master_,smoke_,solo,seqdbg,rep0,mfinal,watch")
        ).split(",") if p
    )

    def __init__(self, budget: Budget | None = None, log_path: str | None = None) -> None:
        self.budget = budget or Budget()
        # Optional human-approval resolver, injected by the executor.
        self.approval_gate: "callable | None" = None
        # Deny-list predicate (Policy.is_protected), injected by the executor.
        self.protected_check: "callable | None" = None
        self.sandbox = os.path.join(tempfile.gettempdir(), "orvex_sandbox")
        os.makedirs(self.sandbox, exist_ok=True)
        self.log_path = log_path or os.path.join(LOG_DIR, "orvex_audit.jsonl")
        self.tasks: dict[str, TaskRecord] = {}
        self._task_seq = 0

    # ------------------------------------------------------------ tasks

    def new_task(self, wanted_id: str = "") -> TaskRecord:
        self._task_seq += 1
        if wanted_id and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", wanted_id) \
                and wanted_id not in self.tasks:
            rec = TaskRecord(task_id=wanted_id, started=time.perf_counter())
        else:
            rec = TaskRecord(task_id=f"T{self._task_seq}-{int(time.time())}",
                             started=time.perf_counter())
        self.tasks[rec.task_id] = rec
        return rec

    def cancel(self, task_id: str) -> bool:
        rec = self.tasks.get(task_id)
        if rec is None:
            return False
        rec.cancelled = True
        return True

    def check_budget(self, rec: TaskRecord, steps_done: int) -> None:
        b = self.budget
        if rec.cancelled:
            raise SafetyError("cancelled", f"task {rec.task_id}")
        if steps_done >= b.max_steps:
            raise SafetyError("execution_budget_exceeded", "max_steps")
        if time.perf_counter() - rec.started > b.max_duration_s:
            raise SafetyError("execution_budget_exceeded", "max_duration")
        if rec.launches >= b.max_process_launches:
            raise SafetyError("execution_budget_exceeded", "max_process_launches")
        if len(rec.windows_opened) >= b.max_window_creations:
            raise SafetyError("resource_limit_exceeded", "max_window_creations")
        if rec.closes >= b.max_window_closes:
            raise SafetyError("execution_budget_exceeded", "max_window_closes")
        if rec.input_events >= b.max_system_input_events:
            raise SafetyError("execution_budget_exceeded", "max_system_input_events")

    # ------------------------------------------------------------ targets

    @staticmethod
    def _exe_of(pid: int) -> str:
        try:
            import ctypes as _ct

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = _ct.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return ""
            buf = _ct.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            ok = _ct.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            _ct.windll.kernel32.CloseHandle(h)
            return os.path.basename(buf.value).lower() if ok else ""
        except Exception:  # noqa: BLE001
            return ""

    def check_process_target(self, pid: int | None, name: str = "") -> None:
        nm = (name or "").lower()
        if nm in SYSTEM_PROCESSES:
            raise SafetyError("blocked_system_target", f"process={name}")
        if pid:
            exe = self._exe_of(pid)
            if exe in SYSTEM_PROCESSES:
                raise SafetyError("blocked_system_target", f"pid={pid} exe={exe}")

    def check_window_target(self, title: str = "", cls: str = "", pid: int | None = None) -> None:
        ident = f"{title} {cls}"
        for pat in SYSTEM_UI_PATTERNS:
            if re.search(pat, ident):
                raise SafetyError("blocked_system_ui", f"target={title!r} class={cls!r}")
        if self._is_protected(ident):
            raise SafetyError("blocked_protected_app", f"target={title!r} is protected")
        if pid:
            self.check_process_target(pid)

    def _is_protected(self, ident: str) -> bool:
        """Deny-list check injected by the executor (Policy.is_protected).
        Fails CLOSED on error. A protected target is never approvable."""
        chk = self.protected_check
        if chk is None:
            return False
        try:
            return bool(chk(ident))
        except Exception:  # noqa: BLE001
            return True

    def _approved(self, code: str, detail: str, **kw) -> bool:
        """Resolve a human approval for a gated operation."""
        gate = self.approval_gate
        if gate is None:
            return False
        try:
            return bool(gate(code, detail, **kw))
        except Exception:  # noqa: BLE001
            return False

    def check_launch(self, app: str, rec: TaskRecord, path: str | None = None) -> None:
        key = (app or "").strip().lower()
        if key in ("settings", "windows settings", "ms-settings"):
            return  # URI activation of Settings, no new binary of ours
        # Protected targets are denied outright: never gated, never approvable.
        if self._is_protected(key):
            raise SafetyError("blocked_protected_app", f"app={app!r} is protected")
        exe = os.path.basename(key)
        if "." not in exe:
            exe += ".exe"
        # explorer.exe is dual-use (shell + file manager): launching is allowed
        # ONLY sandbox-scoped (a folder window); terminating/touching the shell
        # process or non-owned Explorer windows stays blocked elsewhere.
        if exe == "explorer.exe":
            if not path:
                raise SafetyError("blocked_unsafe_path",
                                  "explorer.exe requires sandbox path")
            self.check_path_inside_sandbox(path)
            return
        if exe not in ALLOWED_LAUNCHES:
            if self._approved("needs_approval_launch", f"app={app!r} not in allowlist",
                              app=app, path=path, verb="open_app"):
                return
            raise SafetyError("needs_approval_launch", f"app={app!r} not in allowlist")
        self.check_process_target(None, exe)

    def check_browser_launch(self, headless: bool, profile_dir: str) -> None:
        """Isolated browser launch allowed iff headless + fresh temp profile."""
        if not headless:
            if self._approved("needs_approval_launch", "headed browser needs approval",
                              app="browser", path=profile_dir, verb="browser_open"):
                self._log_launch_gate(profile_dir)
            else:
                raise SafetyError("needs_approval_launch", "headed browser needs approval")
        canon = os.path.normcase(os.path.abspath(profile_dir))
        tmp = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
        if canon != tmp and not canon.startswith(tmp + os.sep):
            raise SafetyError("blocked_unsafe_path", f"browser profile {profile_dir!r}")

    def _log_launch_gate(self, profile_dir: str) -> None:
        self.log(None, "approval_launch_unlocked", profile_is_temp=True,
                 profile=os.path.basename(profile_dir)[:64])

    # ------------------------------------------------------------ input

    def check_keys(self, keys: str) -> None:
        for pat in BLOCKED_KEY_PATTERNS:
            if re.search(pat, keys or ""):
                raise SafetyError("blocked_system_keys", f"keys={keys!r}")

    # ------------------------------------------------------------ paths

    def sandbox_path(self, *parts: str) -> str:
        return os.path.join(self.sandbox, *parts)

    def check_path_inside_sandbox(self, path: str, *, allow_delete: bool = False) -> str:
        canon = os.path.normcase(os.path.abspath(path))
        base = os.path.normcase(os.path.abspath(self.sandbox))
        if canon != base and not canon.startswith(base + os.sep):
            # also block well-known shell locations explicitly for a clear reason
            low = canon.lower()
            if any(sep + s in low for s in SHELL_FOLDERS_BLOCKED for sep in ("\\", "/")):
                raise SafetyError("blocked_unsafe_path", f"shell-location {path!r}")
            raise SafetyError("blocked_unsafe_path", f"outside sandbox {path!r}")
        return canon

    # ------------------------------------------------------------ logging

    def log(self, rec: TaskRecord | None, operation: str, **fields: object) -> None:
        entry = {
            "ts": time.time(),
            "task_id": rec.task_id if rec else "-",
            "operation": operation,
        }
        for k, v in fields.items():
            # Never log sensitive content — redact at the schema level
            if k in ("password", "clipboard", "file_contents", "text", "keys"):
                entry[k] = f"<redacted len={len(str(v))}>"
            else:
                entry[k] = v
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ shell health (diagnostic only)

    @staticmethod
    def shell_health() -> dict:
        health: dict[str, object] = {}
        try:
            import subprocess as _sp

            out = _sp.run(["tasklist", "/FI", "IMAGENAME eq explorer.exe", "/FO", "CSV"],
                          capture_output=True, text=True, timeout=10).stdout
            health["explorer_running"] = "explorer.exe" in out.lower()
            out2 = _sp.run(["tasklist", "/FI", "IMAGENAME eq dwm.exe", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=10).stdout
            health["dwm_running"] = "dwm.exe" in out2.lower()
        except Exception as e:  # noqa: BLE001
            health["process_error"] = str(e)[:120]
        try:
            import uiautomation as auto

            root = auto.GetRootControl()
            names = []
            for c in root.GetChildren():
                try:
                    names.append(c.Name or "")
                except Exception:  # noqa: BLE001
                    pass
            health["top_level_windows"] = len(names)
            health["taskbar_detected"] = any("taskbar" in (n or "").lower() for n in names)
            health["desktop_detected"] = any((n or "") == "Program Manager" for n in names)
        except Exception as e:  # noqa: BLE001
            health["uia_error"] = str(e)[:120]
        try:
            health["foreground_hwnd"] = int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:  # noqa: BLE001
            pass
        return health

    @staticmethod
    def health_diff(before: dict, after: dict) -> list[str]:
        changes = []
        for k in ("explorer_running", "dwm_running", "taskbar_detected", "desktop_detected"):
            if before.get(k) is not None and after.get(k) is not None and before.get(k) != after.get(k):
                changes.append(f"{k}: {before.get(k)} -> {after.get(k)}")
        n0 = before.get("top_level_windows")
        n1 = after.get("top_level_windows")
        if isinstance(n0, int) and isinstance(n1, int) and abs(n1 - n0) > 15:
            changes.append(f"top_level_windows: {n0} -> {n1}")
        return changes

    def dump(self) -> dict:
        return {"sandbox": self.sandbox, "budget": asdict(self.budget), "log": self.log_path}
