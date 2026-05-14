"""Standalone script for STEP fallback_detect_churn (exact)

TAKES:
    rows: ListType(inner=PrimitiveType(name='int'))
GIVES:
    report: ContractRef(name='churn_report', line=27, col=18)

Usage:
    python scripts/03_fallback_detect_churn.py < state.json > state.next.json
"""
from __future__ import annotations

import json
import sys


def fallback_detect_churn(rows):
    """Implement the body of STEP fallback_detect_churn here.

    TAKES:
        rows: ListType(inner=PrimitiveType(name='int'))
    GIVES:
        report: ContractRef(name='churn_report', line=27, col=18)
    """
    raise NotImplementedError(
        "Implement fallback_detect_churn: this is an exact (deterministic) step."
    )


if __name__ == "__main__":
    state = json.load(sys.stdin)
    rows = state.get('fallback_detect_churn', {}).get('rows')
    result = fallback_detect_churn(rows=rows)
    state.setdefault('fallback_detect_churn', {})['report'] = result
    json.dump(state, sys.stdout, indent=2)
    sys.stdout.write('\n')
