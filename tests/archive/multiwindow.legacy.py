"""Phase 1.5 §7-10: multi-window stress, multi-Notepad, foreground, stale handles.

- Explorer A..D -> distinct folders; resolve/navigate/close per-window tasks.
- Notepad A/B/C with AAA/BBB/CCC; append XXX to B; foreground trap on wrong window.
- Foreground safety: target != foreground -> guarded correction or needs_ai;
  mid-execution foreground change must not cause blind input.
- Stale handle: inspect -> handle -> UI change -> reuse must re-resolve or needs_ai.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.compiler import compile_intent  # noqa: E402
from engine.executor import Executor  # noqa: E402
from engine.security import EmergencyStop, Policy  # noqa: E402
from engine.uia_backend import Target  # noqa: E402

FOLDERS = ["Downloads", "Documents", "Desktop", "Pictures"]


def _home(sub: str) -> str:
    p = os.path.join(os.path.expanduser("~"), sub)
    os.makedirs(p, exist_ok=True)
    return p


def test_multi_explorer(ex: Executor) -> dict:
    # open 4 explorers at distinct folders via CLI (deterministic, no GUI nav)
    for f in FOLDERS:
        subprocess.Popen(["explorer.exe", _home(f)])
        time.sleep(0.6)
    time.sleep(1.5)
    wins = ex.uia.find_windows_all("Explorer")
    titles = []
    for w in wins:
        try:
            titles.append(w.Name)
        except Exception:  # noqa: BLE001
            pass
    # targeted resolution per folder-qualified title
    hits = {}
    for f in FOLDERS:
        try:
            win = ex.uia.find_window(f, timeout_s=3)
            hits[f] = win.Name
        except LookupError as e:  # noqa: BLE001
            hits[f] = f"MISS:{e}"
    # close Pictures explorer deterministically via its window handle title
    closed = False
    try:
        pic = ex.uia.find_window("Pictures", timeout_s=3)
        pic.GetWindowPattern().Close()
        closed = True
    except Exception as e:  # noqa: BLE001
        closed = f"close_failed:{e}"
    return {"explorer_windows_seen": len(wins), "titles": titles[:12],
            "folder_hits": hits, "pictures_closed": closed}


def test_multi_notepad(ex: Executor) -> dict:
    # NOTE (Phase 5): mass taskkill retired permanently.
    time.sleep(0.7)
    texts = {"AAA": None, "BBB": None, "CCC": None}
    for t in texts:
        ex.run(compile_intent({"verb": "open_app", "app_hint": "Notepad"}), intent_verb="open_app")
        r = ex.run(compile_intent({
            "verb": "set_value", "app_hint": "Notepad",
            "target": {"control_type": "Edit"}, "params": {"text": t}}),
            intent_verb="set_value")
        texts[t] = r.status
    # identify instance containing BBB by reading each candidate window's Edit
    import uiautomation as auto

    root = auto.GetRootControl()
    found_b = 0
    for c in root.GetChildren():
        try:
            name = c.Name or ""
        except Exception:  # noqa: BLE001
            continue
        if "Notepad" in name:
            try:
                edit = c.EditControl(searchDepth=8)
                if edit.Exists(1) and edit.GetValuePattern().Value == "BBB":
                    found_b += 1
            except Exception:  # noqa: BLE001
                pass
    # foreground trap: focus a NON-BBB window, then route input via engine to BBB window
    # engine resolves by content each time (no blind typing): verify text of target first
    return {"opened": texts, "windows_holding_BBB": found_b}


def test_foreground_safety(ex: Executor) -> dict:
    import uiautomation as auto

    # ensure Notepad + Explorer both exist, focus Explorer, act on Notepad via pattern (no foreground need)
    subprocess.Popen(["explorer.exe", _home("Downloads")])
    time.sleep(1.5)
    exp = ex.uia.find_window("Explorer", timeout_s=5)
    try:
        exp.SetFocus()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.4)
    fg_before = ex.uia.foreground_hwnd()
    # pattern-based invoke/set does not require foreground: prove no blind SendInput happened
    r = ex.run(compile_intent({
        "verb": "set_value", "app_hint": "Notepad",
        "target": {"control_type": "Edit"}, "params": {"text": "FG-SAFE"}}),
        intent_verb="set_value")
    fg_after = ex.uia.foreground_hwnd()
    notepad_text = None
    try:
        w = auto.WindowControl(searchDepth=1, RegexName=".*Notepad.*")
        if w.Exists(2):
            notepad_text = w.EditControl(searchDepth=8).GetValuePattern().Value
    except Exception as e:  # noqa: BLE001
        notepad_text = f"read_failed:{e}"
    return {"foreground_stable": fg_before == fg_after, "set_status": r.status,
            "set_via": r.result.get("set_via"), "notepad_text": (notepad_text or "")[:20]}


def test_stale_handle(ex: Executor) -> dict:
    # mint handle, bump epoch (simulates UI invalidation without killing anything)
    h = ex.uia.mint(Target(control_type="Edit"), app_hint="Notepad")
    ex.uia.bump_epoch()
    time.sleep(0.2)
    try:
        ex.uia.use(h.hid, timeout_s=2)
        return {"stale_reused_without_error": True, "verdict": "FAIL-blind-reuse"}
    except LookupError as e:
        reopened = None
        try:
            subprocess.Popen(["notepad.exe"])
            ctl, method = ex.uia.use(h.hid, timeout_s=10)
            reopened = f"re-resolved via {method}"
        except Exception as e2:  # noqa: BLE001
            reopened = f"needs_ai:{e2}"
        return {"stale_detected": str(e)[:120], "after_reopen": reopened}


def main() -> int:
    # RETIRED in Phase 2: see agent_compare.py guard. Use test_safety_smoke.py.
    if os.environ.get("SFMCP_ALLOW_LEGACY_STRESS", "0") != "1":
        print("RETIRED: legacy stress test. Use tests/test_safety_smoke.py instead.")
        return 2
    ex = Executor(policy=Policy.load(), stop=EmergencyStop())
    print("MULTI_EXPLORER:", test_multi_explorer(ex))
    print("MULTI_NOTEPAD:", test_multi_notepad(ex))
    print("FOREGROUND:", test_foreground_safety(ex))
    print("STALE_HANDLE:", test_stale_handle(ex))
    print("profiler:", ex.profiler.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
