"""Test duplicate controller prevention across multiple processes and MCP sessions."""
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

user32 = ctypes.windll.user32


def count_visible_orvex_windows() -> list[int]:
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass

    found = []

    def enum_cb(hwnd, _l):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == "ORVEX":
                found.append(hwnd)
        return 1

    CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_size_t, ctypes.c_size_t)
    user32.EnumWindows(CB(enum_cb), 0)
    return found


def test_duplicate_prevention_five_processes():
    print("==================================================")
    print("  ORVEX SINGLETON & DUPLICATE PREVENTION TEST")
    print("==================================================")

    worker_file = str(Path(__file__).resolve().parent.parent / "scratch" / "worker.py")

    procs = []
    print("\n[Step 1] Launching 5 independent controller processes concurrently...")
    for i in range(5):
        p = subprocess.Popen([sys.executable, worker_file, str(i + 1)])
        procs.append(p)

    # Allow processes to connect and begin tasks
    time.sleep(1.2)

    # Query visible ORVEX windows
    hwnds = count_visible_orvex_windows()
    print(f"Total concurrent processes running: 5")
    print(f"Total visible ORVEX controller windows detected: {len(hwnds)}")
    print(f"Window Handles: {hwnds}")

    # Wait for processes to finish
    for p in procs:
        p.wait()

    assert len(hwnds) == 1, f"CRITICAL UX FAILURE: Expected exactly 1 window, found {len(hwnds)}!"
    print("PASS: Exactly 1 visible ORVEX controller window during multi-process execution!")

    # 2. Verify that once all tasks finish, the window auto-hides
    print("\n[Step 2] Verifying Activity-Aware Auto-Hide when idle...")
    time.sleep(2.5)  # 1.5s auto-hide delay + buffer
    hwnds_idle = count_visible_orvex_windows()
    print(f"Visible ORVEX windows when idle: {len(hwnds_idle)}")
    assert len(hwnds_idle) == 0, f"Expected 0 visible windows when idle, found {len(hwnds_idle)}!"
    print("PASS: Controller UI correctly hid itself when idle (Zero desktop pollution)!")


if __name__ == "__main__":
    test_duplicate_prevention_five_processes()
