"""Security policy: levels, blocklist, approval gates, emergency stop.

Levels: READ < LOW < SENSITIVE < DANGEROUS.
MVP: READ/LOW automatic, SENSITIVE/DANGEROUS require approval.
Protected apps are denyable via config + env; no tool path can remove them.
ESC stop runs on an independent low-level keyboard hook; AI cannot disable it.
"""
from __future__ import annotations

import ctypes
import os
import re
import threading
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import IntEnum


class Level(IntEnum):
    READ = 0
    LOW = 1
    SENSITIVE = 2
    DANGEROUS = 3


@dataclass
class Policy:
    blocklist: list[str] = field(default_factory=list)  # regexes on exe/title/class
    allow_elevated: bool = False

    @staticmethod
    def load() -> "Policy":
        raw = (os.environ.get("ORVEX_BLOCKLIST")
               or os.environ.get("SFMCP_BLOCKLIST", ""))
        items = [p.strip() for p in raw.split(";") if p.strip()]
        # default protected examples (deny control/screenshot/ocr/vision of
        # credential stores — these are never approvable)
        defaults = [
            r"(?i)keepass",
            r"(?i)1password",
            r"(?i)bitwarden",
            r"(?i)lastpass",
            r"(?i)dashlane",
        ]
        return Policy(blocklist=defaults + items)

    def is_protected(self, ident: str) -> bool:
        return any(re.search(pat, ident or "") for pat in self.blocklist)


def level_for_verb(verb: str) -> Level:
    if verb in ("inspect", "verify", "wait"):
        return Level.READ
    if verb in ("open_app", "find", "invoke", "set_value", "type", "press", "run", "close_window",
                "browser_open", "browser_navigate", "browser_extract", "browser_close",
                "resume"):
        return Level.LOW
    if verb in ("save_file", "submit_form"):
        return Level.SENSITIVE
    return Level.DANGEROUS


class EmergencyStop:
    """Global ESC stop on independent thread. Cannot be disabled via tools."""

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        self._cancel.clear()

    def start_hook(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._hook_loop, daemon=True)
        self._thread.start()

    def _hook_loop(self) -> None:
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            WH_KEYBOARD_LL = 13
            WM_KEYDOWN = 0x0100
            VK_ESCAPE = 0x1B

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", wintypes.DWORD),
                    ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p),
                ]

            cb_type = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
                ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
            )

            def _proc(n_code: int, w_param: int, l_param: int) -> int:
                if n_code >= 0 and w_param == WM_KEYDOWN:
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if kb.vkCode == VK_ESCAPE:
                        self._cancel.set()
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            cb = cb_type(_proc)
            hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, kernel32.GetModuleHandleW(None), 0)
            if not hook:
                return
            msg = wintypes.MSG()
            while not self._cancel.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            user32.UnhookWindowsHookEx(hook)
        except Exception:
            return  # hook is best-effort; executor also polls console fallback
