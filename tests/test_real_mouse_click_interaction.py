"""Test real physical mouse click interaction on the ORVEX Controller UI buttons."""
import ctypes
from ctypes import wintypes
import time
import os
import sys
from pathlib import Path

user32 = ctypes.windll.user32


import threading


def attach_to_input_desktop():
    hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)


def _do_click(x: int, y: int):
    attach_to_input_desktop()
    user32.SetCursorPos(x, y)
    time.sleep(0.08)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
    time.sleep(0.1)


def physical_mouse_click(x: int, y: int):
    t = threading.Thread(target=_do_click, args=(x, y))
    t.start()
    t.join()


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rc = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)


def test_real_ui_click_interaction():
    print("==================================================")
    print("  ORVEX REAL PHYSICAL MOUSE CLICK INTERACTION TEST")
    print("==================================================")

    attach_to_input_desktop()
    from engine.controller import AutomationController

    ctl = AutomationController()
    assert ctl.start(), "Failed to start AutomationController"
    print("\n[Step 1] Controller started. Initial state:", ctl.state)
    assert ctl.state == "IDLE"

    # While IDLE, verify no visible controller window on desktop
    hwnds_idle = [h for h in ctl.owned().get("window_handles", []) if user32.IsWindowVisible(h)]
    assert len(hwnds_idle) == 0, f"Expected 0 visible windows while idle, got {len(hwnds_idle)}"
    print("IsWindowVisible while idle: False (100% hidden)")

    # 1. Begin Task -> UI Appears
    print("\n[Step 2] Beginning active task -> UI must appear...")
    ctl.begin_task("Interactive UI Test", "task_click_1")
    time.sleep(0.8)

    # Refresh HWND
    owned = ctl.owned()
    hwnd = owned["window_handles"][0] if owned["window_handles"] else 0
    assert hwnd > 0, "No HWND acquired when active"
    is_vis = bool(user32.IsWindowVisible(hwnd))
    print(f"Controller HWND: {hwnd}")
    print(f"IsWindowVisible during RUNNING: {is_vis}")
    assert is_vis, "Controller must be VISIBLE during automation!"
    assert ctl.state == "RUNNING"

    l, t, r, b = get_window_rect(hwnd)
    w = r - l
    h = b - t
    print(f"Controller window bounds: left={l}, top={t}, width={w}, height={h}")

    import json
    from engine.controller import STATE_FILE
    data = json.loads(STATE_FILE.read_text())
    if "buttons" in data and "left" in data["buttons"]:
        btn_left_x, btn_left_y = data["buttons"]["left"]
        btn_right_x, btn_right_y = data["buttons"]["right"]
    else:
        btn_left_x = l + int(w * 0.28)
        btn_left_y = t + int(h * 0.72)
        btn_right_x = l + int(w * 0.75)
        btn_right_y = t + int(h * 0.72)
    print(f"Buttons target pixels: Left=({btn_left_x}, {btn_left_y}), Right=({btn_right_x}, {btn_right_y})")

    # 2. Click "Take Control" Button
    print(f"\n[Step 3] Physically clicking [ Take Control ] at ({btn_left_x}, {btn_left_y})...")
    physical_mouse_click(btn_left_x, btn_left_y)
    time.sleep(0.6)

    print("Controller state after physical click:", ctl.state)
    assert ctl.state == "USER_CONTROL", f"Expected USER_CONTROL, got {ctl.state}"
    print("PASS: Physical click on [ Take Control ] transitioned state to USER_CONTROL!")

    # 3. Click "Resume Automation" Button
    print(f"\n[Step 4] Physically clicking [ Resume Automation ] at ({btn_left_x}, {btn_left_y})...")
    physical_mouse_click(btn_left_x, btn_left_y)
    time.sleep(0.6)

    print("Controller state after physical click:", ctl.state)
    assert ctl.state == "RUNNING", f"Expected RUNNING, got {ctl.state}"
    print("PASS: Physical click on [ Resume Automation ] transitioned state to RUNNING!")

    # 4. Click "Stop" Button
    print(f"\n[Step 5] Physically clicking [ Stop ] at ({btn_right_x}, {btn_right_y})...")
    physical_mouse_click(btn_right_x, btn_right_y)
    time.sleep(0.6)

    print("Controller state after physical click:", ctl.state)
    assert ctl.state == "STOPPED", f"Expected STOPPED, got {ctl.state}"
    print("PASS: Physical click on [ Stop ] transitioned state to STOPPED!")

    # 5. Verify Auto-Hide
    print("\n[Step 6] Waiting for auto-hide delay (1.5s + buffer)...")
    time.sleep(2.2)
    is_vis_after = bool(user32.IsWindowVisible(hwnd))
    print(f"IsWindowVisible after auto-hide: {is_vis_after}")
    assert not is_vis_after, "Controller UI must auto-hide after task completion/cancellation!"
    print("PASS: Controller UI disappeared cleanly from desktop!")

    ctl.shutdown()
    print("\nALL PHYSICAL MOUSE INTERACTION CHECKS PASSED!")


if __name__ == "__main__":
    test_real_ui_click_interaction()
