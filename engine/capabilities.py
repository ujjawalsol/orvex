"""Capability registry: static knowledge + runtime detection + health.

Answers per target: which mechanisms exist, which is preferred, what to
fall back to. Costs are priors until bench/router_bench.py measures them
(fields marked measured=False must not drive conclusions).
AI never sees this; the router consumes it internally.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MechanismCost:
    median_ms: float = 0.0
    p95_ms: float = 0.0
    success_rate: float = 0.0
    measured: bool = False


@dataclass
class AppCapability:
    app: str
    protocols: list[str]
    preferred: str
    fallbacks: list[str] = field(default_factory=list)
    notes: str = ""


STATIC_REGISTRY: dict[str, AppCapability] = {
    "notepad": AppCapability("notepad", ["uia-value", "uia-invoke", "clipboard", "sendkeys"],
                             "uia-value", ["clipboard", "sendkeys"]),
    "explorer": AppCapability("explorer", ["uia", "cli-path"], "cli-path", ["uia"],
                              notes="folder windows live under shell PID by design"),
    "settings": AppCapability("settings", ["uri-activate", "uia"], "uri-activate", ["uia"]),
    "chrome": AppCapability("chrome", ["cdp", "uia"], "cdp", ["uia"]),
    "edge": AppCapability("edge", ["cdp", "uia"], "cdp", ["uia"]),
    "terminal": AppCapability("terminal", ["pty"], "pty", []),
    "git": AppCapability("git", ["cli"], "cli", []),
    "generic-win32": AppCapability("generic-win32", ["uia-patterns", "postmessage", "sendkeys"],
                                   "uia-patterns", ["postmessage", "sendkeys"]),
    "canvas-game": AppCapability("canvas-game", ["vision"], "vision", [],
                                 notes="vision backend NOT BUILT — reported unavailable"),
}

# Prior order per op (benchmarkable via costs override in router; defaults
# encode safest-first, not fastest-first).
PRIOR_ORDER: dict[str, list[str]] = {
    "set_value": ["value_pattern", "clipboard_paste", "send_keys"],
    "invoke": ["invoke_pattern", "uia_click"],
    "read": ["value_pattern", "name_property"],
    "select": ["selection_pattern", "uia_click"],
    "toggle": ["toggle_pattern", "uia_click"],
    "expand": ["expand_collapse_pattern", "uia_click"],
    "browse_extract": ["cdp"],
    "browse_navigate": ["cdp"],
}


def probe_control_patterns(control) -> dict[str, bool]:
    """Runtime detection: which UIA patterns does this control actually support?"""
    caps: dict[str, bool] = {}
    for name, meth in (("value_pattern", "GetValuePattern"),
                       ("invoke_pattern", "GetInvokePattern"),
                       ("toggle_pattern", "GetTogglePattern"),
                       ("selection_item_pattern", "GetSelectionItemPattern"),
                       ("expand_collapse_pattern", "GetExpandCollapsePattern")):
        try:
            caps[name] = getattr(control, meth)() is not None
        except Exception:  # noqa: BLE001
            caps[name] = False
    return caps


def probe_browser_targets(timeout_s: float = 2.0) -> dict:
    """Runtime detection: debuggable browser endpoints (never assumes :9222)."""
    from .browser import discover_ports, list_targets

    found: list[dict] = []
    for port in discover_ports():
        try:
            for t in list_targets(port, timeout_s=timeout_s):
                if t.get("type") == "page":
                    found.append({"port": port, "title": t.get("title"),
                                  "url": t.get("url"), "has_ws": bool(t.get("webSocketDebuggerUrl"))})
        except Exception:  # noqa: BLE001
            continue
    return {"page_targets": found, "cdp_available": bool(found)}


def app_capability(app_hint: str) -> AppCapability:
    key = (app_hint or "").strip().lower()
    if "chrome" in key:
        return STATIC_REGISTRY["chrome"]
    if "edge" in key:
        return STATIC_REGISTRY["edge"]
    if "notepad" in key:
        return STATIC_REGISTRY["notepad"]
    if "explorer" in key:
        return STATIC_REGISTRY["explorer"]
    if "setting" in key:
        return STATIC_REGISTRY["settings"]
    return STATIC_REGISTRY["generic-win32"]


COSTS: dict[str, dict[str, MechanismCost]] = {
    op: {m: MechanismCost() for m in mechs} for op, mechs in PRIOR_ORDER.items()
}


def record_costs(op: str, costs: dict[str, MechanismCost]) -> None:
    for m, c in costs.items():
        COSTS.setdefault(op, {})[m] = c
