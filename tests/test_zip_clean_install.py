"""Final Clean Installation Test directly from extracted ORVEX-v1.0.0.zip.
Ensures zero source-tree shortcuts, zero dev-environment leaks.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    zip_path = os.path.join(repo_root, "dist", "ORVEX-v1.0.0.zip")
    assert os.path.exists(zip_path), f"Release ZIP not found at {zip_path}"

    test_dir = tempfile.mkdtemp(prefix="orvex_zip_install_test_")
    print(f"[1] Extracting release zip to clean directory: {test_dir}")

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(test_dir)

        # 1. Create fresh isolated virtual environment
        print("[2] Creating isolated .venv...")
        subprocess.run([sys.executable, "-m", "venv", os.path.join(test_dir, ".venv")], check=True)
        py_exe = os.path.join(test_dir, ".venv", "Scripts", "python.exe")
        pip_exe = os.path.join(test_dir, ".venv", "Scripts", "pip.exe")
        assert os.path.exists(py_exe), "python.exe missing from clean venv"

        # 2. Install dependencies
        print("[3] Installing requirements.txt into clean venv...")
        subprocess.run([pip_exe, "install", "-r", os.path.join(test_dir, "requirements.txt")], check=True)

        # 3. Verify MCP server startup & tool discovery over stdio
        print("[4] Starting real MCP server from clean extraction...")
        proc = subprocess.Popen(
            [py_exe, "-m", "engine.server"],
            cwd=test_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        def send_rpc(msg):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()

        def read_rpc(timeout=10.0):
            t0 = time.time()
            while time.time() - t0 < timeout:
                line = proc.stdout.readline()
                if line:
                    return json.loads(line)
                time.sleep(0.05)
            raise TimeoutError("Timeout reading from MCP stdout")

        print("    [4a] Handshake initialize...")
        send_rpc({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "zip-test", "version": "1.0"}}
        })
        init_res = read_rpc()
        assert init_res.get("result", {}).get("serverInfo", {}).get("name") == "orvex"

        send_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

        print("    [4b] Tools discovery...")
        send_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_res = read_rpc()
        tools = [t.get("name") for t in tools_res.get("result", {}).get("tools", [])]
        print(f"    Discovered {len(tools)} tools: {tools}")
        assert len(tools) == 11, f"Expected 11 tools, got {len(tools)}"

        print("    [4c] Tool call: automation_status...")
        send_rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "automation_status", "arguments": {}}})
        call_res = read_rpc()
        status_text = call_res.get("result", {}).get("content", [])[0].get("text", "")
        status_obj = json.loads(status_text)
        print(f"    Automation status: state={status_obj.get('state')} indicator={status_obj.get('indicator')}")
        assert status_obj.get("state") in ("IDLE", "RUNNING")

        print("    [4d] Clean MCP shutdown...")
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=5)
        print("    MCP process terminated cleanly.")

        # 4. End-to-End Automation execution from clean package: Notepad
        print("[5] Executing Notepad test via clean package...")
        res_np = subprocess.run([
            py_exe, "-c",
            "from engine.compiler import compile_intent; from engine.executor import Executor; from engine.safety import SafetyPolicy; "
            "ex = Executor(safety=SafetyPolicy()); r = ex.run(compile_intent({'verb': 'open_app', 'app_hint': 'Notepad'})); "
            "print('Notepad launch result:', r.status, r.backend); assert r.status in ('success', 'needs_approval')"
        ], cwd=test_dir, capture_output=True, text=True)
        print(f"    Output: {res_np.stdout.strip()}")
        assert res_np.returncode == 0, f"Notepad test failed: {res_np.stderr}"

        # 5. Controller state test from clean package
        print("[6] Executing Controller state lifecycle test via clean package...")
        res_ctl = subprocess.run([
            py_exe, "-c",
            "from engine.controller import AutomationController; c = AutomationController(); ok = c.start(); "
            "print('Controller started:', ok, 'state:', c.state); "
            "rec = c.shutdown(); print('Controller stopped cleanly:', rec.get('controller_exited')); "
            "assert rec.get('controller_exited') is True"
        ], cwd=test_dir, capture_output=True, text=True)
        print(f"    Output: {res_ctl.stdout.strip()}")
        assert res_ctl.returncode == 0, f"Controller test failed: {res_ctl.stderr}"

        # Cleanup test windows
        subprocess.run([sys.executable, os.path.join(repo_root, "tests", "cleanup_test_windows.py")], check=True)

        print("\n" + "=" * 60)
        print("  CLEAN ZIP INSTALL & EXECUTION: 100% PASS")
        print("=" * 60)
        return 0

    finally:
        print(f"Cleaning up extracted test directory: {test_dir}")
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())
