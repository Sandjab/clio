"""Go-specific renderers for per-graph emission.

Filled progressively across Phase 1-6. Imported by `go.py`.

Static Go runtime templates (validate.go, cache.go, …) live in
`_go_runtime_templates.py` so this module stays focused on graph-bound
rendering logic.

CLAUDE.md rule "emitters never import from each other" continues to hold:
this module is a helper for `go.py` only; cross-emitter sharing happens via
`_shared_utils.py`.
"""
from __future__ import annotations

import json
import re

from clio.emitters._shared_utils import (
    _collect_contract_refs,
    _has_parallel,
    _json_type_to_go,
    _shape_from_schema,
    _to_class_name,
    _to_go_field_name,
)
from clio.ir.graph import (
    ApiInvokeIR,
    CliInvokeIR,
    FlowCallIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    McpToolImplIR,
    RescueBlockIR,
    RestImplIR,
    SqlImplIR,
    StepIR,
    WhileBlockIR,
)

_GO_VERSION = "1.22"

_DEP_JSONSCHEMA = "github.com/santhosh-tekuri/jsonschema/v6 v6.0.1"
_DEP_ANTHROPIC = "github.com/anthropics/anthropic-sdk-go v1.43.0"
_DEP_ERRGROUP = "golang.org/x/sync v0.7.0"


def _go_module_name(graph: FlowGraph, default: str = "flow") -> str:
    """Return a valid Go module name derived from the entry FLOW name.

    Go module names must be lowercase alphanumeric (plus underscores for
    word separation). Transformation: lowercase, replace each run of
    non-[a-z0-9] characters with a single underscore, strip leading/trailing
    underscores. Falls back to `default` when no FLOW is selected."""
    name = graph.flow.name if graph.flow is not None else default
    normalised = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return normalised or default


def _flow_uses_judgment(graph: FlowGraph) -> bool:
    """True if any step in the source is judgment mode.

    v0.20.0 refuses FLOW composition, so graph.steps contains exactly the
    steps used by the single entry flow."""
    return any(isinstance(s, StepIR) and s.mode == "judgment" for s in graph.steps)


def _flow_uses_parallel(graph: FlowGraph) -> bool:
    """True if ANY flow contains a FOR EACH PARALLEL block.

    Scans every flow's chain (not just graph.flow.chain), so a PARALLEL
    block reachable only through a sub-flow still pulls golang.org/x/sync
    (errgroup) into go.mod. (_flow_uses_judgment / _flow_uses_cache already
    scan all graph.steps, so they stay correct under FLOW composition.)"""
    return any(_has_parallel(fl.chain) for fl in graph.flows)


def _flow_uses_cache(graph: FlowGraph) -> bool:
    """True if any step in the entry flow declares a CACHE directive.

    Mirrors the gating logic of _flow_uses_judgment / _flow_uses_parallel.
    v0.20 refuses FLOW composition, so graph.steps contains exactly the
    steps used by the single entry flow.

    NOTE: graph.steps over-collects — it includes steps declared but not
    reached from graph.flow.chain. In v0.20 the practical impact is zero
    (extra cache runtime emission is harmless); revisit if step-function
    scoping needs to match the actual chain in a future iteration.
    """
    for step in graph.steps:
        if isinstance(step, StepIR) and step.cache is not None and step.cache.mode != "off":
            return True
    return False


def _flow_uses_rest(graph: FlowGraph) -> bool:
    """True if any step in the source is an impl.mode: rest step.

    Gates emission of clio_runtime/rest + clio_runtime/substitute. graph.steps
    is tuple[StepIR, ...], so no isinstance(StepIR) guard is needed. Like
    _flow_uses_cache it over-collects steps declared but not on the entry chain;
    harmless (the extra runtime is only ever emitted, never wrong-imported)."""
    return any(isinstance(s.impl, RestImplIR) for s in graph.steps)


def render_cmd_main_go(graph: FlowGraph) -> str:
    """Render `cmd/<pkg>/main.go` — the CLI entry point for the emitted module.

    Parses --kwargs as JSON, calls flow.Run(ctx, kwargs), prints the returned
    state as indented JSON, and exits with:
      0 — success
      1 — flow.Run returned an error
      2 — --kwargs is not valid JSON
    """
    pkg = _go_module_name(graph)
    return (
        "package main\n"
        "\n"
        "import (\n"
        '\t"context"\n'
        '\t"encoding/json"\n'
        '\t"flag"\n'
        '\t"fmt"\n'
        '\t"os"\n'
        "\n"
        f'\t"{pkg}/flow"\n'
        ")\n"
        "\n"
        "func main() {\n"
        '\tkwargsRaw := flag.String("kwargs", "{}", "JSON-encoded kwargs for the flow")\n'
        "\tflag.Parse()\n"
        "\n"
        "\tvar kwargs map[string]any\n"
        "\tif err := json.Unmarshal([]byte(*kwargsRaw), &kwargs); err != nil {\n"
        '\t\tfmt.Fprintf(os.Stderr, "invalid --kwargs: %v\\n", err)\n'
        "\t\tos.Exit(2)\n"
        "\t}\n"
        "\n"
        "\tctx := context.Background()\n"
        "\tstate, err := flow.Run(ctx, kwargs)\n"
        "\tif err != nil {\n"
        '\t\tfmt.Fprintf(os.Stderr, "flow.Run: %v\\n", err)\n'
        "\t\tos.Exit(1)\n"
        "\t}\n"
        "\n"
        '\tout, _ := json.MarshalIndent(state, "", "  ")\n'
        "\tfmt.Println(string(out))\n"
        "}\n"
    )


def render_go_mod(graph: FlowGraph) -> str:
    """Render the contents of go.mod for the emitted module.

    Deps included conditionally:
      - jsonschema/v6: always (Validate methods)
      - anthropic-sdk-go: only when >=1 judgment step
      - golang.org/x/sync: only when >=1 FOR EACH PARALLEL
    """
    pkg = _go_module_name(graph)
    lines = [f"module {pkg}", "", f"go {_GO_VERSION}", "", "require ("]
    lines.append(f"\t{_DEP_JSONSCHEMA}")
    if _flow_uses_judgment(graph):
        lines.append(f"\t{_DEP_ANTHROPIC}")
    if _flow_uses_parallel(graph):
        lines.append(f"\t{_DEP_ERRGROUP}")
    lines.append(")")
    return "\n".join(lines) + "\n"


def render_contracts_go(graph: FlowGraph) -> str | None:
    """Render contracts/contracts.go. Returns None when no contract is used.

    Emits one Go struct per CONTRACT referenced in the entry flow's steps,
    plus a backtick-quoted JSON Schema const and a Validate(ctx) method
    so callers can validate at runtime without filesystem reads.
    """
    # Collect contract names referenced by any step in the graph.
    contracts_used: set[str] = set()
    # NOTE: graph.steps over-collects — it includes steps declared but
    # not reached from graph.flow.chain. In v0.20 this is harmless (extra
    # dead struct types); revisit in T9 if step-function scoping needs
    # to match the actual chain.
    for step in graph.steps:
        contracts_used |= _collect_contract_refs(step)
    if not contracts_used:
        return None

    # Build a fast lookup from the tuple (FlowGraph.contracts is a tuple, not a dict).
    contracts_by_name = {c.name: c for c in graph.contracts}

    pkg = _go_module_name(graph)
    parts = [
        "package contracts",
        "",
        "import (",
        '\t"context"',
        "",
        f'\t"{pkg}/clio_runtime/validate"',
        ")",
        "",
    ]
    for name in sorted(contracts_used):
        contract = contracts_by_name[name]
        struct_name = _to_class_name(name)
        # lowerCamelCase for Go unexported const: e.g. customer_risk → customerRiskSchema
        schema_const = struct_name[0].lower() + struct_name[1:] + "Schema"
        parts.append(f"type {struct_name} struct {{")
        for fname, fschema in _shape_from_schema(contract.json_schema):
            go_field = _to_go_field_name(fname)
            go_type = _json_type_to_go(fschema)
            parts.append(f'\t{go_field} {go_type} `json:"{fname}"`')
        parts.append("}")
        parts.append("")
        # Embed the JSON Schema in a Go raw string literal (backtick-delimited).
        # Today's CLIO surface guarantees no backtick can appear in a generated
        # JSON Schema: enum values and field names are identifiers, and no
        # description/title fields are emitted. If a future feature adds a
        # user-supplied string into the schema (e.g. CONTRACT.DESCRIPTION),
        # escape backticks here before render — Go has no raw-string escape.
        # contract.json_schema already contains x-clio-assert when an ASSERT
        # clause is present (embedded by ir/builder.py at ContractIR build time).
        schema_json = json.dumps(contract.json_schema, indent=2)
        parts.append(f"const {schema_const} = `")
        parts.append(schema_json)
        parts.append("`")
        parts.append("")
        parts.append(f"func (c *{struct_name}) Validate(ctx context.Context) error {{")
        parts.append(f"\treturn validate.Schema(ctx, {schema_const}, c)")
        parts.append("}")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Compile-time validation: refuse unsupported IR constructs (E_GO_001..012)
# ---------------------------------------------------------------------------

_GO_E_001_MSG = (
    "E_GO_001: target: go can only embed exact step bodies in Go (LANG: go or "
    "LANG: auto). For Python/Bash/etc., use --target python (or --target "
    "claude-skill to let the LLM host drive the flow); for shell glue "
    "specifically, use impl.mode: shell (supported natively since v0.23)."
)
_GO_E_002_MSG = (
    "E_GO_002: target: go does not subprocess 'claude -p'. Use --target python, "
    "--target mcp-server, or --target claude-cli."
)
_GO_E_003_MSG = (
    "E_GO_003: target: go ships Anthropic and OpenAI SDKs only. Use --target "
    "python for Bedrock/Vertex."
)
_GO_E_004_MSG = (
    "E_GO_004: target: go needs at least one FLOW to emit cmd/<flow>/main.go."
)
_GO_E_005_MSG = (
    "E_GO_005: target: go v0.20.0 does not yet support invoke.protocol: openai. "
    "Use --target python until the v0.20.x OpenAI emitter ships."
)
_GO_E_006_MSG = (
    "E_GO_006: target: go v0.20.0 does not yet support FLOW composition. Use "
    "--target python until the v0.20.x sub-flow emitter ships."
)
_GO_E_007_MSG = (
    "E_GO_007: target: go v0.20.0 does not yet support impl.mode: rest. Use "
    "--target python until the v0.20.x REST emitter ships."
)
_GO_E_009_MSG = (
    "E_GO_009: target: go v0.20.0 does not yet support impl.mode: sql. Use "
    "--target python until the v0.20.x SQL emitter ships."
)
_GO_E_010_MSG = (
    "E_GO_010: target: go v0.20.0 does not yet support impl.mode: mcp_tool. "
    "Use --target python until the v0.20.x MCP emitter ships."
)
_GO_E_012_MSG = (
    "E_GO_012: target: go v0.20.0 does not yet emit TEST blocks as `go test`. "
    "Use --target python until the v0.20.x TEST emitter ships."
)

_GO_OK_LANGS: frozenset[str | None] = frozenset({"go", "auto", None})


def _walk_chain(items: tuple) -> None:  # type: ignore[type-arg]
    """Recursively walk a FLOW chain and raise on unsupported IR nodes."""
    for it in items:
        if isinstance(it, FlowCallIR):
            raise ValueError(_GO_E_006_MSG)
        if isinstance(it, IfBlockIR):
            _walk_chain(it.then_body)
            _walk_chain(it.else_body)
        elif isinstance(it, MatchBlockIR):
            for case in it.cases:
                _walk_chain(case.body)
        elif isinstance(it, WhileBlockIR):
            _walk_chain(it.body)
        elif isinstance(it, ForEachIR):
            _walk_chain(it.body)
        elif isinstance(it, RescueBlockIR):
            _walk_chain(it.body)


def validate_graph_for_go(graph: FlowGraph) -> None:
    """Raise ValueError with an E_GO_NNN code if the graph uses any feature
    outside v0.20.0 scope. Runs before any file is written."""
    # E_GO_006: multiple FLOWs means FLOW composition (entry is ambiguous)
    if len(graph.flows) > 1:
        raise ValueError(_GO_E_006_MSG)

    # E_GO_004: no FLOW at all
    if len(graph.flows) == 0:
        raise ValueError(_GO_E_004_MSG)

    # E_GO_012: TEST blocks
    if graph.tests:
        raise ValueError(_GO_E_012_MSG)

    for step in graph.steps:
        if not isinstance(step, StepIR):
            continue

        # E_GO_001: LANG not go/auto/None on an exact step
        if step.mode == "exact" and step.lang not in _GO_OK_LANGS:
            raise ValueError(
                f"{_GO_E_001_MSG} (step={step.name!r}, lang={step.lang!r})"
            )

        # invoke.* checks
        if isinstance(step.invoke, CliInvokeIR):
            raise ValueError(_GO_E_002_MSG)
        if isinstance(step.invoke, ApiInvokeIR):
            if step.invoke.protocol in {"bedrock", "vertex"}:
                raise ValueError(_GO_E_003_MSG)
            if step.invoke.protocol == "openai":
                raise ValueError(_GO_E_005_MSG)

        # impl.mode checks
        if isinstance(step.impl, RestImplIR):
            raise ValueError(_GO_E_007_MSG)
        # ShellImplIR is supported since v0.23 — no refusal here
        if isinstance(step.impl, SqlImplIR):
            raise ValueError(_GO_E_009_MSG)
        if isinstance(step.impl, McpToolImplIR):
            raise ValueError(_GO_E_010_MSG)

    # Walk chain for nested FlowCallIR (E_GO_006 via composition inside chain)
    if graph.flow is not None:
        _walk_chain(graph.flow.chain)
        for rescue in graph.flow.rescues:
            _walk_chain(rescue.body)

