# ARCHITECTURE

```text
AI model (any MCP client)
  │  semantic intent {verb, app_hint, target, params, steps[], idempotency}
  ▼
MCP adapter (engine/server.py, 11 tools, validated config, stateless facade)
  │  in-process
  ▼
Automation core (engine/executor.py)
  ├── compiler.py: closed verb vocabulary -> internal DAG (timeouts/verify/retry owned here)
  ├── router.py + capabilities.py: op -> ordered mechanisms (measured COSTS else safest-first)
  ├── sessions.py: BS_n@v browser sessions (validate/reuse/evict)
  ├── journal: per-task execution state + R<task>@v<n> resume handles
  ├── uia_backend.py: targeted UIA, versioned H handles + epoch, foreground guard,
  │                    input router (pattern > clipboard > keys), exact close
  ├── browser.py: Chrome/Edge native CDP (existing browser, isolated ephemeral profiles)
  ├── safety.py + security.py + health.py + config.py: policy, budgets, ownership,
  │                    health/input-device monitoring, validated config
  └── profiler.py: per-op spans (lookup/exec/wait/verify/serialization, cache, retries)

Automation controller (engine/controller.py + engine/controller_ui.py)
  │  ONE per engine process — the authoritative automation state for ALL apps
  ├── state machine: IDLE · RUNNING · PAUSED · USER_CONTROL · WAITING_APPROVAL
  │                  STOPPING · STOPPED · FAILED · BLOCKED
  ├── companion UI subprocess: topmost, tool-window (no taskbar slot), top-right,
  │   real widgets + Alt-key accelerators, compact when RUNNING, auto-expands on
  │   APPROVAL/BLOCKED/FAILED. No shell/taskbar/DWM/registry/DOM involvement.
  ├── approval store: structured, single-use, TTL-bounded tokens. Approve is
  │   refused unless the engine-injected SafetyPolicy validator re-checks it.
  ├── user-intervention hooks (WH_KEYBOARD_LL/WH_MOUSE_LL, events only) feeding
  │   USER_INTERVENTION_POLICY = BLOCK | PAUSE | TAKE_CONTROL | ALLOW
  ├── heartbeat watchdog: UI death / lost heartbeat -> safe pause (fail-safe)
  └── ownership: controller PID + child PIDs + HWNDs, closed on shutdown only
```

Model C (hybrid): AI sends intent + optional semantic steps; AI never sends HWND/COM/CacheRequest/coords/timeouts/retries/backends. Execution graphs stay internal.

Key mechanisms (all measured before adoption):
- PID-verified launch attach + task window-affinity routing (duplicate-window class)
- `wait_file(min_mtime)` (async save-commit), modal drain + teardown grace on close
- readiness poll on invoke, focus/settle gates on SendKeys, same-pid post-check
- bounded retries only for safe ops; unsafe ops escalate to structured needs_ai
- `task_id` threading = multi-call session continuity (Tasks-lite; full MCP Tasks
  deferred — not needed for sync fast paths at 3.45 ms/call transport cost)
