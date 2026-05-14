"""Standalone script for STEP route_alerts (exact)

TAKES:
    report: ContractRef(name='churn_report', line=31, col=18)
GIVES:
    alerts: PrimitiveType(name='int')

Usage:
    python scripts/04_route_alerts.py < state.json > state.next.json
"""
from __future__ import annotations

import json
import sys


def route_alerts(report):
    """Implement the body of STEP route_alerts here.

    TAKES:
        report: ContractRef(name='churn_report', line=31, col=18)
    GIVES:
        alerts: PrimitiveType(name='int')
    """
    raise NotImplementedError(
        "Implement route_alerts: this is an exact (deterministic) step."
    )


if __name__ == "__main__":
    state = json.load(sys.stdin)
    report = state.get('route_alerts', {}).get('report')
    result = route_alerts(report=report)
    state.setdefault('route_alerts', {})['alerts'] = result
    json.dump(state, sys.stdout, indent=2)
    sys.stdout.write('\n')
