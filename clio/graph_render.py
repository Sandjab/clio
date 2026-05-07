"""Render a FlowGraph as Mermaid or Graphviz DOT source.

Output is meant to be embedded in GitHub PRs (Mermaid) or piped to a
graphviz tool (DOT) — neither emitter writes a project, only one source
string per call.
"""
from __future__ import annotations

from clio.ir.graph import CallIR, FlowGraph, ForEachIR, StepIR


_MERMAID_CLASSDEFS = (
    "    classDef judgment fill:#e3f2fd,stroke:#1976d2,color:#0d47a1",
    "    classDef exact fill:#fff3e0,stroke:#f57c00,color:#bf360c",
)


def _mermaid_node(step: StepIR) -> str:
    label = f"{step.name}<br/>{step.mode}"
    if step.mode == "judgment":
        return f'{step.name}[/"{label}"/]:::judgment'
    return f'{step.name}["{label}"]:::exact'


def to_mermaid(graph: FlowGraph) -> str:
    """Render a FlowGraph as a Mermaid `flowchart TD` source string.

    EXACT steps render as rectangles, JUDGMENT steps as parallelograms.
    FOR EACH blocks render as labelled subgraphs containing their body;
    edges from a previous step land on the subgraph border.
    """
    steps_by_name = {s.name: s for s in graph.steps}
    lines: list[str] = ["flowchart TD"]
    declared: set[str] = set()
    state = {"foreach_idx": 0}

    def declare(step_name: str, indent: str) -> None:
        if step_name in declared:
            return
        step = steps_by_name.get(step_name)
        if step is not None:
            lines.append(f"{indent}{_mermaid_node(step)}")
        else:
            lines.append(f'{indent}{step_name}["{step_name}<br/>?"]')
        declared.add(step_name)

    def walk(chain, indent: str, prev_id: str | None) -> str | None:
        for elem in chain:
            if isinstance(elem, CallIR):
                declare(elem.step_name, indent)
                if prev_id is not None:
                    lines.append(f"{indent}{prev_id} --> {elem.step_name}")
                prev_id = elem.step_name
            elif isinstance(elem, ForEachIR):
                state["foreach_idx"] += 1
                sg_id = f"foreach_{state['foreach_idx']}"
                if elem.parallel:
                    label = f"FOR EACH {elem.loop_var} IN {elem.collection} [parallel]"
                else:
                    label = f"FOR EACH {elem.loop_var} IN {elem.collection}"
                lines.append(f'{indent}subgraph {sg_id}["{label}"]')
                walk(elem.body, indent + "    ", None)
                lines.append(f"{indent}end")
                if prev_id is not None:
                    lines.append(f"{indent}{prev_id} --> {sg_id}")
                prev_id = sg_id
        return prev_id

    if graph.flow is None:
        for s in graph.steps:
            lines.append(f"    {_mermaid_node(s)}")
    else:
        walk(graph.flow.chain, "    ", None)

    lines.extend(_MERMAID_CLASSDEFS)
    return "\n".join(lines) + "\n"


def _dot_node(step: StepIR) -> str:
    shape = "parallelogram" if step.mode == "judgment" else "box"
    label = f"{step.name}\\n{step.mode}"
    return f'{step.name} [label="{label}", shape={shape}];'


def to_dot(graph: FlowGraph) -> str:
    """Render a FlowGraph as Graphviz DOT.

    FOR EACH is represented by a label on the entering edge rather than a
    cluster — the latter requires `lhead`/invisible nodes that aren't worth
    the complexity for a v0 visualization.
    """
    steps_by_name = {s.name: s for s in graph.steps}
    lines: list[str] = [
        "digraph clio {",
        "    rankdir=TB;",
        '    node [fontname="Helvetica"];',
    ]
    declared: set[str] = set()

    def declare(step_name: str) -> None:
        if step_name in declared:
            return
        step = steps_by_name.get(step_name)
        if step is not None:
            lines.append(f"    {_dot_node(step)}")
        else:
            lines.append(f'    {step_name} [label="{step_name}\\n?", shape=box];')
        declared.add(step_name)

    def first_call(chain) -> str | None:
        for elem in chain:
            if isinstance(elem, CallIR):
                return elem.step_name
            if isinstance(elem, ForEachIR):
                inner = first_call(elem.body)
                if inner is not None:
                    return inner
        return None

    def last_call(chain) -> str | None:
        for elem in reversed(chain):
            if isinstance(elem, CallIR):
                return elem.step_name
            if isinstance(elem, ForEachIR):
                inner = last_call(elem.body)
                if inner is not None:
                    return inner
        return None

    def walk(chain, prev_id: str | None) -> str | None:
        for elem in chain:
            if isinstance(elem, CallIR):
                declare(elem.step_name)
                if prev_id is not None:
                    lines.append(f"    {prev_id} -> {elem.step_name};")
                prev_id = elem.step_name
            elif isinstance(elem, ForEachIR):
                target = first_call(elem.body)
                if target is None:
                    continue
                walk(elem.body, None)
                if prev_id is not None:
                    if elem.parallel:
                        edge_label = f"for each {elem.loop_var} in {elem.collection} [parallel]"
                    else:
                        edge_label = f"for each {elem.loop_var} in {elem.collection}"
                    lines.append(
                        f'    {prev_id} -> {target} [label="{edge_label}", style=dashed];'
                    )
                prev_id = last_call(elem.body) or prev_id
        return prev_id

    if graph.flow is None:
        for s in graph.steps:
            declare(s.name)
    else:
        walk(graph.flow.chain, None)

    lines.append("}")
    return "\n".join(lines) + "\n"
