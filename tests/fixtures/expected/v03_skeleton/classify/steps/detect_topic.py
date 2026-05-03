"""STEP detect_topic (judgment).

Auto-generated. Do not edit; regenerate via `clio compile`.
"""
from __future__ import annotations

import json
import sys

from anthropic import Anthropic

from .. import contracts


_PROMPT_TEMPLATE = 'You are executing the CLIO step `detect_topic`.\n\nInput:\n  text: ${text}\n\nProduce a JSON value that EXACTLY matches this schema:\n${schema}\n\nRules — these are non-negotiable:\n1. Use EXACTLY the property names listed in the schema. Do NOT invent new fields.\n2. Every required property must be present in every item.\n3. For enum properties, use ONLY values from the listed enum.\n4. Respect every constraint (maxLength, etc.).\n5. Output the raw JSON value only. No markdown code fences. No prose. No explanation.\n'
_INLINED_SCHEMA = '{"type":"string"}'
_PRIMARY_MODEL = 'claude-haiku-4-5-20251001'


def _attempt(model, prompt):
    """Single attempt: SDK call → markdown strip → Pydantic validation."""
    try:
        client = Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = msg.content[0].text if msg.content else ''
        if not raw:
            return None
        cleaned = '\n'.join(line for line in raw.splitlines() if not line.startswith('```'))
        return (lambda raw: raw)(json.loads(cleaned))
    except Exception:
        return None


def detect_topic(*, text: str) -> str:
    prompt = _PROMPT_TEMPLATE
    prompt = prompt.replace('${text}', json.dumps(text))
    prompt = prompt.replace('${schema}', _INLINED_SCHEMA)

    response = _attempt(_PRIMARY_MODEL, prompt)
    if response is None:
        print('[clio] step detect_topic: ON_FAIL strategies exhausted', file=sys.stderr)
        raise SystemExit(1)
    return response
