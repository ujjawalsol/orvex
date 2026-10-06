"""System-wide ORVEX automation controller UI (companion process).

One topmost lightweight Tk window in the top-right work area, plus real Tk
buttons so every control is reachable by keyboard, mouse and screen reader.
No Explorer / taskbar / DWM / shell / registry modification. No browser DOM or
webpage injection. The authoritative automation state is the engine-side
AutomationController (controller.py), never this window and never the browser.

Wire protocol: JSON lines on stdin (engine -> UI), JSON lines on stdout
(UI -> engine). UI-owned responsibilities:
  * render state, never compute it
  * emit control intents (pause/resume/stop/take_control/approve/deny)
  * heartbeat (`alive`) so the engine can fail-safe pause if the UI dies
  * report physical user input while RUNNING (event only, never content)

Threading: Tk is touched ONLY on the main thread. Worker threads hand work to
the main loop through `queue.Queue` and a stdlib `threading.Event` for exit.
"""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import queue
import re
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

STATE_IDLE = "IDLE"

# State -> (accent_color, default_title_text, force_expand)
COLORS: dict[str, tuple[str, str, bool]] = {
    "IDLE":             ("#5a5a5a", "Ready",                    False),
    "RUNNING":          ("#0a7d32", "Automation Active",        False),
    "PAUSED":           ("#c26400", "Automation Paused",        False),
    "USER_CONTROL":     ("#c26400", "You have control",         False),
    "WAITING_APPROVAL": ("#b00020", "Approval Required",        True),
    "STOPPING":         ("#c26400", "Stopping",                 False),
    "STOPPED":          ("#5a5a5a", "Automation Stopped",       False),
    "FAILED":           ("#b00020", "Automation Failed",        True),
    "BLOCKED":          ("#b00020", "Automation Blocked",       True),
}

# button label -> wire event. Single mapping, single authority (engine).
BUTTON_EVENTS = {
    "Take Control": "take_control",
    "Pause":        "pause",
    "Resume":       "resume",
    "Stop":         "stop",
    "Approve":      "approve",
    "Deny":         "deny",
}

# keyboard accelerators: Alt+<key>. Safety controls never need the mouse.
ACCEL = {
    "Take Control": "t",
    "Pause":        "p",
    "Resume":       "r",
    "Stop":         "s",
    "Approve":      "a",
    "Deny":         "d",
}

BUTTONS_BY_STATE: dict[str, tuple[str, ...]] = {
    "RUNNING":          ("Take Control", "Pause", "Stop"),
    "PAUSED":           ("Resume", "Stop"),
    "USER_CONTROL":     ("Resume", "Stop"),
    "WAITING_APPROVAL": ("Approve", "Deny", "Stop"),
    "BLOCKED":          ("Stop",),
    "FAILED":           ("Stop",),
    "STOPPING":         ("Stop",),
}

COMPACT_W, COMPACT_H = 300, 58
EXPANDED_W, EXPANDED_H = 340, 220

# Never surface internal handles, pointers, selectors or page content.
_KEYWORDS = (r"(?i)\b(hwnd|pid|cdp|ws|com|dag|cookie|credential|selector|token|"
             r"secret|password|pwd|raw_input|session_id)\b")
_RE_KV  = re.compile(_KEYWORDS + r"\s*[:=]\s*\S+")
_RE_KW  = re.compile(_KEYWORDS)
_RE_HEX = re.compile(r"(?i)\b0x[0-9a-f]{4,}\b")
_RE_NUM = re.compile(r"(?<![.\w])\d{4,}(?![\d.])")
_RE_SEL = re.compile(r"(?<!\w)#{1,2}[\w-]+|(?<!\w)//[\w/\[\]=]+|(?<=\s)\.[\w-]+")


def redact(text: str, limit: int = 300) -> str:
    """Strip internal identifiers, long numbers and selector/credential values
    from user-visible detail. Deliberately aggressive: this string is shown to
    the user, so anything not clearly human-readable is removed."""
    if not text:
        return ""
    out = _RE_KV.sub("<redacted>", str(text))
    out = _RE_SEL.sub("<sel>", out)
    out = _RE_HEX.sub("<hex>", out)
    out = _RE_NUM.sub("<n>", out)
    out = _RE_KW.sub(lambda m: m.group(1).upper(), out)
    return out[:limit]


def send(msg: dict) -> None:
    try:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


class Hooks:
    """WH_KEYBOARD_LL + WH_MOUSE_LL: physical-input *events* only.

    The engine opens a short input window around its own injections, so any
    event outside those windows while RUNNING is reported as
    `user_intervention`. This never reads or stores user content, and never
    interprets it as automation input.
    """

    def __init__(self, on_user_input, is_running) -> None:
        self._on_user   = on_user_input
        self._is_running = is_running
        self._windows: list[tuple[float, float]] = []
        self._lock = threading.Lock()
        self._ids: list[int] = []
        self._last_report = 0.0
        self._user32: ctypes.WinDLL | None = None  # type: ignore[attr-defined]
        self._cb_refs: list = []

    def input_window(self, ms: float) -> None:
        now = time.time()
        with self._lock:
            self._windows.append((now, now + ms / 1000.0))
            self._windows = [(a, b) for a, b in self._windows if b > now][-8:]

    def _ours(self) -> bool:
        now = time.time()
        with self._lock:
            return any(a - 0.05 <= now <= b for a, b in self._windows)

    def _cb(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code >= 0 and self._is_running() and not self._ours():
                now = time.time()
                if now - self._last_report > 2.0:
                    self._last_report = now
                    self._on_user()
        except Exception:  # noqa: BLE001
            pass
        u = self._user32
        return int(u.CallNextHookEx(None, n_code, w_param, l_param)) if u else 0

    def start(self) -> None:
        self._user32 = ctypes.windll.user32
        user32 = self._user32
        WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
        CMPFUNC = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
            ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        self._cb_refs = [CMPFUNC(self._cb), CMPFUNC(self._cb)]
        hmod = ctypes.windll.kernel32.GetModuleHandleW(None)
        kh = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._cb_refs[0], hmod, 0)
        mh = user32.SetWindowsHookExW(WH_MOUSE_LL, self._cb_refs[1], hmod, 0)
        self._ids = [h for h in (kh, mh) if h]
        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self) -> None:
        u = self._user32
        for h in self._ids:
            try:
                if u is not None:
                    u.UnhookWindowsHookEx(h)
            except Exception:  # noqa: BLE001
                pass
        self._ids = []


class UI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("ORVEX")
        # Topmost, small, non-destructive; user may drag it anywhere and the
        # engine's later state updates must NOT snap it back.
        self.root.attributes("-topmost", True)
        # Tool window: occupies no taskbar slot, so the taskbar is never
        # reserved or reshaped. No DWM/shell/registry involvement.
        try:
            self.root.attributes("-toolwindow", True)
        except Exception:  # noqa: BLE001
            pass
        sw = self.root.winfo_screenwidth()
        self._x = max(0, sw - COMPACT_W - 24)
        self._y = 24
        self.root.geometry(f"{COMPACT_W}x{COMPACT_H}+{self._x}+{self._y}")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e1e")

        self.state = STATE_IDLE
        self._expanded = False
        self._forced = False
        self._running = False
        self._closed = False
        self.token = ""

        # Window & header icon
        self._icon_img = None
        for icon_candidate in (
            Path(__file__).resolve().parent / "icon_16.png",
            Path(__file__).resolve().parent.parent / "icon.png",
        ):
            if icon_candidate.exists():
                try:
                    self._icon_img = tk.PhotoImage(file=str(icon_candidate))
                    self.root.iconphoto(False, self._icon_img)
                    break
                except Exception:
                    pass

        ico_path = Path(__file__).resolve().parent.parent / "icon.ico"
        if ico_path.exists():
            try:
                self.root.iconbitmap(str(ico_path))
            except Exception:
                pass

        # Header row: icon + dot + product name + status label
        self.head = tk.Frame(self.root, bg="#1e1e1e")
        self.head.pack(fill="x", padx=0, pady=0)

        if self._icon_img is not None:
            self.icon_lbl = tk.Label(self.head, image=self._icon_img, bg="#1e1e1e")
            self.icon_lbl.pack(side="left", padx=(8, 2))

        self.dot = tk.Label(
            self.head, text="●", fg="#5a5a5a", bg="#1e1e1e",
            font=("Segoe UI", 13))
        self.dot.pack(side="left", padx=(4, 2))

        _brand = tk.Label(
            self.head, text="ORVEX", fg="#888888", bg="#1e1e1e",
            font=("Segoe UI", 8, "bold"))
        _brand.pack(side="left", padx=(0, 6))

        self.title_lbl = tk.Label(
            self.head, text="Ready",
            fg="#cccccc", bg="#1e1e1e",
            anchor="w", font=("Segoe UI", 9, "bold"))
        self.title_lbl.pack(side="left", fill="x", expand=True)

        # Expanded body: detail text + buttons
        self.body = tk.Frame(self.root, bg="#1e1e1e")
        self.detail = tk.Label(
            self.body, text="", wraplength=COMPACT_W - 20,
            justify="left", fg="#aaaaaa", bg="#1e1e1e",
            font=("Segoe UI", 9))
        self.detail.pack(padx=10, pady=(4, 2), anchor="w")

        self.bar = tk.Frame(self.body, bg="#1e1e1e")
        self.bar.pack(padx=8, pady=6, fill="x")
        self.buttons: dict[str, tk.Button] = {}
        for name in BUTTON_EVENTS:
            b = tk.Button(
                self.bar,
                text=f"{name}  (Alt+{ACCEL[name].upper()})",
                width=14,
                font=("Segoe UI", 9),
                bg="#2d2d2d", fg="#cccccc",
                activebackground="#3d3d3d", activeforeground="#ffffff",
                relief="flat", bd=1,
                command=lambda n=name: self._emit(n),
                takefocus=True)
            self.buttons[name] = b

        self.q: queue.Queue = queue.Queue()
        self._quit = threading.Event()
        self._apply({"cmd": "state", "state": "IDLE", "text": "Ready"})
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.root.bind("<Alt-KeyPress>", self._accel)
        self.root.after(80, self._pump)

    # ------------------------------------------------------------- helpers

    def _emit(self, name: str) -> None:
        msg = {"event": BUTTON_EVENTS[name]}
        if name in ("Approve", "Deny") and self.token:
            msg["token"] = self.token
        send(msg)

    def _accel(self, ev) -> str:
        ch = (ev.char or "").lower()
        for name, key in ACCEL.items():
            if ch == key and self.buttons[name].winfo_ismapped():
                self._emit(name)
                return "break"
        return ""

    def request_close(self) -> None:
        """User closed the panel: behave like a lost indicator (fail-safe)."""
        send({"event": "ui_closed_by_user"})
        self._quit.set()

    def _visible(self) -> tuple[str, ...]:
        if self.state in ("IDLE", "STOPPED"):
            return ()
        return BUTTONS_BY_STATE.get(self.state, ("Stop",))

    def _sync_geometry(self) -> None:
        # Position is user-owned: only ever change size, never coordinates.
        w, h = (EXPANDED_W, EXPANDED_H) if self._expanded else (COMPACT_W, COMPACT_H)
        self.root.geometry(f"{w}x{h}")

    def _apply(self, msg: dict) -> None:
        kind = msg.get("cmd", "")
        if kind == "state":
            self.state = msg.get("state", "IDLE")
            self.token = msg.get("token", "") or ""
            color, default_text, force = COLORS.get(self.state, ("#5a5a5a", "", False))
            self._running = self.state == "RUNNING"
            text = redact(msg.get("text") or default_text, 120)
            n = msg.get("task_count") or 0
            if n > 1:
                text = f"{text} ({n} tasks)"
            self.title_lbl.config(text=text, fg="#cccccc" if self.state == "IDLE" else "#ffffff")
            self.dot.config(fg=color)
            self.detail.config(text=redact(msg.get("detail", ""), EXPANDED_W - 20))
            self._forced = bool(msg.get("expand", force))
            self._expanded = self._forced or self._hovered
            self._layout()
            self._sync_geometry()
        elif kind == "exit":
            self._quit.set()

    # ------------------------------------------------------------- layout

    _hovered = False

    def _enter(self, _ev=None) -> None:
        if self._hovered:
            return
        self._hovered = True
        if not self._forced:
            self._expanded = True
            self._sync_geometry()
        self._layout()

    def _leave(self, _ev=None) -> None:
        if not self._hovered:
            return
        self._hovered = False
        if not self._forced:
            self._expanded = False
            self._sync_geometry()
        self._layout()

    def _layout(self) -> None:
        if self._expanded:
            if not self.body.winfo_ismapped():
                self.body.pack(fill="x", padx=0, pady=0)
        elif self.body.winfo_ismapped():
            self.body.pack_forget()
        for b in self.buttons.values():
            b.pack_forget()
        for n in self._visible():
            self.buttons[n].pack(side="left", padx=3, pady=2)
        self.root.update_idletasks()

    def _pump(self) -> None:
        if self._quit.is_set():
            self._drain()
            try:
                self.root.quit()
            except Exception:  # noqa: BLE001
                pass
            return
        self._drain()
        self.root.after(80, self._pump)

    def _drain(self) -> None:
        while True:
            try:
                self._apply(self.q.get_nowait())
            except queue.Empty:
                return
            except Exception:  # noqa: BLE001
                continue


def main() -> int:
    ui = UI()
    hooks = Hooks(on_user_input=lambda: send({"event": "user_intervention"}),
                  is_running=lambda: ui._running)

    for w in (ui.root, ui.head, ui.body):
        w.bind("<Enter>", ui._enter, add="+")
        w.bind("<Leave>", ui._leave, add="+")

    threading.Thread(target=hooks.start, daemon=True).start()

    def reader() -> None:
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if msg.get("cmd") == "input_window":
                    hooks.input_window(float(msg.get("ms", 400)))
                else:
                    ui.q.put(msg)
        except Exception:  # noqa: BLE001
            pass
        finally:
            # Engine pipe broken -> exit 0. Engine watchdog safe-pauses.
            ui.q.put({"cmd": "exit"})

    threading.Thread(target=reader, daemon=True).start()

    def heartbeat() -> None:
        while not ui._quit.is_set():
            send({"event": "alive"})
            time.sleep(2.0)

    threading.Thread(target=heartbeat, daemon=True).start()

    try:
        ui.root.mainloop()
    finally:
        ui._quit.set()
        hooks.stop()
        try:
            ui.root.destroy()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())