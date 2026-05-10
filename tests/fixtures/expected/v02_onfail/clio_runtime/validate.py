"""Validate a JSON instance against a JSON Schema (and `x-clio-assert` if present).

Copied verbatim into the emitted project as `clio_runtime/validate.py`.

Public surface:
    validate(schema_path, instance) -> None  (raises on failure)
    main()  (CLI entry point)

`x-clio-assert` is a small JSON AST evaluated by an explicit walker. There is
no interpreter or string-compilation involved. The walker accepts only the
node kinds emitted by the v0.1 parser: ident, int, float, str, call(len, ...),
and compare(==,!=,<,>,<=,>=). Any other node raises ValueError.

Uses the `referencing` library (jsonschema 4.18+ replacement for RefResolver)
to resolve relative `$ref`s against the schema's filesystem location.
"""

import json
import sys
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def validate(schema_path: Path, instance: object) -> None:
    schema = json.loads(schema_path.read_text())
    base_dir = schema_path.resolve().parent
    registry = _build_registry(base_dir)
    validator = jsonschema.Draft202012Validator(schema, registry=registry)
    validator.validate(instance)
    _check_assert(schema, instance, base_dir)


def _build_registry(base_dir: Path) -> Registry:
    """Registry with a retrieve callback that loads schemas from disk relative to base_dir."""
    def retrieve(uri: str):
        if uri.startswith("file://"):
            path = Path(uri[len("file://"):])
        else:
            path = Path(uri)
        if not path.is_absolute():
            path = base_dir / uri
        contents = json.loads(path.read_text())
        return Resource.from_contents(contents, default_specification=DRAFT202012)
    # `retrieve` is the public attrs alias for the `_retrieve` field; mypy sees
    # only the field name, so the kwarg looks unknown.
    return Registry(retrieve=retrieve)  # type: ignore[call-arg]


def _check_assert(schema: dict, instance: object, base_dir: Path) -> None:
    expr = schema.get("x-clio-assert")
    if expr is not None and isinstance(instance, dict):
        if not _walk(expr, instance):
            raise AssertionError(f"x-clio-assert failed on {instance!r}: {expr!r}")

    if schema.get("type") == "array" and isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            target_schema = items
            if "$ref" in items:
                target_schema = json.loads((base_dir / items["$ref"]).read_text())
            inner_expr = target_schema.get("x-clio-assert")
            if inner_expr is not None:
                for item in instance:
                    if isinstance(item, dict) and not _walk(inner_expr, item):
                        raise AssertionError(
                            f"x-clio-assert failed on item {item!r}: {inner_expr!r}"
                        )


def _walk(node: dict, item: dict) -> object:
    kind = node.get("kind")
    if kind == "ident":
        name = node["name"]
        if name not in item:
            raise AssertionError(f"unknown identifier in x-clio-assert: {name!r}")
        return item[name]
    if kind == "int":
        return int(node["value"])
    if kind == "float":
        return float(node["value"])
    if kind == "str":
        return str(node["value"])
    if kind == "call":
        if node["func"] != "len":
            raise ValueError(f"unsupported function in x-clio-assert: {node['func']!r}")
        if len(node["args"]) != 1:
            raise ValueError("len() expects exactly one argument")
        arg_value = _walk(node["args"][0], item)
        # `_walk` returns `object`; mypy can't see that arg_value is always a
        # str/list/dict here (the AST forbids reducing `len(...)` to non-sized).
        return len(arg_value)  # type: ignore[arg-type]
    if kind == "compare":
        left = _walk(node["left"], item)
        right = _walk(node["right"], item)
        op = node["op"]
        return _COMPARE[op](left, right)
    raise ValueError(f"unknown x-clio-assert node kind: {kind!r}")


_COMPARE = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
}


def _read_instance(arg: str) -> object:
    if arg == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(arg).read_text())


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m clio_runtime.validate <schema_path> <instance_path|->", file=sys.stderr)
        return 2
    schema_path = Path(args[0])
    instance = _read_instance(args[1])
    try:
        validate(schema_path, instance)
    except (jsonschema.ValidationError, AssertionError, ValueError) as e:
        print(f"[clio_runtime] validation failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
