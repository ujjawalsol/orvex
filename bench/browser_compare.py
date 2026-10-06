"""Browser MCP path comparison: granular calls vs ONE steps[] call.

Same BT4-equivalent task (open/navigate/extract x2) on local fixtures.
Granular: 4 MCP calls in ONE client session (session continuity).
Engine: 1 MCP call. 3 reps each. Real stdio transport. Health-gated.
No downloads, no profile writes (isolated), fixture pages only.
"""
from __future__ import annotations

import asyncio
import functools
import json
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
PORT = 18924

GRANULAR = [
    ("browser_open", {}),
    ("browser_navigate", {"url": f"http://127.0.0.1:{PORT}/simple.html"}),
    ("browser_extract", {"kind": "text", "css": "#intro"}),
    ("browser_extract", {"kind": "text", "css": ".num"}),
]
ENGINE_STEPS = {"verb": "run", "steps": [
    {"verb": "browser_open"},
    {"verb": "browser_navigate", "params": {"url": f"http://127.0.0.1:{PORT}/simple.html"}},
    {"verb": "browser_extract", "params": {"kind": "text", "css": "#intro"}},
    {"verb": "browser_extract", "params": {"kind": "text", "css": ".num"}},
]}


def _verb_for(tool: str, args: dict) -> str:
    return {"browser_open": "browser_open", "browser_navigate": "browser_navigate",
            "browser_extract": "browser_extract"}.get(tool, "run")


async def _path(session, calls, use_steps: bool) -> dict:
    t0 = time.perf_counter()
    arg_b = res_b = 0
    ok = True
    result: dict = {}
    if use_steps:
        ab = len(json.dumps(ENGINE_STEPS).encode())
        arg_b += ab
        res = await session.call_tool("execute", {"intent": ENGINE_STEPS})
        txt = res.content[0].model_dump(mode="json").get("text", "")
        r = json.loads(txt) if txt.startswith("{") else {}
        res_b += len(txt.encode())
        ok = r.get("status") == "success"
        result = r.get("result", {})
    else:
        tid = f"granular-{time.time_ns()}"
        for tool, args in calls:
            a = {"verb": _verb_for(tool, args), "params": args,
                 "target": None, "app_hint": "", "steps": [], "idempotency_key": ""}
            # execute tool takes {intent}; map browser tools onto execute intents
            payload = {"intent": a, "task_id": tid}
            ab = len(json.dumps(payload).encode())
            arg_b += ab
            res = await session.call_tool("execute", payload)
            txt = res.content[0].model_dump(mode="json").get("text", "")
            r = json.loads(txt) if txt.startswith("{") else {}
            res_b += len(txt.encode())
            if r.get("status") != "success":
                ok = False
                break
            if tool == "browser_extract":
                result.update(r.get("result", {}))
    return {"wall_ms": (time.perf_counter() - t0) * 1000.0,
            "mcp_calls": 1 if use_steps else len(calls),
            "arg_bytes": arg_b, "res_bytes": res_b, "success": ok, "result": result}


async def amain() -> int:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from engine.safety import SafetyPolicy

    pre = SafetyPolicy.shell_health()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(
        SimpleHTTPRequestHandler, directory=str(BASE / "tests" / "fixtures")))
    import threading

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out: dict = {"granular": [], "engine": []}
    for rep in range(3):
        for name, use_steps in (("granular", False), ("engine", True)):
            params = StdioServerParameters(
                command=sys.executable, args=[str(BASE / "engine" / "server.py")], cwd=str(BASE))
            async with stdio_client(params) as (rs, ws):
                async with ClientSession(rs, ws) as session:
                    await session.initialize()
                    await session.list_tools()
                    # browser_open per path needs backend pin for comparability
                    out[name].append(await _path(session, GRANULAR, use_steps))
            time.sleep(0.3)
    for name in ("granular", "engine"):
        walls = sorted(r["wall_ms"] for r in out[name])
        print(name, {"median_ms": round(walls[1], 1),
                     "calls": sum(r["mcp_calls"] for r in out[name]),
                     "arg_bytes": sum(r["arg_bytes"] for r in out[name]),
                     "res_bytes": sum(r["res_bytes"] for r in out[name]),
                     "success": sum(1 for r in out[name] if r["success"]),
                     "result": out[name][-1]["result"]})
    print("health changes:", SafetyPolicy.health_diff(pre, SafetyPolicy.shell_health()) or "none")
    srv.shutdown()
    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
