"""STEP load_customers (exact)
TAKES:
    file: str
GIVES:
    customers: List<{name: str, revenue: float}>

Implement the body below. The orchestrator passes arguments by keyword
and expects the return value to conform to the GIVES type.
"""
from __future__ import annotations


def load_customers(*, file: str) -> list[dict]:
    raise NotImplementedError(
        "Implement steps/load_customers.py: this is an exact (deterministic) step."
    )
