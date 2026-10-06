"""MCP transport micro (Part 8): per-call overhead isolated.

50x execute(verify noop, no UI work) through REAL stdio MCP.
Measures call_ms distribution = transport + dispatch + trivial exec.
Receipt sizes recorded. Health-neutral (no windows).
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


async def amain() -> int:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=[str(BASE / "engine" / "server.py")], cwd=str(BASE))
    async with stdio_client(params) as (rs, ws):
        async with ClientSession(rs, ws) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", len(tools.tools))
            dts, sizes = [], []
            ok = 0
            for _ in range(50):
                t0 = time.perf_counter()
                res = await session.call_tool("execute", {"intent": {"verb": "verify"}})
                dts.append((time.perf_counter() - t0) * 1000.0)
                txt = res.content[0].model_dump(mode="json").get("text", "")
                sizes.append(len(txt.encode()))
                if json.loads(txt).get("status") == "success":
                    ok += 1
            dts.sort()
            n = len(dts)
            print(f"calls={n} ok={ok} median={dts[n//2]:.2f}ms "
                  f"p95={dts[min(n-1,int(n*0.95))]:.2f}ms min={dts[0]:.2f}ms "
                  f"receipt_bytes_med={sorted(sizes)[n//2]}")
    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
