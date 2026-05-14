"""STEP detect_topic (judgment).

Auto-generated. Do not edit; regenerate via `clio compile`.
"""
from __future__ import annotations

import json
import sys
import time

import anthropic

from ..clio_runtime import logging as _log

from .. import contracts


_PROMPT_TEMPLATE = 'You are executing the CLIO step `detect_topic`.\n\nInput:\n  text: ${text}\n\nProduce a JSON value that EXACTLY matches this schema:\n${schema}\n\nRules — these are non-negotiable:\n1. Use EXACTLY the property names listed in the schema. Do NOT invent new fields.\n2. Every required property must be present in every item.\n3. For enum properties, use ONLY values from the listed enum.\n4. Respect every constraint (maxLength, etc.).\n5. Output the raw JSON value only. No markdown code fences. No prose. No explanation.\n'
_INLINED_SCHEMA = '{"type":"string"}'
_SYSTEM_PROMPT = (
    'You are a strict JSON-only API. Output exactly one JSON document matching '
    'the requested schema, with no prose, no markdown code fences, no commentary, '
    'and no leading or trailing whitespace beyond the JSON itself.'
)
_MODELS = ('claude-haiku-4-5-20251001',)


def detect_topic(*, text: str) -> str:
    _t0 = time.monotonic()
    _log.emit("step_start", step='detect_topic', mode="judgment")
    _last_usage: dict = {}

    def _attempt(model, prompt):
        """Single attempt: SDK call → markdown strip → Pydantic validation."""
        nonlocal _last_usage
        try:
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': prompt}],
            )
            if hasattr(msg, 'usage') and msg.usage is not None:
                _last_usage = {
                    'tokens_in': getattr(msg.usage, 'input_tokens', None),
                    'tokens_out': getattr(msg.usage, 'output_tokens', None),
                }
                _last_usage = {k: v for k, v in _last_usage.items() if v is not None}
            raw = msg.content[0].text if msg.content else ''
            if not raw:
                return None
            cleaned = '\n'.join(line for line in raw.splitlines() if not line.startswith('```'))
            return (lambda raw: raw)(json.loads(cleaned))
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError):
            raise
        except Exception:
            return None

    prompt = _PROMPT_TEMPLATE
    prompt = prompt.replace('${text}', json.dumps(text))
    prompt = prompt.replace('${schema}', _INLINED_SCHEMA)

    model_idx = 0
    response = None

    response = _attempt(_MODELS[model_idx], prompt)

    if response is None:
        print('[clio] step detect_topic: ON_FAIL strategies exhausted', file=sys.stderr)
        _log.emit("step_end", step='detect_topic', mode="judgment",
                  duration_ms=int((time.monotonic() - _t0) * 1000),
                  cache_hit=False, model=_MODELS[model_idx],
                  fallback_used=False, success=False)
        raise SystemExit(1)

    _log.emit("step_end", step='detect_topic', mode="judgment",
              duration_ms=int((time.monotonic() - _t0) * 1000),
              cache_hit=False, model=_MODELS[model_idx],
              fallback_used=False, success=True, **_last_usage)
    return response
