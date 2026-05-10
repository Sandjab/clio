"""Runtime helpers for impl.mode: rest steps.

Copied verbatim into the emitted package's `clio_runtime/` directory by the
python and mcp-server emitters when the flow has any REST step. See
LANGUAGE_SPEC.md §impl.mode: rest for the source-language reference.
"""

from __future__ import annotations

import os
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_ENV_WHOLE = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")

_EXT_TO_CONTENT_TYPE = {
    ".json": "application/json",
    ".xml": "application/xml",
    ".txt": "text/plain",
    ".html": "text/html",
    ".csv": "text/csv",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def subst(template: str, takes: dict[str, Any]) -> str:
    """Substitute ${var} placeholders from `takes` and resolve `env:NAME`
    when the entire string equals `env:NAME`.

    - The whole string `env:FOO` → `os.environ["FOO"]` (raises KeyError if unset).
    - Any occurrence of `${var}` → `str(takes[var])` (raises KeyError if unset).
    - Plain text passes through unchanged.

    These rules deliberately do NOT mix: `env:` substring inside a longer string
    is treated as plain text. Use `${var}` for inline interpolation.
    """
    m = _ENV_WHOLE.match(template)
    if m is not None:
        name = m.group(1)
        if name not in os.environ:
            raise KeyError(f"impl.rest: env var {name!r} is not set")
        return os.environ[name]

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in takes:
            raise KeyError(f"impl.rest: ${{{key}}} not found in TAKES")
        return str(takes[key])
    return _PLACEHOLDER.sub(_repl, template)


def render_dict(items: tuple[tuple[str, Any], ...], takes: dict[str, Any]) -> dict[str, Any]:
    """Render an inline dict (e.g. query, headers, JSON body) by substituting
    string values via `subst`. Non-string scalars (int/float/bool/None) pass through."""
    out: dict[str, Any] = {}
    for k, v in items:
        if isinstance(v, str):
            out[k] = subst(v, takes)
        else:
            out[k] = v
    return out


def content_type_for_path(path: str) -> str:
    """Infer Content-Type from a file extension. Defaults to application/octet-stream."""
    _, dot, ext = path.rpartition(".")
    if not dot:
        return "application/octet-stream"
    return _EXT_TO_CONTENT_TYPE.get(f".{ext.lower()}", "application/octet-stream")


def read_file_body(path: str, takes: dict[str, Any]) -> tuple[bytes, str]:
    """Read a file referenced by `body: \"@./path\"` at runtime.

    Returns (data, content_type). Text files (utf-8 decodable) are templated
    via `subst`; binary files pass through unchanged.
    """
    with open(path, "rb") as f:
        raw = f.read()
    ct = content_type_for_path(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw, ct
    return subst(text, takes).encode("utf-8"), ct


def is_retryable_response(status_code: int, on: tuple[str, ...]) -> bool:
    if "5xx" in on and 500 <= status_code < 600:
        return True
    if "429" in on and status_code == 429:
        return True
    return False


def is_retryable_exception(exc: BaseException, on: tuple[str, ...]) -> bool:
    import requests as _req
    if "timeout" in on and isinstance(exc, _req.exceptions.Timeout):
        return True
    if "network" in on and isinstance(
        exc,
        (
            _req.exceptions.ConnectionError,
            _req.exceptions.ChunkedEncodingError,
            _req.exceptions.ContentDecodingError,
        ),
    ):
        return True
    return False


def compute_delay(attempt_index: int, base: float, cap: float, backoff: str) -> float:
    """Delay before retry attempt `attempt_index` (1-indexed)."""
    if backoff == "constant":
        return min(base, cap)
    return min(base * (2 ** (attempt_index - 1)), cap)


def parse_retry_after(value: str | None) -> float | None:
    """Parse the Retry-After header value (seconds-only — HTTP-date is not supported)."""
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None
