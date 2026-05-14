#!/usr/bin/env python3
"""Bundled deterministic cache-key generator for CLIO-emitted skills.

Usage: python _cache_key.py <state.json> <step_name> <key_fields_json>
Emits SHA256 hex on stdout.

`key_fields_json` is a JSON array of dotted paths into <state.json>
(e.g. '["customer.id", "order.items"]'). Missing paths are treated as
null, which deterministically participates in the hash.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _get(state, dotted_path):
    cur = state
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: _cache_key.py <state.json> <step_name> <key_fields_json>", file=sys.stderr)
        return 2
    state = json.loads(Path(sys.argv[1]).read_text())
    step_name = sys.argv[2]
    key_fields = json.loads(sys.argv[3])
    payload = {"step": step_name, "inputs": {p: _get(state, p) for p in key_fields}}
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    print(hashlib.sha256(canon).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
