"""STEP finalize (exact)
TAKES:
    draft: str
    review: summary_judgment
GIVES:
    result: final_summary

Implement the body below. The orchestrator passes arguments by keyword
and expects the return value to conform to the GIVES type.

NOTE: when implementing, emit a step_end before returning:
    _log.emit("step_end", step='finalize', mode="exact",
              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)
"""
from __future__ import annotations

import time

from ..clio_runtime import logging as _log
from .. import contracts


def finalize(*, draft: str, review: contracts.SummaryJudgment) -> contracts.FinalSummary:
    _t0 = time.monotonic()
    _log.emit("step_start", step='finalize', mode="exact")
    raise NotImplementedError(
        "Implement steps/finalize.py: this is an exact (deterministic) step."
    )
