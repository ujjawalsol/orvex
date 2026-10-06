"""Browser session model: explicit engine-owned reusable sessions.

Handle form: BS_<n>@v<version>. AI sees ONLY the handle (never PID, profile
path, websocket URL). Reuse validates process + target + version; stale
sessions re-attach once, else needs_ai. Idle sessions evicted after timeout.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


@dataclass
class BrowserSession:
    sid: str
    version: int
    backend: str
    pid: int | None
    port: int | None
    profile: str | None
    ws_url: str | None = None
    session_obj: object = None
    proc: object = None
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    owner_task: str = ""
    use_count: int = 0

    @property
    def handle(self) -> str:
        return f"{self.sid}@v{self.version}"


class SessionStore:
    def __init__(self, idle_timeout_s: float = 180.0, max_sessions: int = 4,
                 max_age_s: float = 900.0) -> None:
        self.sessions: dict[str, BrowserSession] = {}
        self._seq = 0
        self.idle_timeout_s = idle_timeout_s
        self.max_sessions = max_sessions
        self.max_age_s = max_age_s
        self.evictions = 0
        self.stale_hits = 0

    def create(self, backend: str, **kw) -> BrowserSession:
        import time as _t

        self.evict_idle()
        # max age sweep
        now = _t.time()
        for sid in [s for s, v in self.sessions.items() if now - v.created_at > self.max_age_s]:
            self.close(sid)
            self.evictions += 1
        # cap: evict least-recently-used (never exceed max_sessions)
        while len(self.sessions) >= self.max_sessions:
            oldest = min(self.sessions.values(), key=lambda s: s.last_used)
            self.close(oldest.sid)
            self.evictions += 1
        self._seq += 1
        s = BrowserSession(sid=f"BS_{self._seq}", version=1, backend=backend, **kw)
        self.sessions[s.sid] = s
        return s

    def get(self, handle: str) -> BrowserSession | None:
        sid = handle.split("@")[0]
        return self.sessions.get(sid)

    def validate(self, handle: str) -> tuple[bool, str]:
        """Process + target + version + max-age checks. Returns (ok, reason)."""
        import time as _t

        s = self.get(handle)
        if s is None:
            return False, "session_unknown"
        if _t.time() - s.created_at > self.max_age_s:
            return False, "session_stale"
        want_ver = handle.split("@v")[1] if "@v" in handle else None
        if want_ver is not None and want_ver != str(s.version):
            self.stale_hits += 1
            return False, "session_stale"
        # process alive?
        if s.pid:
            try:
                import ctypes as _ct

                h = _ct.windll.kernel32.OpenProcess(0x1000, False, s.pid)
                if not h:
                    return False, "session_stale"
                _ct.windll.kernel32.CloseHandle(h)
            except Exception:  # noqa: BLE001
                return False, "session_stale"
        # target reachable? (CDP sessions validated via target list probe)
        if s.backend == "cdp" and s.port:
            try:
                from .browser import list_targets

                targets = list_targets(s.port, timeout_s=2.0)
                if not any(t.get("type") == "page" for t in targets):
                    return False, "session_stale"
            except Exception:  # noqa: BLE001
                return False, "session_stale"
        # profile still inside temp (never follow a moved profile)
        if s.profile:
            canon = os.path.normcase(os.path.abspath(s.profile))
            import tempfile as _tf

            tmp = os.path.normcase(os.path.abspath(_tf.gettempdir()))
            if not canon.startswith(tmp + os.sep):
                return False, "session_stale"
        return True, "ok"

    def touch(self, s: BrowserSession) -> None:
        s.last_used = time.time()
        s.use_count += 1

    def bump(self, s: BrowserSession) -> str:
        s.version += 1
        return s.handle

    def evict_idle(self) -> list[str]:
        now = time.time()
        dead = [sid for sid, s in self.sessions.items() if now - s.last_used > self.idle_timeout_s]
        for sid in dead:
            self.close(sid)
            self.evictions += 1
        return dead

    def close(self, sid_or_handle: str) -> bool:
        sid = sid_or_handle.split("@")[0]
        s = self.sessions.pop(sid, None)
        if s is None:
            return False
        try:
            if s.session_obj is not None:
                s.session_obj.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if s.proc is not None:
                s.proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        if s.profile:
            import shutil as _sh

            try:
                _sh.rmtree(s.profile, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
        return True

    def stats(self) -> dict:
        return {"live": len(self.sessions), "evictions": self.evictions,
                "stale_hits": self.stale_hits}
