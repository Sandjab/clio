"""STEP detect_churn_naive (exact)
TAKES:
    customers: List<{name: str, revenue: float}>
GIVES:
    risks: List<customer_risk>

Implement the body below. The orchestrator passes arguments by keyword
and expects the return value to conform to the GIVES type.

NOTE: when implementing, emit a step_end before returning:
    _log.emit("step_end", step='detect_churn_naive', mode="exact",
              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)
"""
from __future__ import annotations

import time

from ..clio_runtime import logging as _log


def detect_churn_naive(*, customers: list[dict]) -> list[contracts.CustomerRisk]:
    _t0 = time.monotonic()
    _log.emit("step_start", step='detect_churn_naive', mode="exact")
    raise NotImplementedError(
        "Implement steps/detect_churn_naive.py: this is an exact (deterministic) step."
    )
