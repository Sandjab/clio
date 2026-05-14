"""Standalone script for STEP foo (exact)

TAKES:
    (no TAKES)
GIVES:
    (no GIVES)

Usage:
    python scripts/01_foo.py < state.json > state.next.json
"""
from __future__ import annotations

import json
import sys


def foo():
    """Implement the body of STEP foo here.

    TAKES:
        (no TAKES)
    GIVES:
        (no GIVES)
    """
    raise NotImplementedError(
        "Implement foo: this is an exact (deterministic) step."
    )


if __name__ == "__main__":
    state = json.load(sys.stdin)
    # no TAKES
    result = foo()
    # no GIVES — state unchanged by foo
    json.dump(state, sys.stdout, indent=2)
    sys.stdout.write('\n')
