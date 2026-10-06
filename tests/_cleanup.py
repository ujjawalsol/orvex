"""Shared test-window cleanup for the suite scripts (§28: test windows = 0).

Only unambiguous test artifacts are touched:
  * Notepad documents with a test-artifact title, discarding unsaved changes
    through the window's own Don't Save button
  * Explorer windows titled after the engine sandbox folder

Never touches any other window. Read-only detection, owned cleanup only.
"""
from __future__ import annotations

import os
import time

import uiautomation as auto

PREFIXES = ("untitled", "orvex_", "sfmcp_", "smoke_", "wa_", "phase1-", "master_", "ctl_")


def _sandbox_title() -> str:
    from engine.safety import SafetyPolicy

    return os.path.basename(SafetyPolicy().sandbox)


def _is_notepad_artifact(name: str) -> bool:
    return name.lstrip("* ").lower().startswith(PREFIXES)


def is_test_window(name: str) -> bool:
    try:
        sb = _sandbox_title()
    except Exception:  # noqa: BLE001
        sb = "orvex_sandbox"
    return (("Notepad" in name and _is_notepad_artifact(name)) or name.endswith(sb) or name.endswith("windows_mcp_test_sandbox"))


def leftover_test_windows() -> list[str]:
    try:
        return [(c.Name or "") for c in auto.GetRootControl().GetChildren()
                if is_test_window(c.Name or "")]
    except Exception:  # noqa: BLE001
        return []


def cleanup_test_windows(settle_s: float = 0.8) -> list[str]:
    """Close every leftover test window. Returns what is still open."""
    try:
        sb = _sandbox_title()
    except Exception:  # noqa: BLE001
        sb = "windows_mcp_test_sandbox"
    for c in auto.GetRootControl().GetChildren():
        name = c.Name or ""
        try:
            if "Notepad" in name and _is_notepad_artifact(name):
                b = c.ButtonControl(RegexName=r".*Don't Save.*", searchDepth=8)
                if b.Exists(0):
                    b.GetInvokePattern().Invoke()
                time.sleep(0.35)
                ctl = c.GetWindowPattern()
                if ctl is not None:
                    ctl.Close()
            elif name.endswith(sb):
                ctl = c.GetWindowPattern()
                if ctl is not None:
                    ctl.Close()
        except Exception:  # noqa: BLE001
            continue
    time.sleep(settle_s)
    left = leftover_test_windows()
    print("leftover test windows:", left)
    return left