from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    CallIR,
    ContractIR,
    FieldIR,
    FlowGraph,
    FlowIR,
    StepIR,
)
from clio.ir.types import names_equal, types_equal
from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractDecl,
    ContractRef,
    EnumType,
    FlowDecl,
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

    steps_by_name: dict[str, StepIR] = {}
    for d in program.decls:
        if isinstance(d, StepDecl):
            for f in d.takes:
                _check_refs(f.type, contracts, f.line, f.col)
            if d.gives is not None:
                _check_refs(d.gives.type, contracts, d.gives.line, d.gives.col)
            steps_by_name[d.name] = _build_step(d)

    flow_ir: FlowIR | None = None
    for d in program.decls:
        if isinstance(d, FlowDecl):
            if flow_ir is not None:
                raise IRBuildError(
                    f"line {d.line}:{d.col}: only one FLOW declaration is allowed in v0.1"
                )
            flow_ir = _build_flow(d, steps_by_name, contracts)

    return FlowGraph(
        steps=tuple(steps_by_name.values()),
        contracts=tuple(contracts.values()),
        flow=flow_ir,
    )


def _build_step(decl: StepDecl) -> StepIR:
    return StepIR(
        name=decl.name,
        mode=decl.mode,
        takes=tuple(FieldIR(name=f.name, type=f.type) for f in decl.takes),
        gives=FieldIR(name=decl.gives.name, type=decl.gives.type) if decl.gives else None,
        line=decl.line,
    )


def _build_flow(
    decl: FlowDecl,
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
) -> FlowIR:
    available: dict[str, TypeExpr] = {}

    calls: list[CallIR] = []
    for call in decl.chain:
        if call.name not in steps_by_name:
            raise IRBuildError(
                f"line {call.line}:{call.col}: unknown STEP {call.name!r} in FLOW {decl.name}"
            )
        step = steps_by_name[call.name]

        provided = dict(call.kwargs)
        for taken in step.takes:
            if taken.name not in provided:
                raise IRBuildError(
                    f"line {call.line}:{call.col}: STEP {step.name} requires kwarg {taken.name!r}, "
                    f"got {sorted(provided)}"
                )
            value = provided[taken.name]
            if isinstance(value, str) and value.startswith("@"):
                ref = value[1:]
                if ref not in available:
                    raise IRBuildError(
                        f"line {call.line}:{call.col}: state reference {ref!r} not produced by "
                        f"any previous step"
                    )
                ref_type = available[ref]
                if not (
                    types_equal(ref_type, taken.type, contracts)
                    or names_equal(ref_type, taken.type)
                ):
                    raise IRBuildError(
                        f"line {call.line}:{call.col}: type mismatch on {taken.name!r}: "
                        f"step {step.name} expects {_render(taken.type)}, "
                        f"flow provides {_render(ref_type)}"
                    )

        if step.gives is not None:
            available[step.gives.name] = step.gives.type

        calls.append(CallIR(step_name=call.name, kwargs=call.kwargs, line=call.line))

    return FlowIR(name=decl.name, chain=tuple(calls), line=decl.line)


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
    elif isinstance(t, ConstrainedType):
        _check_refs(t.base, contracts, line, col)


def _render(t: TypeExpr) -> str:
    if isinstance(t, PrimitiveType):
        return t.name
    if isinstance(t, ListType):
        return f"List<{_render(t.inner)}>"
    if isinstance(t, RecordType):
        return "{" + ", ".join(f"{n}: {_render(ty)}" for n, ty in t.fields) + "}"
    if isinstance(t, EnumType):
        return f"enum({'|'.join(t.values)})"
    if isinstance(t, ConstrainedType):
        cs = ", ".join(f"{k}={v}" for k, v in t.constraints)
        return f"{_render(t.base)}({cs})"
    if isinstance(t, ContractRef):
        return t.name
    return type(t).__name__
