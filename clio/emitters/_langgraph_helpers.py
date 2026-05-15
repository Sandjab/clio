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

from clio.emitters._shared_utils import (
    _python_condition_expr,
    _to_field_name,
    _type_to_python,
)
from clio.ir.graph import (
    CallIR,
    ContractIR,
    FlowGraph,
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

    return fields


def emit_state_typeddict(
    graph: FlowGraph, contracts_by_name: dict[str, ContractIR]
) -> str:
    """Generate the State TypedDict class as Python code (no leading/trailing newline)."""
    fields = _collect_state_fields(graph, contracts_by_name)
    if not fields:
        return "class State(TypedDict, total=False):\n    pass"
    lines = [
        '"""State threaded through the LangGraph nodes.',
        "",
        "Marked total=False because each node only writes its own GIVES key;",
        "fields are filled progressively as the graph executes.",
        '"""',
        "class State(TypedDict, total=False):",
    ]
    for name, ty in fields:
        lines.append(f"    {_to_field_name(name)}: {ty}")
    return "\n".join(lines)


def _emit_node_wrapper(call: CallIR, step: StepIR) -> str:
    """Generate `def <step>_node(state: State) -> dict:` that translates between
    state-dict semantics and the underlying step function's keyword signature."""
    kw_parts = []
    for name, value in call.kwargs:
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            kw_parts.append(f"{name}=state[{ref!r}]")
        else:
            kw_parts.append(f"{name}={value!r}")
    kwargs_str = ", ".join(kw_parts)
    state_key = _step_state_key(step)
    return (
        f"def {step.name}_node(state: State) -> dict:\n"
        f"    _result = {step.name}_mod.{step.name}({kwargs_str})\n"
        f"    return {{{state_key!r}: _result}}"
    )


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
    all_calls = _collect_all_calls(graph.flow.chain)
    if not all_calls:
        return (
            '"""FLOW has no step calls."""\n'
            "from __future__ import annotations\n\n"
            "def run(**kwargs):\n"
            "    return {}\n"
        )

    # Imports section
    imports: list[str] = [
        "from __future__ import annotations",
        "",
        "from typing_extensions import TypedDict",
        "from langgraph.graph import START, END, StateGraph",
    ]
    needs_retry = any(
        _retry_max_attempts(steps_by_name[c.step_name]) is not None for c in all_calls
    )
    if needs_retry:
        imports.append("from langgraph.types import RetryPolicy")
    imports.append("")
    if graph.contracts:
        imports.append("from . import contracts")
        imports.append("")

    imported_steps: list[str] = []
    for c in all_calls:
        if c.step_name not in imported_steps:
            imported_steps.append(c.step_name)
    imports += [f"from .steps import {n} as {n}_mod" for n in imported_steps]
    imports.append("")
    imports.append("")

    state_block = emit_state_typeddict(graph, contracts_by_name)

    # Node wrappers (one per step, dedup'd)
    node_wrappers: list[str] = []
    seen_nodes: set[str] = set()
    for call in all_calls:
        if call.step_name in seen_nodes:
            continue
        seen_nodes.add(call.step_name)
        node_wrappers.append(_emit_node_wrapper(call, steps_by_name[call.step_name]))

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
    for call in all_calls:
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
