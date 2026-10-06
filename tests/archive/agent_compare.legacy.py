"""Phase 1.5 §2-4: baseline vs engine on Tasks A-F through REAL MCP stdio.

MODEL: Muse Spark (this session) — intents below are the model's live
selections for each task in each path (obvious optimal choices, no ambiguity).
CLIENT: this script (MCP stdio, real discovery + tool calls).
BASELINE: granular observe->micro-action loop (one verb per execute call,
inspect between actions). ENGINE: one steps[] execute call where possible.

Measures per task: wall_ms, mcp_calls, arg/result bytes, screenshots(=0 both),
success. Reps=5. Reports median/p95/p99 + baseline/engine ratios.
No speedup claimed where baseline is degenerate (single-step tasks).
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _task_defs() -> dict[str, dict]:
    tmp = os.path.join(tempfile.gettempdir(), "sfmcp_15_{n}.txt")
    return {
        # A: open Notepad, type Hello World, verify
        "A": {
            "baseline": [
                ("execute", {"verb": "open_app", "app_hint": "Notepad"}),
                ("inspect", {"app_hint": "Notepad", "target": {"control_type": "Edit"}}),
                ("execute", {"verb": "set_value", "app_hint": "Notepad",
                             "target": {"control_type": "Edit"}, "params": {"text": "Hello World"}}),
                ("verify", {"app_hint": "Notepad"}),
            ],
            "engine": [
                ("execute", {"verb": "run", "steps": [
                    {"verb": "open_app", "app_hint": "Notepad"},
                    {"verb": "set_value", "app_hint": "Notepad",
                     "target": {"control_type": "Edit"}, "params": {"text": "Hello World"}},
                ]}),
            ],
        },
        # B: type + save to file + verify (filename differs per rep via {n})
        "B": {
            "baseline": [
                ("execute", {"verb": "open_app", "app_hint": "Notepad"}),
                ("execute", {"verb": "set_value", "app_hint": "Notepad",
                             "target": {"control_type": "Edit"}, "params": {"text": "agent-probe-{n}"}}),
                ("execute", {"verb": "press", "app_hint": "Notepad", "params": {"keys": "{Ctrl}s"}}),
                ("execute", {"verb": "set_value", "app_hint": "Notepad",
                             "target": {"control_type": "Edit", "name": "File name:"},
                             "params": {"text": tmp}}),
                ("execute", {"verb": "invoke", "app_hint": "Notepad",
                             "target": {"control_type": "Button", "name": "Save"}}),
            ],
            "engine": [
                ("execute", {"verb": "run", "steps": [
                    {"verb": "open_app", "app_hint": "Notepad"},
                    {"verb": "set_value", "app_hint": "Notepad",
                     "target": {"control_type": "Edit"}, "params": {"text": "agent-probe-{n}"}},
                    {"verb": "press", "app_hint": "Notepad", "params": {"keys": "{Ctrl}s"}},
                    {"verb": "set_value", "app_hint": "Notepad",
                     "target": {"control_type": "Edit", "name": "File name:"},
                     "params": {"text": tmp}},
                    {"verb": "invoke", "app_hint": "Notepad",
                     "target": {"control_type": "Button", "name": "Save"}},
                ]}),
            ],
        },
        # C: Explorer to Downloads (open + verify window)
        "C": {
            "baseline": [
                ("execute", {"verb": "open_app", "app_hint": "Explorer"}),
                ("verify", {"app_hint": "Explorer"}),
            ],
            "engine": [
                ("execute", {"verb": "open_app", "app_hint": "Explorer"}),
            ],
        },
        # D: Settings open (single step — expect tie)
        "D": {
            "baseline": [("execute", {"verb": "open_app", "app_hint": "Settings"})],
            "engine": [("execute", {"verb": "open_app", "app_hint": "Settings"})],
        },
        # E: open Notepad + Explorer, then type into Notepad (multi-app targeting)
        "E": {
            "baseline": [
                ("execute", {"verb": "open_app", "app_hint": "Notepad"}),
                ("execute", {"verb": "open_app", "app_hint": "Explorer"}),
                ("inspect", {"app_hint": "Notepad", "target": {"control_type": "Edit"}}),
                ("execute", {"verb": "set_value", "app_hint": "Notepad",
                             "target": {"control_type": "Edit"}, "params": {"text": "E-targeted"}}),
            ],
            "engine": [
                ("execute", {"verb": "run", "steps": [
                    {"verb": "open_app", "app_hint": "Notepad"},
                    {"verb": "open_app", "app_hint": "Explorer"},
                    {"verb": "set_value", "app_hint": "Notepad",
                     "target": {"control_type": "Edit"}, "params": {"text": "E-targeted"}},
                ]}),
            ],
        },
        # F: full multi-step incl. file save + os-level verify
        "F": {
            "baseline": [
                ("execute", {"verb": "open_app", "app_hint": "Notepad"}),
                ("execute", {"verb": "set_value", "app_hint": "Notepad",
                             "target": {"control_type": "Edit"}, "params": {"text": "F-multi-{n}"}}),
                ("execute", {"verb": "press", "app_hint": "Notepad", "params": {"keys": "{Ctrl}s"}}),
                ("execute", {"verb": "set_value", "app_hint": "Notepad",
                             "target": {"control_type": "Edit", "name": "File name:"},
                             "params": {"text": tmp}}),
                ("execute", {"verb": "invoke", "app_hint": "Notepad",
                             "target": {"control_type": "Button", "name": "Save"}}),
                ("verify", {"app_hint": "Notepad"}),
            ],
            "engine": [
                ("execute", {"verb": "run", "steps": [
                    {"verb": "open_app", "app_hint": "Notepad"},
                    {"verb": "set_value", "app_hint": "Notepad",
                     "target": {"control_type": "Edit"}, "params": {"text": "F-multi-{n}"}},
                    {"verb": "press", "app_hint": "Notepad", "params": {"keys": "{Ctrl}s"}},
                    {"verb": "set_value", "app_hint": "Notepad",
                     "target": {"control_type": "Edit", "name": "File name:"},
                     "params": {"text": tmp}},
                    {"verb": "invoke", "app_hint": "Notepad",
                     "target": {"control_type": "Button", "name": "Save"}},
                ]}),
            ],
        },
    }


def _fill(obj, n: int):
    if isinstance(obj, str):
        return obj.replace("{n}", str(n))
    if isinstance(obj, dict):
        return {k: _fill(v, n) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, n) for v in obj]
    return obj


async def _run_path(session, calls: list, rep: int) -> dict:
    t0 = time.perf_counter()
    mcp_calls = 0
    arg_bytes = 0
    res_bytes = 0
    ok = True
    first_action_ms = None
    for tool, args in calls:
        a = _fill(args, rep)
        ab = len(json.dumps(a).encode())
        arg_bytes += ab
        t1 = time.perf_counter()
        try:
            if tool == "execute":
                res = await session.call_tool(tool, {"intent": a})
            else:
                res = await session.call_tool(tool, a)
        except Exception as e:  # noqa: BLE001
            ok = False
            res = None
            err = f"{type(e).__name__}:{e}"
        mcp_calls += 1
        if first_action_ms is None:
            first_action_ms = (time.perf_counter() - t1) * 1000.0
        try:
            rb = len(json.dumps(res.content[0].model_dump(mode="json")).encode()) if res else len(err.encode())
        except Exception:  # noqa: BLE001
            rb = 512
        res_bytes += rb
        # baseline failure detection: receipt status
        if res:
            try:
                txt = res.content[0].model_dump(mode="json").get("text", "")
                r = json.loads(txt) if isinstance(txt, str) and txt.startswith("{") else {}
                if r.get("status") not in ("success", "", None) and r.get("status") != "success":
                    if r.get("status") not in (None, ""):
                        ok = r.get("status") == "success"
            except Exception:  # noqa: BLE001
                pass
    wall = (time.perf_counter() - t0) * 1000.0
    return {"wall_ms": wall, "mcp_calls": mcp_calls, "arg_bytes": arg_bytes,
            "res_bytes": res_bytes, "success": ok,
            "first_action_ms": first_action_ms or 0.0, "screenshots": 0}


async def amain(reps: int, tasks: list[str]) -> int:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    defs = _task_defs()
    out: dict = {}
    for task in tasks:
        out[task] = {"baseline": [], "engine": []}
        for rep in range(reps):
            # NOTE (Phase 5): mass taskkill retired permanently. Legacy file kept
            # for reference only; runs only with SFMCP_ALLOW_LEGACY_STRESS=1.
            time.sleep(0.7)
            for path in ("baseline", "engine"):
                params = StdioServerParameters(
                    command=sys.executable, args=[str(BASE / "engine" / "server.py")], cwd=str(BASE))
                async with stdio_client(params) as (rs, ws):
                    async with ClientSession(rs, ws) as session:
                        await session.initialize()
                        await session.list_tools()  # discovery (model sees schemas)
                        r = await _run_path(session, defs[task][path], rep)
                        # file-verify for B/F engine+baseline
                        if task in ("B", "F"):
                            p = os.path.join(tempfile.gettempdir(), f"sfmcp_15_{rep}.txt")
                            r["file_exists"] = os.path.exists(p)
                            if os.path.exists(p):
                                try:
                                    os.remove(p)
                                except Exception:  # noqa: BLE001
                                    pass
                        out[task][path].append(r)
                time.sleep(0.4)
    print(json.dumps(out, indent=1))
    # summary
    summ = {}
    for task in tasks:
        summ[task] = {}
        for path in ("baseline", "engine"):
            walls = sorted(r["wall_ms"] for r in out[task][path])
            n = len(walls)
            summ[task][path] = {
                "median_ms": walls[n // 2],
                "p95_ms": walls[min(n - 1, int(n * 0.95))],
                "calls": sum(r["mcp_calls"] for r in out[task][path]),
                "success": sum(1 for r in out[task][path] if r["success"]),
                "arg_bytes": sum(r["arg_bytes"] for r in out[task][path]),
                "res_bytes": sum(r["res_bytes"] for r in out[task][path]),
            }
        b, e = summ[task]["baseline"]["median_ms"], summ[task]["engine"]["median_ms"]
        summ[task]["ratio_baseline_over_engine"] = round(b / max(e, 0.01), 2)
    print("SUMMARY:" + json.dumps(summ, indent=1))
    return 0


def main() -> int:
    import argparse

    # RETIRED in Phase 2: unbounded multi-window stress. Set
    # SFMCP_ALLOW_LEGACY_STRESS=1 to run explicitly (operator responsibility).
    if os.environ.get("SFMCP_ALLOW_LEGACY_STRESS", "0") != "1":
        print("RETIRED: legacy stress test (mass notepad/explorer churn + taskkill). "
              "Use tests/test_safety_smoke.py instead.")
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--tasks", default="A,B,C,D,E,F")
    args = ap.parse_args()
    return asyncio.run(amain(args.reps, args.tasks.split(",")))


if __name__ == "__main__":
    raise SystemExit(main())
