"""Pure rendering helpers for the claude-skill emitter.

Functions in this module take IR nodes and produce strings or dicts.
No filesystem I/O. No imports from other emitter modules.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from clio.ir.graph import FlowGraph, StepIR


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


def detect_skill_language(graph: FlowGraph) -> str:
    """Heuristic: FR if any common French diacritic appears in flow description
    or step docstrings; otherwise EN."""
    samples = []
    flow = getattr(graph, "flow", None)
    if flow is not None:
        samples.append(getattr(flow, "description", "") or "")
    for step in graph.steps:
        samples.append(getattr(step, "description", "") or "")
    text = " ".join(samples)
    fr_markers = set("éèàçôîêûïü")
    return "fr" if any(c in fr_markers for c in text) else "en"


def render_exact_script(step: StepIR, contracts_by_name: dict, idx: int) -> str:
    """Build a standalone Python script for an exact STEP.

    Reads state JSON from stdin, calls the step's body function, merges the
    output back into state under state[step.name], writes updated state to
    stdout.  If the step has no impl (no CODE block), the script is a trivial
    pass-through that echoes state unchanged.
    """
    takes_doc = (
        "\n    ".join(f"{t.name}: {t.type}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {step.gives.type}"
        if step.gives is not None else "(no GIVES)"
    )

    # Build the parameter unpacking lines for the step function call.
    if step.takes:
        param_lines = "\n".join(
            f"    {t.name} = state.get({step.name!r}, {{}}).get({t.name!r})"
            for t in step.takes
        )
        call_kwargs = ", ".join(f"{t.name}={t.name}" for t in step.takes)
        call_expr = f"result = {step.name}({call_kwargs})"
    else:
        param_lines = "    # no TAKES"
        call_expr = f"result = {step.name}()"

    # Merge result back into state.
    if step.gives is not None:
        merge_line = f"    state.setdefault({step.name!r}, {{}})[{step.gives.name!r}] = result"
    else:
        merge_line = f"    # no GIVES — state unchanged by {step.name}"

    return (
        f'"""Standalone script for STEP {step.name} (exact)\n'
        f"\n"
        f"TAKES:\n"
        f"    {takes_doc}\n"
        f"GIVES:\n"
        f"    {gives_doc}\n"
        f"\n"
        f"Usage:\n"
        f"    python scripts/{idx:02d}_{step.name}.py < state.json > state.next.json\n"
        f'"""\n'
        f"from __future__ import annotations\n"
        f"\n"
        f"import json\n"
        f"import sys\n"
        f"\n"
        f"\n"
        f"def {step.name}({', '.join(t.name for t in step.takes)}):\n"
        f'    """Implement the body of STEP {step.name} here.\n'
        f"\n"
        f"    TAKES:\n"
        f"        {takes_doc}\n"
        f"    GIVES:\n"
        f"        {gives_doc}\n"
        f'    """\n'
        f"    raise NotImplementedError(\n"
        f'        "Implement {step.name}: this is an exact (deterministic) step."\n'
        f"    )\n"
        f"\n"
        f"\n"
        f'if __name__ == "__main__":\n'
        f"    state = json.load(sys.stdin)\n"
        f"{param_lines}\n"
        f"    {call_expr}\n"
        f"{merge_line}\n"
        f"    json.dump(state, sys.stdout, indent=2)\n"
        f"    sys.stdout.write('\\n')\n"
    )


def render_exact_step_section(step: StepIR, idx: int, lang: str = "en") -> str:
    """Markdown section for an exact STEP.

    ``lang``: "en" → "Step NN", "fr" → "Étape NN".
    """
    label = {"en": "Step", "fr": "Étape"}[lang]
    title = f"## {label} {idx:02d} — {step.name} (MODE: exact)\n"
    doc = (getattr(step, "description", "") or "").strip()
    doc_block = f"\n{doc}\n" if doc else ""
    cmd = (
        f"\nRun:\n\n"
        f"    python scripts/{idx:02d}_{step.name}.py < state.json > state.next.json "
        f"&& mv state.next.json state.json\n\n"
    )
    tail = (
        "Tick the corresponding TodoWrite todo. "
        "Do not advance until the script exited 0.\n\n"
    )
    return title + doc_block + cmd + tail


def render_skill_md(
    graph: FlowGraph,
    *,
    warn: Callable[[str], None] | None = None,
) -> str:
    """Render the full SKILL.md content for a flow."""
    lang = detect_skill_language(graph)
    parts = [render_frontmatter(graph, warn=warn), f"\n# {_flow_name(graph)}\n"]
    for idx, step in enumerate(graph.steps, start=1):
        if step.mode == "exact":
            parts.append(render_exact_step_section(step, idx, lang=lang))
        # judgment branch: deferred to Task 5
    return "".join(parts)


def render_process_flow_dot(graph: FlowGraph) -> str:
    """Render the flow as DOT (reuses the existing `clio graph --format dot` renderer)."""
    from clio.graph_render import to_dot

    return to_dot(graph)


def render_state_example(graph: FlowGraph) -> str:
    """Initial-state template. One empty namespace per top-level STEP.

    Format: {"step01": {}, "step02": {}, ...} — keyed by top-level step name,
    in source order.
    """
    state = {step.name: {} for step in graph.steps}
    return json.dumps(state, indent=2) + "\n"


def render_readme(graph: FlowGraph) -> str:
    """Render a brief README.md for the emitted skill directory."""
    raw_name = _flow_name(graph)
    return (
        f"# {raw_name} — claude-skill\n\n"
        f"Compiled from a CLIO `.clio` source for the `claude-skill` target.\n\n"
        "## How to install\n\n"
        "Copy this directory to `~/.claude/skills/<name>/`, then invoke from any Claude Code session.\n\n"
        "## Caveats\n\n"
        "This skill is executed by the LLM host. Fidelity of execution is conditioned on the "
        "rigor of the host — the TodoWrite checklist in `SKILL.md` provides the main anchor "
        "against drift.\n"
    )
