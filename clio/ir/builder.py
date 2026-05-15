import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    ApiInvokeIR,
    BoolOpIR,
    CacheConfigIR,
    CallIR,
    CliInvokeIR,
    CodeImplIR,
    ConditionIR,
    ContractIR,
    DatabaseSpecIR,
    ErrorAccessIR,
    FieldIR,
    FileBodyIR,
    FlowCallIR,
    FlowGraph,
    FlowIR,
    ForEachIR,
    FormBodyIR,
    HttpServerSpecIR,
    IfBlockIR,
    ImplIR,
    InvokeIR,
    JsonBodyIR,
    MatchBlockIR,
    MatchCaseIR,
    McpServerSpecIR,
    McpToolImplIR,
    MultipartBodyIR,
    OnFailChainIR,
    OnFailStrategyIR,
    PredicateIR,
    RawBodyIR,
    RescueBlockIR,
    ResourcesIR,
    RestBodyIR,
    RestImplIR,
    ResumeIR,
    RetryPolicyIR,
    ShellImplIR,
    SqlImplIR,
    SseServerSpecIR,
    StdioServerSpecIR,
    StepIR,
    TestIR,
    WhileBlockIR,
)
from clio.ir.types import names_equal, types_equal
from clio.parser.ast_nodes import (
    ApiInvoke,
    BoolAndExpr,
    BoolOrExpr,
    CliInvoke,
    CodeImpl,
    ConstrainedType,
    ContractDecl,
    ContractRef,
    DatabaseSpec,
    EnumType,
    ErrorAccessExpr,
    Field,
    FieldRefExpr,
    FileBody,
    FloatExpr,
    FlowDecl,
    ForEachBlock,
    FormBody,
    HttpServerSpec,
    IdentExpr,
    IfBlock,
    ImplBlock,
    IntExpr,
    InvokeBlock,
    JsonBody,
    ListType,
    MatchBlock,
    MatchCase,
    McpServerSpec,
    McpToolImpl,
    MultipartBody,
    OnFailChain,
    OnFailStrategy,
    Predicate,
    PrimitiveType,
    Program,
    RawBody,
    RecordType,
    ReexportDecl,
    RescueBlock,
    ResourcesDecl,
    RestBody,
    RestImpl,
    ResumeAst,
    RetryPolicy,
    ShellImpl,
    SqlImpl,
    SseServerSpec,
    StdioServerSpec,
    StepCall,
    StepDecl,
    StrExpr,
    TestDecl,
    TypeExpr,
    WhileBlock,
)


class IRBuildError(ValueError):
    pass


def build_ir(
    parsed: dict[Path, Program] | Program,
    entry: Path | None = None,
    flow_name: str | None = None,
) -> FlowGraph:
    """Build a FlowGraph from either a single Program (v0.17 callers)
    or a dict[Path, Program] (v0.18 multi-file).

    For the multi-file case, internal (non-exposed) STEP/CONTRACT/FLOW
    names are alpha-renamed to '{file_stem}__{name}' to avoid global
    name collisions in the flat output. Exposed names keep their
    original form. RESOURCES and TEST blocks come from the entry file
    only.
    """
    if isinstance(parsed, Program):
        return _build_ir_single(parsed, flow_name=flow_name)

    if entry is None:
        raise ValueError("build_ir requires `entry` when called with a dict")

    from clio.ir.resolver import (
        compute_exposed_sets,
        validate_imports,
        validate_per_file,
    )
    validate_per_file(parsed, entry=entry)
    exposed_sets = compute_exposed_sets(parsed)
    validate_imports(parsed, exposed_sets)

    merged_program = _flatten_to_program(parsed, entry, exposed_sets)
    return _build_ir_single(merged_program, flow_name=flow_name)


def _build_ir_single(program: Program, flow_name: str | None = None) -> FlowGraph:
    """Build the IR graph for one (already-flattened) Program.

    When the source declares multiple FLOWs, `flow_name` must be set to the
    one the caller wants compiled. With a single FLOW (the common case),
    `flow_name` is ignored if it matches, otherwise rejected. With zero
    FLOWs (compiler-only sources, e.g. a unit of reusable STEPs), the
    result is the same as before.
    """
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

    flow_decls: list[FlowDecl] = [d for d in program.decls if isinstance(d, FlowDecl)]
    flow_names_seen: set[str] = set()
    for d in flow_decls:
        if d.name in flow_names_seen:
            raise IRBuildError(
                f"line {d.line}:{d.col}: duplicate FLOW name {d.name!r}"
            )
        flow_names_seen.add(d.name)

    # v0.17: a STEP and a FLOW cannot share a name (would render call
    # resolution ambiguous).
    for d in flow_decls:
        if d.name in steps_by_name:
            raise IRBuildError(
                f"line {d.line}:{d.col}: name collision — {d.name!r} is already "
                f"declared as a STEP on line {steps_by_name[d.name].line}"
            )

    flow_sigs = _extract_flow_signatures(flow_decls)

    # Build every FlowIR (signed + unsigned). Sub-flow calls resolve
    # against flow_sigs; unsigned flows remain runnable as the main
    # flow but can't be called as sub-flows.
    all_flows: dict[str, FlowIR] = {}
    for d in flow_decls:
        all_flows[d.name] = _build_flow(d, steps_by_name, flow_sigs, contracts)

    _detect_flow_call_cycles(all_flows)

    main: FlowIR | None = None
    if flow_name is not None:
        if flow_name not in all_flows:
            available = ", ".join(sorted(all_flows)) or "<none>"
            raise IRBuildError(
                f"flow {flow_name!r} not found in source (available: {available})"
            )
        main = all_flows[flow_name]
    elif len(all_flows) == 1:
        main = next(iter(all_flows.values()))
    # else: main stays None; targets that need a main (python, langgraph,
    # claude-skill, claude-cli) will fail in their own emit pass.

    resources_ir: ResourcesIR | None = None
    for d in program.decls:
        if isinstance(d, ResourcesDecl):
            if resources_ir is not None:
                raise IRBuildError(
                    f"line {d.line}:{d.col}: only one RESOURCES declaration is allowed"
                )
            resources_ir = ResourcesIR(
                target=d.target,
                models=d.models,
                mcp_servers=tuple(_build_mcp_server_spec(s) for s in d.mcp_servers),
                databases=tuple(_build_database_spec(s) for s in d.databases),
            )

    flows_by_name = {d.name: d for d in flow_decls}
    tests_ir = _build_tests(program, flows_by_name)

    # v0.18: derive exposed_flow_names from the explicit EXPOSE marker
    # on entry-file FlowDecls. The v0.17 sibling-call heuristic is gone.
    exposed_flow_names = frozenset(
        f.name for f in all_flows.values()
        if _was_exposed_in_source(f, program)
    )

    # E_MCP_001: target=mcp-server requires at least one EXPOSE FLOW
    # in the entry file (the public tool surface).
    if (
        resources_ir is not None
        and resources_ir.target == "mcp-server"
        and not exposed_flow_names
    ):
        from clio.ir.resolver import CompileError
        source_str = (
            str(program.source_path) if program.source_path else "<inline>"
        )
        raise CompileError(
            f"{source_str}: target 'mcp-server' requires at least one "
            f"EXPOSE FLOW in the entry file"
        )

    graph = FlowGraph(
        steps=tuple(steps_by_name.values()),
        contracts=tuple(contracts.values()),
        flow=main,
        resources=resources_ir,
        tests=tests_ir,
        flows=tuple(all_flows.values()),
        exposed_flow_names=exposed_flow_names,
    )
    _validate_parallel_for_each(graph)
    _validate_mcp_tool_servers(graph)
    _validate_sql_databases(graph)
    return graph


def _file_stem(path: Path) -> str:
    """Derive a safe identifier prefix from a file path.

    'lib/nlp.clio' → 'nlp'
    'shared-utils.clio' → 'shared_utils'
    """
    return path.stem.replace("-", "_")


def _flatten_to_program(
    parsed: dict[Path, Program],
    entry: Path,
    exposed_sets: dict[Path, dict[str, object]],
) -> Program:
    """Merge multiple Programs into a single Program with internal
    symbols alpha-renamed.

    Convention: internal name X in file 'lib/nlp.clio' becomes
    'nlp__X'. Exposed names keep their original form. RESOURCES
    and TEST blocks are taken only from the entry file. ReexportDecls
    contribute no IR decls (they're consumed by the resolver phases)."""
    all_decls: list[object] = []

    # Per-file rename tables: {original_name: renamed_name}
    rename_tables: dict[Path, dict[str, str]] = {}

    # Pass 1: build rename tables for each file
    for path, program in parsed.items():
        stem = _file_stem(path)
        local_renames: dict[str, str] = {}
        for decl in program.decls:
            if isinstance(decl, (FlowDecl, ContractDecl)):
                if not decl.exposed:
                    local_renames[decl.name] = f"{stem}__{decl.name}"
            elif isinstance(decl, StepDecl):
                # STEPs are always internal in v0.18
                local_renames[decl.name] = f"{stem}__{decl.name}"
        rename_tables[path] = local_renames

    # Pass 2: emit decls with renames applied to internal references,
    # imports collapsed to a flat per-file scope, and RESOURCES/TEST
    # restricted to the entry file.
    for path, program in parsed.items():
        # Build the per-file imported scope: {local_name: target_name}.
        # `target_name` is the post-rename name in the merged program
        # (exposed symbols keep their original form, so the rename
        # table lookup falls through to the identity).
        imported_scope: dict[str, str] = {}
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            child_exposed = exposed_sets.get(child, {})
            child_renames = rename_tables.get(child, {})
            for item in imp.items:
                local_name = item.alias or item.name
                target = child_exposed.get(item.name)
                if target is None:
                    # validate_imports would have already raised; defend
                    # against a partial state.
                    continue
                # The exposed symbol's target name is its original name
                # (exposed names are not in child_renames). If for any
                # reason it is in child_renames, prefer that.
                target_name = child_renames.get(
                    getattr(target, "name", item.name),
                    getattr(target, "name", item.name),
                )
                imported_scope[local_name] = target_name

        # Emit each local decl, applying renames + import resolution.
        is_entry = path == entry
        for decl in program.decls:
            if isinstance(decl, ReexportDecl):
                continue  # re-exports do not produce new decls
            if isinstance(decl, ResourcesDecl):
                if is_entry:
                    all_decls.append(decl)
                continue
            if isinstance(decl, TestDecl):
                if is_entry:
                    all_decls.append(
                        _rename_test_decl(decl, rename_tables[path]),
                    )
                continue
            renamed_decl = _rename_decl(
                decl, rename_tables[path], imported_scope,
            )
            # Only entry-file FLOW/CONTRACT declarations keep the EXPOSE
            # marker in the merged Program. Imported exposed symbols
            # remain visible (their original names are kept) but they are
            # not part of the public surface of the merged compilation
            # unit — `exposed_flow_names` is derived from this flag and
            # must reflect only what the entry file chose to expose.
            if not is_entry and isinstance(renamed_decl, (FlowDecl, ContractDecl)):
                renamed_decl = replace(renamed_decl, exposed=False)
            all_decls.append(renamed_decl)

    return Program(decls=tuple(all_decls), source_path=entry)


_FlowItem = (
    StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst
)
_NestedItem = StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock


def _rename_decl(
    decl: object,
    local_renames: dict[str, str],
    imported_scope: dict[str, str],
) -> object:
    """Apply alpha-renames to one decl. The decl's own name is rewritten
    if it's in `local_renames`; references to other symbols (in type
    expressions, step calls, rescues, on_fail) go through the combined
    resolver `imported_scope` then `local_renames`."""

    def resolve_name(n: str) -> str:
        if n in imported_scope:
            return imported_scope[n]
        return local_renames.get(n, n)

    def rename_type(t: TypeExpr) -> TypeExpr:
        if isinstance(t, ContractRef):
            return ContractRef(name=resolve_name(t.name), line=t.line, col=t.col)
        if isinstance(t, ListType):
            return ListType(inner=rename_type(t.inner))
        if isinstance(t, RecordType):
            return RecordType(
                fields=tuple(
                    (fname, rename_type(ftype)) for fname, ftype in t.fields
                ),
            )
        if isinstance(t, ConstrainedType):
            return ConstrainedType(
                base=rename_type(t.base),
                constraints=t.constraints,
            )
        return t

    def rename_field(f: Field) -> Field:
        return Field(name=f.name, type=rename_type(f.type), line=f.line, col=f.col)

    def rename_kwargs(
        kwargs: tuple[tuple[str, object], ...],
    ) -> tuple[tuple[str, object], ...]:
        out: list[tuple[str, object]] = []
        for kname, value in kwargs:
            if isinstance(value, ErrorAccessExpr):
                out.append((
                    kname,
                    ErrorAccessExpr(
                        step_name=resolve_name(value.step_name),
                        field=value.field,
                        line=value.line,
                    ),
                ))
            else:
                out.append((kname, value))
        return tuple(out)

    def rename_call(c: _FlowItem) -> _FlowItem:
        if isinstance(c, StepCall):
            return StepCall(
                name=resolve_name(c.name),
                kwargs=rename_kwargs(c.kwargs),
                line=c.line,
                col=c.col,
            )
        if isinstance(c, ForEachBlock):
            return ForEachBlock(
                loop_var=c.loop_var,
                collection=c.collection,
                body=tuple(rename_call(x) for x in c.body),
                line=c.line,
                col=c.col,
                parallel=c.parallel,
                collector=c.collector,
            )
        if isinstance(c, IfBlock):
            # IfBlock bodies are the narrower union (no ResumeAst).
            return IfBlock(
                condition=c.condition,
                then_body=tuple(
                    cast(_NestedItem, rename_call(x)) for x in c.then_body
                ),
                else_body=tuple(
                    cast(_NestedItem, rename_call(x)) for x in c.else_body
                ),
                line=c.line,
                col=c.col,
            )
        if isinstance(c, MatchBlock):
            return MatchBlock(
                scrutinee=c.scrutinee,
                cases=tuple(
                    MatchCase(
                        value=case.value,
                        body=tuple(
                            cast(_NestedItem, rename_call(x)) for x in case.body
                        ),
                        line=case.line,
                        col=case.col,
                    )
                    for case in c.cases
                ),
                line=c.line,
                col=c.col,
            )
        if isinstance(c, WhileBlock):
            return WhileBlock(
                condition=c.condition,
                max_iters=c.max_iters,
                body=tuple(
                    cast(_NestedItem, rename_call(x)) for x in c.body
                ),
                line=c.line,
                col=c.col,
            )
        if isinstance(c, ResumeAst):
            return ResumeAst(
                fallback_step=resolve_name(c.fallback_step),
                field_name=c.field_name,
                line=c.line,
                col=c.col,
            )
        return c

    def rename_rescue(r: RescueBlock) -> RescueBlock:
        return RescueBlock(
            step_name=resolve_name(r.step_name),
            body=tuple(rename_call(x) for x in r.body),
            line=r.line,
            col=r.col,
        )

    def rename_on_fail(chain: OnFailChain | None) -> OnFailChain | None:
        if chain is None:
            return None
        new_strategies: list[OnFailStrategy] = []
        for s in chain.strategies:
            if s.kind == "fallback" and s.fallback_step_name is not None:
                new_strategies.append(replace(
                    s, fallback_step_name=resolve_name(s.fallback_step_name),
                ))
            else:
                new_strategies.append(s)
        return OnFailChain(
            strategies=tuple(new_strategies),
            line=chain.line,
            col=chain.col,
        )

    if isinstance(decl, StepDecl):
        return replace(
            decl,
            name=local_renames.get(decl.name, decl.name),
            takes=tuple(rename_field(f) for f in decl.takes),
            gives=rename_field(decl.gives) if decl.gives is not None else None,
            on_fail=rename_on_fail(decl.on_fail),
        )
    if isinstance(decl, ContractDecl):
        return replace(
            decl,
            name=local_renames.get(decl.name, decl.name),
            shape=rename_type(decl.shape),
        )
    if isinstance(decl, FlowDecl):
        return replace(
            decl,
            name=local_renames.get(decl.name, decl.name),
            chain=tuple(rename_call(x) for x in decl.chain),
            rescues=tuple(rename_rescue(r) for r in decl.rescues),
            takes=tuple(rename_field(f) for f in decl.takes),
            gives=tuple(rename_field(f) for f in decl.gives),
        )
    return decl


def _rename_test_decl(
    decl: TestDecl,
    local_renames: dict[str, str],
) -> TestDecl:
    """Apply alpha-renames to a TEST decl. Only `flow_name` may need
    rewriting — WITH kwargs, EXPECTS, and EXPECTS_NOT all reference
    FLOW state field names which are not affected by alpha-renaming."""
    return replace(
        decl,
        flow_name=local_renames.get(decl.flow_name, decl.flow_name),
    )


def _build_tests(
    program: Program,
    flows_by_name: dict[str, FlowDecl],
) -> tuple[TestIR, ...]:
    """Validate TEST decls and lower them to TestIR.

    When the target FLOW declares TAKES, WITH kwargs are checked at compile
    time (name in declared set + Python literal type matches TypeExpr).
    When the target FLOW declares GIVES, EXPECTS / EXPECTS_NOT field paths
    are validated at compile time (root in declared set + dotted path
    resolves through RecordType). When neither is declared, v0.15
    runtime-only behaviour is preserved."""
    seen: set[str] = set()
    out: list[TestIR] = []
    for d in program.decls:
        if not isinstance(d, TestDecl):
            continue
        if d.name in seen:
            raise IRBuildError(
                f"line {d.line}:{d.col}: duplicate TEST name {d.name!r}"
            )
        seen.add(d.name)
        if d.flow_name not in flows_by_name:
            available = ", ".join(sorted(flows_by_name)) or "<none>"
            raise IRBuildError(
                f"line {d.line}:{d.col}: TEST {d.name!r} references unknown "
                f"flow {d.flow_name!r} (available: {available})"
            )
        flow_decl = flows_by_name[d.flow_name]

        # WITH: kwarg validation — only when FLOW declares TAKES.
        if flow_decl.takes:
            declared_takes = {f.name: f.type for f in flow_decl.takes}
            for kw_name, kw_value in d.with_kwargs:
                if kw_name not in declared_takes:
                    available = ", ".join(sorted(declared_takes)) or "<none>"
                    raise IRBuildError(
                        f"line {d.line}:{d.col}: TEST {d.name!r}: WITH "
                        f"kwarg {kw_name!r} is not declared in FLOW "
                        f"{flow_decl.name!r}.TAKES (declared: {available})"
                    )
                declared_type = declared_takes[kw_name]
                if not _literal_matches_type(kw_value, declared_type):
                    raise IRBuildError(
                        f"line {d.line}:{d.col}: TEST {d.name!r}: WITH "
                        f"{kw_name}={kw_value!r} does not match declared type "
                        f"{_render(declared_type)} in FLOW {flow_decl.name!r}.TAKES"
                    )

        # EXPECTS / EXPECTS_NOT field path validation — only when FLOW declares GIVES.
        if flow_decl.gives:
            declared_gives = {f.name: f.type for f in flow_decl.gives}
            for clause_name, clauses in (("EXPECTS", d.expects), ("EXPECTS_NOT", d.expects_not)):
                for field_path, _pred in clauses:
                    segments = field_path.split(".")
                    root = segments[0]
                    if root not in declared_gives:
                        available = ", ".join(sorted(declared_gives)) or "<none>"
                        raise IRBuildError(
                            f"line {d.line}:{d.col}: TEST {d.name!r}: {clause_name} "
                            f"field path {field_path!r} root {root!r} is not in "
                            f"FLOW {flow_decl.name!r}.GIVES (declared: {available})"
                        )
                    # Walk deeper segments through RecordType; defer ContractRef to runtime.
                    cursor_type = declared_gives[root]
                    for seg in segments[1:]:
                        if isinstance(cursor_type, RecordType):
                            fields_by_name = dict(cursor_type.fields)
                            if seg not in fields_by_name:
                                raise IRBuildError(
                                    f"line {d.line}:{d.col}: TEST {d.name!r}: "
                                    f"{clause_name} field path {field_path!r}: "
                                    f"segment {seg!r} not in record"
                                )
                            cursor_type = fields_by_name[seg]
                        elif isinstance(cursor_type, ContractRef):
                            # Defer deep field-path validation to runtime.
                            break
                        else:
                            raise IRBuildError(
                                f"line {d.line}:{d.col}: TEST {d.name!r}: "
                                f"{clause_name} field path {field_path!r}: cannot "
                                f"navigate into {_render(cursor_type)}"
                            )

        out.append(TestIR(
            name=d.name,
            flow_name=d.flow_name,
            with_kwargs=d.with_kwargs,
            expects=tuple((f, _lower_predicate(p)) for f, p in d.expects),
            expects_not=tuple((f, _lower_predicate(p)) for f, p in d.expects_not),
            line=d.line,
        ))
    return tuple(out)


def _lower_predicate(p: Predicate) -> PredicateIR:
    return PredicateIR(kind=p.kind, value=p.value)


@dataclass(frozen=True)
class FlowSignature:
    """Lightweight projection of a FlowDecl used for call-site resolution.
    Only flows that explicitly declared TAKES *and* GIVES are callable."""

    name: str
    takes: tuple[Field, ...]
    gives: tuple[Field, ...]
    line: int


def _extract_flow_signatures(
    flow_decls: list[FlowDecl],
) -> dict[str, FlowSignature]:
    """Pass 0.5 (v0.17): collect signatures of FLOWs that declare BOTH
    TAKES and GIVES. Unsigned FLOWs are silently omitted (they remain
    runnable as the main flow but cannot be called as sub-flows)."""
    sigs: dict[str, FlowSignature] = {}
    for d in flow_decls:
        if d.takes and d.gives:
            sigs[d.name] = FlowSignature(
                name=d.name, takes=d.takes, gives=d.gives, line=d.line,
            )
    return sigs


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
            description=step.description,
            strategies=step.strategies,
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


def _collect_flow_call_names(
    items: "tuple[object, ...]",
) -> set[str]:
    """Walk a FLOW chain (or nested body) and collect every FLOW name
    invoked via FlowCallIR, including inside FOR EACH / IF / MATCH /
    WHILE bodies."""
    out: set[str] = set()
    for it in items:
        if isinstance(it, FlowCallIR):
            out.add(it.flow_name)
        elif isinstance(it, (ForEachIR, WhileBlockIR)):
            out.update(_collect_flow_call_names(it.body))
        elif isinstance(it, IfBlockIR):
            out.update(_collect_flow_call_names(it.then_body))
            out.update(_collect_flow_call_names(it.else_body))
        elif isinstance(it, MatchBlockIR):
            for arm in it.cases:
                out.update(_collect_flow_call_names(arm.body))
    return out


def _collect_flow_call_names_rescues(
    rescues: "tuple[RescueBlockIR, ...]",
) -> set[str]:
    out: set[str] = set()
    for r in rescues:
        out.update(_collect_flow_call_names(r.body))
    return out


def _detect_flow_call_cycles(flows: dict[str, FlowIR]) -> None:
    """DFS three-color cycle detection over the flow->flow call graph.
    Self-edges are reported as 'recursion'; longer cycles as 'cycle'."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in flows}
    edges: dict[str, set[str]] = {
        n: _collect_flow_call_names(f.chain) | _collect_flow_call_names_rescues(f.rescues)
        for n, f in flows.items()
    }

    def visit(name: str, path: list[str]) -> None:
        color[name] = GRAY
        for nb in sorted(edges.get(name, ())):
            if nb not in flows:
                continue
            if nb == name:
                f = flows[name]
                raise IRBuildError(
                    f"line {f.line}:0: FLOW {name!r} calls itself "
                    f"(recursion not supported in v0.17)"
                )
            if color[nb] == GRAY:
                f = flows[name]
                raise IRBuildError(
                    f"line {f.line}:0: sub-flow call creates a cycle: "
                    f"{' -> '.join([*path, name, nb])}"
                )
            if color[nb] == WHITE:
                visit(nb, [*path, name])
        color[name] = BLACK

    for n in flows:
        if color[n] == WHITE:
            visit(n, [])


def _was_exposed_in_source(flow_ir: FlowIR, program: Program) -> bool:
    """Return True if the FLOW was declared with EXPOSE in the source
    program. Used by `_build_ir_single` to derive `exposed_flow_names`
    from the explicit v0.18 marker instead of the v0.17 sibling-call
    heuristic."""
    for decl in program.decls:
        if (
            isinstance(decl, FlowDecl)
            and decl.name == flow_ir.name
            and decl.exposed
        ):
            return True
    return False


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
        description=decl.description,
        strategies=decl.strategies,
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
    if isinstance(decl, McpToolImpl):
        return McpToolImplIR(
            server=decl.server,
            tool=decl.tool,
            args=decl.args,
            timeout_seconds=decl.timeout_seconds,
            parse=decl.parse,
        )
    if isinstance(decl, SqlImpl):
        return SqlImplIR(db=decl.db, query=decl.query)
    raise IRBuildError(f"unknown ImplBlock subtype: {type(decl).__name__}")


def _build_mcp_server_spec(decl: McpServerSpec) -> McpServerSpecIR:
    if isinstance(decl, StdioServerSpec):
        return StdioServerSpecIR(
            name=decl.name,
            command=decl.command,
            args=decl.args,
            env=decl.env,
        )
    if isinstance(decl, SseServerSpec):
        return SseServerSpecIR(name=decl.name, url=decl.url, headers=decl.headers)
    if isinstance(decl, HttpServerSpec):
        return HttpServerSpecIR(name=decl.name, url=decl.url, headers=decl.headers)
    raise IRBuildError(f"unknown McpServerSpec subtype: {type(decl).__name__}")


def _build_database_spec(decl: DatabaseSpec) -> DatabaseSpecIR:
    return DatabaseSpecIR(name=decl.name, driver=decl.driver, url=decl.url)


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
    flow_sigs: dict[str, FlowSignature],
    contracts: dict[str, ContractIR],
) -> FlowIR:
    available: dict[str, TypeExpr] = {}

    # Reject duplicate FLOW.TAKES at the AST Field's own source position
    # so the error points at the offending line/col, matching the rest of
    # this file's `line {L}:{C}: ...` IRBuildError convention.
    if decl.takes:
        seen: set[str] = set()
        for f in decl.takes:
            if f.name in seen:
                raise IRBuildError(
                    f"line {f.line}:{f.col}: FLOW {decl.name!r} "
                    f"duplicate TAKES field {f.name!r}"
                )
            seen.add(f.name)

    takes_ir: tuple[FieldIR, ...] = tuple(
        FieldIR(name=f.name, type=f.type) for f in decl.takes
    )
    if takes_ir:
        # Declared TAKES is the single source of truth — seed `available`
        # directly and DO NOT run the auto-promote.
        for fi in takes_ir:
            available[fi.name] = fi.type
    else:
        # Issue #19: auto-promote the first step's identifier kwargs that
        # don't match an upstream produced field as FLOW-level inputs. They
        # are passed at runtime via `run(**initial)` and seeded into
        # `state[]`, so the IR builder must accept them here even though no
        # prior step produced them. Only the FIRST step gets this treatment
        # — every later call still strictly validates against produced
        # fields. Type is taken from the matching TAKES entry on the first
        # step so downstream type-checks work.
        if decl.chain and isinstance(decl.chain[0], StepCall):
            first = decl.chain[0]
            first_step = steps_by_name.get(first.name)
            if first_step is not None:
                takes_by_name = {t.name: t.type for t in first_step.takes}
                for kw_name, kw_value in first.kwargs:
                    if not (isinstance(kw_value, str) and kw_value.startswith("@")):
                        continue
                    ref = kw_value[1:]
                    taken_type = takes_by_name.get(kw_name)
                    if taken_type is not None and ref not in available:
                        available[ref] = taken_type

    items = _build_flow_items(decl.chain, steps_by_name, flow_sigs, contracts, available)
    # The set of step names that appear directly in the top-level chain —
    # used by _build_rescue to enforce the v0.8 "top-level only" rule.
    top_level_step_names: set[str] = {
        item.step_name for item in items if isinstance(item, CallIR)
    }
    # v0.16: validate FLOW.GIVES against the chain's final effective state.
    # Subset semantics: each declared GIVES field must exist in `available`
    # with a structurally-equivalent type. Extra fields in `available` are
    # allowed and remain internal to the flow.
    gives_ir: tuple[FieldIR, ...] = tuple(
        FieldIR(name=f.name, type=f.type) for f in decl.gives
    )
    if gives_ir:
        seen_gives: set[str] = set()
        for f in decl.gives:
            if f.name in seen_gives:
                raise IRBuildError(
                    f"line {f.line}:{f.col}: FLOW {decl.name!r} "
                    f"duplicate GIVES field {f.name!r}"
                )
            seen_gives.add(f.name)
        for f in decl.gives:
            if f.name not in available:
                raise IRBuildError(
                    f"line {f.line}:{f.col}: FLOW {decl.name!r} declares "
                    f"GIVES field {f.name!r} but no step in the chain "
                    f"produces it"
                )
            actual_type = available[f.name]
            if not types_equal(f.type, actual_type, contracts):
                raise IRBuildError(
                    f"line {f.line}:{f.col}: FLOW {decl.name!r} declares "
                    f"GIVES field {f.name!r} as {_render(f.type)} but the "
                    f"chain produces {_render(actual_type)}"
                )

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
            rb, steps_by_name, flow_sigs, contracts,
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
        takes=takes_ir,
        gives=gives_ir,
        description=decl.description,
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


def _validate_error_accesses(body_items: list, rescued_step_name: str) -> None:
    """Walk a RESCUE body's IR items and validate every ErrorAccessIR found.

    Rules:
    - `rescued_step` must equal `rescued_step_name` (the step this RESCUE protects).
    - `field` must be one of {"message", "type"}.

    Only walks direct (top-level) CallIR kwargs; nested block builders call
    _build_rescue which invokes this helper for their own body slices.
    """
    _VALID_ERROR_FIELDS = {"message", "type"}
    for item in body_items:
        if not isinstance(item, CallIR):
            continue
        for _kname, value in item.kwargs:
            if not isinstance(value, ErrorAccessIR):
                continue
            if value.rescued_step != rescued_step_name:
                raise IRBuildError(
                    f"line {value.line}: <step>.error.<field>: can only reference "
                    f"the step protected by this RESCUE "
                    f"(got {value.rescued_step!r}, expected {rescued_step_name!r})"
                )
            if value.field not in _VALID_ERROR_FIELDS:
                raise IRBuildError(
                    f"line {value.line}: unknown error field {value.field!r}, "
                    f"expected one of {sorted(_VALID_ERROR_FIELDS)}"
                )


def _build_rescue(
    decl: RescueBlock,
    steps_by_name: dict[str, StepIR],
    flow_sigs: dict[str, FlowSignature],
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
        decl.body, steps_by_name, flow_sigs, contracts, body_scope, in_rescue=True,
    )

    # (6) ErrorAccessIR cross-step and unknown-field validation.
    _validate_error_accesses(body_items, decl.step_name)

    # (5) Body terminal: must be abort(...) or RESUME(<step>.<field>) at the
    #     top level.
    _last = body_items[-1] if body_items else None
    _is_abort = isinstance(_last, CallIR) and _last.step_name == "abort"
    _is_resume = isinstance(_last, ResumeIR)
    if not body_items or not (_is_abort or _is_resume):
        raise IRBuildError(
            f"line {decl.line}: RESCUE body for {decl.step_name!r} must end "
            f"with abort(...) or RESUME(...) at the top level of the body chain"
        )

    # (5b) If RESUME, validate: fallback_step called earlier, field exists,
    #      and its type matches the rescued step's GIVES type.
    if _is_resume:
        assert isinstance(_last, ResumeIR)
        chain_call_names = [
            item.step_name for item in body_items[:-1] if isinstance(item, CallIR)
        ]
        if _last.fallback_step not in chain_call_names:
            raise IRBuildError(
                f"line {_last.line}: RESUME({_last.fallback_step}.{_last.field_name}): "
                f"step {_last.fallback_step!r} is not called in this RESCUE handler"
            )
        fallback_step_ir = steps_by_name[_last.fallback_step]
        fb_gives = fallback_step_ir.gives
        if fb_gives is None or fb_gives.name != _last.field_name:
            known = ([fb_gives.name] if fb_gives is not None else [])
            raise IRBuildError(
                f"line {_last.line}: RESUME({_last.fallback_step}.{_last.field_name}): "
                f"{_last.field_name!r} is not a field of step {_last.fallback_step!r}'s "
                f"GIVES (got: {sorted(known)})"
            )
        rescued_step_ir = steps_by_name[decl.step_name]
        rescued_gives = rescued_step_ir.gives
        if rescued_gives is None:
            raise IRBuildError(
                f"line {_last.line}: RESUME({_last.fallback_step}.{_last.field_name}): "
                f"rescued step {decl.step_name!r} has no GIVES field"
            )
        if not (
            types_equal(fb_gives.type, rescued_gives.type, contracts)
            or names_equal(fb_gives.type, rescued_gives.type)
        ):
            raise IRBuildError(
                f"line {_last.line}: RESUME({_last.fallback_step}.{_last.field_name}): "
                f"type {_render(fb_gives.type)} is incompatible with rescued step's "
                f"GIVES type {_render(rescued_gives.type)}"
            )

    return RescueBlockIR(
        step_name=decl.step_name,
        body=tuple(body_items),
        line=decl.line,
    )


def _build_flow_items(
    chain: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst, ...]",
    steps_by_name: dict[str, StepIR],
    flow_sigs: dict[str, FlowSignature],
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
                item, steps_by_name, flow_sigs, contracts, available, in_rescue=in_rescue,
            )
            out.append(foreach_ir)
            # PARALLEL FOR EACH with a collector makes List<body.gives.type> available.
            # v0.17: a sub-flow body publishes List<sub_flow.gives.type> when the
            # sub-flow has a single GIVES; multi-GIVES sub-flows defer publishing
            # (the collector still exists at runtime as a list[dict] but no
            # parent type-check can rely on it).
            if foreach_ir.parallel and foreach_ir.collector and len(foreach_ir.body) == 1:
                body_call = foreach_ir.body[0]
                if isinstance(body_call, CallIR):
                    body_step = steps_by_name.get(body_call.step_name)
                    if body_step is not None and body_step.gives is not None:
                        available[foreach_ir.collector] = ListType(inner=body_step.gives.type)
                elif isinstance(body_call, FlowCallIR):
                    sub_sig = flow_sigs.get(body_call.flow_name)
                    if sub_sig is not None and len(sub_sig.gives) == 1:
                        available[foreach_ir.collector] = ListType(inner=sub_sig.gives[0].type)
            continue
        if isinstance(item, IfBlock):
            out.append(_build_if_block(
                item, steps_by_name, flow_sigs, contracts, available, in_rescue=in_rescue,
            ))
            continue
        if isinstance(item, MatchBlock):
            out.append(_build_match_block(
                item, steps_by_name, flow_sigs, contracts, available, in_rescue=in_rescue,
            ))
            continue
        if isinstance(item, WhileBlock):
            out.append(_build_while_block(
                item, steps_by_name, flow_sigs, contracts, available, in_rescue=in_rescue,
            ))
            continue
        if isinstance(item, ResumeAst):
            out.append(ResumeIR(
                fallback_step=item.fallback_step,
                field_name=item.field_name,
                line=item.line,
            ))
            continue
        # StepCall path
        out.append(_build_call(
            item, steps_by_name, flow_sigs, contracts, available, in_rescue=in_rescue,
        ))
        # `abort` is a synthetic terminator inside RESCUE bodies — it has no
        # registered StepIR and produces no state field.
        if item.name == "abort":
            continue
        if item.name in steps_by_name:
            step = steps_by_name[item.name]
            if step.gives is not None:
                available[step.gives.name] = step.gives.type
        elif item.name in flow_sigs:
            sig = flow_sigs[item.name]
            for g in sig.gives:
                available[g.name] = g.type
    return out


def _build_call(
    call: StepCall,
    steps_by_name: dict[str, StepIR],
    flow_sigs: dict[str, FlowSignature],
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> "CallIR | FlowCallIR":
    # `abort` is a synthetic call legal only inside RESCUE bodies. Outside a
    # rescue body the IR builder rejects it.
    if call.name == "abort":
        if not in_rescue:
            raise IRBuildError(
                f"line {call.line}:{call.col}: abort(...) is only valid "
                f"inside a RESCUE body"
            )
        return CallIR(step_name=call.name, kwargs=call.kwargs, line=call.line)

    if call.name in steps_by_name:
        return _build_step_call(call, steps_by_name, contracts, available, in_rescue)
    if call.name in flow_sigs:
        return _build_flow_call(call, flow_sigs, contracts, available)

    # Unknown name. Help users who tried to call an unsigned FLOW.
    raise IRBuildError(
        f"line {call.line}:{call.col}: unknown STEP or signed FLOW "
        f"{call.name!r} (signed FLOWs must declare both TAKES and GIVES)"
    )


def _build_step_call(
    call: StepCall,
    steps_by_name: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
    in_rescue: bool = False,
) -> CallIR:
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

    new_kwargs: list[tuple[str, object]] = []
    for name, value in call.kwargs:
        if isinstance(value, ErrorAccessExpr):
            if not in_rescue:
                raise IRBuildError(
                    f"line {value.line}: step.error.<field> is only valid inside a RESCUE handler"
                )
            ir_value: object = ErrorAccessIR(
                rescued_step=value.step_name,
                field=value.field,
                line=value.line,
            )
        else:
            ir_value = value
        new_kwargs.append((name, ir_value))
    return CallIR(step_name=call.name, kwargs=tuple(new_kwargs), line=call.line)


def _build_flow_call(
    call: StepCall,
    flow_sigs: dict[str, FlowSignature],
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
) -> FlowCallIR:
    sig = flow_sigs[call.name]
    provided = dict(call.kwargs)
    for taken in sig.takes:
        if taken.name not in provided:
            raise IRBuildError(
                f"line {call.line}:{call.col}: FLOW {sig.name} requires "
                f"kwarg {taken.name!r}, got {sorted(provided)}"
            )
        value = provided[taken.name]
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            if ref not in available:
                raise IRBuildError(
                    f"line {call.line}:{call.col}: state reference "
                    f"{ref!r} not produced by any previous step"
                )
            ref_type = available[ref]
            if not (
                types_equal(ref_type, taken.type, contracts)
                or names_equal(ref_type, taken.type)
            ):
                raise IRBuildError(
                    f"line {call.line}:{call.col}: type mismatch on "
                    f"{taken.name!r}: FLOW {sig.name} expects "
                    f"{_render(taken.type)}, parent provides "
                    f"{_render(ref_type)}"
                )
    return FlowCallIR(
        flow_name=call.name, kwargs=tuple(call.kwargs), line=call.line,
    )


def _build_if_block(
    decl: IfBlock,
    steps_by_name: dict[str, StepIR],
    flow_sigs: dict[str, FlowSignature],
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
        decl.then_body, steps_by_name, flow_sigs, contracts, then_scope, in_rescue=in_rescue,
    )

    else_scope = dict(outer_available)
    else_items = _build_flow_items(
        decl.else_body, steps_by_name, flow_sigs, contracts, else_scope, in_rescue=in_rescue,
    )

    return IfBlockIR(
        condition=cond_ir,
        then_body=tuple(then_items),
        else_body=tuple(else_items),
        line=decl.line,
    )


def _build_condition(
    cond,
    contracts: dict[str, ContractIR],
    available: dict[str, TypeExpr],
    line: int,
    col: int,
):
    """Validate an IF/WHILE condition AST and return its IR equivalent.

    The AST is either a `CompareExpr` (leaf) or a `BoolAndExpr` /
    `BoolOrExpr` composing two sub-conditions. Composition is recursive
    so each leaf still goes through the same validation (state field
    exists, sub-field exists, RHS is a literal of a known kind).
    Returns a `ConditionIR` for a leaf or a `BoolOpIR` for a composition.
    """
    if isinstance(cond, (BoolAndExpr, BoolOrExpr)):
        op = "and" if isinstance(cond, BoolAndExpr) else "or"
        left_ir = _build_condition(cond.left, contracts, available, line, col)
        right_ir = _build_condition(cond.right, contracts, available, line, col)
        return BoolOpIR(op=op, left=left_ir, right=right_ir)

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
    flow_sigs: dict[str, FlowSignature],
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
        decl.body, steps_by_name, flow_sigs, contracts, body_scope, in_rescue=in_rescue,
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
    flow_sigs: dict[str, FlowSignature],
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
                arm.body, steps_by_name, flow_sigs, contracts, dict(outer_available),
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
            arm.body, steps_by_name, flow_sigs, contracts, dict(outer_available),
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
    flow_sigs: dict[str, FlowSignature],
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
        decl.body, steps_by_name, flow_sigs, contracts, inner_available, in_rescue=in_rescue,
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
    # v0.17 — FlowCallIR body lookup (validates the called sub-flow declares GIVES).
    flows_by_name = {f.name: f for f in graph.flows}

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
            if isinstance(elem, CallIR):
                # CallIR — record GIVES into populated state
                step = steps_by_name.get(elem.step_name)
                if step is not None and step.gives is not None:
                    populated.add(step.gives.name)
                continue

            if isinstance(elem, FlowCallIR):
                # Sub-flow call — parallel-FOR-EACH constraints don't apply.
                # Sub-flow IRs are not yet wired into FlowGraph (Task 5+6),
                # so the produced field name is not tracked here.
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

            if isinstance(elem, ResumeIR):
                continue

            # ForEachIR
            if elem.parallel:
                if len(elem.body) != 1:
                    raise IRBuildError(
                        f"FOR EACH PARALLEL body must contain exactly one "
                        f"step or sub-flow call in v1 (line {elem.line})"
                    )
                inner = elem.body[0]
                if isinstance(inner, CallIR):
                    step = steps_by_name.get(inner.step_name)
                    if step is None or step.gives is None:
                        raise IRBuildError(
                            f"FOR EACH PARALLEL body step "
                            f"{inner.step_name!r} must have a GIVES "
                            f"(line {elem.line})"
                        )
                elif isinstance(inner, FlowCallIR):
                    # v0.17 — sub-flow call as the body. The sub-flow must be
                    # signed (declares GIVES); the IR builder already enforces
                    # signature presence when resolving FlowCallIR, so we only
                    # need a defensive lookup here. The collector receives a
                    # list of the sub-flow's GIVES dicts at runtime.
                    sub = flows_by_name.get(inner.flow_name)
                    if sub is None or not sub.gives:
                        raise IRBuildError(
                            f"FOR EACH PARALLEL body sub-flow "
                            f"{inner.flow_name!r} must declare GIVES "
                            f"(line {elem.line})"
                        )
                else:
                    if hasattr(inner, "parallel") and inner.parallel:
                        raise IRBuildError(
                            f"FOR EACH PARALLEL cannot be nested inside another "
                            f"PARALLEL block in v1 (line {inner.line})"
                        )
                    raise IRBuildError(
                        f"FOR EACH PARALLEL cannot contain nested FOR EACH "
                        f"in v1 (line {elem.line})"
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


def _validate_mcp_tool_servers(graph: FlowGraph) -> None:
    """Cross-validate impl.mcp_tool steps against RESOURCES.mcp_servers.

    Checks:
      1. Every `impl.mcp_tool.server` references a name declared in
         `RESOURCES.mcp_servers` (compile-time error otherwise).
      2. `impl.mcp_tool.parse: text` requires GIVES of type `str`
         (compile-time error otherwise — the runtime cannot coerce a
         non-text content block into a non-str shape).
      3. Server specs declared but never referenced trigger a `stderr`
         warning ('dead spec' lint).
    """
    mcp_steps = [s for s in graph.steps if isinstance(s.impl, McpToolImplIR)]
    declared: dict[str, McpServerSpecIR] = {}
    if graph.resources is not None:
        declared = {s.name: s for s in graph.resources.mcp_servers}

    referenced: set[str] = set()
    for step in mcp_steps:
        impl = step.impl
        assert isinstance(impl, McpToolImplIR)
        if impl.server not in declared:
            available = sorted(declared.keys())
            hint = (
                f" (available: {available})"
                if available
                else " (RESOURCES.mcp_servers is empty or absent)"
            )
            raise IRBuildError(
                f"STEP {step.name!r}: impl.mcp_tool.server "
                f"{impl.server!r} is not declared in RESOURCES.mcp_servers"
                f"{hint}"
            )
        referenced.add(impl.server)
        if impl.parse == "text":
            if step.gives is None or not (
                isinstance(step.gives.type, PrimitiveType)
                and step.gives.type.name == "str"
            ):
                gtype = (
                    _render(step.gives.type) if step.gives is not None else "(no GIVES)"
                )
                raise IRBuildError(
                    f"STEP {step.name!r}: impl.mcp_tool.parse: text requires "
                    f"GIVES of type 'str', got {gtype}"
                )

    unused = sorted(set(declared) - referenced)
    for name in unused:
        print(
            f"warning: RESOURCES.mcp_servers.{name} is declared but never "
            f"referenced by any impl.mcp_tool step (dead spec)",
            file=sys.stderr,
        )


def _validate_sql_databases(graph: FlowGraph) -> None:
    """Cross-validate impl.sql steps against RESOURCES.databases.

    Checks:
      1. Every `impl.sql.db` references a name declared in
         `RESOURCES.databases` (compile-time error otherwise).
      2. Every impl.sql STEP must declare a `GIVES` (the runtime cannot
         do anything useful with a SELECT result that has no target shape).
      3. Database specs declared but never referenced trigger a `stderr`
         warning ('dead spec' lint, mirroring `mcp_servers`).
    """
    sql_steps = [s for s in graph.steps if isinstance(s.impl, SqlImplIR)]
    declared: dict[str, DatabaseSpecIR] = {}
    if graph.resources is not None:
        declared = {d.name: d for d in graph.resources.databases}

    referenced: set[str] = set()
    for step in sql_steps:
        impl = step.impl
        assert isinstance(impl, SqlImplIR)
        if impl.db not in declared:
            available = sorted(declared.keys())
            hint = (
                f" (available: {available})"
                if available
                else " (RESOURCES.databases is empty or absent)"
            )
            raise IRBuildError(
                f"STEP {step.name!r}: impl.sql.db {impl.db!r} is not declared "
                f"in RESOURCES.databases{hint}"
            )
        referenced.add(impl.db)
        if step.gives is None:
            raise IRBuildError(
                f"STEP {step.name!r}: impl.sql requires a GIVES declaration "
                f"(the runtime maps query rows onto the GIVES shape)"
            )

    unused = sorted(set(declared) - referenced)
    for name in unused:
        print(
            f"warning: RESOURCES.databases.{name} is declared but never "
            f"referenced by any impl.sql step (dead spec)",
            file=sys.stderr,
        )


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


def _literal_matches_type(value: object, t: TypeExpr) -> bool:
    """Liberal compatibility check: does the Python value satisfy the
    declared TypeExpr enough to be a valid TEST WITH-kwarg?

    Strictness rules:
    - PrimitiveType('int')   → int (rejects bool subclass)
    - PrimitiveType('float') → int or float (Python convention)
    - PrimitiveType('str')   → str
    - PrimitiveType('bool')  → bool
    - ListType(inner)        → list, every element matches inner
    - RecordType(fields)     → dict, every declared key present, each value matches
    - ContractRef(name)      → dict (delegating deep validation to runtime Pydantic)
    - EnumType(values)       → str in values
    - ConstrainedType(base)  → matches base (constraints checked at runtime)
    """
    if isinstance(t, PrimitiveType):
        if t.name == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if t.name == "float":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if t.name == "str":
            return isinstance(value, str)
        if t.name == "bool":
            return isinstance(value, bool)
        return False
    if isinstance(t, ListType):
        if not isinstance(value, list):
            return False
        return all(_literal_matches_type(item, t.inner) for item in value)
    if isinstance(t, RecordType):
        if not isinstance(value, dict):
            return False
        for fname, ftype in t.fields:
            if fname not in value:
                return False
            if not _literal_matches_type(value[fname], ftype):
                return False
        return True
    if isinstance(t, ContractRef):
        # Compile-time defer: accept any dict (runtime Pydantic catches finer mismatches).
        return isinstance(value, dict)
    if isinstance(t, EnumType):
        return isinstance(value, str) and value in t.values
    if isinstance(t, ConstrainedType):
        return _literal_matches_type(value, t.base)
    return False
