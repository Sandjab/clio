"""Validate a JSON instance against a JSON Schema (and `x-clio-assert` if present).

Copied verbatim into the emitted project as `clio_runtime/validate.py`.

Public surface:
    validate(schema_path, instance) -> None  (raises on failure)
    main()  (CLI entry point)

`x-clio-assert` is a small JSON AST evaluated by an explicit walker. There is
no interpreter or string-compilation involved. The walker accepts only the
node kinds emitted by the v0.1 parser: ident, int, float, str, call(len, ...),
and compare(==,!=,<,>,<=,>=). Any other node raises ValueError.
"""

import json
import sys
from pathlib import Path

import jsonschema
from jsonschema import RefResolver


def validate(schema_path: Path, instance: object, base_uri: str | None = None) -> None:
    schema = json.loads(schema_path.read_text())
    resolver_uri = base_uri or schema_path.resolve().parent.as_uri() + "/"
    resolver = RefResolver(base_uri=resolver_uri, referrer=schema)
    jsonschema.validate(instance=instance, schema=schema, resolver=resolver)
    _check_assert(schema, instance, resolver)


def _check_assert(schema: dict, instance: object, resolver: RefResolver) -> None:
    expr = schema.get("x-clio-assert")
    if expr is not None and isinstance(instance, dict):
        if not _walk(expr, instance):
            raise AssertionError(f"x-clio-assert failed on {instance!r}: {expr!r}")

    if schema.get("type") == "array" and isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            target_schema = items
            if "$ref" in items:
                _, target_schema = resolver.resolve(items["$ref"])
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
        return len(arg_value)
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
