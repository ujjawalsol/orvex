"""ORVEX MCP server: semantic intent → engine → receipts, plus system-wide control.

Tools:
  execute / inspect / wait / verify / workflow / system / cancel
  automation_status / decide_approval / approval_status / profiler

Contract: AI sends semantic intent (verb, app_hint, target, params,
constraints, steps[], idempotency_key). Engine owns HWND/COM/CacheRequest/
backend/timeouts/retries/DAG. Returns compact receipts.

A single process-wide AutomationController owns the authoritative automation
state and surfaces it in a user-visible system-wide indicator, so the user can
always pause, take control, resume, approve/deny or stop automation. The
controller holds no authority the executor lacks.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from engine.compiler import compile_intent  # noqa: E402
from engine.config import apply_to_budget, load as load_config  # noqa: E402
from engine.controller import AutomationController  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.safety import SafetyPolicy  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402

# validated at startup: invalid config fails fast (never silent unsafe fallback)
_SAFETY_CFG, _EXEC_CFG, _BROWSER_CFG, _LOG_CFG = load_config()
server = MCPServer(name="orvex", version="1.0.0")

# ONE system-wide automation controller per engine process: the authoritative
# source of automation state for every controlled application.
_CONTROLLER = AutomationController(intervention=os.environ.get(
    "ORVEX_USER_INTERVENTION",
    os.environ.get("SFMCP_USER_INTERVENTION", "PAUSE")))
_CONTROLLER.start()

_executor = Executor(policy=Policy.load(), stop=EmergencyStop(),
                     safety=SafetyPolicy(budget=apply_to_budget(_SAFETY_CFG)),
                     browser_cfg=_BROWSER_CFG, controller=_CONTROLLER)
_executor.stop.start_hook()


def _shutdown() -> None:
    try:
        _CONTROLLER.shutdown()
    except Exception:  # noqa: BLE001
        pass


atexit.register(_shutdown)


@server.tool(description="Execute semantic intent: verb+app_hint+target+params (+optional steps). One call runs the compiled internal DAG with verification and returns a compact receipt. If status is needs_approval, show the approval panel text to the user and re-call with approval_token after they decide.")
def execute(intent: dict[str, Any], task_id: str = "",
            approval_token: str = "") -> dict[str, Any]:
    graph = compile_intent(intent)
    receipt = _executor.run(graph, intent_verb=intent.get("verb", "invoke"),
                            task_id=task_id or None,
                            approval_token=approval_token or "")
    return receipt.to_dict()


@server.tool(description="Targeted inspection: resolve one semantic target, return handle. Never dumps full desktop tree.")
def inspect(app_hint: str, target: dict[str, Any] | None = None) -> dict[str, Any]:
    receipt = _executor.run(
        compile_intent({"verb": "inspect", "app_hint": app_hint, "target": target or {}}),
        intent_verb="inspect",
    )
    return receipt.to_dict()


@server.tool(description="Condition wait: wait for window/element with deadline. Returns immediately when true.")
def wait(app_hint: str = "", target: dict[str, Any] | None = None, timeout_s: int = 10) -> dict[str, Any]:
    receipt = _executor.run(
        compile_intent({"verb": "wait", "app_hint": app_hint, "target": target or {}, "params": {"timeout_s": timeout_s}}),
        intent_verb="wait",
    )
    return receipt.to_dict()


@server.tool(description="Deterministic verification of a semantic target or window state.")
def verify(app_hint: str = "", target: dict[str, Any] | None = None) -> dict[str, Any]:
    receipt = _executor.run(
        compile_intent({"verb": "verify", "app_hint": app_hint, "target": target or {}}),
        intent_verb="verify",
    )
    return receipt.to_dict()


@server.tool(description="Saved workflow by name. Versioned, parameterized.")
def workflow(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    if name == "notepad_hello":
        return execute({"verb": "open_app", "app_hint": "Notepad"})
    if name == "notepad_type_verify":
        text = params.get("text", "Hello")
        return execute({
            "verb": "set_value", "app_hint": "Notepad",
            "target": {"control_type": "Edit"}, "params": {"text": text},
        })
    return {"status": "failed", "reason": f"unknown_workflow {name}"}


@server.tool(description="Gated system operations (disabled unless ORVEX_ENABLE_SYSTEM=1). SENSITIVE/DANGEROUS need approval.")
def system(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = (os.environ.get("ORVEX_ENABLE_SYSTEM", "0") == "1" or
               os.environ.get("SFMCP_ENABLE_SYSTEM", "0") == "1")
    if not enabled:
        return {"status": "needs_approval", "reason": "system_disabled_by_default"}
    return {"status": "failed", "reason": "system_ops_not_implemented"}


@server.tool(description="Cancel a running task by task_id (no UI interaction needed). Stops new actions/retries/launches/input and cleans up task-owned resources.")
def cancel(task_id: str) -> dict[str, Any]:
    return _executor.cancel(task_id)


@server.tool(description="System-wide automation status: controller state, whether the user-visible indicator is active, active tasks, pending approvals, controller-owned resources and intervention policy. Read-only.")
def automation_status() -> dict[str, Any]:
    return _executor.automation_status()


@server.tool(description="Record the user's approval decision for a needs_approval token. Routes to the engine's safety policy; the decision is re-validated before any operation may proceed.")
def decide_approval(token: str, decision: str) -> dict[str, Any]:
    return _executor.decide_approval(token, decision)


@server.tool(description="Status of a pending approval request without deciding it.")
def approval_status(token: str) -> dict[str, Any]:
    return _executor.approval_status(token)


@server.tool(description="Profiler summary + recent spans. Proves speed claims with data.")
def profiler() -> dict[str, Any]:
    return _executor.profiler.summary()


def main() -> None:
    import asyncio

    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
