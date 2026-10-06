"""Phase 0 benchmark harness: statistics + JSONL recording.

Every measurement records: timestamp, duration_ms, success, backend,
operation, cache_hit/miss, retry_count, error_code. Summaries use
median / p95 / p99 (never mean-only).
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class Sample:
    timestamp: str
    operation: str
    backend: str
    duration_ms: float
    success: bool
    cache_hit: bool | None = None
    retry_count: int = 0
    error_code: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(samples: list[Sample]) -> dict[str, Any]:
    durs = [s.duration_ms for s in samples if s.success]
    fails = sum(1 for s in samples if not s.success)
    return {
        "n": len(samples),
        "failures": fails,
        "success_rate": (len(samples) - fails) / max(len(samples), 1),
        "median_ms": statistics.median(durs) if durs else 0.0,
        "p95_ms": percentile(durs, 95) if durs else 0.0,
        "p99_ms": percentile(durs, 99) if durs else 0.0,
        "min_ms": min(durs) if durs else 0.0,
        "max_ms": max(durs) if durs else 0.0,
    }


class Recorder:
    """Append-only JSONL recorder."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._samples: list[Sample] = []

    def record(self, sample: Sample) -> None:
        self._samples.append(sample)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(sample.to_dict()) + "\n")

    def measure(
        self,
        operation: str,
        backend: str,
        fn: Callable[[], Any],
        cache_hit: bool | None = None,
    ) -> tuple[Any, Sample]:
        import datetime

        t0 = time.perf_counter()
        success = True
        error_code: str | None = None
        result: Any = None
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - harness must capture, not crash
            success = False
            error_code = f"{type(e).__name__}:{e}"
        dt_ms = (time.perf_counter() - t0) * 1000.0
        sample = Sample(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            operation=operation,
            backend=backend,
            duration_ms=dt_ms,
            success=success,
            cache_hit=cache_hit,
            error_code=error_code,
        )
        self.record(sample)
        return result, sample

    @property
    def samples(self) -> list[Sample]:
        return list(self._samples)
