"""Standalone script for STEP echo_str (exact)

TAKES:
    input: PrimitiveType(name='str')
GIVES:
    output: PrimitiveType(name='str')

Usage:
    python scripts/01_echo_str.py < state.json > state.next.json
"""
from __future__ import annotations

import json
import sys


def echo_str(input):
    """Implement the body of STEP echo_str here.

    TAKES:
        input: PrimitiveType(name='str')
    GIVES:
        output: PrimitiveType(name='str')
    """
    raise NotImplementedError(
        "Implement echo_str: this is an exact (deterministic) step."
    )


if __name__ == "__main__":
    state = json.load(sys.stdin)
    input = state.get('echo_str', {}).get('input')
    result = echo_str(input=input)
    state.setdefault('echo_str', {})['output'] = result
    json.dump(state, sys.stdout, indent=2)
    sys.stdout.write('\n')
