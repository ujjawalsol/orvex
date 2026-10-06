"""Playwright backend — ARCHIVED HISTORICAL BENCHMARK ONLY.

This file is preserved solely as historical benchmark evidence for why
CDP was chosen for production ORVEX. It is NOT part of the production runtime,
is NOT imported by ORVEX, and Playwright is NOT an ORVEX dependency.
"""
from __future__ import annotations

import time


class PWSession:
    def __init__(self) -> None:
        from playwright.sync_api import sync_playwright

        t0 = time.perf_counter()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(channel="chrome", headless=True)
        self.launch_ms = (time.perf_counter() - t0) * 1000.0
        self._page = self._browser.new_page()

    def navigate(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded", timeout=20000)

    def wait_for_url(self, needle: str, timeout_s: float = 15.0) -> str:
        # goto() already waited; return immediately if already there
        if needle in self._page.url:
            return self._page.url
        self._page.wait_for_url(f"*{needle}*", timeout=int(timeout_s * 1000))
        return self._page.url

    def wait_for_selector(self, css: str, timeout_s: float = 15.0) -> bool:
        self._page.wait_for_selector(css, timeout=int(timeout_s * 1000))
        return True

    def extract_text(self, css: str = "body") -> str:
        el = self._page.query_selector(css) or self._page.query_selector("body")
        return (el.inner_text() if el else "")[:4000]

    def extract_table(self, css: str = "table") -> list[list[str]]:
        rows = self._page.query_selector_all(f"{css} tr")
        out: list[list[str]] = []
        for r in rows:
            cells = r.query_selector_all("th,td")
            out.append([(c.inner_text() or "").strip() for c in cells])
        return out

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
