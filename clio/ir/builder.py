from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import ContractIR, FieldIR, FlowGraph, StepIR
from clio.parser.ast_nodes import (
    ContractDecl,
    ContractRef,
    EnumType,
    Field,
    ListType,
    PrimitiveType,
    Program,
    RecordType,
    StepDecl,
    TypeExpr,
)


class IRBuildError(ValueError):
    pass


def build_ir(program: Program) -> FlowGraph:
    contracts: dict[str, ContractIR] = {}
    for d in program.decls:
        if isinstance(d, ContractDecl):
            contracts[d.name] = ContractIR(
                name=d.name,
                json_schema=type_to_json_schema(d.shape),
                line=d.line,
            )

    steps: list[StepIR] = []
    for d in program.decls:
        if isinstance(d, StepDecl):
            for f in d.takes:
                _check_refs(f.type, contracts, f.line, f.col)
            if d.gives is not None:
                _check_refs(d.gives.type, contracts, d.gives.line, d.gives.col)
            steps.append(_build_step(d))

    return FlowGraph(steps=tuple(steps), contracts=tuple(contracts.values()))


def _build_step(decl: StepDecl) -> StepIR:
    return StepIR(
        name=decl.name,
        mode=decl.mode,
        takes=tuple(FieldIR(name=f.name, type=f.type) for f in decl.takes),
        gives=FieldIR(name=decl.gives.name, type=decl.gives.type) if decl.gives else None,
        line=decl.line,
    )


def _check_refs(t: TypeExpr, contracts: dict[str, ContractIR], line: int, col: int) -> None:
    if isinstance(t, ContractRef):
        if t.name not in contracts:
            raise IRBuildError(
                f"line {t.line}:{t.col}: unknown contract reference {t.name!r}"
            )
    elif isinstance(t, ListType):
        _check_refs(t.inner, contracts, line, col)
    elif isinstance(t, RecordType):
        for _, ty in t.fields:
            _check_refs(ty, contracts, line, col)
    # Primitive and Enum nodes have no refs to check.
