from dataclasses import dataclass


@dataclass(frozen=True)
class TypeExpr:
    """Base class for type expression nodes."""


@dataclass(frozen=True)
class PrimitiveType(TypeExpr):
    name: str       # one of: int, float, str, bool


@dataclass(frozen=True)
class ListType(TypeExpr):
    inner: TypeExpr


@dataclass(frozen=True)
class RecordType(TypeExpr):
    fields: tuple[tuple[str, TypeExpr], ...]   # ((name, type), ...)


@dataclass(frozen=True)
class EnumType(TypeExpr):
    values: tuple[str, ...]


@dataclass(frozen=True)
class Field:
    name: str
    type: TypeExpr
    line: int
    col: int


@dataclass(frozen=True)
class StepDecl:
    name: str
    mode: str
    takes: tuple[Field, ...]
    gives: Field | None
    line: int
    col: int


@dataclass(frozen=True)
class ContractRef(TypeExpr):
    name: str          # the contract being referenced
    line: int
    col: int


@dataclass(frozen=True)
class ContractDecl:
    name: str
    shape: TypeExpr    # always a RecordType in v0.1
    line: int
    col: int


@dataclass(frozen=True)
class Program:
    decls: tuple[object, ...]    # StepDecl | ContractDecl
