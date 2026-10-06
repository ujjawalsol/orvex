# ORVEX

**Fast Windows automation MCP — low-latency, direct, reliable system interaction.**

> Version 1.0.0 — Production Release

> AI decides WHAT. Engine decides HOW. SafetyPolicy decides WHAT IS ALLOWED. Verifier decides WHETHER IT WORKED.

ORVEX is a Windows automation MCP server designed to be significantly faster than typical automation implementations. Where other solutions may take 10+ minutes on a 2-minute task due to unnecessary tool calls, excessive screenshots, redundant DOM queries and slow abstraction layers, ORVEX uses direct Windows APIs (UIA, CDP, Win32) with a deterministic execution model.

---

## What ORVEX can automate

- **Windows applications** — open, find, focus, interact via UI Automation patterns

- **Desktop UI** — native controls, dialogs, menus

- **Keyboard & mouse** — guarded, target-verified input injection

- **Files & folders** — sandbox-scoped file operations

- **Browser** — Chrome/Edge via CDP (attach to existing or launch headless)

- **System UI** — supported Windows controls (not shell internals)

- **Processes** — launch, track, close (task-owned only)

- **Clipboard** — read/write via controlled paste path

## What ORVEX does NOT do

- Remote desktop / device management / USB/HID control

- Interact with protected apps (KeePass, 1Password, Bitwarden, etc.)

- Modify the Windows registry, taskbar, shell, DWM

- Access files outside the sandbox without explicit approval

- Perform dangerous key combos (Win+L, Ctrl+Alt+Del, Alt+F4, etc.)

---

## System Requirements & Prerequisites

- **Operating System:** Windows 10 21H2+ or Windows 11 (22H2+ recommended).
- **Official Prerequisite:** **Python 3.11+** (3.12+ recommended). Download from [python.org](https://python.org) (ensure *"Add python.exe to PATH"* is checked) or install via `winget install Python.Python.3.12`.
- **Direct Runtime Dependencies:** Exactly **5 direct packages** (`mcp>=2.0.0,<3.0.0`, `uiautomation>=2.0.18`, `comtypes>=1.2.0`, `websockets>=12.0`, `psutil>=5.9.0`).
- **Browser Automation:** Automates the user's existing installed **Google Chrome** or **Microsoft Edge**. Zero Playwright, zero bundled browser binaries, and zero browser drivers.
- **System Footprint:** Zero Windows Registry modifications during installation or runtime. Zero background services or daemons installed.
- **Permissions:** Runs under standard user integrity (Medium). No Administrator privileges required for standard user applications.

---

## Installation

Install ORVEX once.

ORVEX automatically detects supported AI clients and registers itself as an MCP server.

No manual MCP configuration is normally required.

### Supported Clients:
- **OpenCode**
- **Claude Code**
- **VS Code / GitHub Copilot**
- **Cursor**
- **Windsurf**
- **Antigravity**
- **Claude Desktop**

During installation, the installer reports client status:
- `✓` **Automatically configured** (Connected)
- `⚠` **Detected but manual setup required**
- `—` **Not installed** (safely skipped)

### One-Click Windows Setup (Recommended)
Run the automated installer script:
```powershell
.\install.bat
```
*(Idempotent: safe to run repeatedly. Automatically sets up `.venv`, installs direct dependencies, verifies the engine, and registers ORVEX across all detected AI clients while preserving 100% of existing user MCP servers).*

### Clean Uninstallation
```powershell
.\uninstall.bat
```
*(Unregisters ORVEX from all AI clients without touching other servers, removes `.venv`, and clears temporary test sandboxes).*

---

## MCP Architecture & Universal Entry Point

ORVEX provides a single canonical MCP server entry point across all AI clients:

```text
OpenCode ───────┐
Claude Code ────┤
VS Code ────────┤
Cursor ─────────┤
Windsurf ───────┤
Antigravity ────┤
Claude Desktop ─┤
                 ↓
             ORVEX MCP
                 ↓
        Windows Automation Engine
                 ↓
          Controller UI
```

All clients launch the same tested server:
```powershell
python -m engine.server
```
with cwd set to the ORVEX installation directory. An updated reference file `orvex_mcp_config.json` is also maintained in the repository root for manual inspection or custom setups.

---

## Available Tools

| Tool | Description |

|------|-------------|

| `execute` | Execute semantic intent: verb + app_hint + target + params. Core automation tool. |

| `inspect` | Targeted inspection: resolve one semantic target, return handle. |

| `wait` | Condition wait: wait for window/element with deadline. |

| `verify` | Deterministic verification of target or window state. |

| `workflow` | Run a saved parameterized workflow by name. |

| `system` | Gated system operations (disabled by default). |

| `cancel` | Cancel a running task by task_id. |

| `automation_status` | System-wide automation status (read-only). |

| `decide_approval` | Record user approval/denial for a pending request. |

| `approval_status` | Check status of a pending approval without deciding. |

| `profiler` | Profiler summary with per-operation timing. |

### Intent format

```json

{

"verb": "open_app",

"app_hint": "Notepad",

"target": {"control_type": "Edit"},

"params": {"text": "Hello, ORVEX!"},

"steps": []

}

```

**Supported verbs:** `open_app`, `find`, `invoke`, `set_value`, `type`, `press`, `wait`, `verify`, `inspect`, `close_window`, `browser_open`, `browser_navigate`, `browser_extract`, `browser_close`, `resume`

---

## Chrome / Browser Automation

ORVEX uses a **capability-aware browser routing** model for Google Chrome and Microsoft Edge:

### 1. Normal Chrome (Existing User Session)
For surface-level browser controls:
- Open / focus Chrome window
- Read address bar (Omnibox URL)
- Navigate to URL via address bar
- Switch tabs / enumerate open tabs
- Window controls (back, forward, reload, close)

**Mechanism:** Native Windows UI Automation (UIA).  
**Requirements:** None! Operates directly on your existing normal Chrome session. No special startup flags, no debugger port, no extensions, and zero duplicate browser instances.

### 2. Deep Web Automation (DOM / JavaScript / Structured Extraction)
For deep in-page operations:
- CSS selector queries
- DOM node inspection
- JavaScript expression evaluation
- Structured table data extraction

**Mechanism:** Native Chrome DevTools Protocol (CDP) over WebSocket (`websockets` library).  
- **When attachable:** If Chrome is running with remote debugging enabled (`--remote-debugging-port=9222`), ORVEX attaches directly via native WebSocket CDP.
- **When unavailable:** If Chrome is running normally without a debugging port, deep DOM operations return a structured `browser_not_attachable` error with a clear explanation and remediation options. ORVEX **never** fakes success and never silently replaces your browser.
- **Isolated fallback:** If deep DOM automation is needed and your existing Chrome has no debugging port, ORVEX can launch an isolated, temporary sandboxed session with an ephemeral profile upon request (`params: {"fallback": "isolated"}`).

---

## Automation Status Indicator

While automation runs, a small panel appears in the top-right of your screen:

```

● ORVEX  Automation Active

```

**States:**

- **Ready** — no automation running

- **Automation Active** — ORVEX is controlling your PC

- **Automation Paused** — paused, state preserved

- **You have control** — you took manual control

- **Approval Required** — waiting for your approval

- **Automation Stopped** — stopped by user or system

- **Automation Failed** — an error occurred

- **Automation Blocked** — safety policy blocked the action

**Controls:** Hover to expand. Use **Take Control**, **Pause**, **Resume**, **Stop** buttons or `Alt+T/P/R/S` keyboard shortcuts.

**Stop:** The Stop button, the global **ESC** key, and the MCP `cancel` tool all converge on the same emergency stop path.

---

## Configuration

All configuration is via environment variables. Set them before starting the MCP server.

| Variable | Default | Description |

|----------|---------|-------------|

| `ORVEX_MODE` | `prod` | `dev` or `prod` (verbosity only) |

| `ORVEX_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

| `ORVEX_USER_INTERVENTION` | `PAUSE` | What happens when user touches machine during automation: `PAUSE`, `TAKE_CONTROL`, `BLOCK`, `ALLOW` |

| `ORVEX_ENABLE_SYSTEM` | `0` | Set to `1` to enable gated system operations |

| `ORVEX_APPROVAL_WAIT_S` | `120` | Seconds to wait for human approval |

| `ORVEX_MAX_STEPS` | `20` | Max automation steps per task |

| `ORVEX_MAX_DURATION_S` | `30` | Max task duration in seconds |

| `ORVEX_MAX_LAUNCHES` | `5` | Max process launches per task |

| `ORVEX_ALLOWED_APPS` | *(see safety.py)* | Semicolon-separated additional allowed app names |

| `ORVEX_BLOCKLIST` | *(credential managers)* | Semicolon-separated regex patterns to block |

| `ORVEX_LOG_DIR` | `%APPDATA%\ORVEX\logs` | Override audit log directory |

Example with Claude Desktop:

```json

{

"mcpServers": {

"orvex": {

"command": "python",

"args": ["C:\\path\\to\\orvex\\engine\\server.py"],

"env": {

"ORVEX_USER_INTERVENTION": "PAUSE",

"ORVEX_MAX_STEPS": "50"

}

}

}

}

```

---

## Permissions

ORVEX runs at **Medium integrity** (normal user level) by default.

- Can automate apps running at Medium integrity (most user apps)

- Cannot inject input into High-integrity (elevated/admin) apps — refused with a clear error

- Cannot interact with the secure desktop (UAC dialogs, logon screen)

- Does not require administrator privileges for normal use

---

## Development

```powershell

# Run the test suites

python tests/test_safety_smoke.py --reps 1

python tests/test_certification.py

python tests/test_controller.py

# Clean up test windows left behind by interrupted runs

python tests/cleanup_test_windows.py

python tests/audit_controller_processes.py   # should report 0

# Run benchmarks

python bench/master_final.py

```

### Project layout

```

engine/

server.py           MCP server (11 tools)

executor.py         Graph executor — core performance path

compiler.py         Intent -> DAG compiler

controller.py       System-wide automation state machine

controller_ui.py    Status indicator UI (companion subprocess)

uia_backend.py      Windows UIA backend (targeted, no tree dumps)

browser.py          Chrome/Edge native CDP browser backend (zero extra runtimes)

router.py           Action router (op -> ordered mechanisms)

capabilities.py     Capability registry + pattern detection

sessions.py         Browser session store (isolated ephemeral profiles)

safety.py           Safety policy (all paths pass here)

security.py         Security levels, blocklist, emergency stop

health.py           System health snapshots (read-only)

config.py           Validated configuration

profiler.py         Per-operation timing

bench/                Benchmarks and final certified results (bench/results/final/)

tests/                Test suites (archive/ = retired stress tests & historical benchmark artifacts)

prototypes/           Rust/C# prototype measurements (build artifacts removed)

docs/                 ARCHITECTURE, SAFETY, SECURITY, PERFORMANCE, THREAT_MODEL,

TROUBLESHOOTING, RELEASE

```

---

## Troubleshooting

**"CoInitialize has not been called" errors in @AutomationLog.txt**

This is a `uiautomation` library diagnostic log for COM initialization in non-STA threads. It does not indicate a malfunction — ORVEX initializes COM correctly on the main execution thread. These messages appear when the library is imported in a background thread context.

**"window_not_found" error**

The target application may not be running or the title hint doesn't match. Try using `inspect` first to verify the window is accessible.

**"blocked_system_target" or "blocked_system_ui"**

ORVEX cannot control Windows shell components (taskbar, Start Menu, system tray). This is by design.

**"needs_approval_launch" for a specific app**

The app is not in the default allow-list. Add it via `ORVEX_ALLOWED_APPS=myapp.exe` environment variable, or approve the request via the `decide_approval` tool.

**Status indicator doesn't appear**

The indicator requires `tkinter` (included with standard Python). If it fails, ORVEX enters degraded mode — automation continues but without the visual indicator. The ESC emergency stop still works.

**Chrome automation fails**

Ensure Chrome is running with `--remote-debugging-port=9222` or let ORVEX launch a headless Chrome instance. The headless path requires Chrome to be installed at the standard path.

---

## Uninstalling

To uninstall ORVEX cleanly:

1. Remove the ORVEX entry from your MCP client configuration (`claude_desktop_config.json`).
2. Run `.\uninstall.bat` to remove the `.venv` virtual environment and temporary test sandboxes.
3. Delete the ORVEX folder.

**Zero Residue:** ORVEX creates no Windows Registry entries, registers no Windows services, and leaves no background daemons running. Deleting the directory completely removes ORVEX from your system.

---

## Safety & Security

- **Sandbox**: File operations confined to `%TEMP%\orvex_sandbox\` by default

- **Protected apps**: Credential managers (KeePass, 1Password, etc.) permanently blocked

- **System UI**: Windows shell components cannot be automated

- **Dangerous keys**: Win+L, Ctrl+Alt+Del, Alt+F4, etc. are blocked

- **Input verification**: 7-step foreground/integrity/desktop verification before any key injection

- **Audit log**: Append-only, redacted, at `%APPDATA%\ORVEX\logs\orvex_audit.jsonl`

- **Emergency stop**: ESC key on a global hook, independent of the AI model

See [SAFETY.md](SAFETY.md) and [SECURITY.md](SECURITY.md) for details.
