"""Go-specific renderers + embedded Go runtime templates.

Filled progressively across Phase 1-6. Imported by `go.py`.

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
from clio.ir.graph import FlowGraph, StepIR

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
    plus a backtick-quoted JSON Schema const so Validate() can call
    jsonschema/v6 without filesystem reads. The `import` block is omitted
    in T5; T6 will add it when Validate(ctx context.Context) is introduced.
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

    parts = ["package contracts", ""]
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
        schema_json = json.dumps(contract.json_schema, indent=2)
        parts.append(f"const {schema_const} = `")
        parts.append(schema_json)
        parts.append("`")
        parts.append("")
    return "\n".join(parts)
