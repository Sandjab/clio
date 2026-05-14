"""Pure rendering helpers for the claude-skill emitter.

Functions in this module take IR nodes and produce strings or dicts.
No filesystem I/O. No imports from other emitter modules.
"""

from __future__ import annotations

from collections.abc import Callable

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


def _allowed_tools(graph: FlowGraph) -> list[str]:
    """Static set for v1: every emitted skill uses the same tool surface.

    Read for state.json, Write for state mutations, Bash for exact scripts
    and validation, TodoWrite for the orchestration checklist.
    """
    return ["Bash", "Read", "Write", "TodoWrite"]


def render_frontmatter(
    graph: FlowGraph,
    *,
    warn: Callable[[str], None] | None = None,
) -> str:
    """Render the YAML frontmatter block for SKILL.md (between '---' fences).

    If the flow has no description, emit a warning via ``warn`` (a callable
    that takes a single string — typically
    ``lambda m: print(m, file=sys.stderr)``).

    Returns a string starting with '---\\n' and ending with '---\\n'.
    """
    raw_name = _flow_name(graph)
    name = raw_name.replace("_", "-")

    # TODO(post-v0.14): wire FLOW.description.
    # The parser (clio/parser/parser.py::parse_flow) currently does not capture
    # a description from the .clio source. To enable this:
    #   1. Add `description: str = ""` to FlowDecl in clio/parser/ast_nodes.py.
    #   2. Add `description: str = ""` to FlowIR in clio/ir/graph.py.
    #   3. Thread the value through clio/ir/builder.py::build_ir.
    # Once any of those is non-empty, the lookup below will pick it up.
    description = (getattr(getattr(graph, "flow", None), "description", "") or "").strip()
    if not description:
        description = f"Execute flow {raw_name}"
        if warn is not None:
            warn(
                f"claude-skill warning: FLOW {raw_name} has no description; "
                f"frontmatter description defaulted to '{description}'. "
                f"Auto-trigger of the emitted skill will be weak."
            )

    tools = _allowed_tools(graph)
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"allowed-tools: {', '.join(tools)}\n"
        f"---\n"
    )


def render_skill_md(
    graph: FlowGraph,
    *,
    warn: Callable[[str], None] | None = None,
) -> str:
    """Render the full SKILL.md content for a flow."""
    raw_name = _flow_name(graph)
    return render_frontmatter(graph, warn=warn) + f"\n# {raw_name}\n"
