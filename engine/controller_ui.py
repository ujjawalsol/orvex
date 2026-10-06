"""System-wide ORVEX automation controller UI (singleton companion service).

Maintains a single, persistent, activity-aware control surface for the user's
desktop session. Decoupled from individual MCP server lifetimes:
  * Singleton enforcement via Windows Named Mutex (Local\\ORVEX_ControllerUI_Singleton_Mutex)
  * Local IPC over Windows Named Pipe (\\\\.\\pipe\\orvex_controller_ipc) with secret auth key
  * Activity-Aware Visibility: 100% hidden when idle; visible bottom-center when automating
  * Zero Focus Stealing: WS_EX_NOACTIVATE ensures foreground window is never stolen
  * Dynamic Contextual Controls:
      - RUNNING: [ Take Control ]  [ Stop ]
      - USER_CONTROL: [ Resume Automation ]  [ Stop ]
      - WAITING_APPROVAL: [ Continue ]  [ Stop ]
      - STOPPED / COMPLETED: Brief confirmation (1.5s), then auto-withdraw
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import re
import secrets
import sys
import threading
import time
import tkinter as tk
from multiprocessing.connection import Listener

MUTEX_NAME = "Local\\ORVEX_ControllerUI_Singleton_Mutex"
PIPE_NAME = r"\\.\pipe\orvex_controller_ipc"

user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
]
user32.SetWindowPos.restype = wintypes.BOOL
HWND_TOPMOST = wintypes.HWND(-1)

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
ORVEX_DIR = Path(LOCALAPPDATA) / "ORVEX" if LOCALAPPDATA else Path.home() / ".orvex"
ORVEX_DIR.mkdir(parents=True, exist_ok=True)
KEY_FILE = ORVEX_DIR / ".controller_ipc_key"
STATE_FILE = ORVEX_DIR / ".controller_state.json"

CARD_WIDTH = 380
CARD_HEIGHT_ACTIVE = 115
CARD_HEIGHT_COMPACT = 54

# Safe redaction regexes
_KEYWORDS = (r"(?i)\b(hwnd|pid|cdp|ws|com|dag|cookie|credential|selector|token|"
             r"secret|password|pwd|raw_input|session_id)\b")
_RE_KV = re.compile(_KEYWORDS + r"\s*[:=]\s*\S+")
_RE_KW = re.compile(_KEYWORDS)
_RE_HEX = re.compile(r"(?i)\b0x[0-9a-f]{4,}\b")
_RE_NUM = re.compile(r"(?<![.\w])\d{4,}(?![\d.])")
_RE_SEL = re.compile(r"(?<!\w)#{1,2}[\w-]+|(?<!\w)//[\w/\[\]=]+|(?<=\s)\.[\w-]+")


def redact(text: str, limit: int = 140) -> str:
    if not text:
        return ""
    out = _RE_KV.sub("<redacted>", str(text))
    out = _RE_SEL.sub("<sel>", out)
    out = _RE_HEX.sub("<hex>", out)
    out = _RE_NUM.sub("<n>", out)
    out = _RE_KW.sub(lambda m: m.group(1).upper(), out)
    return out.strip().replace("\n", " ")[:limit]


def get_auth_key() -> bytes:
    if not KEY_FILE.exists():
        KEY_FILE.write_bytes(secrets.token_bytes(32))
    try:
        return KEY_FILE.read_bytes()
    except Exception:
        key = secrets.token_bytes(32)
        try:
            KEY_FILE.write_bytes(key)
        except Exception:
            pass
        return key


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def get_work_area(target_hwnd: int = 0) -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    try:
        target = target_hwnd or user32.GetForegroundWindow()
        hmon = user32.MonitorFromWindow(target, 2)  # MONITOR_DEFAULTTONEAREST
        if hmon:
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                return (int(mi.rcWork.left), int(mi.rcWork.top),
                        int(mi.rcWork.right), int(mi.rcWork.bottom))
    except Exception:
        pass
    rc = wintypes.RECT()
    try:
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0):
            return int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)
    except Exception:
        pass
    return 0, 0, 1920, 1040


class ControllerUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("ORVEX")
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-toolwindow", True)
        except Exception:
            pass

        self.root.resizable(False, False)
        self.root.configure(bg="#18181b")

        self.root.update_idletasks()
        self._wid = int(self.root.winfo_id())
        p = user32.GetParent(self._wid)
        self._hwnd = int(p) if (p and user32.IsWindow(p)) else self._wid
        try:
            user32.SetWindowTextW(self._hwnd, "ORVEX")
            user32.SetWindowTextW(self._wid, "ORVEX")
        except Exception:
            pass
        self._setup_window_styles(self._hwnd)

        # Initial state: completely hidden when idle
        self.root.withdraw()

        self.state = "IDLE"
        self.tasks: dict[str, dict] = {}
        self.clients: dict[str, any] = {}
        self.client_lock = threading.Lock()
        self._auto_hide_id: str | None = None
        self._user_x: int | None = None
        self._user_y: int | None = None

        # Build Card Layout
        self._build_widgets()

        # Thread queue & event loop
        self.msg_queue: queue.Queue = queue.Queue()
        self._running = True
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Alt-KeyPress>", self._on_accel)

        # Write state file for HWND/PID discovery
        self._write_state_file()

        # Start periodic tick
        self.root.after(50, self._pump)

    def _setup_window_styles(self, target_hwnd: int | None = None) -> None:
        target = target_hwnd or self._hwnd
        try:
            user32 = ctypes.windll.user32
            # Add WS_EX_NOACTIVATE (0x08000000) so clicking never steals foreground
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_TOPMOST = 0x00000008
            old = user32.GetWindowLongW(target, GWL_EXSTYLE)
            user32.SetWindowLongW(target, GWL_EXSTYLE,
                                  old | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)

            # Enable DWM Immersive Dark Mode for title/frame
            dwmapi = ctypes.windll.dwmapi
            val = ctypes.c_int(1)
            dwmapi.DwmSetWindowAttribute(target, 20, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass

    def _write_state_file(self) -> None:
        try:
            btn_coords = {}
            if self.state in ("RUNNING", "USER_CONTROL", "WAITING_APPROVAL"):
                try:
                    w1 = self.btn_left.winfo_width()
                    if w1 > 0:
                        btn_coords["left"] = [
                            self.btn_left.winfo_rootx() + w1 // 2,
                            self.btn_left.winfo_rooty() + self.btn_left.winfo_height() // 2,
                        ]
                    else:
                        rx = self.root.winfo_rootx()
                        ry = self.root.winfo_rooty()
                        btn_coords["left"] = [rx + 110, ry + 45]
                except Exception:
                    pass

                try:
                    w2 = self.btn_right.winfo_width()
                    if w2 > 0:
                        btn_coords["right"] = [
                            self.btn_right.winfo_rootx() + w2 // 2,
                            self.btn_right.winfo_rooty() + self.btn_right.winfo_height() // 2,
                        ]
                    else:
                        rx = self.root.winfo_rootx()
                        ry = self.root.winfo_rooty()
                        btn_coords["right"] = [rx + 290, ry + 45]
                except Exception:
                    pass

            data = {
                "pid": os.getpid(),
                "hwnd": self._hwnd,
                "wid": self._wid,
                "timestamp": time.time(),
                "state": self.state,
                "active_tasks": len(self.tasks),
                "buttons": btn_coords,
            }
            STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _build_widgets(self) -> None:
        # Header Row: Dot + Brand + Sep + Status Title
        self.head = tk.Frame(self.root, bg="#18181b")
        self.head.pack(fill="x", padx=14, pady=(6, 2))

        self.dot = tk.Label(self.head, text="●", fg="#10b981", bg="#18181b",
                            font=("Segoe UI", 10))
        self.dot.pack(side="left", padx=(0, 5))

        self.brand = tk.Label(self.head, text="ORVEX", fg="#38bdf8", bg="#18181b",
                              font=("Segoe UI", 9, "bold"))
        self.brand.pack(side="left", padx=(0, 6))

        self.sep = tk.Label(self.head, text="·", fg="#71717a", bg="#18181b",
                            font=("Segoe UI", 9))
        self.sep.pack(side="left", padx=(0, 6))

        self.title_lbl = tk.Label(self.head, text="Automating", fg="#f4f4f5", bg="#18181b",
                                  font=("Segoe UI", 9, "bold"))
        self.title_lbl.pack(side="left")

        # Subtitle Row: App Context & Step
        self.sub_lbl = tk.Label(self.root, text="Ready", fg="#a1a1aa", bg="#18181b",
                                font=("Segoe UI", 8), anchor="w")
        self.sub_lbl.pack(fill="x", padx=14, pady=(0, 6))

        # Action Buttons Row
        self.btn_bar = tk.Frame(self.root, bg="#18181b")
        self.btn_bar.pack(fill="x", padx=14, pady=(0, 8))

        self.btn_left = tk.Button(
            self.btn_bar, text="Take Control", font=("Segoe UI", 8, "bold"),
            bg="#27272a", fg="#f4f4f5", activebackground="#3f3f46", activeforeground="#ffffff",
            relief="flat", bd=1, padx=12, pady=3, takefocus=False,
            command=self._on_left_click)
        self.btn_left.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_right = tk.Button(
            self.btn_bar, text="Stop", font=("Segoe UI", 8, "bold"),
            bg="#b91c1c", fg="#ffffff", activebackground="#dc2626", activeforeground="#ffffff",
            relief="flat", bd=1, padx=12, pady=3, takefocus=False,
            command=self._on_right_click)
        self.btn_right.pack(side="left", expand=True, fill="x")

        # Drag tracking
        self._drag_data = {"x": 0, "y": 0}
        for w in (self.root, self.head, self.brand, self.sep, self.title_lbl, self.sub_lbl):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._do_drag)

    def _start_drag(self, event) -> None:
        self._drag_data["x"] = event.x_root - self.root.winfo_x()
        self._drag_data["y"] = event.y_root - self.root.winfo_y()

    def _do_drag(self, event) -> None:
        x = event.x_root - self._drag_data["x"]
        y = event.y_root - self._drag_data["y"]
        self._user_x = x
        self._user_y = y
        self.root.geometry(f"+{x}+{y}")

    def _on_accel(self, ev) -> str:
        ch = (ev.char or "").lower()
        if ch == "t" and self.btn_left.cget("text") == "Take Control":
            self._on_left_click()
            return "break"
        if ch == "r" and self.btn_left.cget("text") == "Resume Automation":
            self._on_left_click()
            return "break"
        if ch == "c" and self.btn_left.cget("text") in ("Continue", "Approve"):
            self._on_left_click()
            return "break"
        if ch == "s":
            self._on_right_click()
            return "break"
        return ""

    def _on_left_click(self) -> None:
        btn_text = self.btn_left.cget("text")
        if btn_text == "Take Control":
            self.state = "USER_CONTROL"
            self._render_state()
            self._broadcast({"event": "take_control"})
        elif btn_text == "Resume Automation":
            self.state = "RUNNING"
            self._render_state()
            self._broadcast({"event": "resume"})
        elif btn_text in ("Continue", "Approve"):
            self._broadcast({"event": "approve"})
            self.state = "RUNNING"
            self._render_state()

    def _on_right_click(self) -> None:
        self.state = "STOPPED"
        self._render_state()
        self._broadcast({"event": "stop"})
        self.tasks.clear()
        self._schedule_auto_hide(1500)

    def _on_close(self) -> None:
        self.state = "STOPPED"
        self._broadcast({"event": "stop"})
        self.root.withdraw()

    def _broadcast(self, msg: dict) -> None:
        with self.client_lock:
            for cid, conn in list(self.clients.items()):
                try:
                    conn.send(msg)
                except Exception:
                    pass

    def _position_bottom_center(self, height: int) -> None:
        wl, wt, wr, wb = get_work_area()
        frame_pad = 42
        if self._user_x is not None and self._user_y is not None:
            # Preserve user-customized location
            x = max(wl, min(self._user_x, wr - CARD_WIDTH - 5))
            y = max(wt, min(self._user_y, wb - height - frame_pad))
        else:
            x = wl + ((wr - wl) - CARD_WIDTH) // 2
            y = max(wt, wb - height - frame_pad)
        self.root.geometry(f"{CARD_WIDTH}x{height}+{x}+{y}")

    def show_card(self) -> None:
        if self._auto_hide_id is not None:
            try:
                self.root.after_cancel(self._auto_hide_id)
            except Exception:
                pass
            self._auto_hide_id = None

        has_buttons = self.state in ("RUNNING", "USER_CONTROL", "WAITING_APPROVAL")
        h = CARD_HEIGHT_ACTIVE if has_buttons else CARD_HEIGHT_COMPACT

        self.root.deiconify()
        self.root.update_idletasks()
        user32 = ctypes.windll.user32
        p = user32.GetParent(self._wid)
        self._hwnd = int(p) if (p and user32.IsWindow(p)) else self._wid
        try:
            user32.SetWindowTextW(self._hwnd, "ORVEX")
            user32.SetWindowTextW(self._wid, "ORVEX")
        except Exception:
            pass
        self._setup_window_styles(self._hwnd)
        self._position_bottom_center(h)

        try:
            # SW_SHOWNOACTIVATE = 4, SWP_SHOWWINDOW = 0x0040, SWP_NOSIZE = 0x0001, SWP_NOMOVE = 0x0002
            user32.ShowWindow(self._hwnd, 4)
            user32.SetWindowPos(
                self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0040,
            )
        except Exception:
            pass
        try:
            self.root.update()
        except Exception:
            pass
        self._write_state_file()
        self._broadcast({"event": "alive", "pid": os.getpid(), "hwnd": self._hwnd, "wid": self._wid})

    def hide_card(self) -> None:
        if self._auto_hide_id is not None:
            try:
                self.root.after_cancel(self._auto_hide_id)
            except Exception:
                pass
            self._auto_hide_id = None
        self.root.withdraw()
        self.state = "IDLE"
        self._write_state_file()

    def _schedule_auto_hide(self, ms: int = 1500) -> None:
        if self._auto_hide_id is not None:
            try:
                self.root.after_cancel(self._auto_hide_id)
            except Exception:
                pass
        self._auto_hide_id = self.root.after(ms, self.hide_card)

    def _render_state(self) -> None:
        # Determine active task summary
        n_tasks = len(self.tasks)
        latest_task = next(reversed(self.tasks.values())) if self.tasks else {}
        app_hint = latest_task.get("app_hint") or latest_task.get("goal") or "Automation active"
        step_op = latest_task.get("op", "")
        if step_op:
            subtitle = f"{app_hint} · {step_op}"
        else:
            subtitle = app_hint

        if n_tasks > 1:
            subtitle = f"({n_tasks} tasks) {subtitle}"

        if self.state == "RUNNING":
            self.dot.config(fg="#10b981")
            self.title_lbl.config(text="Automating", fg="#f4f4f5")
            self.sub_lbl.config(text=redact(subtitle))
            self.btn_left.config(
                text="Take Control", bg="#27272a", fg="#f4f4f5",
                activebackground="#3f3f46", activeforeground="#ffffff")
            self.btn_right.config(
                text="Stop", bg="#b91c1c", fg="#ffffff",
                activebackground="#dc2626", activeforeground="#ffffff")
            if not self.btn_bar.winfo_ismapped():
                self.btn_bar.pack(fill="x", padx=14, pady=(0, 8))
            self.show_card()

        elif self.state == "USER_CONTROL":
            self.dot.config(fg="#f59e0b")
            self.title_lbl.config(text="Manual Control", fg="#f59e0b")
            self.sub_lbl.config(text="Automation paused. You have control.")
            self.btn_left.config(
                text="Resume Automation", bg="#15803d", fg="#ffffff",
                activebackground="#16a34a", activeforeground="#ffffff")
            self.btn_right.config(
                text="Stop", bg="#b91c1c", fg="#ffffff",
                activebackground="#dc2626", activeforeground="#ffffff")
            if not self.btn_bar.winfo_ismapped():
                self.btn_bar.pack(fill="x", padx=14, pady=(0, 8))
            self.show_card()

        elif self.state == "WAITING_APPROVAL":
            self.dot.config(fg="#ef4444")
            self.title_lbl.config(text="Action Required", fg="#ef4444")
            self.sub_lbl.config(text=redact(subtitle or "User confirmation required"))
            self.btn_left.config(
                text="Continue", bg="#15803d", fg="#ffffff",
                activebackground="#16a34a", activeforeground="#ffffff")
            self.btn_right.config(
                text="Stop", bg="#b91c1c", fg="#ffffff",
                activebackground="#dc2626", activeforeground="#ffffff")
            if not self.btn_bar.winfo_ismapped():
                self.btn_bar.pack(fill="x", padx=14, pady=(0, 8))
            self.show_card()

        elif self.state == "STOPPED":
            self.dot.config(fg="#71717a")
            self.title_lbl.config(text="Automation Stopped", fg="#e4e4e7")
            self.sub_lbl.config(text="Cancelled by user")
            self.btn_bar.pack_forget()
            self.show_card()
            self._schedule_auto_hide(1500)

        elif self.state == "COMPLETED":
            self.dot.config(fg="#10b981")
            self.title_lbl.config(text="Completed", fg="#10b981")
            self.sub_lbl.config(text="Task finished")
            self.btn_bar.pack_forget()
            self.show_card()
            self._schedule_auto_hide(1500)

        elif self.state == "IDLE":
            self.hide_card()

        self._write_state_file()

    def apply_msg(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        cid = msg.get("client_id", "")
        tid = msg.get("task_id", "")

        if cmd == "task_begin":
            self.tasks[tid] = {
                "client_id": cid,
                "goal": msg.get("goal", ""),
                "app_hint": msg.get("app_hint", ""),
                "op": "",
                "start": time.time(),
            }
            if self.state != "USER_CONTROL":
                self.state = "RUNNING"
            self._render_state()

        elif cmd == "node_update":
            if tid in self.tasks:
                self.tasks[tid]["op"] = msg.get("op", "")
                if msg.get("app_hint"):
                    self.tasks[tid]["app_hint"] = msg.get("app_hint")
            elif self.tasks:
                # Update latest task
                latest = next(reversed(self.tasks.values()))
                latest["op"] = msg.get("op", "")
                if msg.get("app_hint"):
                    latest["app_hint"] = msg.get("app_hint")
            self._render_state()

        elif cmd == "task_end":
            self.tasks.pop(tid, None)
            status = msg.get("status", "success")
            if not self.tasks:
                if status == "success":
                    self.state = "COMPLETED"
                elif status in ("stopped", "cancelled"):
                    self.state = "STOPPED"
                else:
                    self.state = "STOPPED"
            self._render_state()

        elif cmd == "set_state":
            new_state = msg.get("state", "IDLE")
            self.state = new_state
            if new_state in ("RUNNING", "USER_CONTROL", "WAITING_APPROVAL"):
                detail = msg.get("detail", "")
                if detail:
                    self.sub_lbl.config(text=redact(detail))
            self._render_state()

        elif cmd == "client_disconnected":
            # Clean up tasks owned by this client
            self.tasks = {k: v for k, v in self.tasks.items() if v.get("client_id") != cid}
            if not self.tasks and self.state in ("RUNNING", "USER_CONTROL"):
                self.state = "IDLE"
            self._render_state()

    def _pump(self) -> None:
        while True:
            try:
                msg = self.msg_queue.get_nowait()
                self.apply_msg(msg)
            except queue.Empty:
                break
            except Exception:
                continue

        # Periodically re-assert state file
        self._write_state_file()
        if self._running:
            self.root.after(50, self._pump)


# --- IPC Server Loop ---
def run_ipc_server(ui: ControllerUI) -> None:
    auth_key = get_auth_key()
    try:
        listener = Listener(PIPE_NAME, "AF_PIPE", authkey=auth_key)
    except Exception as e:
        # Another listener bound? Exit.
        return

    while ui._running:
        try:
            conn = listener.accept()
            client_thread = threading.Thread(
                target=handle_client_connection, args=(ui, conn), daemon=True)
            client_thread.start()
        except Exception:
            if not ui._running:
                break
            time.sleep(0.05)
            continue
    try:
        listener.close()
    except Exception:
        pass


def handle_client_connection(ui: ControllerUI, conn: any) -> None:
    client_id = f"client_{id(conn)}"
    with ui.client_lock:
        ui.clients[client_id] = conn

    try:
        # Handshake: send initial alive info with PID and HWND
        conn.send({
            "event": "alive",
            "pid": os.getpid(),
            "hwnd": ui._hwnd,
            "state": ui.state,
        })

        while ui._running:
            try:
                msg = conn.recv()
            except (EOFError, BrokenPipeError, ConnectionResetError):
                break
            except Exception:
                break

            cmd = msg.get("cmd")
            if cmd == "register_client":
                client_id = msg.get("client_id", client_id)
                with ui.client_lock:
                    ui.clients[client_id] = conn
                conn.send({"event": "alive", "pid": os.getpid(), "hwnd": ui._hwnd})
            elif cmd == "ping":
                conn.send({"event": "alive", "pid": os.getpid(), "hwnd": ui._hwnd})
            elif cmd == "disconnect":
                break
            else:
                ui.msg_queue.put(msg)

    finally:
        with ui.client_lock:
            ui.clients.pop(client_id, None)
        ui.msg_queue.put({"cmd": "client_disconnected", "client_id": client_id})
        try:
            conn.close()
        except Exception:
            pass


def attach_to_input_desktop() -> None:
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass


def main() -> int:
    # 0. Attach thread to active user input desktop before UI creation
    attach_to_input_desktop()

    # 1. Enforce strict Windows Session Singleton via Named Mutex
    kernel32 = ctypes.windll.kernel32
    h_mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # Another controller UI process already created the mutex.
        # Wait up to 2.5s for the primary instance's IPC pipe to become responsive.
        auth_key = get_auth_key()
        deadline = time.time() + 2.5
        while time.time() < deadline:
            try:
                from multiprocessing.connection import Client
                c = Client(PIPE_NAME, "AF_PIPE", authkey=auth_key)
                c.send({"cmd": "ping"})
                c.close()
                if h_mutex:
                    kernel32.CloseHandle(h_mutex)
                return 0
            except Exception:
                time.sleep(0.08)
        # If deadline elapsed without response, close handle and exit to prevent duplicate UI
        if h_mutex:
            kernel32.CloseHandle(h_mutex)
        return 0

    # 2. Initialize UI & IPC
    ui = ControllerUI()
    ipc_thread = threading.Thread(target=run_ipc_server, args=(ui,), daemon=True)
    ipc_thread.start()

    try:
        ui.root.mainloop()
    finally:
        ui._running = False
        if h_mutex:
            kernel32.CloseHandle(h_mutex)
        try:
            STATE_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())