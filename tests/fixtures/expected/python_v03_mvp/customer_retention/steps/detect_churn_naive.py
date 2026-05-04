"""STEP detect_churn_naive (exact)
TAKES:
    customers: List<{name: str, revenue: float}>
GIVES:
    risks: List<customer_risk>

Implement the body below. The orchestrator passes arguments by keyword
and expects the return value to conform to the GIVES type.
"""
from __future__ import annotations


def detect_churn_naive(*, customers: list[dict]) -> list[contracts.CustomerRisk]:
    raise NotImplementedError(
        "Implement steps/detect_churn_naive.py: this is an exact (deterministic) step."
    )
