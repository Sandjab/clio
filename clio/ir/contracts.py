from clio.parser.ast_nodes import PrimitiveType, TypeExpr


_PRIMITIVE_JSON_TYPES = {
    "int": "integer",
    "float": "number",
    "str": "string",
    "bool": "boolean",
}


def type_to_json_schema(t: TypeExpr) -> dict:
    if isinstance(t, PrimitiveType):
        return {"type": _PRIMITIVE_JSON_TYPES[t.name]}
    raise NotImplementedError(f"type_to_json_schema: {type(t).__name__}")
