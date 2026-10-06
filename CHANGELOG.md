# CHANGELOG

## 1.0.0 — Production Release

- Released: v1.0.0. Promoted from RC after passing final certification (CERT 24/24,
  smoke 1/1) with no regressions against the Phase 5 baseline.
- Cleanup: removed downloaded toolchain artifacts (winlibs gcc ~961 MB), Rust build
  target (~164 MB), uiautomation library diagnostic log, intermediate bench smoke
  logs, duplicate schema files, `orvex.egg-info`, and all `__pycache__` dirs.
- Archived retired stress tests to `tests/archive/` (agent_compare, multiwindow,
  mcp_loop, browser_micro) — these were documented as retired in Phase 5 and contain
  the mass-taskkill scenarios that are permanently decommissioned.
- Added LICENSE (MIT).
- Added `docs/RELEASE.md` with final release certification report.
- Installer (`install.bat`) cleaned and verified; generates `orvex_mcp_config.json`
  with machine-local absolute path ready for MCP host paste-in.
- Security audit: no production path bypasses SafetyPolicy; no taskkill, no
  TerminateProcess, no registry, no device, no shutdown/logoff, no Explorer restart
  in any production engine path. All dangerous-keyword hits verified to be
  (a) winlibs header files (now removed) or (b) comments documenting retired behavior.
- Browser architecture correction: removed Playwright completely from production
  dependencies, `pyproject.toml`, and runtime execution paths. ORVEX defaults
  exclusively to native CDP over `websockets` using the user's existing Google Chrome
  or Microsoft Edge installation.
- User profile safety: CDP sessions run in ephemeral temporary profiles (`%TEMP%/sfmcp-*`),
  guaranteeing zero modification to user cookies, bookmarks, extensions, or passwords.
- Archived `browser_pw.py` to `tests/archive/browser_pw.legacy.py` as historical benchmark
  evidence only; not included in the production release package.

## 0.3.0 (final RC) — system-wide user control

- Added: **system-wide automation controller** (`engine/controller.py` +
  `engine/controller_ui.py`). One indicator per engine process represents every
  controlled application; states `IDLE/RUNNING/PAUSED/USER_CONTROL/
  WAITING_APPROVAL/STOPPING/STOPPED/FAILED/BLOCKED`; authoritative controls
  Pause / Take Control / Resume / Approve / Deny / Stop, all reachable by mouse,
  keyboard (Alt+key) and screen reader.
- Added: human approval pipeline. `SafetyPolicy` gates (`needs_approval_launch`,
  headed browser) resolve through the controller with structured, single-use,
  TTL-bounded tokens; `approve` is refused unless the engine re-validates the
  request, and `deny` guarantees the operation never executes. New MCP tools:
  `automation_status`, `decide_approval`, `approval_status`.
- Added: `USER_INTERVENTION_POLICY` (`SFMCP_USER_INTERVENTION` = BLOCK/PAUSE/
  TAKE_CONTROL/ALLOW, default PAUSE) driven by keyboard+mouse hooks that report
  events only and never content.
- Added: fail-safe watchdog — indicator death, user-closed panel or lost
  heartbeat transitions to a safe pause; ESC/MCP cancel stay independent.
- Added: strict controller resource ownership (PID, child PIDs, HWNDs), visible
  detail redaction (no HWND/PID/selectors/credentials), click-through safety,
  compact-when-running / auto-expand-on-attention layout, tool window so the
  taskbar is never touched.
- Hardened: deny-list targets (`blocked_protected_app`) and the sandbox-only
  `explorer.exe` rule are now absolute — refused outright, never approvable.
- Fixed: engine no longer raised `needs_approval` for refused operations when no
  controller is present (fails closed as `blocked`); `cancel` returns a status.
- Fixed: Tk is touched only on the main thread; the indicator no longer resets a
  user-repositioned window; engine pipes close cleanly on shutdown.
- Tests: `tests/test_controller.py` — 110 bounded checks (A–H, clean shutdown,
  performance, accessibility, click-through, ownership, system health). Suites
  now close every window they open via `tests/_cleanup.py`;
  `tests/cleanup_test_windows.py` clears older test artifacts and
  `tests/audit_controller_processes.py` verifies no controller process remains.
- Fixed: `shutdown()` now sends the exit request before dropping the process
  handle, so the indicator exits cleanly instead of needing a forced terminate.
- Fixed: `test_phase1` T3 now opens Explorer sandbox-scoped, matching the
  hardened policy (a bare Explorer launch is refused by design, not a bug).
- Measured: indicator boot ~10 ms; every state transition < 0.25 ms; pause,
  take-control, resume, stop and approval bookkeeping all < 0.25 ms, so the
  automation hot path never waits on UI rendering.

## 0.2.0 (Phase 5) — production hardening

- Added: health guard (shell + input desktop + HID/USB/RawInput enumeration),
  ABORTED_SAFE semantics, input-desktop + integrity gates (`blocked_input`),
  validated config layer + prod/dev modes (safety-identical), session caps/TTL/
  max-age + eviction, `wait_file(min_mtime)`, owned modal drain + teardown
  grace, invoke readiness poll, focus/settle gates, task window-affinity +
  PID-verified attach, resolve cache, journal/resume, `cancel` tool, `task_id`
  threading, browser sessions over MCP, CDP `extract_table`, Playwright backend.
- Measured optimizations: SetValue 502→1.7 ms, Invoke 534→35 ms, resolution
  1073→79 ms, resolve cache 1.7×, session reuse 5.72×, recovery 3.4–8.6×.
- Removed: owned-fallback close (closed a wrong window — caught by its own
  negative probe), all mass-`taskkill` paths, blind retries on unsafe ops.
- Docs: full release set (this folder).

## 0.1.0 (Phases 0–1) — vertical slice

- Intent compiler, UIA backend, executor with receipts, 7-tool MCP, Phase 0
  harness, 5/5 Windows tests.
