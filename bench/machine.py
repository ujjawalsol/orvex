"""Machine metadata for reproducible benchmarks (§3)."""
from __future__ import annotations

import ctypes
import os
import platform
import sys
from typing import Any


def get_metadata() -> dict[str, Any]:
    meta: dict[str, Any] = {}
    meta["os"] = platform.system()
    meta["windows_version"] = platform.version()
    meta["windows_release"] = platform.release()
    meta["architecture"] = platform.machine()
    meta["python"] = sys.version.replace("\n", " ")
    try:
        import psutil

        meta["cpu_count_logical"] = psutil.cpu_count(logical=True)
        meta["cpu_count_physical"] = psutil.cpu_count(logical=False)
        meta["ram_total_bytes"] = psutil.virtual_memory().total
        try:
            freq = psutil.cpu_freq()
            meta["cpu_freq_mhz"] = freq.current if freq else None
        except Exception:
            meta["cpu_freq_mhz"] = None
    except Exception as e:  # noqa: BLE001
        meta["psutil_error"] = str(e)
    # CPU name via env / wmi fallback
    meta["cpu_name"] = os.environ.get("PROCESSOR_IDENTIFIER", "unknown")
    # Displays: resolution + count via Win32
    try:
        user32 = ctypes.windll.user32
        meta["primary_screen_w"] = user32.GetSystemMetrics(0)
        meta["primary_screen_h"] = user32.GetSystemMetrics(1)
        meta["monitor_count"] = user32.GetSystemMetrics(80)
    except Exception as e:  # noqa: BLE001
        meta["screen_error"] = str(e)
    # DPI awareness: best-effort, report process DPI awareness value
    try:
        shcore = ctypes.windll.shcore
        awareness = ctypes.c_int(0)
        if shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness)) == 0:
            meta["dpi_awareness"] = awareness.value
    except Exception:
        meta["dpi_awareness"] = None
    # Integrity level: best-effort via whoami is done by runner; record medium default
    meta["integrity_hint"] = "see run log (whoami /groups)"
    try:
        import mcp  # type: ignore

        meta["mcp_sdk"] = getattr(mcp, "__version__", "unknown")
    except Exception:
        meta["mcp_sdk"] = None
    try:
        import importlib.metadata as md

        for pkg in ("pywinauto", "comtypes", "pillow", "mss", "psutil"):
            try:
                meta[f"pkg_{pkg}"] = md.version(pkg)
            except Exception:
                pass
    except Exception:
        pass
    return meta
