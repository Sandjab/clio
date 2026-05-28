from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    DictType,
    EnumType,
    ListType,
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
        if not isinstance(t.base, PrimitiveType) or t.base.name != "str":
            raise NotImplementedError("v0.1 only supports str(max=N) constraints")
        out = type_to_json_schema(t.base)
        for kind, value in t.constraints:
            if kind == "max":
                out["maxLength"] = value
            else:
                raise NotImplementedError(f"unknown constraint kind: {kind!r}")
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
