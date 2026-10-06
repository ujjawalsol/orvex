# SAFETY AUDIT REPORT — Phase 2 §1 (audit before code)

Date: 2026-10-04. Scope: ORVEX repository root
(`engine/`, `bench/`, `tests/`). No code modified for this audit.
Root cause of the black-UI incident is NOT proven; hypotheses ranked by evidence.

## 1. Operations actually performed during the failing stress-test window

| # | Operation | Where | Count (approx) | Global-state capable? |
|---|---|---|---|---|
| 1 | `taskkill /IM notepad.exe /F` (kills ALL Notepad incl. unsaved user work) | `agent_compare.py`, `test_phase1.py`, `multiwindow.py`, manual cleanup cmds | 60+ | YES — data loss in other windows |
| 2 | `taskkill /IM explorer.exe /F` (kills the Windows SHELL: taskbar+desktop+file manager are explorer.exe) | manual cleanup cmd, this session | 1 (6 shell procs) | YES — see §3 |
| 3 | `explorer.exe <user-folder>` launches (Downloads/Documents/Desktop/Pictures — outside any sandbox) | `multiwindow.py`, executor `_launch`, agent C/E runs | 15+ windows | YES — shell load, 12+ stale windows observed |
| 4 | `notepad.exe` launches | executor `_launch`, all tests | 20+ | window-count growth |
| 5 | `WindowPattern.Close()` on first regex match ("Pictures") | `multiwindow.py` | 1 | YES if match wrong |
| 6 | `SendKeys` system-wide (`{Ctrl}s`, `{Enter}`, text) via `press`/`type`/fallback | executor, tests | 30+ | YES — goes to foreground window |
| 7 | `SetFocus` / foreground changes | executor `press`, tests | 20+ | focus theft, routes input |
| 8 | `ValuePattern.SetValue` / `InvokePattern.Invoke` / `Click` | executor | 60+ | app-scoped (preferred path) |
| 9 | Clipboard takeover (EmptyClipboard/SetClipboardData) | `bench/ops_win.py` | 21 | clobbers user clipboard |
| 10 | Headless Chrome on :9333–:9334 with fresh profiles + terminate | `browser_micro.py`, dbg | 3 | isolated (fresh profile) |
| 11 | File writes to `%TEMP%\sfmcp_*.txt` | agent B/F, test_phase1 | 15+ | user-visible temp files |
| 12 | Screenshots via mss | bench only | 40+ | read-only |
| 13 | Screenshots/OCR/vision of shell, registry writes, shutdown/logoff/Win+L/SAS, secure desktop | — | 0 | never issued |

## 2. Win32 calls in engine capable of affecting global state

- `subprocess.Popen(app)` with model-influenced string (`_launch`): arbitrary process
  launch, currently classified LOW — **allows `powershell.exe ...` at LOW. Finding.**
- `user32.SendInput` (bench null-move; harmless) and `uiautomation.SendKeys`
  (real keystrokes to foreground window).
- `SetForegroundWindow`/`SetFocus`, `GetForegroundWindow` polling.
- `WindowPattern.Close()` reachable on any UIA-resolved window (incl. Explorer).
- `OpenClipboard/EmptyClipboard/SetClipboardData` (bench harness).
- `os.startfile("ms-settings:")`, `explorer.exe`, `notepad.exe` launches.
- No `TerminateProcess`, no service/shell restart, no `LockWorkStation`, no SAS,
  no registry, no shutdown APIs anywhere in `engine/`.

## 3. Leading hypothesis (evidence-backed, not proven)

**`taskkill /IM explorer.exe /F` killed the desktop shell.** explorer.exe owns the
taskbar, desktop window, notification area and file-browser windows. Force-killing
all instances mid-automation (while UIA polls/COM traffic against shell providers
was in flight, with 12+ stale Explorer windows and repeated focus changes) is
consistent with: taskbar disappearance → black/missing UI after minimizing windows
(DWM/composition + shell view hosts torn down, automatic shell restart racing or
failing). Contributing load: dozens of Notepad/Explorer windows, repeated
`SetFocus` foreground churn, system-wide `SendKeys`, and `taskkill notepad`
destroying windows the engine had cached handles for (stale-handle storms on next
lookup). UIA pattern ops alone (reads, `SetValue`, `Invoke`) are unlikely to take
down the shell; no evidence implicates them. **NOT VERIFIED** as sole cause —
treated as hypothesis #1 with guardrails against ALL hypotheses.

## 4. Unsafe patterns found (must fix regardless of root cause)

1. Tests shell out to `taskkill /IM <name> /F` — name-based mass kill, hits user
   resources and (for explorer.exe) the OS shell. Never do this again.
2. `open_app` accepts arbitrary launch strings at LOW risk (command execution).
3. `Close` acts on first regex match — no exact HWND/PID/ownership check, no
   ambiguity error for multi-match.
4. `press`/`type` retry path can re-inject keys without re-verifying foreground.
5. No budgets: unbounded windows (12+ stale Explorers observed), launches, steps,
   duration, input events. No per-task process/window ownership tracking.
6. No dangerous-key blocklist: `{Win}`, `Win+L`, `Alt+F4`, `Ctrl+Esc` etc. passable
   via `press keys` today.
7. Test artifacts land in user folders (`Downloads/Documents/Desktop/Pictures`,
   `%TEMP%` root) — no sandbox, no path validator, deletion unconstrained.
8. No shell-health snapshot/abort, no structured danger-op log, no `cancel(task)`.
9. Clipboard test clobbers user clipboard without save/restore note in engine path.
10. No `blocked_system_target` / `blocked_system_ui` / `blocked_unsafe_path` /
    `ambiguous_target` / `resource_limit_exceeded` / `execution_budget_exceeded`
    error contract — failures are generic strings.

## 5. What is NOT implicated

No evidence of registry, driver, DWM API, lock-screen, SAS, shutdown, or secure
desktop interaction. Screenshots/reads/pattern invoke+set on Notepad are app-scoped.
