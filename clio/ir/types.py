from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    EnumType,
    ListType,
    PrimitiveType,
    RecordType,
    TypeExpr,
)


def types_equal(a: TypeExpr, b: TypeExpr, contracts: dict) -> bool:
    """Structural equality of type expressions.

    ContractRef is treated by name; we don't unfold contracts in v0.1.
    """
    a = _resolve(a, contracts)
    b = _resolve(b, contracts)
    if isinstance(a, PrimitiveType) and isinstance(b, PrimitiveType):
        return a.name == b.name
    if isinstance(a, ListType) and isinstance(b, ListType):
        return types_equal(a.inner, b.inner, contracts)
    if isinstance(a, RecordType) and isinstance(b, RecordType):
        if len(a.fields) != len(b.fields):
            return False
        return all(
            an == bn and types_equal(at, bt, contracts)
            for (an, at), (bn, bt) in zip(a.fields, b.fields)
        )
    if isinstance(a, EnumType) and isinstance(b, EnumType):
        return a.values == b.values
    if isinstance(a, ConstrainedType) and isinstance(b, ConstrainedType):
        return types_equal(a.base, b.base, contracts) and a.constraints == b.constraints
    if isinstance(a, ContractRef) and isinstance(b, ContractRef):
        return a.name == b.name
    return False


def _resolve(t: TypeExpr, contracts: dict) -> TypeExpr:
    return t


def names_equal(a: TypeExpr, b: TypeExpr) -> bool:
    """A coarser equality: ContractRef matches ContractRef by name."""
    if isinstance(a, ContractRef) and isinstance(b, ContractRef):
        return a.name == b.name
    return False
