"""Clean-machine installation & execution test for ORVEX.
Simulates a fresh checkout in a temporary directory with a fresh virtual environment.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

def main():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    temp_dir = tempfile.mkdtemp(prefix="orvex_clean_test_")
    print(f"Creating clean sandbox installation in: {temp_dir}")

    try:
        # Copy minimal release files
        shutil.copytree(os.path.join(root_dir, "engine"), os.path.join(temp_dir, "engine"))
        shutil.copy(os.path.join(root_dir, "requirements.txt"), temp_dir)
        shutil.copy(os.path.join(root_dir, "pyproject.toml"), temp_dir)
        shutil.copy(os.path.join(root_dir, "README.md"), temp_dir)
        shutil.copy(os.path.join(root_dir, "install.bat"), temp_dir)
        shutil.copy(os.path.join(root_dir, "uninstall.bat"), temp_dir)

        print("[1] Creating fresh isolated virtual environment (.venv)...")
        venv_dir = os.path.join(temp_dir, ".venv")
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)

        venv_py = os.path.join(venv_dir, "Scripts", "python.exe")
        venv_pip = os.path.join(venv_dir, "Scripts", "pip.exe")
        assert os.path.exists(venv_py), "venv python not found"

        print("[2] Installing 5 direct dependencies via pip into fresh venv...")
        # Use find-links or cache for fast offline/local reproducibility
        subprocess.run([venv_pip, "install", "-r", os.path.join(temp_dir, "requirements.txt")], check=True)

        print("[3] Verifying direct dependencies in clean environment...")
        res = subprocess.run([
            venv_py, "-c",
            "import mcp; import uiautomation; import websockets; import psutil; import comtypes; print('Imports OK')"
        ], capture_output=True, text=True, check=True)
        print(f"    {res.stdout.strip()}")

        print("[4] Testing MCP server startup and tool discovery in fresh environment...")
        test_script = os.path.join(root_dir, "tests", "test_mcp_host_stdio.py")
        env = os.environ.copy()
        env["PYTHONPATH"] = temp_dir
        res2 = subprocess.run([venv_py, test_script], cwd=temp_dir, capture_output=True, text=True)
        print(f"    MCP Host test output: {res2.stdout.strip()}")
        assert res2.returncode == 0, f"MCP host test failed: {res2.stderr}"

        print("[4b] Verifying client configuration manager in fresh environment...")
        res_cfg = subprocess.run([
            venv_py, os.path.join(temp_dir, "engine", "configure_clients.py"), "--status",
            "--install-dir", temp_dir, "--python-exe", venv_py
        ], cwd=temp_dir, capture_output=True, text=True)
        assert res_cfg.returncode == 0, f"configure_clients status failed: {res_cfg.stderr}"
        assert "AI CLIENTS" in res_cfg.stdout, f"Missing AI CLIENTS summary: {res_cfg.stdout}"
        print("    Client configuration manager: OK")

        print("[5] Testing Notepad automation through fresh environment...")
        res3 = subprocess.run([
            venv_py, "-c",
            "from engine.compiler import compile_intent; from engine.executor import Executor; from engine.safety import SafetyPolicy; "
            "ex = Executor(safety=SafetyPolicy()); r = ex.run(compile_intent({'verb': 'open_app', 'app_hint': 'Notepad'})); "
            "print('Notepad launch status:', r.status); assert r.status in ('success', 'needs_approval')"
        ], cwd=temp_dir, capture_output=True, text=True)
        print(f"    Automation result: {res3.stdout.strip()}")
        assert res3.returncode == 0, f"Automation failed: {res3.stderr}"

        # Clean up any test notepad
        subprocess.run([sys.executable, os.path.join(root_dir, "tests", "cleanup_test_windows.py")], check=True)

        print("\n[PASS] Clean installation and end-to-end execution verified successfully!")
        return 0

    finally:
        print(f"Cleaning up temporary test installation: {temp_dir}")
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())
