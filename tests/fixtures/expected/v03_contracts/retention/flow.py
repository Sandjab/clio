"""FLOW retention.

Auto-generated. Calls steps in chain order, threading state through a dict.
"""

import time
from .steps import load_customers as load_customers_mod
from .steps import detect_churn as detect_churn_mod

from .clio_runtime import logging as _log


def run(**initial: object) -> dict:
    state: dict = dict(initial)
    _log.set_flow("retention")
    _log.emit("flow_start")
    _success = False
    _t0 = time.monotonic()
    try:
        state['customers'] = load_customers_mod.load_customers(file='customers.csv')
        state['risks'] = detect_churn_mod.detect_churn(customers=state['customers'])
        _success = True
        return state
    finally:
        _log.emit("flow_end", duration_ms=int((time.monotonic() - _t0) * 1000), success=_success)
        _log.set_flow(None)
