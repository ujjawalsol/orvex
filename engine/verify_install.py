"""ORVEX Installation Verification Self-Test.
Verifies dependencies, MCP stdio protocol handshake, 11 tools, and controller lifecycle.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure root repository directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXPECTED_11_TOOLS = {
    "execute", "inspect", "wait", "verify", "workflow", "system",
    "cancel", "automation_status", "decide_approval", "approval_status", "profiler"
}

def verify_all() -> int:
    print("[1] Verifying core dependencies...")
    try:
        import mcp
        import uiautomation
        import websockets
        import psutil
        import comtypes
        from engine.compiler import compile_intent
        from engine.safety import SafetyPolicy
        from engine.controller import AutomationController
        print("    Core imports: OK")
    except Exception as e:
        print(f"    [FAIL] Dependency import error: {e}")
        return 1

    print("[2] Verifying MCP server stdio lifecycle & tools...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen(
        [sys.executable, "-m", "engine.server"],
        cwd=root_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def send_rpc(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def read_rpc(timeout: float = 8.0) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = proc.stdout.readline()
            if line:
                return json.loads(line)
            time.sleep(0.05)
        raise TimeoutError("Timeout reading JSON-RPC line from MCP server stdout")

    try:
        # Initialize
        send_rpc({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "orvex-installer-check", "version": "1.0.0"}
            }
        })
        init_resp = read_rpc()
        server_info = init_resp.get("result", {}).get("serverInfo", {})
        assert server_info.get("name") == "orvex", f"Unexpected server name: {server_info}"
        print(f"    MCP initialize: OK ({server_info.get('name')} v{server_info.get('version')})")

        # Initialized
        send_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # List tools
        send_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_resp = read_rpc()
        tools = tools_resp.get("result", {}).get("tools", [])
        tool_names = set(t.get("name") for t in tools)
        assert tool_names == EXPECTED_11_TOOLS, f"Mismatch in tools: {tool_names}"
        print(f"    Tool discovery: OK (all {len(tool_names)} tools registered)")

        # Call automation_status
        send_rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "automation_status", "arguments": {}}})
        call_resp = read_rpc()
        content = call_resp.get("result", {}).get("content", [])
        assert len(content) > 0, "Empty content in tool response"
        status_data = json.loads(content[0].get("text", "{}"))
        assert status_data.get("state") in ("IDLE", "RUNNING"), f"Bad state: {status_data}"
        print(f"    Tool invocation: OK (state={status_data.get('state')})")

        # Shutdown
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=4)
        print("    MCP shutdown: OK")

    except Exception as e:
        print(f"    [FAIL] MCP server check error: {e}")
        proc.kill()
        return 1

    print("[3] Verifying System-Wide Controller lifecycle...")
    try:
        from engine.controller import AutomationController
        c = AutomationController()
        ok = c.start()
        assert ok, "Controller failed to start companion process"
        rec = c.shutdown()
        assert rec.get("controller_exited") is True, f"Controller shutdown incomplete: {rec}"
        print("    Controller lifecycle: OK")
    except Exception as e:
        print(f"    [FAIL] Controller check error: {e}")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(verify_all())
