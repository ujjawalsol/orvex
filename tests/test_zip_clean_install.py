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

    # Snapshot real user configs to guarantee zero leakage / mutation
    real_configs = [
        os.path.expanduser("~/.config/opencode/opencode.jsonc"),
        os.path.expanduser("~/opencode.json"),
        os.path.expandvars("%APPDATA%\\Code\\User\\mcp.json"),
        os.path.expandvars("%APPDATA%\\Claude\\claude_desktop_config.json"),
        os.path.expanduser("~/.gemini/config/mcp_config.json"),
        os.path.expanduser("~/.claude.json"),
        os.path.expanduser("~/.cursor/mcp.json"),
        os.path.expanduser("~/.codeium/windsurf/mcp_config.json"),
    ]
    real_snapshots = {}
    for p in real_configs:
        if os.path.exists(p):
            with open(p, "rb") as f:
                real_snapshots[p] = f.read()
        else:
            real_snapshots[p] = None

    test_dir = tempfile.mkdtemp(prefix="orvex_zip_install_test_")
    print(f"[1] Extracting release zip to clean directory: {test_dir}")

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(test_dir)

        pkg_dir = os.path.join(test_dir, "ORVEX-v1.0.0") if os.path.exists(os.path.join(test_dir, "ORVEX-v1.0.0")) else test_dir

        # Create isolated sandbox environment
        sandbox_dir = os.path.join(test_dir, "sandbox")
        sandbox_home = os.path.join(sandbox_dir, "home")
        sandbox_appdata = os.path.join(sandbox_home, "AppData", "Roaming")
        sandbox_localappdata = os.path.join(sandbox_home, "AppData", "Local")
        os.makedirs(sandbox_appdata, exist_ok=True)
        os.makedirs(sandbox_localappdata, exist_ok=True)

        # 1. Execute install.bat directly in the clean extraction directory within sandbox
        print("[2] Running install.bat from clean package with sandboxed user environment...")
        env = os.environ.copy()
        env["ORVEX_UNATTENDED"] = "1"
        env["USERPROFILE"] = sandbox_home
        env["HOME"] = sandbox_home
        env["APPDATA"] = sandbox_appdata
        env["LOCALAPPDATA"] = sandbox_localappdata
        env["ORVEX_ALLOW_TEMP"] = "1"

        bat_res = subprocess.run(
            ["cmd.exe", "/c", "install.bat"],
            cwd=pkg_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        print("    install.bat output summary:")
        for line in bat_res.stdout.splitlines():
            if any(k in line for k in ("[OK]", "[INFO]", "complete", "written", "error", "ERROR", "Location:", "Runtime:", "Deploying")):
                print(f"      {line.strip()}")
        assert bat_res.returncode == 0, f"install.bat failed (rc={bat_res.returncode}):\n{bat_res.stderr}\n{bat_res.stdout}"

        # Target installation directory in the sandboxed LOCALAPPDATA
        target_install_dir = os.path.join(sandbox_localappdata, "ORVEX")
        assert os.path.isdir(target_install_dir), f"Permanent install directory not created at {target_install_dir}"

        py_exe = os.path.join(target_install_dir, "runtime", "Scripts", "python.exe")
        assert os.path.exists(py_exe), f"python.exe missing from clean runtime at {py_exe}"

        cfg_file = os.path.join(target_install_dir, "orvex_mcp_config.json")
        assert os.path.exists(cfg_file), f"orvex_mcp_config.json missing from {target_install_dir}"

        # 2. Verify MCP server startup & tool discovery over stdio using installed runtime
        print("[3] Starting real MCP server from permanent installation...")
        proc = subprocess.Popen(
            [py_exe, "-m", "engine.server"],
            cwd=target_install_dir,
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

        # 4. End-to-End Automation execution from installed package: Notepad
        print("[5] Executing Notepad test via installed package...")
        res_np = subprocess.run([
            py_exe, "-c",
            "from engine.compiler import compile_intent; from engine.executor import Executor; from engine.safety import SafetyPolicy; "
            "ex = Executor(safety=SafetyPolicy()); r = ex.run(compile_intent({'verb': 'open_app', 'app_hint': 'Notepad'})); "
            "print('Notepad launch result:', r.status, r.backend); assert r.status in ('success', 'needs_approval')"
        ], cwd=target_install_dir, capture_output=True, text=True)
        print(f"    Output: {res_np.stdout.strip()}")
        assert res_np.returncode == 0, f"Notepad test failed: {res_np.stderr}"

        # 5. Controller state test from installed package
        print("[6] Executing Controller state lifecycle test via installed package...")
        res_ctl = subprocess.run([
            py_exe, "-c",
            "from engine.controller import AutomationController; c = AutomationController(); ok = c.start(); "
            "print('Controller started:', ok, 'state:', c.state); "
            "rec = c.shutdown(); print('Controller stopped cleanly:', rec.get('controller_exited')); "
            "assert rec.get('controller_exited') is True"
        ], cwd=target_install_dir, capture_output=True, text=True)
        print(f"    Output: {res_ctl.stdout.strip()}")
        assert res_ctl.returncode == 0, f"Controller test failed: {res_ctl.stderr}"

        # Cleanup test windows
        subprocess.run([sys.executable, os.path.join(repo_root, "tests", "cleanup_test_windows.py")], check=True)

        # 6. Verify real user config isolation
        print("[7] Verifying real user configurations were strictly untouched...")
        for p, snap in real_snapshots.items():
            if snap is None:
                assert not os.path.exists(p), f"Sandboxed test created unexpected file in real profile: {p}"
            else:
                with open(p, "rb") as f:
                    current = f.read()
                assert current == snap, f"Sandboxed test modified real user profile config: {p}"
        print("    Zero leaks into real user environment confirmed.")

        print("\n" + "=" * 60)
        print("  CLEAN ZIP INSTALL & EXECUTION: 100% PASS")
        print("=" * 60)
        return 0

    finally:
        print(f"Cleaning up extracted test directory: {test_dir}")
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())
