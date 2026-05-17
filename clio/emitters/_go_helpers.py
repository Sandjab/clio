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
    _type_to_go,
)
from clio.ir.graph import ContractIR, FlowGraph, StepIR

_GO_VERSION = "1.22"

_DEP_JSONSCHEMA = "github.com/santhosh-tekuri/jsonschema/v6 v6.0.1"
_DEP_ANTHROPIC = "github.com/anthropics/anthropic-sdk-go v0.5.0"
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
    """True if the entry flow contains a FOR EACH PARALLEL block."""
    if graph.flow is None:
        return False
    return _has_parallel(graph.flow.chain)


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


def _step_in_out_struct(
    step: StepIR, contracts: dict[str, ContractIR]
) -> tuple[str, str]:
    """Return (in_struct_body, out_struct_body) for the step's typed stubs.

    Each body is the multi-line content between `struct {` and `}` — the
    caller wraps them in `type <Step>In struct { ... }` declarations.

    In struct: one field per entry in step.takes.
    Out struct: one field for step.gives (singular), or empty when None.
    """
    in_lines: list[str] = []
    for field in step.takes:
        go_field = _to_go_field_name(field.name)
        go_type = _type_to_go(field.type, contracts)
        in_lines.append(f'\t{go_field} {go_type} `json:"{field.name}"`')
    in_body = "\n".join(in_lines)

    out_body = ""
    if step.gives is not None:
        go_field = _to_go_field_name(step.gives.name)
        go_type = _type_to_go(step.gives.type, contracts)
        out_body = f'\t{go_field} {go_type} `json:"{step.gives.name}"`'

    return in_body, out_body


def render_exact_step_go(
    step: StepIR, contracts: dict[str, ContractIR]
) -> str:
    """Render a single exact step as a Go source file in the `steps` package.

    Emits:
      - package steps
      - import ("context")
      - type <Step>In struct { ... }
      - type <Step>Out struct { ... }
      - func <Step>(ctx context.Context, in <Step>In) (<Step>Out, error)
          with panic("fill me in: <step_name>") body
    """
    step_name_go = _to_class_name(step.name)
    in_body, out_body = _step_in_out_struct(step, contracts)

    lines: list[str] = [
        "package steps",
        "",
        "import (",
        '\t"context"',
        ")",
        "",
        f"type {step_name_go}In struct {{",
    ]
    if in_body:
        lines.append(in_body)
    lines.append("}")
    lines.append("")
    lines.append(f"type {step_name_go}Out struct {{")
    if out_body:
        lines.append(out_body)
    lines.append("}")
    lines.append("")
    lines.append(f"// {step_name_go} implements the '{step.name}' step.")
    lines.append(
        f"func {step_name_go}(ctx context.Context, in {step_name_go}In)"
        f" ({step_name_go}Out, error) {{"
    )
    lines.append(f'\tpanic("fill me in: {step.name}")')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)

