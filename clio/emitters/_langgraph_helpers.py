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

from clio.emitters._python_helpers import _type_to_python
from clio.ir.graph import (
    CallIR,
    ContractIR,
    FlowGraph,
    StepIR,
)


def _to_field_name(name: str) -> str:
    if keyword.iskeyword(name):
        return f"{name}_"
    return name


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
    declaration order: TAKES inputs first, then GIVES outputs."""
    seen: set[str] = set()
    fields: list[tuple[str, str]] = []

    # First pass: TAKES that are NOT produced by any upstream GIVES become
    # external inputs the user passes via run(...) or app.invoke({...}).
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
    calls = [item for item in graph.flow.chain if isinstance(item, CallIR)]
    if not calls:
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
        _retry_max_attempts(steps_by_name[c.step_name]) is not None for c in calls
    )
    if needs_retry:
        imports.append("from langgraph.types import RetryPolicy")
    imports.append("")
    if graph.contracts:
        imports.append("from . import contracts")
        imports.append("")

    imported_steps: list[str] = []
    for c in calls:
        if c.step_name not in imported_steps:
            imported_steps.append(c.step_name)
    imports += [f"from .steps import {n} as {n}_mod" for n in imported_steps]
    imports.append("")
    imports.append("")

    state_block = emit_state_typeddict(graph, contracts_by_name)

    # Node wrappers
    node_wrappers: list[str] = []
    for call in calls:
        node_wrappers.append(_emit_node_wrapper(call, steps_by_name[call.step_name]))

    # Graph builder
    graph_lines: list[str] = [
        "def build_graph():",
        '    """Compile and return the StateGraph for this flow."""',
        "    workflow = StateGraph(State)",
    ]
    for call in calls:
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

    for i, call in enumerate(calls):
        step = steps_by_name[call.step_name]
        if i == 0:
            graph_lines.append(f"    workflow.add_edge(START, {step.name!r})")
        else:
            prev = steps_by_name[calls[i - 1].step_name]
            graph_lines.append(
                f"    workflow.add_edge({prev.name!r}, {step.name!r})"
            )
    last = steps_by_name[calls[-1].step_name]
    graph_lines.append(f"    workflow.add_edge({last.name!r}, END)")
    graph_lines.append("    return workflow.compile()")

    # run() entrypoint
    run_block = "\n".join([
        "def run(**initial: object) -> dict:",
        '    """Compile the graph and invoke it once with `initial` as starting state."""',
        "    app = build_graph()",
        "    result = app.invoke(initial)",
        "    return dict(result)",
    ])

    return "\n".join(
        imports + [state_block, "", ""]
        + ["\n\n".join(node_wrappers), "", "", "\n".join(graph_lines), "", "", run_block, ""]
    )


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
