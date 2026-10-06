"""Phase 1.5 §5-6: UIA set_value breakdown + adaptive absent-target lookup.

Breaks set_value into: find / pattern acquisition / SetValue / verify-read /
MCP-serialization proxy. Compares fixed-2s Exists timeout vs adaptive
(fast probe 0.3s -> targeted 1s -> escalate) on existing/absent/deep/wrong/
ambiguous targets.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.harness import Recorder, summarize  # noqa: E402


def ensure_notepad():
    import uiautomation as auto

    w = auto.WindowControl(searchDepth=1, RegexName=".*Notepad.*")
    if not w.Exists(maxSearchSeconds=1):
        subprocess.Popen(["notepad.exe"])
        w = auto.WindowControl(searchDepth=1, RegexName=".*Notepad.*")
        assert w.Exists(maxSearchSeconds=10), "notepad did not open"
    return w


def main() -> int:
    import os as _os

    # micro-bench opens/kills Notepad: allowed only explicitly in Phase 2.
    if _os.environ.get("SFMCP_ALLOW_LEGACY_STRESS", "0") != "1":
        print("RETIRED in Phase 2 default runs (spawns/kills Notepad). "
              "Set SFMCP_ALLOW_LEGACY_STRESS=1 to run explicitly.")
        return 2
    base = Path(__file__).resolve().parent.parent
    out = base / "bench" / "results" / "uia_micro.jsonl"
    if out.exists():
        out.unlink()
    rec = Recorder(out)
    import uiautomation as auto

    # NOTE (Phase 5): mass taskkill retired permanently; open path reuses any
    # existing window or launches exactly one.
    time.sleep(0.2)
    w = ensure_notepad()

    # --- set_value breakdown (Notepad Edit) ---
    def find_edit():
        return w.EditControl(searchDepth=8)

    ctl, _ = rec.measure("find_edit", "uia", find_edit)
    assert ctl.Exists(maxSearchSeconds=2)

    def acquire_pattern():
        return ctl.GetValuePattern()

    pat, s_pat = rec.measure("pattern_acquire", "uia", acquire_pattern)

    def do_set():
        pat.SetValue("micro-probe-text")

    _, s_set = rec.measure("valuepattern_setvalue", "uia", do_set)

    def verify_read():
        return pat.Value

    val, s_verify = rec.measure("verify_read", "uia", verify_read)
    assert val == "micro-probe-text", f"verify mismatch {val!r}"

    def uncached_prop():
        return ctl.GetPropertyValue(30005)  # UIA Name property id

    _, _ = rec.measure("uncached_property", "uia", uncached_prop)

    # second write measures warm path
    _, _ = rec.measure("valuepattern_setvalue_warm", "uia", lambda: pat.SetValue("micro-probe-2"))

    # --- absent/adaptive lookup ---
    scenarios = {
        "existing": ("Notepad", True),
        "absent": ("NoSuchAppXYZ123", False),
        "deep": ("File name:", True),  # nested in save dialog; absent unless dialog open
        "wrong": ("Notepad###", False),
    }
    for name, (regex, _exp) in scenarios.items():
        def fixed():
            c = auto.WindowControl(searchDepth=1, RegexName=f".*{regex}.*")
            return c.Exists(maxSearchSeconds=2)

        _, _ = rec.measure(f"lookup_fixed2s_{name}", "uia-fixed", fixed)

        def adaptive():
            # fast probe 0.3s, then targeted 1s once
            c = auto.WindowControl(searchDepth=1, RegexName=f".*{regex}.*")
            if c.Exists(maxSearchSeconds=0.3):
                return True
            return c.Exists(maxSearchSeconds=1.0)

        _, _ = rec.measure(f"lookup_adaptive_{name}", "uia-adaptive", adaptive)

    # ambiguous: how many top-level Explorers match?
    def ambiguous():
        root = auto.GetRootControl()
        return sum(1 for c in root.GetChildren() if "Explorer" in (c.Name or ""))

    n, _ = rec.measure("ambiguous_explorer_count", "uia", ambiguous)

    by_op: dict[str, list] = {}
    for s in rec.samples:
        by_op.setdefault(s.operation, []).append(s)
    print(f"ambiguous_explorer_windows={n}")
    for op, v in by_op.items():
        print(op, summarize(v))
    print(f"Wrote {len(rec.samples)} samples to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
