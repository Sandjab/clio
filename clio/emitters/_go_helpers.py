"""Go-specific renderers + embedded Go runtime templates.

Filled progressively across Phase 1-6. Imported by `go.py`.

CLAUDE.md rule "emitters never import from each other" continues to hold:
this module is a helper for `go.py` only; cross-emitter sharing happens via
`_shared_utils.py`.
"""
from __future__ import annotations

import re

from clio.emitters._shared_utils import _has_parallel
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
