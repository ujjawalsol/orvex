# SECURITY

## Trust model

The engine runs as the invoking user (Medium integrity typical) and must remain
*less powerful than the user's session in every safety-critical dimension*.
It may drive applications; it may not administer the system.

## Enforcement layers

1. **Levels**: READ/LOW auto; SENSITIVE/DANGEROUS need approval; SYSTEM blocked.
   No silent escalation (verified: no level-bypass paths in executor).
2. **Allowlist launches**: notepad/Settings-URI/sandbox-scoped Explorer only;
   everything else → `needs_approval_launch`. Arbitrary command execution at LOW
   was an audit finding and is closed.
3. **Protected processes/UI**: explorer/dwm/winlogon/csrss/services/lsass/smss/
   wininit/System (+exe+PID checks); taskbar/Start/tray/secure-desktop/CAD/lock/
   shutdown blocklist. Tests prove refusal.
4. **Input gating**: dangerous combos blocked centrally (Win+L, CAD, Alt+F4,
   Ctrl+Shift+Esc, Win+R/X, Ctrl+Esc, Alt+Tab, any Win); 10-step SendInput guard
   (task, target, HWND/PID, input-desktop, integrity>=, foreground, focus/settle,
   minimum input, post-verify). UIPI Medium→High refused up front with reason
   instead of silent swallow.
5. **Ownership**: processes/windows/handles/sessions tracked per task; closes only
   exact-owned (+audited test-cleanup prefix path); cleanup never taskkills
   (`processes_terminated` always 0).
6. **Sandbox**: test files inside `%TEMP%\orvex_sandbox` only
   (canonical-path validator); deletion sandbox-only.
7. **Browser**: uses user's existing Chrome or Edge via native CDP (no separate
   automation runtimes or browser binaries downloaded); isolated headless profiles,
   ephemeral ports, never touches the user's live profile; no downloads/uploads/settings/extensions.
8. **Audit**: append-only JSONL, secret redaction (verified: 0 test-content leaks
   in 5000+ events); prod/dev modes identical safety (asserted by test).
9. **Cancellation + ESC**: task cancel, global ESC hook (AI cannot disable).
10. **Automation controller**: one per engine process, holding **no additional
    privilege**. It can only request `pause`/`resume`/`stop`/`take_control`/
    `approve`/`deny`; it cannot terminate processes, delete files, write the
    registry, manage devices or run system commands. Being visible grants it
    nothing. Approvals are single-use, TTL-bounded, structured tokens that the
    engine re-validates against `SafetyPolicy` before taking effect, so the UI
    can never call a dangerous backend function directly.
11. **Deny-list is absolute**: `blocked_protected_app` targets (password
    managers etc.) and the sandbox-only `explorer.exe` rule are refused
    outright and are never presented for approval.

## Automation visibility

A system-wide indicator makes active automation visible for every controlled
application, not just a browser. The user can pause, take control, resume,
approve/deny supported actions, or stop automation at any time.

The indicator is user-space only: a topmost companion tool window with real
widgets and `Alt`+key accelerators. It does not modify Explorer, the taskbar,
DWM, the shell, system UI internals or the registry, and it injects nothing into
any page DOM. It is created as a tool window so it occupies no taskbar slot.

If the indicator dies or stops heartbeating, the engine safe-pauses rather than
continuing silently, and ESC + MCP cancel remain independent always-available
stops. Safety never depends on the cosmetic UI element surviving.

Stop means *stop the automation*: it cancels the task, stops retries and
pending input, and performs owned cleanup only. It does not destroy the
resources being automated.

## What is explicitly NOT present (audited)

No taskkill/mass-kill path, no process termination primitive, no shutdown/restart/
logoff, no registry writes, no USB/HID/driver manipulation (read-only enumeration
only), no Secure Attention Sequence, no credential handling, no browser DOM or
extension-based control surface.
