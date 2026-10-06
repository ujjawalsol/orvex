"""Windows operations measured by Phase 0 harness.

Backends used here (all local, no network):
- win32: ctypes -> user32/kernel32 (window enum, foreground, SendInput null-move)
- uia: `uiautomation` package (raw UIA, targeted lookup, no full tree walk)
- clipboard: Win32 clipboard via ctypes with correct 64-bit restypes
- capture-mss: full-screen grab via mss; region grab via mss + PIL
- mcp-echo: in-process JSON-RPC echo to isolate dispatch/serialization cost

Each op is a small function so Rust/C# prototypes can implement the same
operation names and their results can be compared 1:1.
"""
from __future__ import annotations

import ctypes
import io
import json
from ctypes import wintypes
from typing import Any


# ---------------------------------------------------------------- Win32

def op_startup_noop() -> bool:
    """Process-startup-adjacent baseline: pure Python call overhead."""
    return True


def op_window_enumeration() -> int:
    user32 = ctypes.windll.user32
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]
    def _cb(hwnd: int, _lp: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(_cb, 0)
    return len(titles)


def op_get_foreground() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow())


def op_sendinput_null() -> int:
    """Zero-movement mouse SendInput: measures input injection path cost only."""

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

    # INPUT_MOUSE=0, MOUSEEVENTF_MOVE=0x0001 with dx=dy=0 -> no visible motion
    inp = INPUT(0, MOUSEINPUT(0, 0, 0, 0x0001, 0, None))
    n = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    return int(n)


def _win32_setup_clipboard_types() -> tuple[Any, Any]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE
    return user32, kernel32


def op_clipboard_roundtrip() -> int:
    user32, kernel32 = _win32_setup_clipboard_types()
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    text = "bench-clipboard-probe"
    if not user32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed (another app may hold it)")
    try:
        user32.EmptyClipboard()
        n_chars = len(text) + 1
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, n_chars * 2)
        if not h:
            raise RuntimeError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            raise RuntimeError("GlobalLock failed")
        try:
            src = ctypes.create_unicode_buffer(text)
            ctypes.memmove(ptr, src, n_chars * 2)
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise RuntimeError("SetClipboardData failed")
        # read back (system owns the handle after SetClipboardData: do not free)
        h_get = user32.GetClipboardData(CF_UNICODETEXT)
        if not h_get:
            raise RuntimeError("GetClipboardData failed")
        ptr2 = kernel32.GlobalLock(h_get)
        if not ptr2:
            raise RuntimeError("GlobalLock(get) failed")
        try:
            out = ctypes.wstring_at(ptr2)
        finally:
            kernel32.GlobalUnlock(h_get)
        if out != text:
            raise RuntimeError(f"clipboard mismatch: {out!r}")
        return len(out)
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- UIA (uiautomation, targeted)

def op_uia_cold_lookup(window_name_substr: str = "Notepad") -> str:
    """Cold: fresh UIA query for top-level window containing substring.

    Targeted: does not walk full desktop tree; single FindWindow-level query.
    """
    import uiautomation as auto

    # depth=1 keeps scope to top-level windows only (minimal scope)
    win = auto.WindowControl(searchDepth=1, RegexName=f".*{window_name_substr}.*")
    return f"exists={win.Exists(maxSearchSeconds=2)}"


def op_uia_warm_lookup(window_name_substr: str = "Notepad") -> str:
    """Warm: repeat targeted query (UIA provider + COM path already initialized)."""
    import uiautomation as auto

    win = auto.WindowControl(searchDepth=1, RegexName=f".*{window_name_substr}.*")
    return f"exists={win.Exists(maxSearchSeconds=2)}"


def op_uia_edit_probe() -> str:
    """Find Notepad Edit control by control type; read-only probe, no side effects."""
    import uiautomation as auto

    wins = auto.GetRootControl().GetChildren()
    notepad = None
    for w in wins:
        try:
            name = w.Name or ""
        except Exception:  # noqa: BLE001
            continue
        if "Notepad" in name:
            notepad = w
            break
    if notepad is None:
        return "notepad_not_running"
    try:
        edit = notepad.EditControl(searchDepth=4)
        return f"edit_exists={edit.Exists(maxSearchSeconds=2)}"
    except Exception as e:  # noqa: BLE001
        return f"edit_probe_failed:{e}"


# ---------------------------------------------------------------- Capture

def op_screenshot_full() -> tuple[int, int]:
    import mss

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        return (shot.width, shot.height)


def op_screenshot_region() -> tuple[int, int]:
    import mss

    with mss.mss() as sct:
        mon = sct.monitors[1]
        region = {
            "left": mon["left"],
            "top": mon["top"],
            "width": min(300, mon["width"]),
            "height": min(200, mon["height"]),
        }
        shot = sct.grab(region)
        # encode to JPEG in-memory to include serialization cost
        from PIL import Image

        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return (shot.width, shot.height)


def op_mcp_dispatch_echo(payload_size: int = 256) -> int:
    """In-process JSON-RPC echo: isolates MCP serialization cost."""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "execute", "arguments": {"pad": "x" * payload_size}},
    }
    raw = json.dumps(req)
    parsed = json.loads(raw)
    resp = {"jsonrpc": "2.0", "id": parsed["id"], "result": {"ok": True}}
    return len(json.dumps(resp))


def op_browser_probe() -> str:
    """Phase 0 browser op: discovery only (no CDP yet). Lists candidate
    Chrome/Edge remote-debugging endpoints without assuming :9222."""
    import os

    candidates: list[str] = [f"http://127.0.0.1:{port}/json" for port in (9222, 9333)]
    home = os.path.expanduser("~")
    for rel in (
        r"AppData\Local\Google\Chrome\User Data\DevToolsActivePort",
        r"AppData\Local\Microsoft\Edge\User Data\DevToolsActivePort",
    ):
        p = os.path.join(home, rel)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    content = f.read().split()
                candidates.append(f"activeport:{rel}:{'|'.join(content[:2])}")
            except Exception:  # noqa: BLE001
                candidates.append(f"activeport:{rel}:unreadable")
    return ";".join(candidates)
