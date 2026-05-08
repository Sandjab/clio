"""STEP load_corpus (exact, impl: shell)
TAKES:
    file: str
GIVES:
    corpus: List<str>

Auto-generated from `impl: mode: shell`. Argv-style invocation —
no shell pipes/redirections (subprocess.run is called with shell=False).
TAKES are substituted into argv tokens via ${var} placeholders.
"""
from __future__ import annotations

import subprocess
import json
import time

from ..clio_runtime import logging as _log


def load_corpus(*, file: str) -> list[str]:
    _t0 = time.monotonic()
    _log.emit("step_start", step='load_corpus', mode="exact")
    _argv = ['cat', '${file}']
    _argv = [_t.replace('${file}', str(file)) for _t in _argv]
    result = subprocess.run(_argv, capture_output=True, text=True, check=True, timeout=None)
    _log.emit("step_end", step='load_corpus', mode="exact",
              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)
    return json.loads(result.stdout)
