"""Capture desktop screenshots of the ORVEX Controller UI in each state:
- IDLE (hidden)
- RUNNING (active automation)
- USER_CONTROL (manual control paused)
- STOPPED (task stopped)
"""
import ctypes
from ctypes import wintypes
import time
import json
import os
from pathlib import Path
import mss
import mss.tools

user32 = ctypes.windll.user32

def attach_to_input_desktop():
    hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)

def capture_screen(output_path: Path):
    with mss.mss() as sct:
        # Capture primary monitor
        monitor = sct.monitors[1]  # primary monitor
        sct_img = sct.grab(monitor)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mss.tools.to_png(sct_img.rgb, sct_img.size, output=str(output_path))
        print(f"Captured: {output_path} ({sct_img.size[0]}x{sct_img.size[1]})")

def capture_window_crop(hwnd: int, output_path: Path, pad: int = 40):
    rc = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    left, top, right, bottom = int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)
    
    with mss.mss() as sct:
        # Screen bounds
        mon = sct.monitors[1]
        c_left = max(0, left - pad)
        c_top = max(0, top - pad)
        c_right = min(mon["width"], right + pad)
        c_bottom = min(mon["height"], bottom + pad)
        
        region = {
            "top": c_top,
            "left": c_left,
            "width": c_right - c_left,
            "height": c_bottom - c_top
        }
        sct_img = sct.grab(region)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mss.tools.to_png(sct_img.rgb, sct_img.size, output=str(output_path))
        print(f"Captured crop: {output_path} ({sct_img.size[0]}x{sct_img.size[1]})")

def main():
    attach_to_input_desktop()
    from engine.controller import AutomationController, STATE_FILE
    
    out_dir = Path(r"C:\Users\ujjaw\.gemini\antigravity-ide\brain\84c673ca-c8ad-4010-aa0b-150f30825e59\screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    ctl = AutomationController()
    assert ctl.start(), "Failed to start AutomationController"
    time.sleep(0.5)
    
    print("\n--- [1] IDLE STATE ---")
    assert ctl.state == "IDLE"
    hwnds_idle = [h for h in ctl.owned().get("window_handles", []) if user32.IsWindowVisible(h)]
    print(f"Visible windows in IDLE: {len(hwnds_idle)}")
    capture_screen(out_dir / "state_1_idle_desktop.png")
    
    print("\n--- [2] RUNNING STATE ---")
    ctl.begin_task("Opening Shopify in Chrome", "task_capture_101")
    time.sleep(1.0)
    owned = ctl.owned()
    hwnd = owned["window_handles"][0] if owned["window_handles"] else 0
    assert hwnd > 0 and user32.IsWindowVisible(hwnd), "Controller window must be visible!"
    capture_screen(out_dir / "state_2_running_desktop.png")
    capture_window_crop(hwnd, out_dir / "state_2_running_controller_card.png")
    
    print("\n--- [3] USER_CONTROL STATE ---")
    ctl.take_control()
    time.sleep(0.8)
    assert ctl.state == "USER_CONTROL"
    capture_screen(out_dir / "state_3_user_control_desktop.png")
    capture_window_crop(hwnd, out_dir / "state_3_user_control_controller_card.png")
    
    print("\n--- [4] RESUMED RUNNING STATE ---")
    ctl.resume()
    time.sleep(0.8)
    assert ctl.state == "RUNNING"
    
    print("\n--- [5] STOPPED STATE ---")
    ctl.stop()
    time.sleep(0.4)
    assert ctl.state == "STOPPED"
    capture_screen(out_dir / "state_4_stopped_desktop.png")
    capture_window_crop(hwnd, out_dir / "state_4_stopped_controller_card.png")
    
    print("\n--- [6] AUTO-HIDDEN STATE AFTER STOP ---")
    time.sleep(2.0)
    is_vis = bool(user32.IsWindowVisible(hwnd))
    print(f"IsWindowVisible after auto-hide: {is_vis}")
    assert not is_vis, "Window must be hidden after stop"
    capture_screen(out_dir / "state_5_autohidden_desktop.png")
    
    ctl.shutdown()
    print("\nAll state screenshots successfully captured!")

if __name__ == "__main__":
    main()
