"""Browser micro-slice test: isolated Chrome (fresh profile, port 9333).

attach -> navigate example.com -> wait_for_url -> wait_for_selector(h1)
-> extract_text -> semantic result. 0 screenshots, 0 vision.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.browser import CDP, discover_page_target, list_targets  # noqa: E402


def main() -> int:
    profile = tempfile.mkdtemp(prefix="sfmcp-chrome-")
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    proc = subprocess.Popen([
        chrome, "--remote-debugging-port=9333",
        f"--user-data-dir={profile}", "--no-first-run",
        "--headless=new", "about:blank",
    ])
    try:
        port, target = None, None
        t0 = time.time()
        while time.time() - t0 < 20:
            try:
                port, target = discover_page_target([9333])
                break
            except LookupError:
                time.sleep(0.5)
        assert target, "no debuggable target in 20s"
        print(f"attached port={port} title={target.get('title')!r} url={target.get('url')!r}")
        cdp = CDP(target["webSocketDebuggerUrl"])
        t1 = time.time()
        cdp.navigate("https://example.com")
        url = cdp.wait_for_url("example.com", timeout_s=20)
        cdp.wait_for_selector("p", timeout_s=15)
        text = cdp.extract_text("body")
        dt = (time.time() - t1) * 1000
        print(f"url={url} text_head={text[:80]!r} nav_extract_ms={dt:.0f}")
        assert "example" in text.lower(), f"unexpected body {text[:120]!r}"
        print("BROWSER_MICRO: PASS (0 screenshots, 0 vision)")
        cdp.close()
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
