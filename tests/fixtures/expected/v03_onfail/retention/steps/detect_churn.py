"""STEP detect_churn (judgment).

Auto-generated. Do not edit; regenerate via `clio compile`.
"""
from __future__ import annotations

import json
import sys
import time
import os
from pathlib import Path

import anthropic

from ..clio_runtime import logging as _log

from ..clio_runtime import cache as _cache

from .. import contracts


_PROMPT_TEMPLATE = 'You are executing the CLIO step `detect_churn`.\n\nInput:\n  customers: ${customers}\n\nProduce a JSON value that EXACTLY matches this schema:\n${schema}\n\nRules — these are non-negotiable:\n1. Use EXACTLY the property names listed in the schema. Do NOT invent new fields.\n2. Every required property must be present in every item.\n3. For enum properties, use ONLY values from the listed enum.\n4. Respect every constraint (maxLength, etc.).\n5. Output the raw JSON value only. No markdown code fences. No prose. No explanation.\n'
_INLINED_SCHEMA = '{"type":"array","items":{"type":"object","properties":{"client":{"type":"string"},"risk":{"enum":["low","mid","high"]},"reason":{"type":"string"}},"required":["client","risk","reason"],"additionalProperties":false}}'
_SYSTEM_PROMPT = (
    'You are a strict JSON-only API. Output exactly one JSON document matching '
    'the requested schema, with no prose, no markdown code fences, no commentary, '
    'and no leading or trailing whitespace beyond the JSON itself.'
)
_MODELS = ('claude-haiku-4-5-20251001', 'claude-sonnet-4-6')


def _serialize(response):
    """Re-serialize a validated response for cache storage."""
    if isinstance(response, list):
        return json.dumps([(item.model_dump() if hasattr(item, 'model_dump') else item) for item in response])
    if hasattr(response, 'model_dump'):
        return json.dumps(response.model_dump())
    return json.dumps(response)


def detect_churn(*, customers: list[dict]) -> list[contracts.CustomerRisk]:
    _t0 = time.monotonic()
    _log.emit("step_start", step='detect_churn', mode="judgment")
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
            return (lambda raw: [contracts.CustomerRisk.model_validate(item) for item in raw])(json.loads(cleaned))
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError):
            raise
        except Exception:
            return None

    prompt = _PROMPT_TEMPLATE
    prompt = prompt.replace('${customers}', json.dumps(customers))
    prompt = prompt.replace('${schema}', _INLINED_SCHEMA)

    model_idx = 0
    response = None

    cache_dir = Path(os.environ.get('CLIO_CACHE_DIR', '.cache'))
    primary_key = _cache.cache_key('detect_churn', _MODELS[0], prompt, _INLINED_SCHEMA)
    hit = _cache.cache_lookup(cache_dir, 'detect_churn', primary_key, 86400)
    if hit is not None:
        try:
            _ret = (lambda raw: [contracts.CustomerRisk.model_validate(item) for item in raw])(json.loads(hit))
            _log.emit("step_end", step='detect_churn', mode="judgment",
                      duration_ms=int((time.monotonic() - _t0) * 1000),
                      cache_hit=True, model=_MODELS[0],
                      fallback_used=False, success=True)
            return _ret
        except Exception:
            pass  # stale cache (schema changed): fall through to a fresh call

    response = _attempt(_MODELS[model_idx], prompt)

    if response is None:
        for _ in range(3):
            response = _attempt(_MODELS[model_idx], prompt)
            if response is not None:
                break

    if response is None and model_idx < len(_MODELS) - 1:
        model_idx += 1
        esc_key = _cache.cache_key('detect_churn', _MODELS[model_idx], prompt, _INLINED_SCHEMA)
        esc_hit = _cache.cache_lookup(cache_dir, 'detect_churn', esc_key, 86400)
        if esc_hit is not None:
            try:
                _ret = (lambda raw: [contracts.CustomerRisk.model_validate(item) for item in raw])(json.loads(esc_hit))
                _log.emit("step_end", step='detect_churn', mode="judgment",
                          duration_ms=int((time.monotonic() - _t0) * 1000),
                          cache_hit=True, model=_MODELS[model_idx],
                          fallback_used=False, success=True)
                return _ret
            except Exception:
                pass  # stale escalate cache: fall through
        response = _attempt(_MODELS[model_idx], prompt)
        if response is not None:
            _cache.cache_store(cache_dir, 'detect_churn', esc_key, _MODELS[model_idx], _serialize(response))

    if response is None:
        print('[clio] step detect_churn: churn detection failed', file=sys.stderr)
        _log.emit("step_end", step='detect_churn', mode="judgment",
                  duration_ms=int((time.monotonic() - _t0) * 1000),
                  cache_hit=False, model=_MODELS[model_idx],
                  fallback_used=False, success=False)
        raise SystemExit(1)

    if model_idx == 0 and response is not None:
        _cache.cache_store(cache_dir, 'detect_churn', primary_key, _MODELS[0], _serialize(response))

    _log.emit("step_end", step='detect_churn', mode="judgment",
              duration_ms=int((time.monotonic() - _t0) * 1000),
              cache_hit=False, model=_MODELS[model_idx],
              fallback_used=False, success=True, **_last_usage)
    return response
