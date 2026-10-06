"""Tests ORVEX MCP server as an external MCP host over stdio JSON-RPC.
Validates both legacy (2024-11-05) and modern (v2 negotiated) protocol handshakes.
"""
import json
import subprocess
import sys
import time

EXPECTED_11_TOOLS = {
    "execute", "inspect", "wait", "verify", "workflow", "system",
    "cancel", "automation_status", "decide_approval", "approval_status", "profiler"
}

def run_session(protocol_version: str, label: str):
    print(f"\n--- Testing Handshake: {label} (protocolVersion='{protocol_version}') ---")
    proc = subprocess.Popen(
        [sys.executable, "-m", "engine.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def send_rpc(msg):
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()

    def read_rpc(timeout=10.0):
        start = time.time()
        while time.time() - start < timeout:
            line = proc.stdout.readline()
            if line:
                return json.loads(line)
            time.sleep(0.05)
        raise TimeoutError("Timed out reading JSON-RPC line from ORVEX server stdout")

    try:
        # 1. Initialize Handshake
        send_rpc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": f"test-host-{label}", "version": "1.0.0"}
            }
        })

        init_resp = read_rpc()
        result = init_resp.get("result", {})
        server_info = result.get("serverInfo", {})
        negotiated_proto = result.get("protocolVersion")
        print(f"    Negotiated Protocol: {negotiated_proto}")
        print(f"    Server Info: name={server_info.get('name')} version={server_info.get('version')}")

        assert init_resp.get("id") == 1
        assert server_info.get("name") == "orvex"
        assert server_info.get("version") == "1.0.0"

        # 2. Initialized notification
        send_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 3. Discover Tools & Verify Schemas
        send_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_resp = read_rpc()
        tools = tools_resp.get("result", {}).get("tools", [])
        tool_names = set(t.get("name") for t in tools)
        assert tool_names == EXPECTED_11_TOOLS, f"Mismatch in expected 11 tools: {tool_names}"

        # 4. Tool Invocation (automation_status)
        send_rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "automation_status", "arguments": {}}})
        call_resp = read_rpc()
        assert call_resp.get("id") == 3
        status_text = call_resp.get("result", {}).get("content", [])[0].get("text", "")
        status_obj = json.loads(status_text)
        assert status_obj.get("state") in ("IDLE", "RUNNING")
        print(f"    Tool call returned: state={status_obj.get('state')} indicator={status_obj.get('indicator')}")

        # 5. Clean Shutdown
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=5)
        print(f"    {label} handshake: PASS")
        return True

    except Exception as e:
        print(f"    {label} handshake FAILED: {e}")
        proc.kill()
        stderr_out = proc.stderr.read()
        if stderr_out:
            print(f"    Server STDERR: {stderr_out[:500]}")
        return False

def main():
    import mcp.types as t
    latest = getattr(t, "LATEST_PROTOCOL_VERSION", "2026-07-28")

    ok_legacy = run_session("2024-11-05", "Legacy Protocol (2024-11-05)")
    ok_modern = run_session(latest, f"Modern Protocol ({latest})")

    if ok_legacy and ok_modern:
        print("\n[PASS] Dual-protocol compatibility certified (Legacy + Modern)!")
        return 0
    else:
        print("\n[FAIL] Protocol compatibility check failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())
