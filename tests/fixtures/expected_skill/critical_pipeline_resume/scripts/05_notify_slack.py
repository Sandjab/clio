"""Standalone script for STEP notify_slack (exact)

TAKES:
    channel: PrimitiveType(name='str')
    reason: PrimitiveType(name='str')
    err_type: PrimitiveType(name='str')
GIVES:
    sent: PrimitiveType(name='bool')

Usage:
    python scripts/05_notify_slack.py < state.json > state.next.json
"""
from __future__ import annotations

import json
import sys


def notify_slack(channel, reason, err_type):
    """Implement the body of STEP notify_slack here.

    TAKES:
        channel: PrimitiveType(name='str')
    reason: PrimitiveType(name='str')
    err_type: PrimitiveType(name='str')
    GIVES:
        sent: PrimitiveType(name='bool')
    """
    raise NotImplementedError(
        "Implement notify_slack: this is an exact (deterministic) step."
    )


if __name__ == "__main__":
    state = json.load(sys.stdin)
    channel = state.get('notify_slack', {}).get('channel')
    reason = state.get('notify_slack', {}).get('reason')
    err_type = state.get('notify_slack', {}).get('err_type')
    result = notify_slack(channel=channel, reason=reason, err_type=err_type)
    state.setdefault('notify_slack', {})['sent'] = result
    json.dump(state, sys.stdout, indent=2)
    sys.stdout.write('\n')
