# PERFORMANCE

Workload-specific measurements only. No universal speedup claims.

## End-to-end (medians, 3/3 unless noted; MODEL LATENCY NOT MEASURED)

| Workload | Baseline (calls/KB/ms) | Engine 1-call (calls/KB/ms) | Reused |
|---|---|---|---|
| WA open/type/save/verify/close | 7 / 20.9 / ~3100 | 1 / 2.9 / ~3070 | — |
| WF multi-step+save | 7 / 20.9 / ~3180 | 1 / 3.1 / ~3170 | — |
| WB multi-DOM browser | 5 / 14.1 / ~1300 | 1 / 2.8 / ~1300 | 2 calls / 5.6 KB / **~450 (2.9×)** |
| Browser x5 sequential | — | fresh 23.9 s total | reuse 4.2 s (**5.72×**) |
| Recovery F-A | restart-fix 5.4–0.7 s* | resume 1.6 s→83 ms (**3.4–8.6×**) | — |
| Recovery F-C browser | fail 11.5 s | resume **20 ms**, no relaunch | — |

*restart time varies with window state; resume replays only failed steps.

Structural (deterministic): 5–7× fewer MCP calls, 4–7× fewer return bytes,
N:1 model decisions, per-call MCP tax 3.45 ms (p95 6.7 ms), receipt ~0.8 KB,
schema 3.0 KB total. Native execution approximately equal for identical work
— the product wins on interaction + state, not on native speed.

## Micro (medians)

- SetValue: 502 ms → **1.7 ms** (wrapper sleep removed, verified write);
  raw COM ~2–7 ms both runtimes. Invoke 534→35 ms. Resolution 1073→79 ms
  (timeout bug) + resolve cache 1.7× on repeated lookup.
- UIA lookup raw: STA 1.07 / MTA×4 4.11 / hybrid-query 2.05 ms; input thread 0.19 ms.
- CLR in-box: find 5–9 ms, enum 10.7 ms (same order as raw COM).
- Rust exe: 1.8 MB, 11 MB RSS; serde dispatch proxy 0.004 ms vs Python 0.04 ms.
- CDP vs Playwright (historical research): same order; CDP 1.2–1.4× faster on nav/extract; table high-variance both.
  *(Note: CDP selected for production; Playwright is NOT an ORVEX dependency).*
- BT3 variance: navigation spikes (CDP p95 2.3 s) + launch spikes (PW p95 4.2 s);
  query/eval/serialize tight → environmental contention, mitigated by reuse.

## Automation controller overhead (final RC, measured)

Measured by `tests/test_controller.py` (`PERF-*`), not estimated:

| Operation | Cost |
|---|---|
| Indicator process start (Popen return) | ~10 ms, off the hot path |
| State transition (queue + JSON line) | < 0.25 ms |
| Pause / Take Control / Resume / Stop | < 0.25 ms each |
| Approval bookkeeping | < 0.25 ms (excludes the human's think time) |

The indicator is asynchronous by design: the engine pushes state over a pipe and
never waits for a window to be painted or mapped, so controller overhead is
negligible against task execution (hundreds of ms to seconds per UIA action).
Panel sizing is compact while RUNNING and expands on hover/click, so the hot path
does no extra layout work.

## Regression (Phase 4 → Phase 5, hardened)

WA engine ~3.1–3.2 s → ~3.1–4.5 s (guards add tens of ms; variance is
environmental). BT2 1321→1321, BT4 1372→1372-class, recovery faster
(F-A resume 1595→83 ms via fast resolution). No safety check removed for speed.
Safety cost quantified: desktop+integrity queries ≈ single-digit ms per input op.
