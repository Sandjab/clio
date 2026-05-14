"""Pure rendering helpers for the claude-skill emitter.

Functions in this module take IR nodes and produce strings or dicts.
No filesystem I/O. No imports from other emitter modules.
"""

from __future__ import annotations

from clio.ir.graph import FlowGraph


def _flow_name(graph: FlowGraph) -> str:
    """Derive the canonical flow name from the IR.

    FlowGraph exposes the name via ``graph.flow.name`` when a FLOW block is
    present.  For files that declare only STEPs (no FLOW), fall back to the
    first step's name.
    """
    if graph.flow is not None:
        return graph.flow.name
    if graph.steps:
        return graph.steps[0].name
    return "unnamed"


def render_frontmatter(graph: FlowGraph) -> str:
    """Render the YAML frontmatter block for SKILL.md (between '---' fences).

    Returns a string starting with '---\\n' and ending with '---\\n'.
    """
    raw_name = _flow_name(graph)
    name = raw_name.replace("_", "-")
    description = f"Execute flow {raw_name}"
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"allowed-tools: Bash, Read, Write, TodoWrite\n"
        f"---\n"
    )


def render_skill_md(graph: FlowGraph) -> str:
    """Render the full SKILL.md content for a flow."""
    raw_name = _flow_name(graph)
    return render_frontmatter(graph) + f"\n# {raw_name}\n"
