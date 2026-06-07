"""target: swift — graph-bound renderers + compile-time validation."""
from __future__ import annotations

import json

from clio.emitters._shared_utils import _collect_contract_refs, _shape_from_schema, _to_class_name
from clio.ir.graph import (
    ApiInvokeIR,
    CacheConfigIR,
    CliInvokeIR,
    ContractIR,
    FlowCallIR,
    FlowGraph,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    JsonBodyIR,
    MatchBlockIR,
    McpToolImplIR,
    RawBodyIR,
    RescueBlockIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
    StepIR,
    WhileBlockIR,
)
from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    DictType,
    EnumType,
    ListType,
    OptionalType,
    PrimitiveType,
    RecordType,
    TypeExpr,
)

# ---------------------------------------------------------------------------
# Compile-time validation error codes (permanent — stable across releases)
# ---------------------------------------------------------------------------

E_SWIFT_001 = (
    "E_SWIFT_001: target: swift can only embed exact step bodies in Swift "
    "(LANG: swift or LANG: auto). For Python/Go/Bash/etc., use --target "
    "python or --target go."
)
E_SWIFT_002 = (
    "E_SWIFT_002: target: swift does not subprocess 'claude -p'. Use "
    "--target python, --target mcp-server, or --target claude-cli."
)
E_SWIFT_003 = (
    "E_SWIFT_003: target: swift ships the Anthropic SDK only. "
    "Use --target python for Bedrock/Vertex."
)
E_SWIFT_004 = "E_SWIFT_004: source declares no FLOW; nothing to orchestrate."
E_SWIFT_005 = (
    "E_SWIFT_005: target: swift does not yet support invoke.protocol: openai. "
    "Use --target python until the Swift OpenAI emitter ships."
)
E_SWIFT_006 = (
    "E_SWIFT_006: target: swift does not support a multi-GIVES sub-flow used "
    "as a FOR EACH ... PARALLEL body — a single typed Array collector cannot "
    "hold multiple GIVES fields. Use --target python, or give the sub-flow a "
    "single GIVES field."
)
E_SWIFT_009 = (
    "E_SWIFT_009: target: swift does not yet support impl.mode: sql. "
    "Use --target python until the Swift SQL emitter ships."
)
E_SWIFT_010 = (
    "E_SWIFT_010: target: swift does not yet support impl.mode: mcp_tool. "
    "Use --target python until the Swift MCP emitter ships."
)
E_SWIFT_012 = (
    "E_SWIFT_012: target: swift does not yet emit TEST blocks. "
    "Use --target python until the Swift TEST emitter ships."
)
E_SWIFT_013 = (
    "E_SWIFT_013: target: swift impl.rest supports json and raw bodies only; "
    "form/file/multipart are not yet supported — use --target python."
)

# Langs accepted by the swift target on exact steps (no LANG = None = auto-detect).
_SWIFT_OK_LANGS: frozenset[str | None] = frozenset({"swift", "auto", None})

_SWIFT_PRIMITIVES: dict[str, str] = {
    "str": "String",
    "int": "Int",
    "float": "Double",
    "bool": "Bool",
    "any": "Any",
}


def _type_to_swift(t: TypeExpr, contracts: dict[str, ContractIR]) -> str:
    """Render a CLIO TypeExpr as a Swift type expression."""
    if isinstance(t, ConstrainedType):
        return _type_to_swift(t.base, contracts)
    if isinstance(t, PrimitiveType):
        return _SWIFT_PRIMITIVES[t.name]
    if isinstance(t, EnumType):
        return "String"
    if isinstance(t, ListType):
        return f"[{_type_to_swift(t.inner, contracts)}]"
    if isinstance(t, DictType):
        return (
            f"[{_type_to_swift(t.key, contracts)}: "
            f"{_type_to_swift(t.value, contracts)}]"
        )
    if isinstance(t, OptionalType):
        return f"{_type_to_swift(t.inner, contracts)}?"
    if isinstance(t, ContractRef):
        return _to_class_name(t.name)
    if isinstance(t, RecordType):
        # Anonymous record: [String: Any] for Phase 1; typed structs deferred.
        return "[String: Any]"
    raise ValueError(f"unsupported TypeExpr for Swift target: {type(t).__name__}")


def _swift_module_name(graph: FlowGraph, default: str = "flow") -> str:
    name = graph.flow.name if graph.flow else default
    cleaned = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    return cleaned or default


def _flow_uses_judgment(graph: FlowGraph) -> bool:
    """True if any step in the source is judgment mode.

    Mirrors _go_helpers._flow_uses_judgment — scans graph.steps so judgment
    in a sub-flow still triggers Anthropic.swift emission."""
    return any(isinstance(s, StepIR) and s.mode == "judgment" for s in graph.steps)


def _cache_ttl_seconds(cache: CacheConfigIR | None) -> int | None:
    """Resolve a CacheConfigIR to a TTL in seconds, None (permanent), or 0 (no cache).

    Return values:
      0    — CACHE: off or no CACHE directive (skip cache blocks entirely)
      None — CACHE: on  (permanent; nil ttlSeconds in Swift)
      int  — CACHE: ttl(Xh/Xm/Xs) converted to seconds

    Logic mirrors _go_step_renderers._cache_ttl_seconds exactly.
    """
    if cache is None or cache.mode == "off":
        return 0
    if cache.mode == "on":
        return None  # permanent
    # mode == "ttl"
    if cache.ttl_seconds is not None:
        return cache.ttl_seconds
    return 0


def _flow_uses_cache(graph: FlowGraph) -> bool:
    """True if any judgment step in the graph has an active CACHE directive.

    Used to gate emission of SHA256.swift and Cache.swift."""
    return any(
        isinstance(s, StepIR)
        and s.mode == "judgment"
        and _cache_ttl_seconds(s.cache) != 0
        for s in graph.steps
    )


def _flow_uses_parallel_foreach(graph: FlowGraph) -> bool:
    """True if any FLOW chain contains a PARALLEL FOR EACH.

    Parallel FOR EACH emits withThrowingTaskGroup, a Swift Concurrency runtime
    API that requires macOS 10.15+ / iOS 13+.  The Package.swift platforms
    clause must declare this minimum to avoid availability warnings."""
    def _walk(items: tuple) -> bool:  # type: ignore[type-arg]
        for it in items:
            if isinstance(it, ForEachIR):
                if it.parallel:
                    return True
                if _walk(it.body):
                    return True
            elif isinstance(it, IfBlockIR):
                if _walk(it.then_body) or _walk(it.else_body):
                    return True
            elif isinstance(it, MatchBlockIR):
                if any(_walk(c.body) for c in it.cases):
                    return True
            elif isinstance(it, WhileBlockIR):
                if _walk(it.body):
                    return True
        return False

    return any(_walk(fl.chain) for fl in graph.flows)


def _walk_chain_swift(
    items: tuple,  # type: ignore[type-arg]
    flows_by_name: dict[str, FlowIR],
    loop_vars: frozenset[str] = frozenset(),
) -> None:
    """Recursively walk a FLOW chain and raise on constructs unsupported in
    Phase 3c.

    Permanent refusals: E_SWIFT_006 (multi-GIVES sub-flow in PARALLEL body).
    Temporary refusals: FlowCallIR (Phase 5); FOR EACH over a loop variable.

    IF/ELSE, MATCH/CASE, WHILE, sequential FOR EACH, and parallel FOR EACH
    are all supported from Phase 3a/3b/3c — this walker recurses into their
    bodies so that nested permanent refusals (E_SWIFT_006, FlowCallIR) are
    still caught.

    `loop_vars` is the set of loop-variable names bound by enclosing FOR EACH
    blocks. A FOR EACH whose collection is one of those names iterates a loop
    variable (a typed List element), not a state field — but the collection
    resolver in _swift_flow_renderer only consults state_field_to_step /
    take_types, so it would emit a runtime-wrong `state["<loopvar>"]` lookup
    (and an [Any] element that is non-Sendable inside a parallel TaskGroup).
    We refuse that case fail-loud here, in the gate, where the message is
    clean and fires before the renderer runs.

    The renderer in _swift_flow_renderer.py raises on unsupported item kinds
    as a backstop; this gate fires first with a cleaner message."""
    for it in items:
        if isinstance(it, IfBlockIR):
            _walk_chain_swift(it.then_body, flows_by_name, loop_vars)
            _walk_chain_swift(it.else_body, flows_by_name, loop_vars)
        elif isinstance(it, MatchBlockIR):
            for case in it.cases:
                _walk_chain_swift(case.body, flows_by_name, loop_vars)
        elif isinstance(it, WhileBlockIR):
            _walk_chain_swift(it.body, flows_by_name, loop_vars)
        elif isinstance(it, ForEachIR):
            # Refuse a FOR EACH (seq or parallel) over an enclosing loop var.
            if it.collection in loop_vars:
                raise ValueError(
                    "swift target: FOR EACH over a loop variable is not yet "
                    "supported (planned later); use --target python or go for "
                    f"now (collection={it.collection!r})"
                )
            if it.parallel:
                # E_SWIFT_006 (permanent): multi-GIVES sub-flow as PARALLEL body.
                # Kept as a defensive permanent refusal even though FlowCallIR
                # bodies are unreachable today (len(graph.flows) > 1 is refused
                # earlier).  It fires before any other parallel-body validation
                # so the stable code is always surfaced.
                if len(it.body) == 1:
                    body0 = it.body[0]
                    if isinstance(body0, FlowCallIR):
                        sub = flows_by_name.get(body0.flow_name)
                        if sub is not None and len(sub.gives) >= 2:
                            raise ValueError(E_SWIFT_006)
            # Recurse into the body with the loop var added to scope so a nested
            # FOR EACH over THIS loop var (and other unsupported constructs) is
            # caught.
            _walk_chain_swift(it.body, flows_by_name, loop_vars | {it.loop_var})
        elif isinstance(it, FlowCallIR):
            raise ValueError(
                f"swift target: sub-flow composition (FlowCallIR) is not yet "
                f"supported (planned for Phase 5); use --target python or go "
                f"for now (flow={it.flow_name!r})"
            )
        elif isinstance(it, RescueBlockIR):
            _walk_chain_swift(it.body, flows_by_name, loop_vars)


def validate_graph_for_swift(graph: FlowGraph) -> None:
    """Raise ValueError with a clear message if the graph uses any feature
    outside the supported swift-target scope for Phase 1.

    Permanent refusals use stable E_SWIFT_NNN codes.
    Temporary refusals describe what phase will lift the restriction.

    Check order: most-specific permanent codes win over broader temporary
    ones (e.g., invoke checks fire before the general judgment refusal so
    E_SWIFT_002/003/005 are surfaced even while judgment is unimplemented).
    """
    # E_SWIFT_004: no FLOW at all
    if len(graph.flows) == 0:
        raise ValueError(E_SWIFT_004)

    # E_SWIFT_012: TEST blocks
    if graph.tests:
        raise ValueError(E_SWIFT_012)

    for step in graph.steps:
        if not isinstance(step, StepIR):
            continue  # guard: graph.steps is tuple[StepIR, ...] but typed loosely

        # E_SWIFT_001: unsupported LANG on an exact step
        if step.mode == "exact" and step.lang not in _SWIFT_OK_LANGS:
            raise ValueError(
                f"{E_SWIFT_001} (step={step.name!r}, lang={step.lang!r})"
            )

        # invoke checks — permanent; fire before the dispatch so stable codes
        # are surfaced even when a new invoke type is added later.
        if isinstance(step.invoke, CliInvokeIR):
            raise ValueError(E_SWIFT_002)
        if isinstance(step.invoke, ApiInvokeIR):
            if step.invoke.protocol in {"bedrock", "vertex"}:
                raise ValueError(E_SWIFT_003)
            if step.invoke.protocol == "openai":
                raise ValueError(E_SWIFT_005)
        # Anthropic judgment (nil invoke or ApiInvokeIR with protocol anthropic)
        # is supported from Phase 2 — no refusal here.

        # E_SWIFT_009 / E_SWIFT_010: sql and mcp_tool impls (permanent)
        if isinstance(step.impl, SqlImplIR):
            raise ValueError(f"{E_SWIFT_009} (step={step.name!r})")
        if isinstance(step.impl, McpToolImplIR):
            raise ValueError(f"{E_SWIFT_010} (step={step.name!r})")

        # E_SWIFT_013 (permanent): form/file/multipart REST body.
        # Checked BEFORE the temporary rest refusal so the stable code
        # is surfaced and survives Phase 4 when rest is otherwise lifted.
        if isinstance(step.impl, RestImplIR) and step.impl.body is not None and not (
            isinstance(step.impl.body, (JsonBodyIR, RawBodyIR))
        ):
            raise ValueError(f"{E_SWIFT_013} (step={step.name!r})")

        # Temporary: rest impl (Phase 4)
        if isinstance(step.impl, RestImplIR):
            raise ValueError(
                f"swift target: impl.mode: rest is not yet supported "
                f"(planned for Phase 4); use --target python or go for now "
                f"(step={step.name!r})"
            )

        # Temporary: shell impl (Phase 4)
        if isinstance(step.impl, ShellImplIR):
            raise ValueError(
                f"swift target: impl.mode: shell is not yet supported "
                f"(planned for Phase 4); use --target python or go for now "
                f"(step={step.name!r})"
            )

    # Temporary: sub-flow composition — more than one FLOW (Phase 5)
    if len(graph.flows) > 1:
        raise ValueError(
            "swift target: sub-flow composition (multiple FLOWs) is not yet "
            "supported (planned for Phase 5); use --target python or go for now"
        )

    # Temporary: RESCUE/RESUME (Phase 5). render_flow_swift only walks
    # flow.chain, never flow.rescues, so a RESCUE handler would be silently
    # dropped — the protected step's error would propagate instead of running
    # the handler. Refuse it until the Swift RESCUE emitter ships.
    for fl in graph.flows:
        if fl.rescues:
            raise ValueError(
                "swift target: RESCUE/RESUME is not yet supported "
                "(planned for Phase 5); use --target python or go for now"
            )

    # Walk every flow's chain for non-linear items and FlowCallIR.
    flows_by_name = {f.name: f for f in graph.flows}
    for fl in graph.flows:
        _walk_chain_swift(fl.chain, flows_by_name)
        for rescue in fl.rescues:
            _walk_chain_swift(rescue.body, flows_by_name)


def render_package_swift(graph: FlowGraph) -> str:
    exe = _swift_module_name(graph)
    # Swift Concurrency runtime APIs require a minimum platform declaration:
    # - withCheckedThrowingContinuation (judgment flows): macOS 12+
    # - withThrowingTaskGroup (parallel FOR EACH): macOS 10.15+
    # We use macOS 12 for both to keep a single consistent minimum.
    # Exact-only flows without parallel FOR EACH have no runtime concurrency
    # API calls and compile fine without a platforms clause.
    if _flow_uses_judgment(graph) or _flow_uses_parallel_foreach(graph):
        platforms_clause = "    platforms: [.macOS(.v12)],\n"
    else:
        platforms_clause = ""
    return (
        "// swift-tools-version:6.0\n"
        "import PackageDescription\n\n"
        "let package = Package(\n"
        f'    name: "{exe}",\n'
        f'{platforms_clause}'
        "    targets: [\n"
        '        .target(name: "ClioFlow"),\n'
        f'        .executableTarget(name: "{exe}", dependencies: ["ClioFlow"]),\n'
        "    ]\n"
        ")\n"
    )


def render_main_swift(graph: FlowGraph) -> str:
    """Render Sources/<exe>/Main.swift — CLI entry point."""
    return (
        "import Foundation\n"
        "import ClioFlow\n"
        "\n"
        "@main\n"
        "struct CLI {\n"
        "    static func main() async throws {\n"
        "        var kwargs: [String: Any] = [:]\n"
        "        let args = CommandLine.arguments\n"
        '        if let i = args.firstIndex(of: "--kwargs"),\n'
        "           i + 1 < args.count,\n"
        "           let data = args[i + 1].data(using: .utf8),\n"
        "           let obj = try? JSONSerialization.jsonObject(with: data)"
        " as? [String: Any] {\n"
        "            kwargs = obj\n"
        "        }\n"
        "        let result = try await Flow.run(kwargs: kwargs)\n"
        "        let out = try JSONSerialization.data(\n"
        "            withJSONObject: result, options: [.sortedKeys]\n"
        "        )\n"
        '        print(String(data: out, encoding: .utf8) ?? "{}")\n'
        "    }\n"
        "}\n"
    )


# ---------------------------------------------------------------------------
# Contract rendering
# ---------------------------------------------------------------------------

def _json_schema_to_swift(schema: dict) -> str:
    """Map a JSON Schema subschema (as emitted by type_to_json_schema) to a
    Swift type expression for struct field declarations.

    Handles the subset CLIO emits:
      - primitive types, enum, array, optional (anyOf), $ref, object/dict.
    Unrecognised shapes fall back to 'AnyCodable' — which won't compile; the
    caller (render_contracts_swift) is responsible for rejecting such schemas
    via validate_graph_for_swift before reaching this point.
    """
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _to_class_name(name)
    if "enum" in schema:
        return "String"
    # anyOf → Optional<T> pattern: {"anyOf": [<T-schema>, {"type": "null"}]}
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        null_b: dict | None = None
        inner_b: dict | None = None
        for branch in any_of:
            if isinstance(branch, dict) and branch.get("type") == "null":
                null_b = branch
            elif isinstance(branch, dict):
                inner_b = branch
        if null_b is not None and inner_b is not None:
            return f"{_json_schema_to_swift(inner_b)}?"
    t = schema.get("type")
    if t == "string":
        return "String"
    if t == "integer":
        return "Int"
    if t == "number":
        return "Double"
    if t == "boolean":
        return "Bool"
    if t == "array":
        items = schema.get("items", {})
        return f"[{_json_schema_to_swift(items)}]"
    if t == "object":
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            return f"[String: {_json_schema_to_swift(ap)}]"
        return "[String: Any]"
    return "Any"


def _contract_refs_in_type(t: TypeExpr) -> set[str]:
    """Return the CONTRACT names referenced anywhere in a TypeExpr tree.

    Mirrors the walk in _shared_utils._collect_contract_refs, but operates on a
    bare TypeExpr (a flow TAKE/GIVES field type) rather than a StepIR. Used so a
    contract referenced ONLY by a FLOW take/gives — e.g. `TAKES: List<risk>`
    accessed via a loop-var condition — still gets its struct emitted."""
    refs: set[str] = set()

    def walk(ty: TypeExpr) -> None:
        if isinstance(ty, ContractRef):
            refs.add(ty.name)
        elif isinstance(ty, ListType):
            walk(ty.inner)
        elif isinstance(ty, DictType):
            walk(ty.key)
            walk(ty.value)
        elif isinstance(ty, OptionalType):
            walk(ty.inner)
        elif isinstance(ty, RecordType):
            for _, fty in ty.fields:
                walk(fty)
        elif isinstance(ty, ConstrainedType):
            walk(ty.base)

    walk(t)
    return refs


def render_contracts_swift(graph: FlowGraph) -> str | None:
    """Render Sources/ClioFlow/Contracts.swift. Returns None when no contracts
    are referenced by any step or by the FLOW's takes/gives.

    Emits one `struct <Name>: Codable, Sendable` per ContractIR, with:
      - fields from the CONTRACT SHAPE (via _json_schema_to_swift)
      - `static let jsonSchema` containing the full JSON Schema (incl. x-clio-assert)
      - `func validate() throws` that delegates to Validate.check
    """
    contracts_used: set[str] = set()
    for step in graph.steps:
        contracts_used |= _collect_contract_refs(step)
    # A contract referenced only by the FLOW signature (e.g. `TAKES: List<risk>`
    # accessed via a loop/condition, never by a step) is still emitted as a type
    # in Flow.swift — collect those refs too or Flow.swift won't compile.
    if graph.flow is not None:
        for field in graph.flow.takes:
            contracts_used |= _contract_refs_in_type(field.type)
        for field in graph.flow.gives:
            contracts_used |= _contract_refs_in_type(field.type)
    if not contracts_used:
        return None

    contracts_by_name = {c.name: c for c in graph.contracts}

    parts: list[str] = [
        "// Auto-generated by CLIO. Do not edit by hand.",
        "import Foundation",
        "",
    ]

    for name in sorted(contracts_used):
        contract = contracts_by_name[name]
        struct_name = _to_class_name(name)

        parts.append(f"struct {struct_name}: Codable, Sendable {{")
        for fname, fschema in _shape_from_schema(contract.json_schema):
            swift_type = _json_schema_to_swift(fschema)
            parts.append(f"    var {fname}: {swift_type}")
        parts.append("")

        # Embed JSON Schema as a Swift multi-line string literal.
        # Each content line is prefixed with 4 spaces; the closing """ is also
        # at 4 spaces indent, so Swift strips exactly 4 spaces from each line,
        # recovering the original JSON string.
        schema_json = json.dumps(contract.json_schema, indent=2)
        indented_lines = "\n".join("    " + line for line in schema_json.splitlines())
        parts.append('    static let jsonSchema = """')
        parts.append(indented_lines)
        parts.append('    """')
        parts.append("")

        parts.append("    func validate() throws {")
        parts.append("        try Validate.check(self, against: Self.jsonSchema)")
        parts.append("    }")
        parts.append("}")
        parts.append("")

    return "\n".join(parts)
