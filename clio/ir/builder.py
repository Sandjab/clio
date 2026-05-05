from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    CacheConfigIR,
    CallIR,
    ContractIR,
    FieldIR,
    FlowGraph,
    FlowIR,
    OnFailChainIR,
    OnFailStrategyIR,
    ResourcesIR,
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
    ResourcesDecl,
    StepDecl,
    TypeExpr,
)


class IRBuildError(ValueError):
    pass


def build_ir(program: Program) -> FlowGraph:
    contracts: dict[str, ContractIR] = {}
    for d in program.decls:
        if isinstance(d, ContractDecl):
            from clio.parser.expressions import expr_to_json_ast
            assert_ast = (
                expr_to_json_ast(d.assert_expr) if d.assert_expr is not None else None
            )
            schema = type_to_json_schema(d.shape)
            if assert_ast is not None:
                schema["x-clio-assert"] = assert_ast
            contracts[d.name] = ContractIR(
                name=d.name,
                json_schema=schema,
                assert_json_ast=assert_ast,
                line=d.line,
            )

    # Pass 1: build StepIRs with fallback placeholders.
    steps_by_name: dict[str, StepIR] = {}
    for d in program.decls:
        if isinstance(d, StepDecl):
            for f in d.takes:
                _check_refs(f.type, contracts, f.line, f.col)
            if d.gives is not None:
                _check_refs(d.gives.type, contracts, d.gives.line, d.gives.col)
            steps_by_name[d.name] = _build_step(d)

    # Pass 2: resolve fallback step refs and check compat.
    steps_by_name = _resolve_fallbacks(steps_by_name, contracts)

    # Pass 3: detect cycles in the fallback graph.
    _detect_fallback_cycles(steps_by_name)

    flow_ir: FlowIR | None = None
    for d in program.decls:
        if isinstance(d, FlowDecl):
            if flow_ir is not None:
                raise IRBuildError(
                    f"line {d.line}:{d.col}: only one FLOW declaration is allowed in v0.1"
                )
            flow_ir = _build_flow(d, steps_by_name, contracts)

    resources_ir: ResourcesIR | None = None
    for d in program.decls:
        if isinstance(d, ResourcesDecl):
            if resources_ir is not None:
                raise IRBuildError(
                    f"line {d.line}:{d.col}: only one RESOURCES declaration is allowed"
                )
            resources_ir = ResourcesIR(target=d.target, models=d.models)

    return FlowGraph(
        steps=tuple(steps_by_name.values()),
        contracts=tuple(contracts.values()),
        flow=flow_ir,
        resources=resources_ir,
    )


def _resolve_fallbacks(
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
) -> dict[str, StepIR]:
    """For each step that has on_fail.strategies containing fallback clauses,
    replace the OnFailChainIR with one where each fallback strategy has its
    fallback_step pointing to the resolved StepIR. Validates compat."""
    new_steps: dict[str, StepIR] = {}
    for name, step in steps_by_name.items():
        if step.on_fail is None:
            new_steps[name] = step
            continue
        new_strategies: list[OnFailStrategyIR] = []
        for s in step.on_fail.strategies:
            if s.kind != "fallback":
                new_strategies.append(s)
                continue
            target_name = s.fallback_step_name
            if target_name not in steps_by_name:
                raise IRBuildError(
                    f"line {step.line}:0: ON_FAIL fallback target {target_name!r} does not exist"
                )
            target = steps_by_name[target_name]
            _check_fallback_compat(step, target, contracts)
            new_strategies.append(OnFailStrategyIR(
                kind="fallback",
                fallback_step_name=target_name,
                fallback_step=target,
                abort_message=None,
                max_retries=None,
            ))
        new_steps[name] = StepIR(
            name=step.name, mode=step.mode, takes=step.takes, gives=step.gives,
            cache=step.cache,
            on_fail=OnFailChainIR(strategies=tuple(new_strategies)),
            lang=step.lang,
            line=step.line,
        )
    return new_steps


def _check_fallback_compat(
    main: StepIR, fb: StepIR, contracts: dict[str, ContractIR]
) -> None:
    if len(main.takes) != len(fb.takes):
        raise IRBuildError(
            f"line {main.line}:0: ON_FAIL fallback {fb.name!r} has incompatible TAKES "
            f"(arity mismatch)"
        )
    for mt, ft in zip(main.takes, fb.takes):
        if mt.name != ft.name or not (
            types_equal(mt.type, ft.type, contracts) or names_equal(mt.type, ft.type)
        ):
            raise IRBuildError(
                f"line {main.line}:0: ON_FAIL fallback {fb.name!r} has incompatible TAKES "
                f"(expected {mt.name}: {_render(mt.type)}, got {ft.name}: {_render(ft.type)})"
            )
    main_gives = main.gives
    fb_gives = fb.gives
    if (main_gives is None) != (fb_gives is None):
        raise IRBuildError(
            f"line {main.line}:0: ON_FAIL fallback {fb.name!r} has incompatible GIVES "
            f"(one is None, the other is not)"
        )
    if main_gives is not None and fb_gives is not None:
        if main_gives.name != fb_gives.name or not (
            types_equal(main_gives.type, fb_gives.type, contracts)
            or names_equal(main_gives.type, fb_gives.type)
        ):
            raise IRBuildError(
                f"line {main.line}:0: ON_FAIL fallback {fb.name!r} has incompatible GIVES "
                f"(expected {main_gives.name}: {_render(main_gives.type)}, "
                f"got {fb_gives.name}: {_render(fb_gives.type)})"
            )


def _detect_fallback_cycles(steps_by_name: dict[str, StepIR]) -> None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in steps_by_name}

    def visit(name: str, path: list[str]) -> None:
        if color[name] == GRAY:
            raise IRBuildError(
                f"line {steps_by_name[name].line}:0: ON_FAIL fallback creates a cycle: "
                + " -> ".join(path + [name])
            )
        if color[name] == BLACK:
            return
        color[name] = GRAY
        step = steps_by_name[name]
        if step.on_fail is not None:
            for s in step.on_fail.strategies:
                if s.kind == "fallback" and s.fallback_step is not None:
                    visit(s.fallback_step.name, path + [name])
        color[name] = BLACK

    for n in steps_by_name:
        if color[n] == WHITE:
            visit(n, [])


def _build_step(decl: StepDecl) -> StepIR:
    cache_ir = (
        CacheConfigIR(mode=decl.cache.mode, ttl_seconds=decl.cache.ttl_seconds)
        if decl.cache is not None else None
    )
    on_fail_ir = _build_on_fail(decl.on_fail) if decl.on_fail is not None else None
    return StepIR(
        name=decl.name,
        mode=decl.mode,
        takes=tuple(FieldIR(name=f.name, type=f.type) for f in decl.takes),
        gives=FieldIR(name=decl.gives.name, type=decl.gives.type) if decl.gives else None,
        cache=cache_ir,
        on_fail=on_fail_ir,
        lang=decl.lang,
        line=decl.line,
    )


def _build_on_fail(chain) -> OnFailChainIR:
    out: list[OnFailStrategyIR] = []
    for s in chain.strategies:
        out.append(OnFailStrategyIR(
            kind=s.kind,
            max_retries=s.max_retries,
            fallback_step_name=s.fallback_step_name,   # capture the name now
            fallback_step=None,                        # resolved in slice G
            abort_message=s.abort_message,
        ))
    return OnFailChainIR(strategies=tuple(out))


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
