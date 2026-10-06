"""Phase 0 runner: executes benchmark ops, writes JSONL + summary.

Usage:
    python bench/run_phase0.py [--reps N] [--out bench/results/phase0.jsonl]

Measures (per §2): startup, window enum, foreground, SendInput null-move,
clipboard roundtrip, UIA cold/warm lookup, UIA edit probe, screenshot
full/region, MCP dispatch echo, browser discovery probe.
Reports median/p95/p99 per operation. Rust/C# prototypes implement the
same operation names; compare tables only after they report.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.harness import Recorder, summarize  # noqa: E402
from bench.machine import get_metadata  # noqa: E402
from bench import ops_win  # noqa: E402

OPS: list[tuple[str, str]] = [
    ("startup_noop", "python"),
    ("window_enumeration", "win32"),
    ("get_foreground", "win32"),
    ("sendinput_null", "win32-sendinput"),
    ("clipboard_roundtrip", "win32-clipboard"),
    ("uia_cold_lookup", "uia-pywinauto"),
    ("uia_warm_lookup", "uia-pywinauto"),
    ("uia_edit_probe", "uia-pywinauto"),
    ("screenshot_full", "capture-mss"),
    ("screenshot_region", "capture-mss"),
    ("mcp_dispatch_echo", "mcp-echo"),
    ("browser_probe", "browser-discovery"),
]

FN_MAP = {
    "startup_noop": ops_win.op_startup_noop,
    "window_enumeration": ops_win.op_window_enumeration,
    "get_foreground": ops_win.op_get_foreground,
    "sendinput_null": ops_win.op_sendinput_null,
    "clipboard_roundtrip": ops_win.op_clipboard_roundtrip,
    "uia_cold_lookup": ops_win.op_uia_cold_lookup,
    "uia_warm_lookup": ops_win.op_uia_warm_lookup,
    "uia_edit_probe": ops_win.op_uia_edit_probe,
    "screenshot_full": ops_win.op_screenshot_full,
    "screenshot_region": ops_win.op_screenshot_region,
    "mcp_dispatch_echo": ops_win.op_mcp_dispatch_echo,
    "browser_probe": ops_win.op_browser_probe,
}


def _whoami_groups() -> str:
    try:
        out = subprocess.run(["whoami", "/groups"], capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            if "Label" in line or "S-1-16" in line:
                return line.strip()[:200]
        return out.stdout[:500]
    except Exception as e:  # noqa: BLE001
        return f"whoami_failed:{e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=21)
    ap.add_argument("--out", default="bench/results/phase0.jsonl")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    out = base / args.out
    if out.exists():
        out.unlink()
    rec = Recorder(out)

    meta = get_metadata()
    meta["integrity_whoami"] = _whoami_groups()
    print(json.dumps({"machine": meta}, indent=2))

    # warm up cold paths once (not recorded) to separate import cost
    try:
        ops_win.op_window_enumeration()
    except Exception:  # noqa: BLE001
        pass

    for name, backend in OPS:
        fn = FN_MAP[name]
        for _ in range(args.reps):
            if name == "uia_warm_lookup" and _ == 0:
                # prime the warm cache outside measurement once
                try:
                    ops_win.op_uia_warm_lookup()
                except Exception:  # noqa: BLE001
                    pass
            rec.measure(name, backend, fn)

    # summarize per operation
    by_op: dict[str, list] = {}
    for s in rec.samples:
        by_op.setdefault(s.operation, []).append(s)
    summary = {op: summarize(v) for op, v in by_op.items()}
    print(json.dumps({"summary": summary}, indent=2))
    schemanote = {
        "note": "Rust/C# prototypes must implement identical operation names. "
        "Do not claim language superiority from this Python baseline alone."
    }
    print(json.dumps(schemanote))
    print(f"Wrote {len(rec.samples)} samples to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
