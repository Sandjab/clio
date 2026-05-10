from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    ApiInvokeIR,
    CacheConfigIR,
    CallIR,
    CliInvokeIR,
    CodeImplIR,
    ConditionIR,
    ContractIR,
    FieldIR,
    FileBodyIR,
    FlowGraph,
    FlowIR,
    ForEachIR,
    FormBodyIR,
    IfBlockIR,
    ImplIR,
    InvokeIR,
    JsonBodyIR,
    MatchBlockIR,
    MatchCaseIR,
    MultipartBodyIR,
    OnFailChainIR,
    OnFailStrategyIR,
    RawBodyIR,
    RescueBlockIR,
    ResourcesIR,
    RestBodyIR,
    RestImplIR,
    RetryPolicyIR,
    ShellImplIR,
    StepIR,
    WhileBlockIR,
)
from clio.ir.types import names_equal, types_equal
from clio.parser.ast_nodes import (
    ApiInvoke,
    CliInvoke,
    CodeImpl,
    CompareExpr,
    ConstrainedType,
    ContractDecl,
    ContractRef,
    EnumType,
    FieldRefExpr,
    FileBody,
    FloatExpr,
    FlowDecl,
    ForEachBlock,
    FormBody,
    IdentExpr,
    IfBlock,
    ImplBlock,
    IntExpr,
    InvokeBlock,
    JsonBody,
    ListType,
    MatchBlock,
    MultipartBody,
    PrimitiveType,
    Program,
    RawBody,
    RecordType,
    RescueBlock,
    ResourcesDecl,
    RestBody,
    RestImpl,
    RetryPolicy,
    ShellImpl,
    StepCall,
    StepDecl,
    StrExpr,
    TypeExpr,
    WhileBlock,
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

    graph = FlowGraph(
        steps=tuple(steps_by_name.values()),
        contracts=tuple(contracts.values()),
        flow=flow_ir,
        resources=resources_ir,
    )
    _validate_parallel_for_each(graph)
    return graph


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
            impl=step.impl,
            invoke=step.invoke,
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
    for mt, ft in zip(main.takes, fb.takes, strict=True):
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
                + " -> ".join([*path, name])
            )
        if color[name] == BLACK:
            return
        color[name] = GRAY
        step = steps_by_name[name]
        if step.on_fail is not None:
            for s in step.on_fail.strategies:
                if s.kind == "fallback" and s.fallback_step is not None:
                    visit(s.fallback_step.name, [*path, name])
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
        impl=_build_impl(decl.impl) if decl.impl is not None else None,
        invoke=_build_invoke(decl.invoke) if decl.invoke is not None else None,
        line=decl.line,
    )


def _build_impl(decl: ImplBlock) -> ImplIR:
    if isinstance(decl, CodeImpl):
        return CodeImplIR(lang=decl.lang)
    if isinstance(decl, RestImpl):
        return RestImplIR(
            method=decl.method,
            url=decl.url,
            query=decl.query,
            headers=decl.headers,
            body=_build_rest_body(decl.body) if decl.body is not None else None,
            response_path=decl.response_path,
            timeout_seconds=decl.timeout_seconds,
            retry=_build_retry_policy(decl.retry) if decl.retry is not None else None,
        )
    if isinstance(decl, ShellImpl):
        import shlex
        try:
            argv = tuple(shlex.split(decl.cmd))
        except ValueError as e:
            raise IRBuildError(
                f"line {decl.line}: impl.cmd is not a valid shell tokenization "
                f"({e}); fix unbalanced quotes or escapes"
            ) from e
        if not argv:
            raise IRBuildError(
                f"line {decl.line}: impl.cmd must contain at least one token"
            )
        return ShellImplIR(argv=argv, timeout_seconds=decl.timeout_seconds, parse=decl.parse)
    raise IRBuildError(f"unknown ImplBlock subtype: {type(decl).__name__}")


def _build_rest_body(decl: RestBody) -> RestBodyIR:
    if isinstance(decl, JsonBody):
        return JsonBodyIR(fields=decl.fields)
    if isinstance(decl, RawBody):
        return RawBodyIR(template=decl.template)
    if isinstance(decl, FileBody):
        return FileBodyIR(path=decl.path)
    if isinstance(decl, FormBody):
        return FormBodyIR(fields=decl.fields)
    if isinstance(decl, MultipartBody):
        return MultipartBodyIR(fields=decl.fields)
    raise IRBuildError(f"unknown RestBody subtype: {type(decl).__name__}")


def _build_retry_policy(decl: RetryPolicy) -> RetryPolicyIR:
    return RetryPolicyIR(
        attempts=decl.attempts,
        backoff=decl.backoff,
        base=decl.base,
        cap=decl.cap,
        on=decl.on,
    )


def _build_invoke(decl: InvokeBlock) -> InvokeIR:
    if isinstance(decl, CliInvoke):
        return CliInvokeIR(
            cli=decl.cli,
            model=decl.model,
            output_format=decl.output_format,
            max_turns=decl.max_turns,
        )
    if isinstance(decl, ApiInvoke):
        return ApiInvokeIR(
            protocol=decl.protocol,
            model=decl.model,
            base_url=decl.base_url,
            auth=decl.auth,
            temperature=decl.temperature,
            max_tokens=decl.max_tokens,
            timeout_seconds=decl.timeout_seconds,
            retries=decl.retries,
        )
    raise IRBuildError(f"unknown InvokeBlock subtype: {type(decl).__name__}")


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
    items = _build_flow_items(decl.chain, steps_by_name, contracts, available)
    # The set of step names that appear directly in the top-level chain —
    # used by _build_rescue to enforce the v0.8 "top-level only" rule.
    top_level_step_names: set[str] = {
        item.step_name for item in items if isinstance(item, CallIR)
    }
    seen_rescue_steps: set[str] = set()
    # Scope each RESCUE body to the state fields available BEFORE the
    # protected step runs. If we passed the post-chain `available` dict,
    # rescue bodies could reference fields produced by steps that come
    # AFTER the protected step in source order — but at runtime those
    # later steps never ran (the protected step raised), so state[...]
    # would KeyError. Replay the top-level chain up to (but excluding)
    # the protected step's CallIR; only top-level CallIRs and PARALLEL
    # FOR EACH collectors expose fields to the outer scope.
    rescues_ir = tuple(
        _build_rescue(
            rb, steps_by_name, contracts,
            _scope_before_step(items, rb.step_name, steps_by_name),
            top_level_step_names, seen_rescue_steps,
        )
        for rb in decl.rescues
    )
    return FlowIR(
        name=decl.name,
        chain=tuple(items),
        rescues=rescues_ir,
        line=decl.line,
    )


def _scope_before_step(
    chain_items: list,
    protected_step_name: str,
    steps_by_name: dict[str, StepIR],
) -> dict[str, TypeExpr]:
    """Return the state-field type map visible just BEFORE `protected_step_name`
    runs in the top-level chain. Mirrors the field-publishing logic of
    `_build_flow_items`: top-level CallIRs publish their `step.gives`, and
    PARALLEL FOR EACH with a collector publishes `List<body.gives.type>`.
    Stops as soon as the protected step's CallIR is encountered; nested
    inner blocks (IF/MATCH/WHILE/sequential FOR EACH) do not expose fields
    to the outer scope (no narrowing in v0)."""
    scope: dict[str, TypeExpr] = {}
    for item in chain_items:
        if isinstance(item, CallIR):
            if item.step_name == protected_step_name:
                break
            step = steps_by_name.get(item.step_name)
            if step is not None and step.gives is not None:
                scope[step.gives.name] = step.gives.type
            continue
        if isinstance(item, ForEachIR) and item.parallel and item.collector and len(item.body) == 1:
            body_call = item.body[0]
            if hasattr(body_call, "step_name"):
                body_step = steps_by_name.get(body_call.step_name)
                if body_step is not None and body_step.gives is not None:
                    scope[item.collector] = ListType(inner=body_step.gives.type)
    return scope


def _build_rescue(
    decl: RescueBlock,
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    outer_available: dict[str, TypeExpr],
    top_level_step_names: set[str],
    seen_rescue_steps: set[str],
) -> RescueBlockIR:
    """Build a RescueBlockIR with validation. Rules:

    1. step_name must reference an existing STEP.
    2. step_name must appear in the top-level FLOW chain (v0.8 limitation —
       rescues for steps nested inside FOR EACH / IF / MATCH / WHILE bodies
       are deferred).
    3. Each STEP gets at most one RESCUE.
    4. ON_FAIL ending with abort + RESCUE on the same step is rejected
       (redundant double-abort).
    5. The body's last top-level item must be a CallIR to ``abort``.
    """
    # (1) Step exists.
    step = steps_by_name.get(decl.step_name)
    if step is None:
        raise IRBuildError(
            f"line {decl.line}: RESCUE refers to unknown step "
            f"{decl.step_name!r}"
        )

    # (2) Top-level only.
    if decl.step_name not in top_level_step_names:
        raise IRBuildError(
            f"line {decl.line}: RESCUE target {decl.step_name!r} must appear "
            f"in the top-level FLOW chain (v0.8 limitation)"
        )

    # (3) Single rescue per step.
    if decl.step_name in seen_rescue_steps:
        raise IRBuildError(
            f"line {decl.line}: step {decl.step_name!r} already has a RESCUE "
            f"handler (duplicate)"
        )
    seen_rescue_steps.add(decl.step_name)

    # (4) Reject redundant ON_FAIL trailing-abort + RESCUE on the same step.
    if (
        step.on_fail is not None
        and step.on_fail.strategies
        and step.on_fail.strategies[-1].kind == "abort"
    ):
        raise IRBuildError(
            f"line {decl.line}: 'abort(...)' final clause in ON_FAIL is "
            f"redundant when RESCUE {decl.step_name!r} is declared "
            f"(rescue at line {decl.line}, abort in step at line {step.line})"
        )

    # Build the rescue body. The in_rescue flag flows through every nested
    # block builder so `abort(...)` calls anywhere in the body's recursion
    # tree are accepted.
    body_scope = dict(outer_available)
    body_items = _build_flow_items(
        decl.body, steps_by_name, contracts, body_scope, in_rescue=True,
    )

    # (5) Body terminal abort (top-level only — abort buried in nested
    #     IF/MATCH/WHILE/FOR EACH branches does NOT count).
    if (
        not body_items
        or not isinstance(body_items[-1], CallIR)
        or body_items[-1].step_name != "abort"
    ):
        raise IRBuildError(
            f"line {decl.line}: RESCUE body for {decl.step_name!r} must end "
            f"with abort(...) at the top level of the body chain"
        )

    return RescueBlockIR(
        step_name=decl.step_name,
        body=tuple(body_items),
        line=decl.line,
    )


def _build_flow_items(
    chain: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]",
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> list:
    """Build IR items from a chain of FlowItems. Mutates `available` to track
    fields produced by step.gives so that downstream calls can reference them.

    `in_rescue` is True when this chain is the body of a RESCUE block (or
    nested inside one). It propagates to recursive block builders and to
    `_build_call` which uses it to permit synthetic `abort(...)` calls only
    inside rescue bodies."""
    out: list = []
    for item in chain:
        if isinstance(item, ForEachBlock):
            foreach_ir = _build_for_each(
                item, steps_by_name, contracts, available, in_rescue=in_rescue,
            )
            out.append(foreach_ir)
            # PARALLEL FOR EACH with a collector makes List<body.gives.type> available.
            if foreach_ir.parallel and foreach_ir.collector and len(foreach_ir.body) == 1:
                body_call = foreach_ir.body[0]
                if hasattr(body_call, "step_name"):
                    body_step = steps_by_name.get(body_call.step_name)
                    if body_step is not None and body_step.gives is not None:
                        available[foreach_ir.collector] = ListType(inner=body_step.gives.type)
            continue
        if isinstance(item, IfBlock):
            out.append(_build_if_block(
                item, steps_by_name, contracts, available, in_rescue=in_rescue,
            ))
            continue
        if isinstance(item, MatchBlock):
            out.append(_build_match_block(
                item, steps_by_name, contracts, available, in_rescue=in_rescue,
            ))
            continue
        if isinstance(item, WhileBlock):
            out.append(_build_while_block(
                item, steps_by_name, contracts, available, in_rescue=in_rescue,
            ))
            continue
        # StepCall path
        out.append(_build_call(
            item, steps_by_name, contracts, available, in_rescue=in_rescue,
        ))
        # `abort` is a synthetic terminator inside RESCUE bodies — it has no
        # registered StepIR and produces no state field.
        if item.name == "abort":
            continue
        step = steps_by_name[item.name]
        if step.gives is not None:
            available[step.gives.name] = step.gives.type
    return out


def _build_call(
    call: StepCall,
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> CallIR:
    # `abort` is a synthetic call legal only inside RESCUE bodies. Outside a
    # rescue body the IR builder rejects it (closes the Task 3 passthrough).
    if call.name == "abort":
        if not in_rescue:
            raise IRBuildError(
                f"line {call.line}:{call.col}: abort(...) is only valid "
                f"inside a RESCUE body"
            )
        return CallIR(step_name=call.name, kwargs=call.kwargs, line=call.line)
    if call.name not in steps_by_name:
        raise IRBuildError(
            f"line {call.line}:{call.col}: unknown STEP {call.name!r}"
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

    return CallIR(step_name=call.name, kwargs=call.kwargs, line=call.line)


def _build_if_block(
    decl: IfBlock,
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    outer_available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> IfBlockIR:
    """Validate and build an IF block IR node.

    The condition must be `<state_field>.<sub_field> <op> <literal>` where:
    - <state_field> was produced by an upstream step
    - that field's type is a ContractRef (so it has nested sub-fields)
    - <sub_field> is declared in that contract's RecordType shape
    - <literal> is a string / number / bare-ident (enum value)

    Both branches are built in their own scope copy — fields produced inside
    a branch do not leak to the outer chain (no type narrowing in v0).
    `in_rescue` propagates to nested chains so abort(...) is permitted in
    branches when the IF is itself inside a rescue body."""
    cond_ir = _build_condition(decl.condition, contracts, outer_available, decl.line, decl.col)

    then_scope = dict(outer_available)
    then_items = _build_flow_items(
        decl.then_body, steps_by_name, contracts, then_scope, in_rescue=in_rescue,
    )

    else_scope = dict(outer_available)
    else_items = _build_flow_items(
        decl.else_body, steps_by_name, contracts, else_scope, in_rescue=in_rescue,
    )

    return IfBlockIR(
        condition=cond_ir,
        then_body=tuple(then_items),
        else_body=tuple(else_items),
        line=decl.line,
    )


def _build_condition(
    cond: CompareExpr,
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
    line: int,
    col: int,
) -> ConditionIR:
    """Validate a CompareExpr representing an IF/WHILE condition.

    Left must be a `FieldRefExpr` (`<state_field>.<sub_field>`); right must
    be a literal node (StrExpr / IntExpr / FloatExpr / IdentExpr — the latter
    treated as a bare-ident enum value).
    """
    if not isinstance(cond.left, FieldRefExpr):
        raise IRBuildError(
            f"line {line}:{col}: IF/WHILE condition must start with "
            "<state_field>.<sub_field>, got an unsupported left-hand side"
        )
    state_field = cond.left.step_name
    sub_field = cond.left.field

    if state_field not in available:
        raise IRBuildError(
            f"line {line}:{col}: IF/WHILE references {state_field!r} which is "
            "not produced by any previous step"
        )

    state_type = available[state_field]
    if not isinstance(state_type, ContractRef):
        raise IRBuildError(
            f"line {line}:{col}: IF/WHILE condition reads {state_field}.{sub_field} "
            f"but {state_field!r} is not a CONTRACT — wrap the value in a CONTRACT "
            "to expose named fields for the condition"
        )
    contract = contracts.get(state_type.name)
    if contract is None:
        raise IRBuildError(
            f"line {line}:{col}: unknown contract {state_type.name!r}"
        )

    schema = contract.json_schema or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if sub_field not in props:
        known = sorted(props.keys())
        raise IRBuildError(
            f"line {line}:{col}: contract {state_type.name!r} has no field "
            f"{sub_field!r} (known: {known})"
        )

    if isinstance(cond.right, StrExpr):
        literal_value: object = cond.right.value
        literal_kind = "str"
    elif isinstance(cond.right, IntExpr):
        literal_value = cond.right.value
        literal_kind = "int"
    elif isinstance(cond.right, FloatExpr):
        literal_value = cond.right.value
        literal_kind = "float"
    elif isinstance(cond.right, IdentExpr):
        # Recognize `true` / `false` as bool literals; everything else stays
        # an enum-value ident (str at runtime).
        if cond.right.name == "true":
            literal_value = True
            literal_kind = "bool"
        elif cond.right.name == "false":
            literal_value = False
            literal_kind = "bool"
        else:
            literal_value = cond.right.name
            literal_kind = "ident"
    else:
        raise IRBuildError(
            f"line {line}:{col}: IF/WHILE condition right-hand side must be "
            "a string, number, or identifier literal"
        )

    return ConditionIR(
        step_name=state_field,
        field=sub_field,
        op=cond.op,
        literal_value=literal_value,
        literal_kind=literal_kind,
    )


def _build_while_block(
    decl: WhileBlock,
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    outer_available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> WhileBlockIR:
    """Validate and build a WHILE block IR node. Reuses _build_condition for
    the condition validation (same shape as IF). Body is built in its own
    scope copy. `in_rescue` propagates so abort(...) is permitted inside the
    body when the WHILE is itself inside a rescue body."""
    cond_ir = _build_condition(
        decl.condition, contracts, outer_available, decl.line, decl.col,
    )
    body_scope = dict(outer_available)
    body_items = _build_flow_items(
        decl.body, steps_by_name, contracts, body_scope, in_rescue=in_rescue,
    )
    return WhileBlockIR(
        condition=cond_ir,
        max_iters=decl.max_iters,
        body=tuple(body_items),
        line=decl.line,
    )


def _build_match_block(
    decl: MatchBlock,
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    outer_available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> MatchBlockIR:
    """Validate and build a MATCH block IR node.

    The scrutinee `<state_field>.<sub_field>` must point at an enum sub-field
    of an upstream contract. CASE values are checked against the enum
    variants — any unknown variant is an IR error. DEFAULT, if present, must
    be the last arm. Each arm is built in its own scope copy. `in_rescue`
    propagates so abort(...) is permitted in arms when the MATCH is itself
    inside a rescue body."""
    if not isinstance(decl.scrutinee, FieldRefExpr):
        raise IRBuildError(
            f"line {decl.line}:{decl.col}: MATCH scrutinee must be "
            "<state_field>.<sub_field>"
        )
    state_field = decl.scrutinee.step_name
    sub_field = decl.scrutinee.field
    if state_field not in outer_available:
        raise IRBuildError(
            f"line {decl.line}:{decl.col}: MATCH scrutinee {state_field!r} is "
            "not produced by any previous step"
        )
    state_type = outer_available[state_field]
    if not isinstance(state_type, ContractRef):
        raise IRBuildError(
            f"line {decl.line}:{decl.col}: MATCH scrutinee {state_field}.{sub_field} "
            f"requires {state_field!r} to be a CONTRACT (got "
            f"{_render(state_type)})"
        )
    contract = contracts.get(state_type.name)
    if contract is None:
        raise IRBuildError(
            f"line {decl.line}:{decl.col}: unknown contract {state_type.name!r}"
        )
    schema = contract.json_schema or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if sub_field not in props:
        known = sorted(props.keys())
        raise IRBuildError(
            f"line {decl.line}:{decl.col}: contract {state_type.name!r} has no "
            f"field {sub_field!r} (known: {known})"
        )
    sub_schema = props[sub_field]
    enum_values = sub_schema.get("enum") if isinstance(sub_schema, dict) else None
    if enum_values is None:
        raise IRBuildError(
            f"line {decl.line}:{decl.col}: MATCH on {state_field}.{sub_field} "
            "requires that field to be an enum (got non-enum type)"
        )
    enum_set = set(enum_values)

    cases_ir: list[MatchCaseIR] = []
    seen_values: set[str] = set()
    for arm in decl.cases:
        if arm.value is None:
            # DEFAULT — already enforced last by the parser.
            arm_body = _build_flow_items(
                arm.body, steps_by_name, contracts, dict(outer_available),
                in_rescue=in_rescue,
            )
            cases_ir.append(MatchCaseIR(value=None, body=tuple(arm_body), line=arm.line))
            continue
        if arm.value in seen_values:
            raise IRBuildError(
                f"line {arm.line}:{arm.col}: MATCH has duplicate CASE "
                f"{arm.value!r}"
            )
        if arm.value not in enum_set:
            raise IRBuildError(
                f"line {arm.line}:{arm.col}: CASE {arm.value!r} is not one of "
                f"the enum variants of {state_field}.{sub_field} "
                f"(allowed: {sorted(enum_set)})"
            )
        seen_values.add(arm.value)
        arm_body = _build_flow_items(
            arm.body, steps_by_name, contracts, dict(outer_available),
            in_rescue=in_rescue,
        )
        cases_ir.append(MatchCaseIR(value=arm.value, body=tuple(arm_body), line=arm.line))

    return MatchBlockIR(
        state_field=state_field,
        sub_field=sub_field,
        cases=tuple(cases_ir),
        line=decl.line,
    )


def _build_for_each(
    decl: ForEachBlock,
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    outer_available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> ForEachIR:
    """Validate and build a FOR EACH IR node.

    v0 validation:
    - `collection` must be a state field produced by an upstream step
    - that field must be a ListType (otherwise iteration is undefined)
    - inside the body, `loop_var` is bound to the inner type and is also
      visible to nested calls/loops via the (mutated) inner scope

    `in_rescue` propagates so abort(...) is permitted in the body when the
    FOR EACH is itself inside a rescue body.
    """
    if decl.collection not in outer_available:
        raise IRBuildError(
            f"line {decl.line}:{decl.col}: FOR EACH iterates over {decl.collection!r} "
            f"which is not produced by any previous step"
        )
    coll_type = outer_available[decl.collection]
    if not isinstance(coll_type, ListType):
        raise IRBuildError(
            f"line {decl.line}:{decl.col}: FOR EACH expects {decl.collection!r} to be a List, "
            f"got {_render(coll_type)}"
        )

    inner_available = dict(outer_available)
    inner_available[decl.loop_var] = coll_type.inner

    body_items = _build_flow_items(
        decl.body, steps_by_name, contracts, inner_available, in_rescue=in_rescue,
    )

    return ForEachIR(
        loop_var=decl.loop_var,
        collection=decl.collection,
        body=tuple(body_items),
        line=decl.line,
        parallel=decl.parallel,
        collector=decl.collector,
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
    elif isinstance(t, ConstrainedType):
        _check_refs(t.base, contracts, line, col)


def _validate_parallel_for_each(graph: FlowGraph) -> None:
    """Enforce v1 constraints on FOR EACH PARALLEL blocks.
    Each error includes the source line number from the .clio source."""
    if graph.flow is None:
        return

    steps_by_name = {s.name: s for s in graph.steps}

    # Track state-field names populated upstream so we can detect collector collisions.
    populated: set[str] = set()
    if graph.flow.chain:
        first = graph.flow.chain[0]
        if hasattr(first, "step_name"):
            first_step = steps_by_name.get(first.step_name)
            if first_step is not None:
                for t in first_step.takes:
                    populated.add(t.name)

    def _walk(chain) -> None:
        for elem in chain:
            if hasattr(elem, "step_name"):
                # CallIR — record GIVES into populated state
                step = steps_by_name.get(elem.step_name)
                if step is not None and step.gives is not None:
                    populated.add(step.gives.name)
                continue

            if isinstance(elem, IfBlockIR):
                # Descend into both branches; either may contain PARALLEL FOR EACH.
                _walk(elem.then_body)
                _walk(elem.else_body)
                continue

            if isinstance(elem, MatchBlockIR):
                for arm in elem.cases:
                    _walk(arm.body)
                continue

            if isinstance(elem, WhileBlockIR):
                _walk(elem.body)
                continue

            # ForEachIR
            if elem.parallel:
                if len(elem.body) != 1:
                    raise IRBuildError(
                        f"FOR EACH PARALLEL body must contain exactly one "
                        f"step call in v1 (line {elem.line})"
                    )
                inner = elem.body[0]
                if not hasattr(inner, "step_name"):
                    if hasattr(inner, "parallel") and inner.parallel:
                        raise IRBuildError(
                            f"FOR EACH PARALLEL cannot be nested inside another "
                            f"PARALLEL block in v1 (line {inner.line})"
                        )
                    raise IRBuildError(
                        f"FOR EACH PARALLEL cannot contain nested FOR EACH "
                        f"in v1 (line {elem.line})"
                    )
                step = steps_by_name.get(inner.step_name)
                if step is None or step.gives is None:
                    raise IRBuildError(
                        f"FOR EACH PARALLEL body step "
                        f"{inner.step_name!r} must have a GIVES "
                        f"(line {elem.line})"
                    )
                if elem.collector in populated:
                    raise IRBuildError(
                        f"AS {elem.collector!r} shadows existing state "
                        f"field; rename the collector (line {elem.line})"
                    )
                populated.add(elem.collector)
            else:
                # Sequential FOR EACH — descend; inner may be PARALLEL
                _walk(elem.body)

    _walk(graph.flow.chain)
    for rb in graph.flow.rescues:
        _walk(rb.body)


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
