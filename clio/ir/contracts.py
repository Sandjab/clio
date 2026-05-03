from clio.parser.ast_nodes import (
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
    if isinstance(t, PrimitiveType):
        return {"type": _PRIMITIVE_JSON_TYPES[t.name]}
    if isinstance(t, ListType):
        return {"type": "array", "items": type_to_json_schema(t.inner)}
    if isinstance(t, RecordType):
        return {
            "type": "object",
            "properties": {name: type_to_json_schema(ty) for name, ty in t.fields},
            "required": [name for name, _ in t.fields],
            "additionalProperties": False,
        }
    if isinstance(t, EnumType):
        return {"enum": list(t.values)}
    raise NotImplementedError(f"type_to_json_schema: {type(t).__name__}")
