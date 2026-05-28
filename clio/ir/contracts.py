from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    DictType,
    EnumType,
    ListType,
    OptionalType,
    PrimitiveType,
    RecordType,
    TypeExpr,
)

_PRIMITIVE_JSON_TYPES = {
    "int": "integer",
    "float": "number",
    "str": "string",
    "bool": "boolean",
}


def type_to_json_schema(t: TypeExpr) -> dict:
    if isinstance(t, ConstrainedType):
        if not isinstance(t.base, PrimitiveType):
            raise NotImplementedError(
                f"constraints not supported on {type(t.base).__name__}"
            )
        out = type_to_json_schema(t.base)
        base = t.base.name
        for kind, value in t.constraints:
            if base == "str" and kind == "max":
                out["maxLength"] = value
            elif base == "str" and kind == "min":
                out["minLength"] = value
            elif base in {"int", "float"} and kind == "min":
                out["minimum"] = value
            elif base in {"int", "float"} and kind == "max":
                out["maximum"] = value
            elif base == "float" and kind == "precision":
                # v0.21: precision=N → multipleOf 10**-N (exact N decimal
                # places). JSON Schema's multipleOf is portable across
                # validators (Pydantic, jsonschema/v6, etc.).
                out["multipleOf"] = 10 ** -value
            else:
                raise NotImplementedError(
                    f"unsupported constraint `{kind}` on `{base}`"
                )
        return out
    if isinstance(t, PrimitiveType):
        return {"type": _PRIMITIVE_JSON_TYPES[t.name]}
    if isinstance(t, ListType):
        return {"type": "array", "items": type_to_json_schema(t.inner)}
    if isinstance(t, DictType):
        # v0.21: key is always PrimitiveType("str") (enforced by the parser).
        # JSON Schema represents homogeneous string-keyed maps as
        # `{"type": "object", "additionalProperties": <V-schema>}`.
        return {"type": "object", "additionalProperties": type_to_json_schema(t.value)}
    if isinstance(t, OptionalType):
        # v0.21: `Optional<T>` means "value matching T or null". `anyOf` works
        # uniformly across primitives, contracts ($ref), arrays, records, and
        # enums — JSON Schema's multi-type-array form (`{"type": ["X", "null"]}`)
        # can't express $ref-or-null. Pydantic v2 round-trips this form.
        return {"anyOf": [type_to_json_schema(t.inner), {"type": "null"}]}
    if isinstance(t, RecordType):
        return {
            "type": "object",
            "properties": {name: type_to_json_schema(ty) for name, ty in t.fields},
            "required": [name for name, _ in t.fields],
            "additionalProperties": False,
        }
    if isinstance(t, EnumType):
        return {"enum": list(t.values)}
    if isinstance(t, ContractRef):
        return {"$ref": f"../contracts/{t.name}.schema.json"}
    raise NotImplementedError(f"type_to_json_schema: {type(t).__name__}")
