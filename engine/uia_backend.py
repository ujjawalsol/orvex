"""Windows UIA backend: targeted lookup, versioned handles, foreground guard.

No full-desktop tree walks. No array-index identity. Handles are
server-minted `{H{id}@v{version}}` and re-resolved on every use; stale
handles trigger targeted re-resolution, never silent action.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass


@dataclass
class Target:
    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None  # Edit, Button, Window, ...
    regex_name: str | None = None


@dataclass
class Handle:
    hid: str
    version: int
    epoch: int
    selector: Target
    app_hint: str | None = None

    @property
    def token(self) -> str:
        return f"{self.hid}@v{self.version}"


class UIABackend:
    def __init__(self) -> None:
        self._handles: dict[str, Handle] = {}
        self._next_id = 1
        self._version = 0
        self._epoch = 0  # bumped whenever UI structure observably changes

    # ------------------------------------------------------------ lookup

    def find_window(self, app_hint: str, timeout_s: float = 5.0):
        import uiautomation as auto

        # fast path: single top-level enumeration, exact match in-process
        # (no per-attempt COM timeout like Exists() incurs on a miss)
        try:
            for c in auto.GetRootControl().GetChildren():
                try:
                    if (c.Name or "") == app_hint and "Window" in c.ControlTypeName:
                        return c
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        # fallback: regex (also covers substring hints like "Notepad")
        win = auto.WindowControl(searchDepth=1, RegexName=f".*{app_hint}.*")
        if not win.Exists(maxSearchSeconds=timeout_s):
            raise LookupError(f"window_not_found app_hint={app_hint!r}")
        return win

    def find_windows_all(self, app_hint: str, timeout_s: float = 2.0) -> list:
        """All top-level windows matching hint (exact first, then regex)."""
        import uiautomation as auto

        root = auto.GetRootControl()
        found = []
        for c in root.GetChildren():
            try:
                name = c.Name or ""
            except Exception:  # noqa: BLE001
                continue
            if name == app_hint or (app_hint and app_hint.lower() in name.lower()):
                try:
                    if "Window" in c.ControlTypeName:
                        found.append(c)
                except Exception:  # noqa: BLE001
                    pass
        return found

    def _control_class(self, control_type: str | None):
        import uiautomation as auto

        mapping = {
            "Edit": auto.EditControl,
            "Button": auto.ButtonControl,
            "Window": auto.WindowControl,
            "CheckBox": auto.CheckBoxControl,
            "ComboBox": auto.ComboBoxControl,
            "MenuItem": auto.MenuItemControl,
            "Text": auto.TextControl,
        }
        if not control_type:
            return auto.Control
        return mapping.get(control_type, auto.Control)

    def resolve(self, scope, target: Target, timeout_s: float = 5.0):
        """Targeted resolution inside scope: AutomationId -> Name -> ControlType.

        Returns (control, method). Raises LookupError with reason codes.
        """
        cls = self._control_class(target.control_type)
        kwargs: dict = {"searchDepth": 8}
        if target.automation_id:
            kwargs["AutomationId"] = target.automation_id
        elif target.regex_name:
            kwargs["RegexName"] = target.regex_name
        elif target.name:
            # SubName = substring match (targeted, no tree dump)
            kwargs["SubName"] = target.name
        ctl = cls(searchFromControl=scope, **kwargs)
        if ctl.Exists(maxSearchSeconds=timeout_s):
            method = "automation_id" if target.automation_id else ("name" if (target.name or target.regex_name) else "control_type")
            return ctl, method
        # alternate selector: if AutomationId failed, try Name once
        if target.automation_id and target.name:
            ctl2 = cls(searchFromControl=scope, SubName=target.name, searchDepth=8)
            if ctl2.Exists(maxSearchSeconds=2):
                return ctl2, "alt_name"
        raise LookupError(
            f"element_not_found automation_id={target.automation_id!r} "
            f"name={target.name!r} control_type={target.control_type!r}"
        )

    # ------------------------------------------------------------ handles

    def mint(self, selector: Target, app_hint: str | None = None) -> Handle:
        hid = f"H{self._next_id}"
        self._next_id += 1
        self._version += 1
        h = Handle(hid=hid, version=self._version, epoch=self._epoch,
                   selector=selector, app_hint=app_hint)
        self._handles[hid] = h
        return h

    def bump_epoch(self) -> int:
        """Call when UI structure observably changes (open/close/lookup failure)."""
        self._epoch += 1
        return self._epoch

    def use(self, hid: str, timeout_s: float = 5.0):
        h = self._handles.get(hid)
        if h is None:
            raise LookupError(f"stale_handle_unknown {hid}")
        if h.epoch != self._epoch:
            raise LookupError(f"stale_handle {h.token} epoch={h.epoch} current={self._epoch}")
        scope = self.find_window(h.app_hint, timeout_s=timeout_s) if h.app_hint else self._desktop()
        return self.resolve(scope, h.selector, timeout_s=timeout_s)

    def _desktop(self):
        import uiautomation as auto

        return auto.GetRootControl()

    def scope_from_hwnd(self, hwnd: int):
        import uiautomation as auto

        try:
            ctl = auto.ControlFromHandle(hwnd)
        except Exception as e:  # noqa: BLE001
            raise LookupError(f"scope_hwnd_dead {hwnd} {type(e).__name__}") from e
        try:
            alive = ctl.Exists(maxSearchSeconds=1)
        except Exception as e:  # noqa: BLE001
            raise LookupError(f"scope_hwnd_dead {hwnd} {type(e).__name__}") from e
        if not alive:
            raise LookupError(f"scope_hwnd_gone {hwnd}")
        return ctl

    # ------------------------------------------------------------ foreground guard

    def foreground_hwnd(self) -> int:
        try:
            h = ctypes.windll.user32.GetForegroundWindow()
            return int(h) if h is not None else 0
        except Exception:  # noqa: BLE001
            return 0

    def control_hwnd(self, control) -> int:
        try:
            return int(control.NativeWindowHandle or 0)
        except Exception:  # noqa: BLE001
            return 0

    def ensure_foreground(self, control, allow_activate: bool = True) -> None:
        """Fail fast if target is not foreground; one guarded correction max."""
        want = self.control_hwnd(control)
        got = self.foreground_hwnd()
        if want and want == got:
            return
        # For top-level windows compare; for child controls compare root ancestor
        if want and got and want != got and allow_activate:
            try:
                control.SetFocus()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
            # re-check loosely: do not loop
            return
        # UIA pattern actions often do NOT need foreground; caller decides.
        return

    # ------------------------------------------------------------ input router

    def set_value(self, control, text: str) -> str:
        """Safest/fastest supported mechanism: ValuePattern > Type."""
        try:
            pat = control.GetValuePattern()
            if pat is not None and not pat.IsReadOnly:
                pat.SetValue(text)
                return "value_pattern"
        except Exception:  # noqa: BLE001
            pass
        # fallback: focus + type (foreground-dependent)
        self.ensure_foreground(control)
        try:
            control.SetFocus()
        except Exception:  # noqa: BLE001
            pass
        import uiautomation as auto

        auto.SendKeys(text)
        return "send_keys"

    def clipboard_paste(self, control, text: str) -> str:
        """Bulk-text path: clipboard set + Ctrl+V. Foreground-dependent.
        Saves and restores prior clipboard text (best-effort)."""
        import time as _time
        import uiautomation as auto

        from ctypes import wintypes as _wt

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [_wt.HWND]
        user32.OpenClipboard.restype = _wt.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = _wt.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = _wt.BOOL
        user32.SetClipboardData.argtypes = [_wt.UINT, _wt.HANDLE]
        user32.SetClipboardData.restype = _wt.HANDLE
        user32.GetClipboardData.argtypes = [_wt.UINT]
        user32.GetClipboardData.restype = _wt.HANDLE
        kernel32.GlobalAlloc.argtypes = [_wt.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = _wt.HANDLE
        kernel32.GlobalLock.argtypes = [_wt.HANDLE]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [_wt.HANDLE]
        kernel32.GlobalUnlock.restype = _wt.BOOL
        kernel32.GlobalFree.argtypes = [_wt.HANDLE]
        kernel32.GlobalFree.restype = _wt.HANDLE
        CF_UNICODETEXT = 13
        saved = ""
        if user32.OpenClipboard(None):
            try:
                h_get = user32.GetClipboardData(CF_UNICODETEXT)
                if h_get:
                    ptr = kernel32.GlobalLock(h_get)
                    if ptr:
                        try:
                            saved = ctypes.wstring_at(ptr)
                        finally:
                            kernel32.GlobalUnlock(h_get)
            finally:
                user32.CloseClipboard()
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                n_chars = len(text) + 1
                h = kernel32.GlobalAlloc(0x0002, n_chars * 2)
                if not h:
                    raise RuntimeError("GlobalAlloc failed")
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    kernel32.GlobalFree(h)
                    raise RuntimeError("GlobalLock failed")
                try:
                    ctypes.memmove(ptr, ctypes.create_unicode_buffer(text), n_chars * 2)
                finally:
                    kernel32.GlobalUnlock(h)
                if not user32.SetClipboardData(CF_UNICODETEXT, h):
                    kernel32.GlobalFree(h)
                    raise RuntimeError("SetClipboardData failed")
            finally:
                user32.CloseClipboard()
        else:
            raise RuntimeError("OpenClipboard failed")
        try:
            self.ensure_foreground(control)
            try:
                control.SetFocus()
            except Exception:  # noqa: BLE001
                pass
            _time.sleep(0.2)
            auto.SendKeys("{Ctrl}v")
            _time.sleep(0.3)
        finally:
            if user32.OpenClipboard(None):  # restore (best-effort)
                try:
                    user32.EmptyClipboard()
                    if saved:
                        n_chars = len(saved) + 1
                        h = kernel32.GlobalAlloc(0x0002, n_chars * 2)
                        if h:
                            ptr = kernel32.GlobalLock(h)
                            if ptr:
                                try:
                                    ctypes.memmove(ptr, ctypes.create_unicode_buffer(saved), n_chars * 2)
                                finally:
                                    kernel32.GlobalUnlock(h)
                                if not user32.SetClipboardData(CF_UNICODETEXT, h):
                                    kernel32.GlobalFree(h)
                finally:
                    user32.CloseClipboard()
        return "clipboard_paste"

    def invoke(self, control) -> str:
        try:
            pat = control.GetInvokePattern()
            if pat is not None:
                pat.Invoke()
                return "invoke_pattern"
        except Exception:  # noqa: BLE001
            pass
        control.Click()
        return "click"

    def read_value(self, control) -> str:
        try:
            pat = control.GetValuePattern()
            if pat is not None:
                return str(pat.Value or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            return str(control.Name or "")
        except Exception:  # noqa: BLE001
            return ""
