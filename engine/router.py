"""Action router: semantic op -> ordered mechanisms -> execute -> verify.

Ordering comes from measured COSTS when available, else PRIOR_ORDER
(safest-first). Order is data, not hardcoded branches, so benchmarks can
re-rank without code changes. Every choice is recorded for receipts.
"""
from __future__ import annotations

from .capabilities import COSTS, PRIOR_ORDER, probe_control_patterns


def order_mechanisms(op: str, control=None) -> list[str]:
    """Return tried-in-order mechanism list for op.

    Filters by actually-supported patterns when a control is available
    (e.g. no ValuePattern -> value_pattern removed, not attempted).
    """
    candidates = list(PRIOR_ORDER.get(op, []))
    if control is not None and op in ("set_value", "invoke", "read", "select", "toggle", "expand"):
        supported = probe_control_patterns(control)
        pat_for = {"set_value": "value_pattern", "invoke": "invoke_pattern",
                   "read": "value_pattern", "select": "selection_item_pattern",
                   "toggle": "toggle_pattern", "expand": "expand_collapse_pattern"}
        want = pat_for[op]
        if not supported.get(want, False):
            candidates = [m for m in candidates if m != want]
    # re-rank by measured median when ALL candidates measured; else keep prior
    costs = COSTS.get(op, {})
    if candidates and all(costs.get(m) and costs[m].measured for m in candidates):
        candidates.sort(key=lambda m: costs[m].median_ms)
    return candidates
