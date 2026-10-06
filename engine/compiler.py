"""Deterministic intent compiler: semantic intent -> internal execution DAG.

AI sends: verb, app_hint, target, params, constraints, optional steps[],
idempotency_key. AI NEVER sends: HWND, COM/STA, CacheRequest, CDP, coords,
timeouts, retries, backend choice. Engine owns all of those.

Closed verb set (MVP): open_app, inspect, find, invoke, set_value, type,
press, wait, verify. Compiler expands each into DAG nodes with verify steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VERBS = ("open_app", "inspect", "find", "invoke", "set_value", "type", "press", "wait", "verify", "run", "close_window",
         "browser_open", "browser_navigate", "browser_extract", "browser_close",
         "resume")


@dataclass
class Node:
    id: str
    op: str
    target: dict[str, Any] | None = None
    params: dict[str, Any] = field(default_factory=dict)
    verify: dict[str, Any] | None = None
    approval: str = "none"  # none|sensitive|dangerous


@dataclass
class Graph:
    goal: str
    nodes: list[Node] = field(default_factory=list)


def compile_intent(intent: dict[str, Any]) -> Graph:
    verb = intent.get("verb")
    if verb not in VERBS:
        raise ValueError(f"unknown_verb {verb!r}; allowed={VERBS}")
    app_hint = intent.get("app_hint")
    target = intent.get("target") or {}
    params = intent.get("params") or {}
    steps = intent.get("steps") or []

    if steps:
        nodes: list[Node] = []
        for i, s in enumerate(steps):
            sv = s.get("verb")
            if sv not in VERBS:
                raise ValueError(f"unknown_verb_in_steps {sv!r}")
            sparams = dict(s.get("params") or {})
            sparams.setdefault("app_hint", s.get("app_hint") or app_hint or "")
            nodes.append(
                Node(
                    id=f"s{i + 1}",
                    op=sv,
                    target=s.get("target"),
                    params=sparams,
                )
            )
        return Graph(goal=f"multi:{verb}", nodes=nodes)

    if verb == "open_app":
        if not app_hint and not params.get("path"):
            raise ValueError("open_app requires app_hint or params.path")
        # Single node: executor performs launch + PID-verified attach + verify
        # atomically (a 3-node expansion would re-resolve by title and risk
        # adopting a stale same-titled window).
        p = dict(params)
        if app_hint:
            p.setdefault("app_hint", app_hint)
        return Graph(goal="open_app", nodes=[Node(id="s1", op="open_app", params=p)])
    if verb == "set_value":
        return Graph(
            goal="set_value",
            nodes=[
                Node(id="s1", op="find", target=target, params={"app_hint": app_hint or ""}),
                Node(id="s2", op="set_value", target=target, params={"text": params.get("text", ""), "app_hint": app_hint or ""},
                     verify={"value_equals": params.get("text", "")}),
            ],
        )
    if verb == "invoke":
        return Graph(
            goal="invoke",
            nodes=[
                Node(id="s1", op="find", target=target, params={"app_hint": app_hint or ""}),
                Node(id="s2", op="invoke", target=target, params={"app_hint": app_hint or ""}),
            ],
        )
    if verb == "type":
        return Graph(
            goal="type",
            nodes=[
                Node(id="s1", op="find", target=target, params={"app_hint": app_hint or ""}),
                Node(id="s2", op="type", target=target, params={"text": params.get("text", ""), "app_hint": app_hint or ""}),
            ],
        )
    if verb == "press":
        p = dict(params)
        if app_hint:
            p.setdefault("app_hint", app_hint)
        return Graph(goal="press", nodes=[Node(id="s1", op="press", params=p)])
    if verb == "inspect":
        return Graph(goal="inspect", nodes=[Node(id="s1", op="inspect", target=target, params={"app_hint": app_hint or ""})])
    if verb == "find":
        return Graph(goal="find", nodes=[Node(id="s1", op="find", target=target, params={"app_hint": app_hint or ""})])
    if verb == "wait":
        return Graph(goal="wait", nodes=[Node(id="s1", op="wait", target=target, params=params)])
    if verb == "verify":
        return Graph(goal="verify", nodes=[Node(id="s1", op="verify", target=target, params=params)])
    if verb == "close_window":
        p = dict(params)
        if app_hint:
            p.setdefault("app_hint", app_hint)
        return Graph(goal="close_window", nodes=[Node(id="s1", op="close_window", target=target, params=p)])
    if verb in ("browser_open", "browser_navigate", "browser_extract", "browser_close"):
        p = dict(params)
        return Graph(goal=verb, nodes=[Node(id="s1", op=verb, target=target, params=p)])
    if verb == "resume":
        return Graph(goal="resume", nodes=[Node(id="s1", op="resume", params=dict(params))])
    raise ValueError(f"unhandled verb {verb}")
