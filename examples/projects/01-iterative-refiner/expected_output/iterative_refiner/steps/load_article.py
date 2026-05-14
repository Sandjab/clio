"""STEP load_article (exact)
TAKES:
    file: str
GIVES:
    article: str

Implement the body below. The orchestrator passes arguments by keyword
and expects the return value to conform to the GIVES type.

NOTE: when implementing, emit a step_end before returning:
    _log.emit("step_end", step='load_article', mode="exact",
              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)
"""
from __future__ import annotations

import time

from ..clio_runtime import logging as _log


def load_article(*, file: str) -> str:
    _t0 = time.monotonic()
    _log.emit("step_start", step='load_article', mode="exact")
    raise NotImplementedError(
        "Implement steps/load_article.py: this is an exact (deterministic) step."
    )
