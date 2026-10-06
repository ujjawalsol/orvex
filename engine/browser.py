"""Browser micro slice (Phase 1.5 §14 ONLY): attach/navigate/wait/query/extract.

Robust discovery (never assumes :9222): scans DevToolsActivePort files +
candidate ports, lists /json targets, attaches to a page target over CDP.
Sync implementation on top of `websockets.sync.client`. No vision/OCR fallback.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request


def find_chromium_browser() -> tuple[str, str]:
    """Locate an existing installed Chromium browser (Google Chrome or Microsoft Edge).

    Returns (browser_name, executable_path).
    Raises LookupError if neither browser is found.
    Does NOT download or bundle any browser binaries.
    """
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in chrome_paths:
        if os.path.exists(p):
            return "chrome", p

    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in edge_paths:
        if os.path.exists(p):
            return "edge", p

    raise LookupError(
        "unsupported_browser: Neither Google Chrome nor Microsoft Edge was found on this system. "
        "ORVEX uses the user's existing installed browser via CDP. "
        "Please ensure Google Chrome or Microsoft Edge is installed."
    )


def discover_ports() -> list[int]:
    ports = [9222, 9333]
    home = os.path.expanduser("~")
    for rel in (
        r"AppData\Local\Google\Chrome\User Data\DevToolsActivePort",
        r"AppData\Local\Microsoft\Edge\User Data\DevToolsActivePort",
    ):
        p = os.path.join(home, rel)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    lines = f.read().split()
                if len(lines) >= 2:
                    ports.append(int(lines[1]))
            except Exception:  # noqa: BLE001
                pass
    return sorted(set(ports))


def list_targets(port: int, timeout_s: float = 3.0) -> list[dict]:
    url = f"http://127.0.0.1:{port}/json"
    with urllib.request.urlopen(url, timeout=timeout_s) as r:
        return json.loads(r.read().decode())


def get_running_browsers() -> list[str]:
    """Return names of Chromium browsers currently running on the system (e.g. ['chrome', 'edge'])."""
    import psutil
    found = set()
    for p in psutil.process_iter(["name"]):
        try:
            name = (p.info.get("name") or "").lower()
            if "chrome" in name:
                found.add("chrome")
            elif "msedge" in name or "edge" in name:
                found.add("edge")
        except Exception:  # noqa: BLE001
            continue
    return sorted(found)


def discover_all_page_targets(ports: list[int] | None = None) -> list[tuple[int, dict]]:
    """Return all debuggable page targets across candidate CDP ports."""
    targets: list[tuple[int, dict]] = []
    for port in ports or discover_ports():
        try:
            for t in list_targets(port):
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    targets.append((port, t))
        except Exception:  # noqa: BLE001
            continue
    return targets


def discover_page_target(
    ports: list[int] | None = None,
    url_filter: str = "",
    title_filter: str = "",
) -> tuple[int, dict]:
    """Find an eligible debuggable page target, optionally filtered by url or title substring."""
    all_targets = discover_all_page_targets(ports)
    if not all_targets:
        raise LookupError("no debuggable page target found on candidate CDP ports")
    if url_filter or title_filter:
        for port, t in all_targets:
            u = t.get("url", "")
            ti = t.get("title", "")
            if url_filter and url_filter.lower() in u.lower():
                return port, t
            if title_filter and title_filter.lower() in ti.lower():
                return port, t
        raise LookupError(
            f"no page target matching url={url_filter!r} title={title_filter!r} "
            f"among {len(all_targets)} open targets"
        )
    return all_targets[0]


class CDP:
    """Minimal sync CDP session: navigate/wait/query/extract."""

    def __init__(self, ws_url: str) -> None:
        from websockets.sync.client import connect

        self._ws = connect(ws_url, max_size=10 * 1024 * 1024)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout_s: float = 15.0) -> dict:
        from websockets.exceptions import ConnectionClosed

        self._id += 1
        mid = self._id
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        t0 = time.time()
        try:
            while time.time() - t0 < timeout_s:
                msg = json.loads(self._ws.recv(timeout=timeout_s))
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"cdp_error {method}: {msg['error']}")
                    return msg.get("result", {})
        except ConnectionClosed as e:
            raise RuntimeError(f"cdp_closed {method}: {e}") from e
        raise TimeoutError(f"cdp_timeout {method}")

    def navigate(self, url: str) -> None:
        self.call("Page.enable")
        self.call("Page.navigate", {"url": url})

    def wait_for_url(self, needle: str, timeout_s: float = 15.0) -> str:
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            info = self.call("Target.getTargetInfo")
            url = (info.get("targetInfo") or {}).get("url", "")
            if needle in url:
                return url
            time.sleep(0.4)
        raise TimeoutError(f"wait_for_url_timeout {needle!r}")

    def evaluate(self, js: str) -> object:
        r = self.call("Runtime.evaluate", {"expression": js, "returnByValue": True})
        # call() unwraps to {"result": {type, value}} — single nesting
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(f"js_error {res.get('description')}")
        return res.get("value")

    def wait_for_selector(self, css: str, timeout_s: float = 15.0) -> bool:
        t0 = time.time()
        js = f"!!document.querySelector({css!r})"
        while time.time() - t0 < timeout_s:
            if self.evaluate(js):
                return True
            time.sleep(0.4)
        raise TimeoutError(f"wait_for_selector_timeout {css!r}")

    def extract_text(self, css: str = "body") -> str:
        return str(self.evaluate(
            f"(document.querySelector({css!r})||document.body).innerText||''"))[:4000]

    def extract_table(self, css: str = "table") -> list[list[str]]:
        js = (
            f"Array.from(document.querySelectorAll({css!r} + ' tr')).map("
            "tr => Array.from(tr.querySelectorAll('th,td')).map(c => (c.innerText||'').trim()))"
        )
        val = self.evaluate(js)
        return [list(r) for r in val] if isinstance(val, list) else []

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass
