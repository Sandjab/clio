"""Structured JSON-Line logging for CLIO emitted projects.

Copied verbatim into the emitted project as `clio_runtime/logging.py`.

Public surface:
    emit(event: str, **fields) -> None
    set_flow(name: str | None) -> None

Activation:
    CLIO_LOG=1                  # enables emission (anything else = no-op)
    CLIO_LOG_FILE=path.jsonl    # redirect stream to file (default: stderr)

Schema: each event is a single JSON object on its own line, always carrying
'ts' (ISO 8601 UTC, ms) and 'event' (str). Caller-supplied fields are merged.
'flow' is taken from the ContextVar set by set_flow(), unless the caller
passed an explicit flow= kwarg.
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
from datetime import datetime, timezone
from typing import IO

_current_flow: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "clio_flow", default=None
)

_file_handle: IO | None = None
_file_path_resolved: str | None = None


def set_flow(name: str | None) -> None:
    """Set the FLOW name carried into subsequent emit() calls in this context."""
    _current_flow.set(name)


def _enabled() -> bool:
    return os.environ.get("CLIO_LOG") == "1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _destination() -> IO:
    """Return the open stream for log writes. Caches a file handle when
    CLIO_LOG_FILE is set; falls back to sys.stderr otherwise."""
    global _file_handle, _file_path_resolved
    requested = os.environ.get("CLIO_LOG_FILE")
    if requested is None:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except Exception:
                pass
            _file_handle = None
            _file_path_resolved = None
        return sys.stderr
    if _file_handle is None or _file_path_resolved != requested:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except Exception:
                pass
        _file_handle = open(requested, "a", encoding="utf-8")
        _file_path_resolved = requested
    return _file_handle


def emit(event: str, **fields) -> None:
    """Emit a JSON-line event. No-op when CLIO_LOG is unset/empty/0.

    Never raises: any I/O error during the write is swallowed (logging must
    not break a flow). Reserved keys 'ts' and 'event' are always set by emit
    itself; caller-supplied 'ts'/'event' would be overwritten.

    Caller-supplied 'flow' (including 'flow=None') overrides the ContextVar.
    This is asymmetric with set_flow(None) which omits the 'flow' key entirely.

    If a kwarg's value is not JSON-serializable, the event is silently dropped
    (no exception, no partial line). Callers must ensure values are JSON-safe."""
    if not _enabled():
        return
    try:
        payload: dict = {"ts": _now(), "event": event}
        flow_in_kwargs = "flow" in fields
        if not flow_in_kwargs:
            ctx_flow = _current_flow.get()
            if ctx_flow is not None:
                payload["flow"] = ctx_flow
        payload.update(fields)
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        try:
            stream = _destination()
        except Exception:
            return
        try:
            stream.write(line)
            stream.flush()
        except Exception:
            return
    except Exception:
        return


def _reset_for_tests() -> None:
    """Test helper: close any cached file handle. Not a public API."""
    global _file_handle, _file_path_resolved
    if _file_handle is not None:
        try:
            _file_handle.close()
        except Exception:
            pass
    _file_handle = None
    _file_path_resolved = None
