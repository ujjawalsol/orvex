# SAFETY

Superset of SECURITY.md focused on operational safety: what the engine refuses,
what it bounds, and how it fails.

## Refusal codes (deterministic, never silent)

`blocked_system_target`, `blocked_system_ui`, `blocked_protected_app`,
`blocked_unsafe_path`, `blocked_system_keys`, `blocked_input`,
`needs_approval[_launch|_close]`, `denied_by_user`, `ambiguous_target`,
`foreground_not_target`, `session_stale`, `stale_handle`, `resume_invalid`,
`resource_limit_exceeded`, `execution_budget_exceeded`, `needs_ai_close_blocked`,
`cancelled`.

## Automation visibility and user control

When automation is active, a **system-wide** status/control surface is displayed
in the top-right of the screen. It is not browser-specific: one indicator
represents every controlled application (Notepad, Explorer, Settings, Chrome,
Edge, Electron/Win32/WPF apps, any session the engine drives).

States: `IDLE · RUNNING · PAUSED · USER_CONTROL · WAITING_APPROVAL · STOPPING ·
STOPPED · FAILED · BLOCKED`.

Authoritative control paths, per state:

| State | Controls |
| --- | --- |
| RUNNING | Take Control · Pause · Stop |
| PAUSED / USER_CONTROL | Resume · Stop |
| WAITING_APPROVAL | Approve · Deny · Stop |
| BLOCKED / FAILED | Stop (no bypass) |

- **Take Control** — automation pauses, pending input stops, task state is
  preserved, and the user's manual input is never recorded as automation input.
- **Pause / Resume** — stops scheduling and injecting input while preserving
  task, session and handle state; resume continues from the preserved state.
- **Stop** — stops the *automation*, not the resources: cancels the task, stops
  retries and pending input, performs owned cleanup only, returns a cancelled
  receipt. No `taskkill`, no Explorer/Windows restart, no HID/USB/driver reset.
- **Approve / Deny** — a human decision on a structured request. Deny means the
  operation does not execute; the panel never offers a bypass.

## The controller does not bypass the safety policy

The controller may request only `pause`, `resume`, `stop`, `approve`, `deny`,
`take_control`. It holds no capability the executor lacks and never executes
process termination, file deletion, registry modification, device management or
system commands.

Approval flow is one-directional:

```text
UI / MCP decide_approval -> controller token store -> SafetyPolicy re-validation -> engine -> operation
```

An `approve` is **refused** unless the engine-injected validator re-runs the
original safety gate (`SafetyPolicy._approved` fails closed). Approval unlocks
exactly one gate and never widens any other rule. Deny-list targets
(`blocked_protected_app`) and the sandbox-only `explorer.exe` rule are absolute:
they are refused outright and are never offered for approval.

Stop, the ESC emergency stop and the MCP `cancel` tool all converge on one
`EmergencyStop.cancel` path — there is exactly one cancellation implementation.

## Indicator failure is fail-safe

If the indicator process dies, is closed by the user, or stops sending its
heartbeat, the engine transitions to a safe pause instead of silently
continuing. Safety never depends on a cosmetic UI element: ESC and MCP cancel
remain independent, always-available stops.

The indicator is a user-space topmost tool window. It does not modify Explorer,
the taskbar, DWM, the shell, system UI internals or the registry, and injects
nothing into any browser DOM.

## User-intervention policy

Physical keyboard/mouse events that fall outside the engine's own input windows
are reported as user intervention (events only, never content). Policy is
configurable via `ORVEX_USER_INTERVENTION` (or `SFMCP_USER_INTERVENTION`):

| Mode | Behaviour |
| --- | --- |
| `BLOCK` | stop the automation |
| `PAUSE` | pause and wait (default) |
| `TAKE_CONTROL` | hand control to the user |
| `ALLOW` | continue |

## Budgets (defaults, all configurable + validated)

steps 20, duration 30 s, launches 5, window creations 5, closes 10,
inputs 20, tracked windows 20; browser: 4 sessions, 180 s idle TTL, 900 s max age.
Approval wait: `ORVEX_APPROVAL_WAIT_S` (default 120 s, also accepts legacy `SFMCP_APPROVAL_WAIT_S`; then a `needs_approval`
receipt is returned with the token so the call can be retried).

## Health gating

Read-only snapshots before/after every suite: explorer/DWM/taskbar/desktop,
foreground, input desktop, top-level counts, owned PIDs, CPU/mem, HID/USB +
RawInput mouse/keyboard counts. Any unexpected shell/device change aborts the
suite (`ABORTED_SAFE` semantics: stop actions/retries/launches, invalidate
handles, preserve diagnostics, no automated repair of USB/HID/drivers/Explorer).

## Input-device doctrine

Physical devices are USER-OWNED INFRASTRUCTURE. Engine contains no USB/HID/
driver operations (audited). SendInput/SendKeys are controlled fallbacks with
pre/post verification. Device enumeration deltas abort benchmarks; a human
decides on restarts. Never trade safety for speed.

## Known hazard classes found by testing (all contained)

- Duplicate same-titled windows hijacking routing → PID attach + affinity.
- Stale sandbox files → silent Confirm-overwrite → freshness waits + hygiene.
- Async save-commit/teardown races → file waits, modal drain, readiness polls.
- Forward-slash paths rejected by Save dialog → native separators at boundary.
- Cold-launch input swallow → settle + focus gates (refuse, don't blast).
- Hung app ignoring Close → needs_ai, exact-PID operator action only.
- Click-through on safety controls → indicator is a non-transparent, non-layered
  tool window; verified by hit-testing all interior points, not by assumption.
- Leaked test windows making later selector resolution ambiguous → suites close
  what they own; `tests/cleanup_test_windows.py` clears older test artifacts and
  `tests/audit_controller_processes.py` confirms no controller process remains.

## Controller resource ownership

The controller tracks its own PID, child PIDs and window handles, and closes
only those on shutdown. It never terminates an unrelated process. Verified by
`tests/test_controller.py` (`OWN-*`), which asserts an unrelated system process
is still alive after controller shutdown and that no controller process remains.
