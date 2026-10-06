# TROUBLESHOOTING

## Automation indicator not visible

The indicator is a companion process (`engine/controller_ui.py`) launched by the
engine. Check `automation_status`:

- `indicator: "degraded-no-ui"` — the UI could not start (no interactive desktop,
  or a previous instance still exiting). Automation still runs, and ESC + MCP
  `cancel` remain valid stops. Restart the MCP server to bring the panel back.
- `indicator: "active"` but nothing on screen — the panel may have been dragged
  off-screen; state updates deliberately never move it, so it cannot snap back.
  Kill the owned child PID (reported in `owned.controller_pid`) and restart.
- `degraded: true` after a panel crash — expected fail-safe behaviour: the engine
  safe-paused rather than continuing silently. Resume once the panel is back.

## Approval panel appeared unexpectedly

`WAITING_APPROVAL` means the engine hit a safety gate (unlisted launch, headed
browser, or a SENSITIVE verb). The panel text is generated from the structured
request. Approve unlocks only that one gate and only after `SafetyPolicy`
re-validates it; Deny means the operation does not run. If the gate is for a
deny-listed or sandbox-protected target it will never be offered — those are
refused outright (`blocked_protected_app`, `blocked_unsafe_path`).

Requests expire after 5 minutes; `ORVEX_APPROVAL_WAIT_S` (default 120 s, also accepts legacy `SFMCP_APPROVAL_WAIT_S`) bounds
how long a call waits before returning a `needs_approval` receipt you can retry
with the same token.

## Automation keeps pausing by itself

That is `USER_INTERVENTION_POLICY=PAUSE` (default) reacting to real physical
keyboard/mouse input outside the engine's own input windows. Set
`ORVEX_USER_INTERVENTION=ALLOW` (or `SFMCP_USER_INTERVENTION=ALLOW`) to continue, `TAKE_CONTROL` to stay paused in
user-control, or `BLOCK` to stop.

## Leaked windows from earlier runs cause flaky set_value

Several same-titled windows (e.g. many `Untitled - Notepad`) make a
`control_type: Edit` selector ambiguous, so `set_value` can resolve the wrong
edit control and text appears duplicated. Run `python tests/cleanup_test_windows.py`
(test-artifact windows only; it discards unsaved Notepad changes through each
window's own Don't Save button, and closes sandbox Explorer views). All suites
now close what they own, so this should not recur.

## needs_ai with element_not_found

Stale duplicates or not-yet-open dialogs. Engine re-resolves once; check
`candidates` + `resume_handle` in the receipt and resume with a refined target.
Keep the desktop free of same-titled strays; prefer unique filenames.

## needs_ai_close_blocked (modal dialog)

An owned modal (usually Save As/Confirm-overwrite) is still open. If the file
was verified saved, wait for teardown and retry close; if a Confirm-overwrite
appeared, the filename collided — choose a fresh name (saves never overwrite
silently by design).

## wait_file_timeout

File didn't appear fresh within deadline. Check sandbox path separators
(Win11 Save dialog needs native `\`), disk/AV latency, or a hidden Confirm
dialog. Receipts carry profiler spans to locate the stall.

## foreground_not_target / blocked_input

Another window stole foreground, target is on another desktop, or integrity is
higher than the engine (Medium→High refused). Resolve interactivity first;
never retry blindly — re-resolve and resume.

## session_stale / stale_handle / resume_invalid

Normal lifecycle signals, not bugs: re-attach / re-resolve / resume with the
current handle version. Handles never silently follow new resources.

## Flaky rapid-fire runs on a loaded box

UI teardown/enumeration lags under CPU/RAM pressure (observed: save-commit and
dialog-teardown latency spikes). Mitigations built in: waits, drains, readiness
polls, bounded retries, resume. Run suites on a quiesced desktop for stable
numbers; treat p95, not median, as the capacity signal.

## Mouse/keyboard hardware issues

The engine has no USB/HID/driver operations (audited) and cannot repair device
state. If device enumeration changes unexpectedly, benchmarks abort with
`input_device_state_changed`; a human decides on restart. Correlation analysis
of the 2026-10-04-adjacent incident: 314 guarded keyboard injections in logs,
zero device-management operations anywhere — NO ENGINE CORRELATION to device
state; cause unknown, mechanism for USB effects does not exist in this codebase.

## Browser automation issues (unsupported_browser / cdp_no_target)

- `unsupported_browser`: Neither Google Chrome nor Microsoft Edge was found in
  standard system paths (`Program Files`, `Program Files (x86)`, or `LocalAppData`).
  Install Google Chrome or Microsoft Edge. ORVEX automates your existing browser
  and does NOT install bundled Chromium or external browser frameworks.
- `cdp_no_target`: Browser started but failed to expose a debuggable page target
  within the timeout. Ensure your antivirus or host firewall is not blocking
  loopback connections (`127.0.0.1`).
- **User profile safety:** ORVEX always creates an ephemeral profile directory in
  `%TEMP%` (`sfmcp-chrome-*` or `sfmcp-edge-*`). Your primary personal Chrome/Edge
  profile, bookmarks, extensions, and passwords are never modified or accessed.
- **Zero extra runtimes:** ORVEX does not use Playwright, Puppeteer, or Selenium.
  No `playwright install` or driver downloads are needed or supported.
