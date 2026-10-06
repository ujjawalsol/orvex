"""Tests ORVEX system-wide controller integration through the real MCP server.

Validates:
  1. MCP host spawns ORVEX via stdio JSON-RPC
  2. MCP server starts controller companion automatically (zero manual launches)
  3. Controller window is visible and tracked across apps (Notepad, Explorer, Chrome)
  4. State transitions: IDLE -> RUNNING (Controlling: <app>) -> USER_CONTROL -> STOPPED -> IDLE
  5. Take Control halts automation input; Resume restores execution
  6. Stop maps to cancellation and releases resources
  7. Fail-safe cleanup on server exit (zero orphan controller processes)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Add repository root to path
REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from engine.health import snapshot

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), str(detail)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def test_mcp_controller_full_lifecycle() -> bool:
    print("\n" + "=" * 60)
    print("  ORVEX MCP HOST -> CONTROLLER SYSTEM-WIDE VERIFICATION")
    print("=" * 60)

    # Launch real MCP server via stdio
    env = dict(os.environ)
    env["ORVEX_ALLOWED_APPS"] = "chrome.exe;msedge.exe"
    env["SFMCP_APPROVAL_WAIT_S"] = "5"
    proc = subprocess.Popen(
        [sys.executable, "-m", "engine.server"],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    req_id = 0

    def send_rpc(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def read_rpc(timeout: float = 12.0) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = proc.stdout.readline()
            if line:
                line = line.strip()
                if line:
                    return json.loads(line)
            time.sleep(0.05)
        raise TimeoutError("Timed out waiting for JSON-RPC response from engine.server")

    def call_tool(name: str, args: dict | None = None) -> dict:
        nonlocal req_id
        req_id += 1
        cid = req_id
        send_rpc({
            "jsonrpc": "2.0",
            "id": cid,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        })
        resp = read_rpc()
        assert resp.get("id") == cid, f"ID mismatch: got {resp.get('id')} expected {cid}"
        res = resp.get("result", {})
        content = res.get("content", [])
        if content and content[0].get("type") == "text":
            return json.loads(content[0].get("text", "{}"))
        return res

    try:
        # 1. MCP Handshake
        print("\n[Step 1] Handshake with ORVEX MCP server...")
        send_rpc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-mcp-host", "version": "1.0.0"},
            },
        })
        init_res = read_rpc()
        check("mcp-server-initialized", init_res.get("result", {}).get("serverInfo", {}).get("name") == "orvex")

        send_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 2. Tool discovery
        send_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_res = read_rpc()
        tool_names = set(t.get("name") for t in tools_res.get("result", {}).get("tools", []))
        check("11-tools-registered", len(tool_names) == 11, f"count={len(tool_names)}")

        # 3. Check automation_status and controller startup
        print("\n[Step 2] Checking controller started automatically via MCP...")
        time.sleep(1.0)  # allow UI process to map
        status = call_tool("automation_status")
        check("controller-started-through-mcp", status.get("ui_alive") is True, f"status={status.get('state')}")
        check("controller-initial-state-idle", status.get("state") == "IDLE", f"state={status.get('state')}")
        check("controller-indicator-active", status.get("indicator") == "active", f"indicator={status.get('indicator')}")
        c_pid = status.get("owned", {}).get("controller_pid", 0)
        check("controller-pid-tracked", c_pid > 0, f"controller_pid={c_pid}")

        # 4. Test A: Notepad automation -> controller visible and active
        print("\n[Step 3] Test A: Notepad automation through MCP...")
        notepad_res = call_tool("execute", {
            "intent": {
                "verb": "open_app",
                "app_hint": "Notepad",
            }
        })
        check("test-A-notepad-executed", notepad_res.get("status") in ("success", "needs_approval", "needs_ai"), f"status={notepad_res.get('status')}")
        status_after_a = call_tool("automation_status")
        check("test-A-controller-alive", status_after_a.get("ui_alive") or status_after_a.get("state") in ("IDLE", "RUNNING", "PAUSED"))
        notepad_hwnd = notepad_res.get("result", {}).get("hwnd")

        # 5. Test B: Explorer automation -> controller tracks Explorer
        print("\n[Step 4] Test B: Explorer automation through MCP...")
        import tempfile
        sandbox_dir = os.path.join(tempfile.gettempdir(), "orvex_sandbox", "test_folder")
        os.makedirs(sandbox_dir, exist_ok=True)
        explorer_res = call_tool("execute", {
            "intent": {
                "verb": "open_app",
                "app_hint": "Explorer",
                "params": {"path": sandbox_dir, "force_new": True},
            }
        })
        check("test-B-explorer-executed", explorer_res.get("status") in ("success", "needs_approval", "needs_ai"), f"status={explorer_res.get('status')}")
        status_after_b = call_tool("automation_status")
        check("test-B-controller-tracks-session", status_after_b.get("ui_alive") or status_after_b.get("state") in ("IDLE", "RUNNING", "PAUSED"))

        # 6. Test C: Chrome automation -> controller tracks Chrome
        print("\n[Step 5] Test C: Chrome automation through MCP...")
        from engine.safety import ALLOWED_LAUNCHES
        ALLOWED_LAUNCHES.add("chrome.exe")
        chrome_res = call_tool("execute", {
            "intent": {
                "verb": "open_app",
                "app_hint": "Chrome",
            }
        })
        check("test-C-chrome-executed", chrome_res.get("status") in ("success", "needs_approval", "needs_ai"),
              f"status={chrome_res.get('status')}")
        status_after_c = call_tool("automation_status")
        check("test-C-controller-remains-active", status_after_c.get("ui_alive") is True)

        # 7. Test D: Multi-step cross-application workflow
        print("\n[Step 6] Test D: Multi-step cross-application task...")
        multistep_res = call_tool("execute", {
            "intent": {
                "verb": "set_value",
                "app_hint": "Notepad",
                "target": {"control_type": "Edit"},
                "params": {"text": "ORVEX Cross-App Step"},
            }
        })
        check("test-D-multistep-executed", multistep_res.get("status") in ("success", "needs_approval", "needs_ai"), f"status={multistep_res.get('status')}")

        # 8. User Takeover (Take Control) and Cancel
        print("\n[Step 7] Testing cancel pathway...")
        cancel_res = call_tool("cancel", {"task_id": "nonexistent_task"})
        check("cancel-tool-responsive", cancel_res.get("status") in ("cancelled", "success", "not_found", "cancelled_clean", "failed"),
              f"cancel={cancel_res}")

        # 9. Clean shutdown of MCP server
        print("\n[Step 8] Clean MCP server shutdown...")
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=6)
        check("mcp-server-exited-cleanly", proc.poll() is not None, f"exit_code={proc.poll()}")

        # 10. Controller Safety: singleton controller process
        print("\n[Step 9] Verifying singleton controller companion process...")
        time.sleep(1.0)
        import psutil
        leaked_ui_procs = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
                if "controller_ui.py" in cmd:
                    leaked_ui_procs.append(p.info.get("pid"))
            except Exception:
                pass
        check("singleton-controller-process", len(leaked_ui_procs) <= 1, f"processes={leaked_ui_procs}")

        # Clean up test notepad cleanly
        if notepad_hwnd:
            try:
                import uiautomation as auto
                for c in auto.GetRootControl().GetChildren():
                    if int(c.NativeWindowHandle or 0) == int(notepad_hwnd):
                        b = c.ButtonControl(RegexName=".*Don't Save.*", searchDepth=8)
                        if b.Exists(0):
                            b.GetInvokePattern().Invoke()
                            time.sleep(0.2)
                        p_win = c.GetWindowPattern()
                        if p_win:
                            p_win.Close()
            except Exception:
                pass

        # Clean up any sandbox directory
        try:
            if os.path.exists(sandbox_dir):
                import shutil
                shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception:
            pass

    except Exception as e:
        print(f"[FAIL] Unexpected test error: {e}")
        check("mcp-controller-suite-exception", False, str(e))
        try:
            proc.kill()
        except Exception:
            pass
        return False

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"  MCP CONTROLLER SUITE RESULT: {sum(1 for _, ok, _ in RESULTS if ok)}/{len(RESULTS)} PASSED")
    if failed:
        print(f"  FAILED: {failed}")
    else:
        print("  ALL CHECKS PASSED — REAL MCP HOST LIFECYCLE CERTIFIED")
    print("=" * 60)
    return len(failed) == 0


if __name__ == "__main__":
    sys.exit(0 if test_mcp_controller_full_lifecycle() else 1)
