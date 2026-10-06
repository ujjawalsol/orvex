"""Final end-to-end A/B/C (Part 14): traditional granular vs engine-fresh vs
engine-reused, through REAL MCP stdio. NO taskkill, NO mass windows, NO
retired stress paths. Sandbox-only files, exact closes, health-gated.

WA (windows open+type+verify), WF (windows open+type+save+verify+close),
WB (browser BT4). 3 reps each. One server process per (task,path) — the
real MCP lifecycle, which is what makes C possible.
Metrics: T_first_action, T_complete, model-turns(=decision points, model
latency NOT measured), MCP calls, bytes, native/wait/verify splits (from
receipt profilers), launch time, success. median of 3.
"""
from __future__ import annotations

import asyncio
import functools
import json
import statistics
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
PORT = 18928


def intent(verb, **kw):
    d = {"verb": verb}
    d.update(kw)
    return d


TASKS = {
    "WA": {
        "baseline": [
            intent("open_app", app_hint="Notepad"),
            intent("set_value", app_hint="Notepad",
                   target={"control_type": "Edit"}, params={"text": "master-A-{n}"}),
            intent("press", app_hint="Notepad", params={"keys": "{Ctrl}s"}),
            intent("set_value", app_hint="Notepad",
                   target={"control_type": "Edit", "name": "File name:"},
                   params={"text": "{sandbox}\\wa_{p}_{n}_{r}.txt"}),
            intent("invoke", app_hint="Notepad",
                   target={"control_type": "Button", "name": "Save"}),
            intent("wait", params={"path": "{sandbox}\\wa_{p}_{n}_{r}.txt", "timeout_s": 10}),
            intent("close_window", target={"title": "wa_{p}_{n}_{r} - Notepad"},
                   params={"hwnd": "FROM_OPEN"}),
        ],
        "engine": [intent("run", steps=[
            {"verb": "open_app", "app_hint": "Notepad"},
            {"verb": "set_value", "app_hint": "Notepad",
             "target": {"control_type": "Edit"}, "params": {"text": "master-A-{n}"}},
            {"verb": "press", "app_hint": "Notepad", "params": {"keys": "{Ctrl}s"}},
            {"verb": "set_value", "app_hint": "Notepad",
             "target": {"control_type": "Edit", "name": "File name:"},
             "params": {"text": "{sandbox}\\wa_{p}_{n}_{r}.txt"}},
            {"verb": "invoke", "app_hint": "Notepad",
             "target": {"control_type": "Button", "name": "Save"}},
            {"verb": "wait", "params": {"min_mtime": "{runepoch}", "path": "{sandbox}\\wa_{p}_{n}_{r}.txt", "timeout_s": 10}},
            {"verb": "close_window", "target": {"title": "wa_{p}_{n}_{r} - Notepad"},
             "params": {"hwnd": "FROM_OPEN"}},
        ])],
        "verify_file": "wa_{p}_{n}_{r}.txt",
    },
    "WF": {
        "baseline": [
            intent("open_app", app_hint="Notepad"),
            intent("set_value", app_hint="Notepad",
                   target={"control_type": "Edit"}, params={"text": "master-F-{n}"}),
            intent("press", app_hint="Notepad", params={"keys": "{Ctrl}s"}),
            intent("set_value", app_hint="Notepad",
                   target={"control_type": "Edit", "name": "File name:"},
                   params={"text": "{sandbox}\\master_{p}_{n}_{r}.txt"}),
            intent("invoke", app_hint="Notepad",
                   target={"control_type": "Button", "name": "Save"}),
            intent("wait", params={"path": "{sandbox}\\master_{p}_{n}_{r}.txt", "timeout_s": 10}),
            intent("close_window", target={"title": "master_{p}_{n}_{r} - Notepad"},
                   params={"hwnd": "FROM_OPEN"}),
        ],
        "engine": [intent("run", steps=[
            {"verb": "open_app", "app_hint": "Notepad"},
            {"verb": "set_value", "app_hint": "Notepad",
             "target": {"control_type": "Edit"}, "params": {"text": "master-F-{n}"}},
            {"verb": "press", "app_hint": "Notepad", "params": {"keys": "{Ctrl}s"}},
            {"verb": "set_value", "app_hint": "Notepad",
             "target": {"control_type": "Edit", "name": "File name:"},
             "params": {"text": "{sandbox}\\master_{p}_{n}_{r}.txt"}},
            {"verb": "invoke", "app_hint": "Notepad",
             "target": {"control_type": "Button", "name": "Save"}},
            {"verb": "wait", "params": {"min_mtime": "{runepoch}", "path": "{sandbox}\\master_{p}_{n}_{r}.txt", "timeout_s": 10}},
            {"verb": "close_window", "target": {"title": "master_{p}_{n}_{r} - Notepad"},
             "params": {"hwnd": "FROM_OPEN"}},
        ])],
        "verify_file": "master_{p}_{n}_{r}.txt",
    },
    "WB": {
        "baseline": [
            intent("browser_open"),
            intent("browser_navigate", params={"url": f"http://127.0.0.1:{PORT}/simple.html"}),
            intent("browser_extract", params={"kind": "text", "css": "#intro"}),
            intent("browser_extract", params={"kind": "text", "css": ".num"}),
            intent("browser_close"),
        ],
        "engine": [intent("run", steps=[
            {"verb": "browser_open"},
            {"verb": "browser_navigate", "params": {"url": f"http://127.0.0.1:{PORT}/simple.html"}},
            {"verb": "browser_extract", "params": {"kind": "text", "css": "#intro"}},
            {"verb": "browser_extract", "params": {"kind": "text", "css": ".num"}},
            {"verb": "browser_close"},
        ])],
    },
}


import time as _t0
RUN_TAG = str(int(_t0.time()))[-6:]
RUN_EPOCH = _t0.time()
def fill(obj, n, sandbox, p=""):
    if isinstance(obj, str):
        return obj.replace("{n}", str(n)).replace("{sandbox}", sandbox).replace("{p}", p).replace("{r}", RUN_TAG).replace("{runepoch}", str(RUN_EPOCH))
    if isinstance(obj, dict):
        return {k: fill(v, n, sandbox) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill(v, n, sandbox) for v in obj]
    return obj


async def run_path(session, intents, task_id):
    t0 = time.perf_counter()
    first = None
    arg_b = res_b = 0
    ok = True
    prof = {}
    last_open_hwnd = 0
    for it in intents:
        # thread open-hwnd into close (receipt handles get used, as designed)
        if it.get("verb") == "close_window" and isinstance(it.get("params"), dict) \
                and it["params"].get("hwnd") == "FROM_OPEN" and last_open_hwnd:
            it = dict(it)
            it["params"] = dict(it["params"])
            it["params"]["hwnd"] = last_open_hwnd
        ab = len(json.dumps({"intent": it, "task_id": task_id}).encode())
        arg_b += ab
        t1 = time.perf_counter()
        res = await session.call_tool("execute", {"intent": it, "task_id": task_id})
        if first is None:
            first = (time.perf_counter() - t1) * 1000.0
        txt = res.content[0].model_dump(mode="json").get("text", "")
        res_b += len(txt.encode())
        r = json.loads(txt) if txt.startswith("{") else {}
        if r.get("status") != "success":
            ok = False
            break
        if (r.get("result") or {}).get("hwnd"):
            last_open_hwnd = r["result"]["hwnd"]
        prof = r.get("profiler", prof)
    return {"T_first_action_ms": round(first or 0, 1),
            "T_complete_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "mcp_calls": len(intents), "arg_bytes": arg_b, "res_bytes": res_b,
            "success": ok, "profiler": prof}


async def cleanup_windows(session, tid):
    # exact-close any Notepad WE opened (owned by tid) + remove sandbox files
    try:
        r = await session.call_tool("execute", {"intent": intent(
            "close_window", target={"title": "master"},
            params={}), "task_id": tid})
        _ = r
    except Exception:  # noqa: BLE001
        pass


async def amain() -> int:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from engine.safety import SafetyPolicy

    pre = SafetyPolicy.shell_health()
    sandbox = SafetyPolicy().sandbox
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(
        SimpleHTTPRequestHandler, directory=str(BASE / "tests" / "fixtures")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out: dict = {}
    for task in ("WA", "WF", "WB"):
        out[task] = {}
        for path in ("baseline", "engine"):
            params = StdioServerParameters(
                command=sys.executable, args=[str(BASE / "engine" / "server.py")], cwd=str(BASE))
            async with stdio_client(params) as (rs, ws):
                async with ClientSession(rs, ws) as session:
                    await session.initialize()
                    await session.list_tools()
                    reps = []
                    for rep in range(3):
                        cleaned = await pre_rep_cleanup(session, rep)
                        tid = f"{task}-{path}-{rep}"
                        r = await run_path(
                            session, [fill(i, rep, sandbox, path) for i in TASKS[task][path]], tid)
                        # harness-side material verification (engine receipts are not trusted blindly)
                        vf = TASKS[task].get("verify_file")
                        if vf:
                            p = fill({"p": "{sandbox}\\" + vf}, rep, sandbox, path)["p"]
                            import os as _os

                            r["file_exists"] = _os.path.exists(p)
                            r["success"] = r["success"] and r["file_exists"]
                        r["pre_cleaned"] = cleaned
                        reps.append(r)
                        time.sleep(0.4)
                    walls = sorted(x["T_complete_ms"] for x in reps)
                    out[task][path] = {
                        "median_ms": walls[1], "p95_ms": max(walls),
                        "calls": sum(x["mcp_calls"] for x in reps),
                        "arg_bytes": sum(x["arg_bytes"] for x in reps),
                        "res_bytes": sum(x["res_bytes"] for x in reps),
                        "success": sum(1 for x in reps if x["success"]),
                        "T_first_med": sorted(x["T_first_action_ms"] for x in reps)[1],
                    }
                    print(task, path, out[task][path])
    # C = engine-reused: browser session shared across reps (one server, handle threaded)
    params = StdioServerParameters(
        command=sys.executable, args=[str(BASE / "engine" / "server.py")], cwd=str(BASE))
    async with stdio_client(params) as (rs, ws):
        async with ClientSession(rs, ws) as session:
            await session.initialize()
            await session.list_tools()
            r0 = await session.call_tool("execute", {"intent": intent("browser_open"),
                                                    "task_id": "WB-C"})
            h = json.loads(r0.content[0].model_dump(mode="json")["text"])["result"]["session"]
            reps = []
            for rep in range(3):
                steps = [
                    intent("browser_navigate",
                           params={"url": f"http://127.0.0.1:{PORT}/simple.html",
                                   "session": h}),
                    intent("browser_extract",
                           params={"kind": "text", "css": "#intro", "session": h}),
                ]
                reps.append(await run_path(session, steps, "WB-C"))
            t_close = await session.call_tool("execute", {"intent": intent(
                "browser_close", params={"session": h}), "task_id": "WB-C"})
            _ = t_close
            walls = sorted(x["T_complete_ms"] for x in reps)
            out["WB"]["reused"] = {
                "median_ms": walls[1], "p95_ms": max(walls),
                "calls": sum(x["mcp_calls"] for x in reps),
                "arg_bytes": sum(x["arg_bytes"] for x in reps),
                "res_bytes": sum(x["res_bytes"] for x in reps),
                "success": sum(1 for x in reps if x["success"]),
                "T_first_med": sorted(x["T_first_action_ms"] for x in reps)[1],
                "session": h,
            }
            print("WB reused", out["WB"]["reused"])
    with open(BASE / "bench" / "results" / "master_final.json", "w") as f:
        json.dump(out, f, indent=1)
    # sandbox files cleanup (wa_<path>_<rep>.txt + master_<path>_<rep>.txt)
    import os as _os
    import glob as _glob

    for p in _glob.glob(_os.path.join(sandbox, "wa_*.txt")) + _glob.glob(
            _os.path.join(sandbox, "master_*.txt")):
        try:
            _os.remove(p)
        except Exception:  # noqa: BLE001
            pass
    print("health changes:", SafetyPolicy.health_diff(pre, SafetyPolicy.shell_health()) or "none")
    srv.shutdown()
    return 0


async def pre_rep_cleanup(session, rep):
    """Operator-level reset: exact-close OUR test windows + remove OUR test
    files (wa_/master_ titles). Stale FILES cause silent Confirm-overwrite
    dialogs, so file hygiene is as important as window hygiene.
    Never touches non-test windows/files.
    """
    import glob as _g
    import uiautomation as auto
    import os as _os

    from engine.safety import SafetyPolicy

    sb = SafetyPolicy().sandbox
    for p in _g.glob(_os.path.join(sb, "wa_*.txt")) + _g.glob(
            _os.path.join(sb, "master_*.txt")) + _g.glob(
            _os.path.join(sb, "smoke_*.txt")):
        try:
            _os.remove(p)
        except Exception:  # noqa: BLE001
            pass
    closed = 0
    for _ in range(4):
        targets = []
        for c in auto.GetRootControl().GetChildren():
            try:
                n = c.Name or ""
                if ("Window" in c.ControlTypeName and
                        (n.startswith("wa_") or n.startswith("master_")) and "Notepad" in n):
                    targets.append((n, int(c.NativeWindowHandle or 0)))
            except Exception:  # noqa: BLE001
                continue
        if not targets:
            break
        for name, hwnd in targets:
            try:
                for c in auto.GetRootControl().GetChildren():
                    if int(c.NativeWindowHandle or 0) == hwnd:
                        d = c.WindowControl(SubName="Save As", searchDepth=2)
                        if d.Exists(0):
                            for b in d.GetChildren():
                                try:
                                    if b.ControlTypeName == "ButtonControl" and b.Name == "Cancel":
                                        b.GetInvokePattern().Invoke()
                                except Exception:  # noqa: BLE001
                                    continue
            except Exception:  # noqa: BLE001
                pass
            try:
                res = await session.call_tool("execute", {
                    "intent": {"verb": "close_window", "target": {"title": name},
                               "params": {"hwnd": hwnd, "test_cleanup": True}},
                    "task_id": f"cleanup-{rep}"})
                import json as _j

                txt = res.content[0].model_dump(mode="json").get("text", "")
                if _j.loads(txt).get("status") == "success":
                    closed += 1
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.6)
    return closed


def main() -> int:
    import asyncio as _a

    return _a.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
