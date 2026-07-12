"""Helpers for target: claude-workflow — diagnostics, literals, meta, README."""
from __future__ import annotations

import json

from clio.ir.graph import FlowGraph, FlowIR

# ---------------------------------------------------------------------------
# Compile-time validation error codes (permanent — stable across releases)
# ---------------------------------------------------------------------------

E_WF_001 = "E_WF_001: source declares no FLOW; nothing to orchestrate."


def js_string(s: str) -> str:
    """A single-quoted JS string literal.

    json.dumps gives correct escaping for backslashes, control chars and
    non-ASCII; we then convert the double-quoted form to single quotes to match
    the house style of the emitted script.
    """
    inner = json.dumps(s, ensure_ascii=False)[1:-1].replace("'", "\\'")
    return f"'{inner}'"


def workflow_name(graph: FlowGraph) -> str:
    """kebab-case name of the entry flow — used for meta.name and the filename."""
    flow = graph.flow or (graph.flows[0] if graph.flows else None)
    if flow is None:
        raise ValueError(E_WF_001)
    return flow.name.replace("_", "-")


def phase_titles(flow: FlowIR) -> list[str]:
    """One phase per top-level element of the flow chain. Task 6 implements the
    non-empty cases; an empty chain has no phases."""
    return []


def render_meta(graph: FlowGraph) -> str:
    """The `export const meta` block. It MUST be a pure literal — no variables,
    no calls, no interpolation — or the Workflow runtime rejects the script."""
    flow = graph.flow or graph.flows[0]
    name = workflow_name(graph)
    desc = flow.description or f"CLIO flow {flow.name}"
    lines = [
        "export const meta = {",
        f"  name: {js_string(name)},",
        f"  description: {js_string(desc)},",
        "  phases: [",
    ]
    for title in phase_titles(flow):
        lines.append(f"    {{ title: {js_string(title)} }},")
    lines.append("  ],")
    lines.append("}")
    return "\n".join(lines)


def validate_graph_for_workflow(graph: FlowGraph) -> None:
    """Raise ValueError with a stable E_WF_NNN code for anything this target
    cannot honor. Called as the first statement of emit()."""
    if len(graph.flows) == 0:
        raise ValueError(E_WF_001)
