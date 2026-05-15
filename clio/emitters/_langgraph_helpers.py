"""Module-level helpers for the LangGraph emitter.

The LangGraph emitter targets a Python package whose `flow.py` builds a
`langgraph.graph.StateGraph` instead of a custom orchestrator. Step files
themselves are reused verbatim from the python target — we just wrap each
step call in a node function `(state: State) -> dict` that the graph can
invoke. State is a `TypedDict` aggregating every TAKES/GIVES field across
the flow.

Scope (v0): linear FLOW only. FOR EACH (any), `invoke.cli`,
`invoke.api.openai/bedrock/vertex`, and ON_FAIL `escalate`/`fallback` are
rejected at compile time with clear messages. Cache and `retry(N)` are
honoured (the latter via `RetryPolicy`)."""
from __future__ import annotations

import keyword

from clio.emitters._shared_utils import (
    _python_condition_expr,
    _to_field_name,
    _type_to_python,
)
from clio.ir.graph import (
    CallIR,
    ContractIR,
    FlowCallIR,
    FlowGraph,
    FlowIR,
    IfBlockIR,
    MatchBlockIR,
    StepIR,
)


def _collect_all_calls(chain) -> list[CallIR]:
    """Flatten a FlowIR chain to every CallIR it contains, including those
    nested inside IfBlockIR / MatchBlockIR branches. Used to enumerate
    add_node calls."""
    out: list[CallIR] = []
    for item in chain:
        if isinstance(item, CallIR):
            out.append(item)
        elif isinstance(item, IfBlockIR):
            out.extend(_collect_all_calls(item.then_body))
            out.extend(_collect_all_calls(item.else_body))
        elif isinstance(item, MatchBlockIR):
            for arm in item.cases:
                out.extend(_collect_all_calls(arm.body))
    return out


def _step_state_key(step: StepIR) -> str:
    """Key under which a step's GIVES is stored in the LangGraph state dict."""
    return step.gives.name if step.gives is not None else f"_{step.name}_result"


def _retry_max_attempts(step: StepIR) -> int | None:
    """Extract the `retry(N)` max_attempts for a step, or None if no retry strategy."""
    if step.on_fail is None:
        return None
    for s in step.on_fail.strategies:
        if s.kind == "retry":
            return s.max_retries
    return None


def _collect_state_fields(
    graph: FlowGraph, contracts_by_name: dict[str, ContractIR]
) -> list[tuple[str, str]]:
    """Walk all STEP TAKES and GIVES to collect the State TypedDict fields.
    Returns an ordered list of (field_name, python_type_str) preserving
    declaration order: TAKES inputs first, then GIVES outputs.

    v0.16: when FLOW.TAKES is declared, those fields take precedence over the
    auto-inferred 'TAKES-not-produced-upstream' first pass. The second pass
    (GIVES) still runs to include every step's output in the State."""
    seen: set[str] = set()
    fields: list[tuple[str, str]] = []

    # v0.16: declared FLOW.TAKES override the auto-inferred first pass.
    if graph.flow is not None and graph.flow.takes:
        for f in graph.flow.takes:
            fields.append((f.name, _type_to_python(f.type, contracts_by_name)))
            seen.add(f.name)
    else:
        # v0.15 fallback: TAKES that are NOT produced by any upstream GIVES
        # become external inputs the user passes via run(...) or app.invoke({...}).
        produced_names = {s.gives.name for s in graph.steps if s.gives is not None}
        for step in graph.steps:
            for f in step.takes:
                if f.name not in produced_names and f.name not in seen:
                    fields.append((f.name, _type_to_python(f.type, contracts_by_name)))
                    seen.add(f.name)

    # Second pass: GIVES, in step declaration order.
    for step in graph.steps:
        if step.gives is not None and step.gives.name not in seen:
            fields.append(
                (step.gives.name, _type_to_python(step.gives.type, contracts_by_name))
            )
            seen.add(step.gives.name)

    # v0.17: sub-flow GIVES are published flat into the parent state by the
    # FlowCallIR node wrapper (matching python/mcp-server/claude-skill).
    # Include them in State so the merged update is typed.
    main_flow_name = graph.flow.name if graph.flow is not None else None
    for sub in graph.flows:
        if sub.name == main_flow_name:
            continue
        for f in sub.gives:
            if f.name not in seen:
                fields.append((f.name, _type_to_python(f.type, contracts_by_name)))
                seen.add(f.name)

    return fields


def emit_state_typeddict(
    graph: FlowGraph, contracts_by_name: dict[str, ContractIR]
) -> str:
    """Generate the State TypedDict class as Python code (no leading/trailing newline).

    Uses the functional `TypedDict(...)` syntax when any field name collides with
    a Python keyword so the declared key keeps its original (unsanitized) form,
    matching the dict-access pattern used by the emitted node wrappers (which
    read/write `state[<original_name>]`). Falls back to the class-statement
    syntax for the common keyword-free case to keep readability."""
    fields = _collect_state_fields(graph, contracts_by_name)
    if not fields:
        return "class State(TypedDict, total=False):\n    pass"
    if any(keyword.iskeyword(name) for name, _ in fields):
        # Functional syntax preserves original (unsanitized) keys.
        field_items = ", ".join(f"{name!r}: {ty}" for name, ty in fields)
        return f"State = TypedDict('State', {{{field_items}}}, total=False)"
    lines = [
        '"""State threaded through the LangGraph nodes.',
        "",
        "Marked total=False because each node only writes its own GIVES key;",
        "fields are filled progressively as the graph executes.",
        '"""',
        "class State(TypedDict, total=False):",
    ]
    for name, ty in fields:
        lines.append(f"    {name}: {ty}")
    return "\n".join(lines)


def _emit_node_wrapper(call: CallIR, step: StepIR) -> str:
    """Generate `def <step>_node(state: State) -> dict:` that translates between
    state-dict semantics and the underlying step function's keyword signature."""
    kw_parts = []
    for name, value in call.kwargs:
        py_name = _to_field_name(name)
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            kw_parts.append(f"{py_name}=state[{ref!r}]")
        else:
            kw_parts.append(f"{py_name}={value!r}")
    kwargs_str = ", ".join(kw_parts)
    state_key = _step_state_key(step)
    py_step_name = _to_field_name(step.name)
    return (
        f"def {step.name}_node(state: State) -> dict:\n"
        f"    _result = {py_step_name}_mod.{py_step_name}({kwargs_str})\n"
        f"    return {{{state_key!r}: _result}}"
    )


def _emit_flow_call_node_wrapper(call: FlowCallIR, sub_flow: FlowIR) -> str:
    """v0.17 — generate `def <flow>_node(state: State) -> dict:` that invokes a
    *pre-compiled* sub-flow StateGraph (see `_emit_compiled_subflow_constant`),
    remapping the parent's state into the sub-flow's input keys and publishing
    the sub-flow's GIVES back into the parent state (flat, matching
    python/mcp-server convention).

    Compiling a LangGraph StateGraph is expensive; we compile each sub-flow
    once at module load (`_compiled_<flow>` constants) and invoke the cached
    instance on every call site."""
    input_parts = []
    for name, value in call.kwargs:
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            input_parts.append(f"{name!r}: state[{ref!r}]")
        else:
            input_parts.append(f"{name!r}: {value!r}")
    input_dict = "{" + ", ".join(input_parts) + "}"
    output_parts = ", ".join(
        f"{f.name!r}: _result[{f.name!r}]" for f in sub_flow.gives
    )
    return (
        f"def {call.flow_name}_node(state: State) -> dict:\n"
        f"    _result = _compiled_{call.flow_name}.invoke({input_dict})\n"
        f"    return {{{output_parts}}}"
    )


def _collect_subflow_state_fields(
    sub_flow: FlowIR,
    steps_by_name: dict[str, StepIR],
    contracts_by_name: dict[str, ContractIR],
) -> list[tuple[str, str]]:
    """v0.17 — derive a sub-flow's State TypedDict fields from its TAKES, the
    GIVES of each step called inside the sub-flow (intermediate fields), and
    its declared GIVES. Mirrors `_collect_state_fields` but scoped to one flow."""
    seen: set[str] = set()
    fields: list[tuple[str, str]] = []
    for f in sub_flow.takes:
        fields.append((f.name, _type_to_python(f.type, contracts_by_name)))
        seen.add(f.name)
    # Intermediate fields produced by step GIVES inside the sub-flow's chain.
    for call in _collect_all_calls(sub_flow.chain):
        step = steps_by_name.get(call.step_name)
        if step is not None and step.gives is not None and step.gives.name not in seen:
            fields.append(
                (step.gives.name, _type_to_python(step.gives.type, contracts_by_name))
            )
            seen.add(step.gives.name)
    for f in sub_flow.gives:
        if f.name not in seen:
            fields.append((f.name, _type_to_python(f.type, contracts_by_name)))
            seen.add(f.name)
    return fields


def _emit_subgraph_builder(
    sub_flow: FlowIR,
    steps_by_name: dict[str, StepIR],
    contracts_by_name: dict[str, ContractIR],
) -> str:
    """v0.17 — emit `def build_<name>_graph() -> CompiledStateGraph` for a
    signed sub-FLOW. The builder constructs its own State TypedDict (locally
    aliased) and StateGraph, adds one node per step in the sub-flow's chain,
    wires linear edges START -> ... -> END, and returns the compiled graph."""
    # Sub-flows in v0.17 are linear chains of CallIR / FlowCallIR — the IR
    # builder forbids FOR EACH / IF / MATCH / WHILE / RESCUE in nested flows
    # via the same `_validate_for_langgraph` path (and FlowCallIR inside a
    # sub-flow remains supported via this same builder, recursively).
    state_name = f"_State_{sub_flow.name}"
    fields = _collect_subflow_state_fields(sub_flow, steps_by_name, contracts_by_name)
    if not fields:
        state_lines = [f"    class {state_name}(TypedDict, total=False):", "        pass"]
    elif any(keyword.iskeyword(name) for name, _ in fields):
        # Functional syntax preserves original (unsanitized) keys — required
        # when any TAKES/GIVES field name collides with a Python keyword.
        field_items = ", ".join(f"{name!r}: {ty}" for name, ty in fields)
        state_lines = [
            f"    {state_name} = TypedDict({state_name!r}, "
            f"{{{field_items}}}, total=False)"
        ]
    else:
        state_lines = [f"    class {state_name}(TypedDict, total=False):"]
        for name, ty in fields:
            state_lines.append(f"        {name}: {ty}")

    builder_lines = [
        f"def build_{sub_flow.name}_graph():",
        f'    """Compile and return the StateGraph for sub-flow {sub_flow.name!r}."""',
        *state_lines,
        f"    workflow = StateGraph({state_name})",
    ]

    # Add one node per step (de-duplicated). Linear chain only.
    added: set[str] = set()
    node_names: list[str] = []
    for item in sub_flow.chain:
        if isinstance(item, CallIR):
            step = steps_by_name[item.step_name]
            if step.name not in added:
                added.add(step.name)
                max_attempts = _retry_max_attempts(step)
                if max_attempts is not None:
                    builder_lines.append(
                        f"    workflow.add_node({step.name!r}, {step.name}_node, "
                        f"retry_policy=RetryPolicy(max_attempts={max_attempts}))"
                    )
                else:
                    builder_lines.append(
                        f"    workflow.add_node({step.name!r}, {step.name}_node)"
                    )
            node_names.append(step.name)
        elif isinstance(item, FlowCallIR):
            # Nested sub-flow call inside a sub-flow.
            builder_lines.append(
                f"    workflow.add_node({item.flow_name!r}, {item.flow_name}_node)"
            )
            node_names.append(item.flow_name)
        else:
            raise ValueError(
                f"langgraph target only supports linear sub-flows in v0.17 "
                f"(sub-flow {sub_flow.name!r} contains a "
                f"{type(item).__name__} which is not yet supported)"
            )

    # Wire linear edges START -> n1 -> n2 -> ... -> END.
    prev = "START"
    for name in node_names:
        builder_lines.append(
            f"    workflow.add_edge("
            f"{'START' if prev == 'START' else repr(prev)}, {name!r})"
        )
        prev = name
    builder_lines.append(
        f"    workflow.add_edge({prev!r}, END)" if prev != "START"
        else "    workflow.add_edge(START, END)"
    )
    builder_lines.append("    return workflow.compile()")
    return "\n".join(builder_lines)


def emit_flow_module(
    graph: FlowGraph, contracts_by_name: dict[str, ContractIR]
) -> str:
    """Generate flow.py: imports + State TypedDict + per-step node wrappers +
    build_graph() returning the compiled StateGraph + run() convenience entrypoint."""
    if graph.flow is None:
        return (
            '"""No FLOW declared."""\n'
            "from __future__ import annotations\n\n"
            "def run(**kwargs):\n"
            "    return {}\n"
        )

    steps_by_name = {s.name: s for s in graph.steps}
    main_flow_name = graph.flow.name
    # v0.17: signed sub-flows are emitted as `build_<name>_graph()` helpers
    # in the same module. The parent's FlowCallIR sites add them as nodes.
    sub_flows: tuple[FlowIR, ...] = tuple(
        f for f in graph.flows
        if f.name != main_flow_name and f.takes and f.gives
    )
    # Calls in the main chain (CallIR). Sub-flow internals are walked
    # separately when emitting their builders.
    main_calls = _collect_all_calls(graph.flow.chain)
    flow_calls = [it for it in graph.flow.chain if isinstance(it, FlowCallIR)]
    if not main_calls and not flow_calls:
        return (
            '"""FLOW has no step calls."""\n'
            "from __future__ import annotations\n\n"
            "def run(**kwargs):\n"
            "    return {}\n"
        )

    # Aggregate every step imported across all flows in the module (main +
    # signed sub-flows) so the shared imports block is correct.
    all_step_calls: list[CallIR] = list(main_calls)
    for sf in sub_flows:
        all_step_calls.extend(_collect_all_calls(sf.chain))

    # Imports section
    imports: list[str] = [
        "from __future__ import annotations",
        "",
        "from typing_extensions import TypedDict",
        "from langgraph.graph import START, END, StateGraph",
    ]
    needs_retry = any(
        _retry_max_attempts(steps_by_name[c.step_name]) is not None
        for c in all_step_calls
    )
    if needs_retry:
        imports.append("from langgraph.types import RetryPolicy")
    imports.append("")
    if graph.contracts:
        imports.append("from . import contracts")
        imports.append("")

    imported_steps: list[str] = []
    for c in all_step_calls:
        if c.step_name not in imported_steps:
            imported_steps.append(c.step_name)
    imports += [
        f"from .steps import {_to_field_name(n)} as {_to_field_name(n)}_mod"
        for n in imported_steps
    ]
    imports.append("")
    imports.append("")

    state_block = emit_state_typeddict(graph, contracts_by_name)

    # Node wrappers (one per step, dedup'd). v0.17: also one per distinct
    # sub-flow call so `<flow>_node` is in scope when build_graph adds it.
    node_wrappers: list[str] = []
    seen_step_nodes: set[str] = set()
    for call in all_step_calls:
        if call.step_name in seen_step_nodes:
            continue
        seen_step_nodes.add(call.step_name)
        node_wrappers.append(_emit_node_wrapper(call, steps_by_name[call.step_name]))

    sub_flows_by_name = {sf.name: sf for sf in sub_flows}
    seen_flow_nodes: set[str] = set()
    # Collect FlowCallIR sites across the main chain AND sub-flow chains so
    # nested sub-flow invocations also get a wrapper.
    for chain in [graph.flow.chain, *[sf.chain for sf in sub_flows]]:
        for it in chain:
            if isinstance(it, FlowCallIR) and it.flow_name not in seen_flow_nodes:
                seen_flow_nodes.add(it.flow_name)
                node_wrappers.append(
                    _emit_flow_call_node_wrapper(it, sub_flows_by_name[it.flow_name])
                )

    # v0.17: emit a builder per signed sub-flow.
    subgraph_builders: list[str] = [
        _emit_subgraph_builder(sf, steps_by_name, contracts_by_name)
        for sf in sub_flows
    ]
    # v0.17: compile each sub-flow exactly once at module load and reuse the
    # cached CompiledStateGraph from every `<flow>_node` wrapper. The wrappers
    # reference `_compiled_<flow>` lazily (function body) so the ordering is
    # safe even though wrappers are emitted before their builders.
    compiled_subflow_constants: list[str] = [
        f"_compiled_{sf.name} = build_{sf.name}_graph()"
        for sf in sub_flows
    ]

    # Router functions for IF blocks (emitted before build_graph so they are
    # in scope when add_conditional_edges references them).
    router_funcs: list[str] = []

    # Graph builder
    graph_lines: list[str] = [
        "def build_graph():",
        '    """Compile and return the StateGraph for this flow."""',
        "    workflow = StateGraph(State)",
    ]
    added_nodes: set[str] = set()
    for call in main_calls:
        if call.step_name in added_nodes:
            continue
        added_nodes.add(call.step_name)
        step = steps_by_name[call.step_name]
        max_attempts = _retry_max_attempts(step)
        if max_attempts is not None:
            graph_lines.append(
                f"    workflow.add_node({step.name!r}, {step.name}_node, "
                f"retry_policy=RetryPolicy(max_attempts={max_attempts}))"
            )
        else:
            graph_lines.append(
                f"    workflow.add_node({step.name!r}, {step.name}_node)"
            )
    # v0.17: register each sub-flow node referenced from the main chain.
    for it in graph.flow.chain:
        if isinstance(it, FlowCallIR) and it.flow_name not in added_nodes:
            added_nodes.add(it.flow_name)
            graph_lines.append(
                f"    workflow.add_node({it.flow_name!r}, {it.flow_name}_node)"
            )

    # Walk the chain producing add_edge / add_conditional_edges in order.
    # `terminals` is the list of node names whose outgoing edge has not yet
    # been wired (typically [last_node]; becomes [then_node, else_node] after
    # an IfBlock).
    terminals: list[str] = ["START"]

    def _emit_edge(src: str, dst: str) -> None:
        src_lit = "START" if src == "START" else repr(src)
        dst_lit = "END" if dst == "END" else repr(dst)
        graph_lines.append(f"    workflow.add_edge({src_lit}, {dst_lit})")

    def _emit_conditional(src: str, router_name: str, mapping: dict[str, str]) -> None:
        src_lit = "START" if src == "START" else repr(src)
        items = ", ".join(f"{k!r}: {v!r}" for k, v in mapping.items())
        graph_lines.append(
            f"    workflow.add_conditional_edges({src_lit}, {router_name}, "
            f"{{{items}}})"
        )

    for item in graph.flow.chain:
        if isinstance(item, CallIR):
            for src in terminals:
                _emit_edge(src, item.step_name)
            terminals = [item.step_name]
            continue

        if isinstance(item, FlowCallIR):
            # v0.17: a sub-flow call is just another node in the main graph.
            for src in terminals:
                _emit_edge(src, item.flow_name)
            terminals = [item.flow_name]
            continue

        if isinstance(item, IfBlockIR):
            # v0.7 langgraph constraints: each branch is exactly one CallIR,
            # and the IF must have an ELSE.
            if not item.else_body:
                raise ValueError(
                    f"langgraph target requires IF to have an ELSE branch in v0.7 "
                    f"(line {item.line}); use --target python for IF without ELSE"
                )
            if (
                len(item.then_body) != 1
                or not isinstance(item.then_body[0], CallIR)
                or len(item.else_body) != 1
                or not isinstance(item.else_body[0], CallIR)
            ):
                raise ValueError(
                    f"langgraph target requires each IF branch to contain exactly "
                    f"one step call in v0.7 (line {item.line}); use --target python "
                    "for multi-step branches"
                )
            then_step = item.then_body[0].step_name
            else_step = item.else_body[0].step_name

            cond_expr = _python_condition_expr(item.condition, set())
            router_name = f"_route_to_{then_step}_or_{else_step}"
            router_funcs.append(
                f"def {router_name}(state: State) -> str:\n"
                f"    if {cond_expr}:\n"
                f"        return {then_step!r}\n"
                f"    return {else_step!r}"
            )

            for src in terminals:
                _emit_conditional(
                    src, router_name, {then_step: then_step, else_step: else_step},
                )
            terminals = [then_step, else_step]
            continue

        if isinstance(item, MatchBlockIR):
            # v0.7 langgraph constraints: each arm must be exactly one CallIR
            # and the MATCH must include a DEFAULT (otherwise an unmapped
            # enum value would crash the router with no fallback edge).
            arms_with_step: list[tuple[str | None, str]] = []
            has_default = False
            for arm in item.cases:
                if (
                    len(arm.body) != 1
                    or not isinstance(arm.body[0], CallIR)
                ):
                    raise ValueError(
                        f"langgraph target requires each MATCH arm to contain "
                        f"exactly one step call in v0.7 (line {arm.line}); "
                        "use --target python for multi-step arms"
                    )
                step_name = arm.body[0].step_name
                arms_with_step.append((arm.value, step_name))
                if arm.value is None:
                    has_default = True
            if not has_default:
                raise ValueError(
                    f"langgraph target requires MATCH to include a DEFAULT arm in "
                    f"v0.7 (line {item.line}); use --target python for non-exhaustive "
                    "MATCH"
                )

            # Router function — string compare against state_field.sub_field.
            base = f"state[{item.state_field!r}]"
            scrutinee = f"{base}.{item.sub_field}"
            non_default = [(v, s) for v, s in arms_with_step if v is not None]
            default_step = next(s for v, s in arms_with_step if v is None)
            router_name = f"_match_{item.state_field}_{item.sub_field}"
            body_lines = [f"def {router_name}(state: State) -> str:"]
            body_lines.append(f"    _scrutinee = {scrutinee}")
            for value, step_name in non_default:
                body_lines.append(f"    if _scrutinee == {value!r}:")
                body_lines.append(f"        return {step_name!r}")
            body_lines.append(f"    return {default_step!r}")
            router_funcs.append("\n".join(body_lines))

            mapping = {step_name: step_name for _, step_name in arms_with_step}
            for src in terminals:
                _emit_conditional(src, router_name, mapping)
            terminals = [step_name for _, step_name in arms_with_step]
            continue

        # ForEach / unknown — should have been rejected by validate.
        raise ValueError(
            f"langgraph emitter cannot lower flow item of type {type(item).__name__}"
        )

    for src in terminals:
        _emit_edge(src, "END")
    graph_lines.append("    return workflow.compile()")

    # run() entrypoint — v0.16: return only declared FLOW.GIVES when present.
    if graph.flow is not None and graph.flow.gives:
        return_expr = "    return {" + ", ".join(
            f"{f.name!r}: result[{f.name!r}]" for f in graph.flow.gives
        ) + "}"
    else:
        return_expr = "    return dict(result)"

    run_block = "\n".join([
        "def run(**initial: object) -> dict:",
        '    """Compile the graph and invoke it once with `initial` as starting state."""',
        "    app = build_graph()",
        "    result = app.invoke(initial)",
        return_expr,
    ])

    sections = [*imports, state_block, "", "", "\n\n".join(node_wrappers)]
    if router_funcs:
        sections += ["", "", "\n\n".join(router_funcs)]
    if subgraph_builders:
        sections += ["", "", "\n\n".join(subgraph_builders)]
    if compiled_subflow_constants:
        sections += ["", "", "\n".join(compiled_subflow_constants)]
    sections += ["", "", "\n".join(graph_lines), "", "", run_block, ""]
    return "\n".join(sections)


def emit_main_module(pkg_name: str, graph: FlowGraph) -> str:
    """Generate __main__.py: invoke the flow once and persist the resulting state."""
    flow_name = graph.flow.name if graph.flow is not None else pkg_name
    return (
        f'"""CLI entrypoint for the {pkg_name} LangGraph flow."""\n'
        "from __future__ import annotations\n\n"
        "import argparse\n"
        "import json\n"
        "import os\n"
        "import sys\n\n"
        "from .flow import run\n\n\n"
        "def main() -> int:\n"
        f'    parser = argparse.ArgumentParser(description="{pkg_name} (LangGraph)")\n'
        '    parser.add_argument("--kwargs", default="{}", help="JSON dict of initial state values")\n'
        "    args = parser.parse_args()\n"
        "    initial = json.loads(args.kwargs)\n"
        "    state = run(**initial)\n"
        '    path = os.environ.get("CLIO_STATE_FILE", "state.json")\n'
        '    payload = {"version": 1, "flow": ' + repr(flow_name) + ', "state": state}\n'
        "    tmp = path + '.tmp'\n"
        '    with open(tmp, "w") as f:\n'
        "        json.dump(payload, f, default=str)\n"
        "    os.replace(tmp, path)\n"
        '    print(f"[clio] flow {' + repr(flow_name) + '!s} done — state at {path}", file=sys.stderr)\n'
        "    return 0\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )


def emit_pyproject(
    pkg_name: str,
    *,
    needs_anthropic: bool,
    needs_pydantic: bool,
    needs_requests: bool,
) -> str:
    """Generate pyproject.toml for a LangGraph-target package.
    Always includes langgraph; conditionally includes anthropic, pydantic, requests."""
    deps = ['"langgraph>=1.0"']
    if needs_anthropic:
        deps.append('"anthropic>=0.40"')
    if needs_pydantic:
        deps.append('"pydantic>=2"')
    if needs_requests:
        deps.append('"requests>=2.31"')
    deps_str = ",\n  ".join(deps)
    return (
        '[build-system]\n'
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        '[project]\n'
        f'name = "{pkg_name}"\n'
        'version = "0.1.0"\n'
        f'description = "Compiled CLIO flow for {pkg_name} (LangGraph target)"\n'
        'requires-python = ">=3.12"\n'
        'dependencies = [\n'
        f'  {deps_str},\n'
        ']\n\n'
        '[project.scripts]\n'
        f'{pkg_name} = "{pkg_name}.__main__:main"\n\n'
        '[tool.setuptools.packages.find]\n'
        f'include = ["{pkg_name}*"]\n'
    )
