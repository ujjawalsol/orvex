# ORVEX v1.0.0 — FINAL RELEASE CERTIFICATION REPORT

**Release Status:** **RELEASE READY (CERTIFIED)**  
**Version:** `1.0.0`  
**License:** MIT  
**Target Platform:** Windows 10 21H2+ / Windows 11 (22H2+)  
**Runtime:** Python 3.11+ (tested on Python 3.12 / 3.14)  
**Direct Production Dependencies:** Exactly 5 direct packages (9.11 MB direct wheels)  
**Browser Runtime Downloads:** 0 bytes (Native CDP to existing Chrome/Edge)  

---

## 1. Product Scope & Real-System Automation

ORVEX is a **system-wide Windows automation execution engine for AI agents**, not merely a browser automation tool. It automates the user's actual Windows environment:
- **Windows applications:** Notepad, File Explorer, Windows Settings, VS Code, Electron, Win32, WPF/WinUI apps via native Windows UI Automation (UIA COM).
- **Chromium browser automation:** Google Chrome and Microsoft Edge via native Chrome DevTools Protocol (CDP) over `websockets`.
- **System-wide automation controller:** A unified, topmost companion indicator that represents the active automation state across all controlled applications and guarantees real-time human intervention (Pause, Take Control, Resume, Stop, Approve, Deny).

### Core Principle: Adopt Existing Applications (No Unnecessary Duplicates)

ORVEX implements the following targeting philosophy:
`FIND EXISTING USER APPLICATION ➔ IDENTIFY EXACT TARGET ➔ VERIFY TARGET ➔ AUTOMATE TARGET`

- **Single existing matching window:** ORVEX binds to the existing user window (`mode='attached_existing'`) rather than launching a duplicate instance.
- **Multiple matching windows:** ORVEX refuses to guess. If 2 or more same-titled windows are open without specific disambiguation, it raises `SafetyError('ambiguous_target')` to escalate safely to the user/AI.
- **No window open or `new_instance=True`:** ORVEX launches a verified process and attaches to the newly spawned window.

---

## 2. Capability State Matrix (Audited & Tested)

Every target application is evaluated across 5 distinct states:
1. **INSTALLED**: Binary exists on the system.
2. **RUNNING**: Process is currently active.
3. **DISCOVERED**: ORVEX can detect and locate the target.
4. **ATTACHABLE**: ORVEX can bind to the existing user target without launching duplicates.
5. **AUTOMATABLE**: Actions (find, set_value, invoke, extract) succeed and verify.

| Application / Capability | INSTALLED | RUNNING | DISCOVERED | ATTACHABLE | AUTOMATABLE | Notes / Mechanism |
|---|---|---|---|---|---|---|
| **Google Chrome (Normal User Session)** | **YES** | **YES** | **YES** | **NO** *(by Chromium security design)* | **FALLBACK REQUIRED** | Normal user launch has no remote debugging port. Returns structured `browser_not_attachable`. |
| **Google Chrome (With CDP Debug Port)** | **YES** | **YES** | **YES** | **YES** | **PASS** | Attaches to existing tab over CDP, automates, preserves user process and other tabs on close. |
| **Google Chrome (Isolated Sandbox Session)** | **YES** | N/A | **YES** | **YES** | **PASS** | Explicit fallback (`params={'fallback': 'isolated'}`) launches sandboxed ephemeral profile. |
| **Microsoft Edge (Chromium)** | **YES** | NO | **YES** | **YES** | **PASS** | Supported via identical native CDP backend (`msedge.exe`). |
| **Notepad (Existing Window)** | **YES** | **YES** | **YES** | **YES** | **PASS** | Adopts existing window via UIA without launching duplicate. Verified with `ValuePattern`. |
| **Notepad (New Launch)** | **YES** | NO | **YES** | **YES** | **PASS** | Launches new instance when none exists or `new_instance=True` requested. |
| **File Explorer** | **YES** | **YES** | **YES** | **YES** | **PASS** | Shell window affinity tracking; folder targeting strictly sandbox-scoped. |
| **Windows Settings** | **YES** | NO | **YES** | **YES** | **PASS** | Launched on demand via `ms-settings:` URI; UIA window affinity. |
| **Multi-Window Ambiguity** | N/A | **YES** (2+ windows) | **YES** | Refused | **SAFETY PASS** | Raises `ambiguous_target` when multiple same-name windows exist; refuses to guess. |
| **System-Wide Controller** | **YES** | **YES** | **YES** | **YES** | **PASS** | Tracks active target across apps (`Controlling: Notepad`, `Controlling: Chrome`); handles manual user control. |

---

## 3. Strict Minimal Production Dependency Audit

Every production dependency has been audited against the codebase:

| Package | Imported By | Why ORVEX Needs It | Required for Default Runtime? | Installed Size |
|---|---|---|---|---|
| `mcp>=2.0.0,<3.0.0` | `engine/server.py` | Official Model Context Protocol implementation for stdio transport, tool schemas, and host communication. | **YES** (Core MCP interface) | 2.74 MB |
| `uiautomation>=2.0.18` | `engine/uia_backend.py`, `engine/executor.py`, `engine/safety.py` | Native Windows UI Automation (UIA) tree traversal, control pattern invocation (Value, Invoke, Selection), window resolution. | **YES** (Core Windows automation) | 1.31 MB |
| `comtypes>=1.2.0` | `uiautomation` | Low-level COM interface binding on Windows required by UI Automation COM interfaces (`IUIAutomation`). | **YES** (Required by uiautomation) | 2.48 MB |
| `websockets>=12.0` | `engine/browser.py` | Synchronous WebSocket client (`websockets.sync.client`) connecting to user's existing Chrome/Edge CDP endpoint. | **YES** (Core browser automation) | 1.77 MB |
| `psutil>=5.9.0` | `engine/health.py`, `engine/browser.py` | Safe, read-only system health snapshots (CPU, memory, process ownership tracking) without invasive OS commands. | **YES** (Health gating & safety) | 0.81 MB |

**Total Production Dependencies Installed Size:** **9.11 MB** (5 packages total)

### Excluded / Archived Packages (Zero Production Footprint)

| Package | Classification | Status & Rationale |
|---|---|---|
| `playwright` | Historical Research Only | **REMOVED completely.** Not in `pyproject.toml`, not in `requirements.txt`, not in `install.bat`. Archived to `tests/archive/browser_pw.legacy.py`. |
| Browser Binaries (Chromium/Firefox/WebKit) | Excluded | **ZERO downloads.** ORVEX connects directly to existing Chrome or Edge via CDP. |
| `mss`, `pillow` | Benchmark / Prototype | **REMOVED.** Phase 0 harness only. Not in production runtime. |
| `pytest>=7.0.0` | Developer Optional | `[project.optional-dependencies] dev` only. Not installed in production. |
| Rust toolchain (`cargo`, `target/`) | Research Prototype | **REMOVED.** Build artifacts cleared (~164 MB recovered). |
| GCC / Winlibs toolchain | Research Benchmark | **REMOVED.** Cleared from repo (~961 MB recovered). |
| .NET SDK | Unnecessary | Uses in-box CLR or python. Zero .NET SDK download. |

---

## 4. Final Installation Footprint Metrics

- **Production Python dependencies:** 9.11 MB
- **Engine source code:** 0.44 MB (437 KB)
- **Active repository size:** 3.19 MB
- **Total runtime footprint:** ~9.55 MB
- **Browser binary downloads:** 0 bytes
- **External automation runtimes:** 0 bytes

---

## 5. Security & Safety Gates (Verified)

- **SafetyPolicy authoritative:** All execution paths pass through `SafetyPolicy`.
- **System tool:** Returns `system_ops_not_implemented` (no arbitrary execution).
- **No invasive primitives:** Zero `taskkill`, zero `TerminateProcess`, zero registry writes, zero USB/HID resets, zero shutdown/logoff, zero Explorer restarts.
- **User manual control:** Real-time WH_KEYBOARD_LL / WH_MOUSE_LL detection transitions safely to `USER_CONTROL` on manual intervention.
- **Fail-safe watchdog:** If the companion indicator UI terminates or misses heartbeats, the engine pauses safely rather than executing silently.

---

## 6. Final Certification Verdict

ORVEX v1.0.0 satisfies all architectural, security, and product requirements:
1. Controls the user's real Windows environment.
2. Adopts existing windows instead of spawning duplicates.
3. Uses native CDP without Playwright or browser binary downloads.
4. Protects the user's personal profiles and preserves running browser processes.
5. Employs a strict 5-package minimal dependency tree (9.11 MB).
6. Provides an authoritative system-wide controller for user visibility and control.

**VERDICT: RELEASE CERTIFIED.**
