"""Real MCP stdio client loop for Phase 1.5 §1.

Connects to engine/server.py over stdio (real transport, real tool
discovery via list_tools, real tool calls). Records per-call wall time
and payload bytes. The MODEL is the AI driving this script (Muse Spark
in this session); the script is the MCP client, not the decision-maker.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path


async def _connect():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    base = Path(__file__).resolve().parent.parent
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(base / "engine" / "server.py")],
        cwd=str(base),
    )
    cm = stdio_client(params)
    streams = await cm.__aenter__()
    session = ClientSession(streams[0], streams[1])
    await session.__aenter__()
    return cm, session


async def amain() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--intent", default="", help="JSON intent for a single execute call")
    ap.add_argument("--intent-file", default="", help="Path to JSON intent file")
    ap.add_argument("--tool", default="execute")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    cm, session = await _connect()
    try:
        await session.initialize()
        t0 = time.perf_counter()
        tools = await session.list_tools()
        dt_list = (time.perf_counter() - t0) * 1000.0
        names = [t.name for t in tools.tools]
        print(json.dumps({"discovery_ms": round(dt_list, 2), "tools": names}))
        if args.list_only:
            # dump full schemas for §13 measurement
            print(json.dumps({"schemas": [
                {"name": t.name, "description": t.description,
                 "inputSchema": t.input_schema} for t in tools.tools
            ]}))
            return 0
        if args.intent_file:
            intent = json.loads(Path(args.intent_file).read_text(encoding="utf-8"))
        else:
            intent = json.loads(args.intent) if args.intent else {"verb": "inspect", "app_hint": "Notepad"}
        arg_bytes = len(json.dumps(intent).encode())
        t1 = time.perf_counter()
        res = await session.call_tool(args.tool, {"intent": intent} if args.tool == "execute" else dict(intent))
        dt_call = (time.perf_counter() - t1) * 1000.0
        # result payload size
        try:
            payload = json.dumps(res.content[0].model_dump() if hasattr(res.content[0], "model_dump") else str(res.content[0]))
        except Exception:  # noqa: BLE001
            payload = str(res.content)
        print(json.dumps({
            "tool": args.tool, "call_ms": round(dt_call, 2),
            "arg_bytes": arg_bytes, "result_bytes": len(payload.encode()),
            "result": json.loads(payload) if payload.startswith("{") else payload,
        }))
        return 0
    finally:
        await session.__aexit__(None, None, None)
        await cm.__aexit__(None, None, None)


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
