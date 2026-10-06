"""Master subset runner: run ONE block at a time for contamination bisection.

Usage: python bench/master_one.py WA baseline   (task WA|WF|WB, path baseline|engine)
"""
import asyncio
import sys

import os
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

TASK = sys.argv[1] if len(sys.argv) > 1 else "WA"
PATH_ = sys.argv[2] if len(sys.argv) > 2 else "baseline"


async def amain():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bench.master_final import TASKS, fill, run_path, pre_rep_cleanup
    from engine.safety import SafetyPolicy
    import uiautomation as auto
    import os as _os

    srv = None
    if TASK == "WB":
        import functools
        import threading
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        fixtures_dir = str(Path(REPO_ROOT) / "tests" / "fixtures")
        srv = ThreadingHTTPServer(("127.0.0.1", 18928), functools.partial(
            SimpleHTTPRequestHandler, directory=fixtures_dir))
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    sb = SafetyPolicy().sandbox
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(REPO_ROOT) / "engine" / "server.py")],
        cwd=REPO_ROOT)
    async with stdio_client(params) as (rs, ws):
        async with ClientSession(rs, ws) as session:
            await session.initialize()
            await session.list_tools()
            for rep in range(3):
                cleaned = await pre_rep_cleanup(session, rep)
                tid = f"{TASK}-{PATH_}-{rep}"
                r = await run_path(
                    session, [fill(i, rep, sb, PATH_) for i in TASKS[TASK][PATH_]], tid)
                vf = TASKS[TASK].get("verify_file")
                if vf:
                    p = fill({"p": "{sandbox}/" + vf}, rep, sb, PATH_)["p"]
                    r["file_exists"] = _os.path.exists(p)
                    r["success"] = r["success"] and r["file_exists"]
                left = [c.Name for c in auto.GetRootControl().GetChildren()
                        if "Notepad" in (c.Name or "")]
                print(f"rep{rep}: ok={r['success']} ms={r['T_complete_ms']} "
                      f"calls={r['mcp_calls']} cleaned={cleaned} leftovers={left}",
                      flush=True)
                if vf:
                    try:
                        if _os.path.exists(p):
                            _os.remove(p)
                    except Exception:  # noqa: BLE001
                        pass
                import time as _t

                _t.sleep(0.4)
    if srv is not None:
        srv.shutdown()


asyncio.run(amain())
