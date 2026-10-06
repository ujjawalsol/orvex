"""STA vs MTA micro-benchmark (raw comtypes IUIAutomation, no wrappers).

A = single STA thread (COINIT_APARTMENTTHREADED), sequential lookups.
B = MTA: main CoInitializeEx(MULTITHREADED) + 4 worker threads, same op.
Op per iteration: ElementFromHandle(notepad_hwnd) + GetCurrentPropertyValue(Name).
50 iters each. Measures: median/p95 latency, throughput, failures.
Event-handler threading NOT tested here (documented gap).
ONE Notepad, closed clean (no text -> no dialog). Health-gated.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

COINIT_APARTMENTTHREADED = 0x2
COINIT_MULTITHREADED = 0x0
CLSCTX_INPROC_SERVER = 0x1
UIA_NamePropertyId = 30005
N = 50


def _notepad_hwnd() -> int:
    import ctypes

    for _ in range(20):
        hwnd = ctypes.windll.user32.FindWindowW("Notepad", None)
        if hwnd:
            return int(hwnd)
        time.sleep(0.5)
    raise RuntimeError("notepad not found")


def _uia():
    import comtypes.client as _cc

    try:
        from comtypes.gen import UIAutomationClient as _uia_mod
    except ImportError:
        _cc.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as _uia_mod
    return _cc.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}",
                            clsctx=CLSCTX_INPROC_SERVER,
                            interface=_uia_mod.IUIAutomation)


def worker_sta(hwnd: int, out: list) -> None:
    import comtypes

    comtypes.CoInitializeEx(COINIT_APARTMENTTHREADED)
    try:
        uia = _uia()
        for _ in range(N):
            t0 = time.perf_counter()
            try:
                el = uia.ElementFromHandle(hwnd)
                name = el.GetCurrentPropertyValue(UIA_NamePropertyId)
                out.append(((time.perf_counter() - t0) * 1000.0, True, str(name)[:20]))
            except Exception as e:  # noqa: BLE001
                out.append(((time.perf_counter() - t0) * 1000.0, False, str(e)[:80]))
    finally:
        try:
            import comtypes as _ct

            _ct.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def worker_mta(hwnd: int, out: list, lock: threading.Lock) -> None:
    import comtypes

    comtypes.CoInitializeEx(COINIT_MULTITHREADED)
    try:
        uia = _uia()
        local: list = []
        for _ in range(N // 4):
            t0 = time.perf_counter()
            try:
                el = uia.ElementFromHandle(hwnd)
                name = el.GetCurrentPropertyValue(UIA_NamePropertyId)
                local.append(((time.perf_counter() - t0) * 1000.0, True, str(name)[:20]))
            except Exception as e:  # noqa: BLE001
                local.append(((time.perf_counter() - t0) * 1000.0, False, str(e)[:80]))
        with lock:
            out.extend(local)
    finally:
        try:
            import comtypes as _ct

            _ct.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def worker_sta_input(out: list) -> None:
    """Dedicated STA thread for SendInput (message-pump apartment model)."""
    import ctypes
    from ctypes import wintypes
    import comtypes

    comtypes.CoInitializeEx(COINIT_APARTMENTTHREADED)
    try:
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

        for _ in range(12):
            t0 = time.perf_counter()
            try:
                inp = INPUT(0, MOUSEINPUT(0, 0, 0, 0x0001, 0, None))
                n = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
                out.append(((time.perf_counter() - t0) * 1000.0, int(n) == 1, ""))
            except Exception as e:  # noqa: BLE001
                out.append(((time.perf_counter() - t0) * 1000.0, False, str(e)[:80]))
    finally:
        try:
            import comtypes as _ct

            _ct.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    from engine.safety import SafetyPolicy

    pre = SafetyPolicy.shell_health()
    proc = subprocess.Popen(["notepad.exe"])
    try:
        hwnd = _notepad_hwnd()
        res: dict[str, list] = {}
        t0 = time.perf_counter()
        out: list = []
        th = threading.Thread(target=worker_sta, args=(hwnd, out))
        th.start()
        th.join(timeout=120)
        res["A_single_STA"] = {"wall_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                               "samples": out, "timeout": th.is_alive()}
        t0 = time.perf_counter()
        out2: list = []
        lock = threading.Lock()
        ths = [threading.Thread(target=worker_mta, args=(hwnd, out2, lock)) for _ in range(4)]
        [t.start() for t in ths]
        [t.join(timeout=120) for t in ths]
        res["B_mta_4workers"] = {"wall_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                                 "samples": out2,
                                 "timeout": any(t.is_alive() for t in ths)}
        # C = MTA query workers + dedicated STA input thread (SendInput null-move)
        t0 = time.perf_counter()
        out3: list = []
        ths3 = [threading.Thread(target=worker_mta, args=(hwnd, out3, lock)) for _ in range(2)]
        inp: list = []
        th_in = threading.Thread(target=worker_sta_input, args=(inp,))
        [t.start() for t in ths3]
        th_in.start()
        [t.join(timeout=120) for t in ths3]
        th_in.join(timeout=120)
        res["C_mta_query_sta_input"] = {
            "wall_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "samples": out3, "input": inp,
            "timeout": any(t.is_alive() for t in ths3) or th_in.is_alive()}

        def summ(v):
            ok = [x[0] for x in v if x[1]]
            bad = len(v) - len(ok)
            s = sorted(ok)
            n = len(s)
            return {"n": len(v), "failures": bad,
                    "median_ms": round(s[n // 2], 3) if n else 0,
                    "p95_ms": round(s[min(n - 1, int(n * 0.95))], 3) if n else 0}

        for k, v in res.items():
            extra = ""
            if "input" in v:
                ok_in = [x[0] for x in v["input"] if x[1]]
                extra = f" input_n={len(v['input'])} input_med={round(sorted(ok_in)[len(ok_in)//2], 3) if ok_in else 0}"
            print(k, "wall:", v["wall_ms"], "timeout:", v["timeout"], summ(v["samples"]), extra)
            errs = {x[2] for x in v["samples"] if not x[1]}
            if errs:
                print("  errors:", list(errs)[:3])
    finally:
        import uiautomation as auto

        try:
            w = auto.WindowControl(searchDepth=1, RegexName=".*Notepad.*")
            if w.Exists(2):
                w.GetWindowPattern().Close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
    print("health changes:", SafetyPolicy.health_diff(pre, SafetyPolicy.shell_health()) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
