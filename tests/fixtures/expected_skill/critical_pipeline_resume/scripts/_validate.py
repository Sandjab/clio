#!/usr/bin/env python3
"""Bundled JSON Schema validator for CLIO-emitted skills.

Usage: python _validate.py <instance.json> <schema.json>
Exits 0 if valid, non-zero with a human-readable message otherwise.

Prefers the `jsonschema` PyPI package when available; falls back to a
minimal stdlib check (type + required + property types) so the skill
remains usable on bare Python installs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _stdlib_validate(instance, schema, path="$"):
    t = schema.get("type")
    if t == "object":
        if not isinstance(instance, dict):
            raise ValueError(f"{path}: expected object, got {type(instance).__name__}")
        for req in schema.get("required", []):
            if req not in instance:
                raise ValueError(f"{path}: missing required field '{req}'")
        for k, sub in schema.get("properties", {}).items():
            if k in instance:
                _stdlib_validate(instance[k], sub, f"{path}.{k}")
    elif t == "array":
        if not isinstance(instance, list):
            raise ValueError(f"{path}: expected array, got {type(instance).__name__}")
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(instance):
                _stdlib_validate(item, items_schema, f"{path}[{i}]")
    elif t == "string":
        if not isinstance(instance, str):
            raise ValueError(f"{path}: expected string")
    elif t == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise ValueError(f"{path}: expected integer")
    elif t == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            raise ValueError(f"{path}: expected number")
    elif t == "boolean":
        if not isinstance(instance, bool):
            raise ValueError(f"{path}: expected boolean")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: _validate.py <instance.json> <schema.json>", file=sys.stderr)
        return 2
    instance = json.loads(Path(sys.argv[1]).read_text())
    schema = json.loads(Path(sys.argv[2]).read_text())
    try:
        import jsonschema  # type: ignore
        jsonschema.validate(instance, schema)
    except ImportError:
        try:
            _stdlib_validate(instance, schema)
        except ValueError as e:
            print(f"validation error: {e}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"validation error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
