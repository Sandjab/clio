"""Standalone script for STEP load_clients (exact)

TAKES:
    path: PrimitiveType(name='str')
GIVES:
    rows: ListType(inner=PrimitiveType(name='int'))

Usage:
    python scripts/01_load_clients.py < state.json > state.next.json
"""
from __future__ import annotations

import json
import sys


def load_clients(path):
    """Implement the body of STEP load_clients here.

    TAKES:
        path: PrimitiveType(name='str')
    GIVES:
        rows: ListType(inner=PrimitiveType(name='int'))
    """
    raise NotImplementedError(
        "Implement load_clients: this is an exact (deterministic) step."
    )


if __name__ == "__main__":
    state = json.load(sys.stdin)
    path = state.get('load_clients', {}).get('path')
    result = load_clients(path=path)
    state.setdefault('load_clients', {})['rows'] = result
    json.dump(state, sys.stdout, indent=2)
    sys.stdout.write('\n')
