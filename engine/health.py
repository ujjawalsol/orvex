"""System health guard: READ-ONLY snapshots before/after production runs.

Covers: uptime, automation PIDs, explorer/DWM, taskbar/desktop, foreground,
input desktop identity, top-level counts, owned processes, CPU/mem, plus
diagnostic-only HID/USB/mouse/keyboard enumeration counts.
NEVER modifies state. Device checks never restart/disable/touch anything.
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes


def _uptime_s() -> float:
    try:
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:  # noqa: BLE001
        return 0.0


def _input_desktop_name() -> str:
    try:
        user32 = ctypes.windll.user32
        h = user32.OpenInputDesktop(0, False, 0x0100)  # GENERIC_READ
        if not h:
            return "unknown-closed"
        n = wintypes.DWORD(0)
        user32.GetUserObjectInformationW(h, 2, None, 0, ctypes.byref(n))
        buf = ctypes.create_unicode_buffer(n.value // 2 + 1 if n.value else 64)
        ok = user32.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(n))
        user32.CloseDesktop(h)
        return buf.value if ok else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown-error"


def _hid_usb_counts() -> dict:
    """Diagnostic-only device enumeration counts (SetupDi, no state change)."""
    out: dict[str, object] = {"mouse": None, "keyboard": None, "hid": None, "usb": None}
    try:
        setupapi = ctypes.windll.SetupAPI
        # 64-bit-correct prototypes (default c_int restype truncates HANDLEs)
        setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                                  ctypes.c_void_p, wintypes.DWORD]
        setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                   ctypes.c_void_p]
        setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
        setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
        guids = {
            # GUID_DEVINTERFACE_MOUSE / KEYBOARD / HID / USB_DEVICE
            "mouse": "{378de44c-56ef-11d1-bc8c-00a0c9145521}",
            "keyboard": "{884b96c3-56ef-11d1-bc8c-00a0c9145521}",
            "hid": "{4d1e55b2-f16f-11cf-88cb-001111000030}",
            "usb": "{a5dcbf10-6530-11d2-901f-00c04fb951ed}",
        }
        import uuid as _uuid

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        for key, gs in guids.items():
            u = _uuid.UUID(gs)
            g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            h = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)  # PRESENT|INTERFACE
            if int(h or 0) in (0, -1):
                out[key] = "query-failed"
                continue

            class SP_DEVINFO_DATA(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
                            ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

            n = 0
            while True:
                info = SP_DEVINFO_DATA()
                info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
                if not setupapi.SetupDiEnumDeviceInfo(h, n, ctypes.byref(info)):
                    break
                n += 1
            out[key] = n
            setupapi.SetupDiDestroyDeviceInfoList(h)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
    # RawInput device counts: the correct presence signal for mice/keyboards
    # (legacy interface GUIDs above enumerate PS/2-style instances only).
    try:
        user32 = ctypes.windll.user32
        user32.GetRawInputDeviceList.argtypes = [ctypes.c_void_p,
                                                 ctypes.POINTER(wintypes.UINT),
                                                 wintypes.UINT]
        user32.GetRawInputDeviceList.restype = wintypes.UINT

        class RAWINPUTDEVICELIST(ctypes.Structure):
            _fields_ = [("hDevice", ctypes.c_void_p), ("dwType", wintypes.DWORD)]

        n = wintypes.UINT(0)
        sz = ctypes.sizeof(RAWINPUTDEVICELIST)
        if user32.GetRawInputDeviceList(None, ctypes.byref(n), sz) != 0xFFFFFFFF:
            arr = (RAWINPUTDEVICELIST * n.value)()
            if user32.GetRawInputDeviceList(arr, ctypes.byref(n), sz) != 0xFFFFFFFF:
                mice = sum(1 for d in arr if d.dwType == 0)
                kbds = sum(1 for d in arr if d.dwType == 1)
                out["rawinput_mouse"] = mice
                out["rawinput_keyboard"] = kbds
    except Exception as e:  # noqa: BLE001
        out["rawinput_error"] = str(e)[:120]
    return out


def snapshot(owned_pids: list[int] | None = None) -> dict:
    """Full read-only health snapshot."""
    from .safety import SafetyPolicy

    snap = SafetyPolicy.shell_health()
    snap["uptime_s"] = round(_uptime_s(), 1)
    snap["automation_pid"] = os.getpid()
    snap["owned_pids"] = list(owned_pids or [])
    snap["input_desktop"] = _input_desktop_name()
    try:
        import psutil as _ps

        snap["cpu_pct"] = _ps.cpu_percent(interval=0.5)
        snap["mem_pct"] = _ps.virtual_memory().percent
        snap["proc_count"] = len(_ps.pids())
    except Exception as e:  # noqa: BLE001
        snap["sys_error"] = str(e)[:120]
    snap["devices"] = _hid_usb_counts()
    snap["ts"] = time.time()
    return snap


def diff_devices(before: dict, after: dict) -> list[str]:
    changes = []
    for k in ("mouse", "keyboard", "hid", "usb", "rawinput_mouse", "rawinput_keyboard"):
        b = (before.get("devices") or {}).get(k)
        a = (after.get("devices") or {}).get(k)
        if isinstance(b, int) and isinstance(a, int) and b != a:
            changes.append(f"device:{k} {b}->{a}")
    return changes


def diff_shell(before: dict, after: dict) -> list[str]:
    from .safety import SafetyPolicy

    return SafetyPolicy.health_diff(before, after)


def is_hung(hwnd: int) -> bool | None:
    """IsHungAppWindow probe (read-only). None if undeterminable."""
    try:
        return bool(ctypes.windll.user32.IsHungAppWindow(hwnd))
    except Exception:  # noqa: BLE001
        return None
