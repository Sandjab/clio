"""FLOW classify.

Auto-generated. Calls steps in chain order, threading state through a dict.
"""

import time
from .steps import detect_topic as detect_topic_mod

from .clio_runtime import logging as _log


def run(**initial: object) -> dict:
    state: dict = dict(initial)
    _log.set_flow("classify")
    _log.emit("flow_start")
    _success = False
    _t0 = time.monotonic()
    try:
        state['topic'] = detect_topic_mod.detect_topic(text='hello')
        _success = True
        return state
    finally:
        _log.emit("flow_end", duration_ms=int((time.monotonic() - _t0) * 1000), success=_success)
        _log.set_flow(None)
