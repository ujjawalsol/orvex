"""Deterministic profiler: per-action spans, cross-cutting and backend-independent."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Span:
    operation: str
    backend: str
    duration_ms: float = 0.0
    cache_hit: bool | None = None
    retries: int = 0
    success: bool = True
    error_code: str | None = None
    detail: dict = field(default_factory=dict)
    _t0: float = field(default=0.0, repr=False)

    def start(self) -> "Span":
        self._t0 = time.perf_counter()
        return self

    def stop(self, success: bool = True, error_code: str | None = None) -> "Span":
        self.duration_ms = (time.perf_counter() - self._t0) * 1000.0
        self.success = success
        self.error_code = error_code
        return self


class Profiler:
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def begin(self, operation: str, backend: str) -> Span:
        s = Span(operation=operation, backend=backend).start()
        self.spans.append(s)
        return s

    def summary(self) -> dict:
        total = sum(s.duration_ms for s in self.spans)
        by_op: dict[str, float] = {}
        for s in self.spans:
            by_op[s.operation] = by_op.get(s.operation, 0.0) + s.duration_ms
        return {
            "spans": len(self.spans),
            "total_ms": round(total, 3),
            "by_operation_ms": {k: round(v, 3) for k, v in by_op.items()},
            "cache_hits": sum(1 for s in self.spans if s.cache_hit is True),
            "cache_misses": sum(1 for s in self.spans if s.cache_hit is False),
            "retries": sum(s.retries for s in self.spans),
            "failures": sum(1 for s in self.spans if not s.success),
        }
