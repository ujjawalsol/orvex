"""Read-only audit: are any automation-controller UI processes left running?

Matches the controller's actual argv shape (python -u .../controller_ui.py) and
excludes this process and its ancestors, so the audit never counts itself.
"""
from __future__ import annotations

import os
import sys

import psutil


def controller_pids() -> list[tuple[int, int, str]]:
    me = os.getpid()
    ancestors = {me}
    try:
        p = psutil.Process(me)
        while p.ppid():
            p = psutil.Process(p.ppid())
            ancestors.add(p.pid)
    except Exception:  # noqa: BLE001
        pass
    out: list[tuple[int, int, str]] = []
    for proc in psutil.process_iter(["pid", "ppid", "cmdline", "create_time"]):
        argv = [str(a) for a in (proc.info.get("cmdline") or [])]
        if not argv:
            continue
        if not argv[-1].endswith("controller_ui.py"):
            continue
        if "python" not in os.path.basename(argv[0]).lower():
            continue
        if proc.info["pid"] in ancestors:
            continue
        out.append((proc.info["pid"], proc.info["ppid"],
                    proc.info.get("create_time", 0)))
    return out


def main() -> int:
    found = controller_pids()
    if not found:
        print("controller processes: 0 (clean)")
        return 0
    print(f"controller processes: {len(found)}")
    for pid, ppid, _ in found:
        alive = psutil.pid_exists(pid)
        print(f"  pid={pid} ppid={ppid} alive={alive}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())