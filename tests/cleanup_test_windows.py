"""Close leftover TEST windows from earlier suite runs (read-only detection,
owned cleanup only).

Handles exactly two unambiguous test-artifact classes:
  * Notepad documents with a test-artifact title (Untitled / sfmcp_* / smoke_* /
    wa_* / phase1-* / master_* / ctl_*), discarding unsaved changes through the
    window's own Don't Save button
  * Explorer windows titled after the engine sandbox folder

Never touches any other window. Explorer folder views hold no unsaved state, so
closing them is always safe.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uiautomation as auto  # noqa: E402

from engine.safety import SafetyPolicy  # noqa: E402

PREFIXES = ("untitled", "sfmcp_", "smoke_", "wa_", "phase1-", "master_", "ctl_")
SANDBOX_TITLE = os.path.basename(SafetyPolicy().sandbox)


def _is_notepad_artifact(name: str) -> bool:
    return name.lstrip("* ").lower().startswith(PREFIXES)


def main() -> int:
    closed_np = closed_ex = skipped = 0
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
                closed_np += 1
            elif name.endswith(SANDBOX_TITLE):
                ctl = c.GetWindowPattern()
                if ctl is not None:
                    ctl.Close()
                closed_ex += 1
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001
            print("skip", name, e)
            skipped += 1
    time.sleep(1.0)
    left = [(c.Name or "") for c in auto.GetRootControl().GetChildren()
            if ("Notepad" in (c.Name or "") and _is_notepad_artifact(c.Name or ""))
            or (c.Name or "").endswith(SANDBOX_TITLE)]
    print(f"closed notepad={closed_np} explorer={closed_ex} untouched={skipped}")
    print("leftover test windows:", left)
    return 0 if not left else 1


if __name__ == "__main__":
    raise SystemExit(main())