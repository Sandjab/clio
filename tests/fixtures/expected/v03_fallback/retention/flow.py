"""FLOW retention.

Auto-generated. Calls steps in chain order, threading state through a dict.
"""

import json
import os
import sys
import time
from .steps import load_customers as load_customers_mod
from .steps import detect_churn as detect_churn_mod

from .clio_runtime import logging as _log


TOTAL_STEPS = 2


def _persist_state(step_idx: int, state: dict) -> None:
    """Atomic write of {version, flow, step_index, state} to state.json."""
    path = os.environ.get("CLIO_STATE_FILE", "state.json")
    payload = {"version": 1, "flow": "retention", "step_index": step_idx, "state": state}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str)
    os.replace(tmp, path)


def run(*, start_at: int = 0, **initial: object) -> dict:
    if start_at > 0:
        path = os.environ.get("CLIO_STATE_FILE", "state.json")
        if not os.path.exists(path):
            print(f'[clio] resume requested (start_at={start_at}) but {path} missing', file=sys.stderr)
            raise SystemExit(2)
        with open(path) as f:
            payload = json.load(f)
        if payload.get("flow") != "retention":
            print(f'[clio] state.json flow mismatch: expected "retention", got {payload.get("flow")!r}', file=sys.stderr)
            raise SystemExit(2)
        if payload.get("step_index", 0) < start_at:
            print(f'[clio] state.json only reached step {payload.get("step_index", 0)}, cannot resume from {start_at}', file=sys.stderr)
            raise SystemExit(2)
        if start_at >= TOTAL_STEPS:
            print(f'[clio] start_at={start_at} >= total steps={TOTAL_STEPS}', file=sys.stderr)
            raise SystemExit(2)
        state: dict = payload["state"]
    else:
        state: dict = dict(initial)
    _log.set_flow("retention")
    _log.emit("flow_start", resumed_from=start_at if start_at > 0 else 0)
    _success = False
    _t0 = time.monotonic()
    try:
        if start_at < 1:
            state['customers'] = load_customers_mod.load_customers(file='customers.csv')
            _persist_state(1, state)
        if start_at < 2:
            state['risks'] = detect_churn_mod.detect_churn(customers=state['customers'])
            _persist_state(2, state)
        _success = True
        return state
    finally:
        _log.emit("flow_end", duration_ms=int((time.monotonic() - _t0) * 1000), success=_success)
        _log.set_flow(None)
