"""The FLOW→FLOW call graph for target: claude-workflow — inlined, never nested.

The Workflow tool exposes `workflow({scriptPath})` to invoke another workflow, and
it would be the obvious mapping for a `FlowCallIR` — except that it caps nesting at
ONE level ("Nesting is one level only: workflow() inside a child throws"), while
CLIO nests FLOWs arbitrarily. workflow_subflow.clio (pipeline → level_a → level_b →
level_c) is already past the cap.

So this target inlines instead: every called flow becomes a local `async function`
in the same script (§4.2). The cap stops applying — there is no child script — and
the output stays a single self-contained file. Cost: a large multi-flow project
emits one large script. Accepted, and the reason no line here ever writes
`workflow(`.

Inlining has one thing it cannot survive, which nesting could: recursion. A flow
that calls itself inlines to a function that calls itself, and the script
stack-overflows at run time rather than failing at compile time. `reachable_flows`
raises E_WF_007 on the back edge instead — see its docstring for why the check
lives here even though the IR builder already has one.

This module is analysis only: it walks the IR and returns FlowIRs. It renders no
JS, so the renderers can import it without a cycle.
"""
from __future__ import annotations

from collections.abc import Mapping

from clio.emitters._workflow_helpers import E_WF_007
from clio.ir.graph import (
    FlowCallIR,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    WhileBlockIR,
)


def subflow_fn_name(flow_name: str) -> str:
    """The JS function an inlined sub-flow is emitted as.

    The `$` is not decoration. Without it, a FLOW `x` and a STEP literally named
    `flow_x` would both emit `function flow_x` — a duplicate declaration, which is
    a SyntaxError in module code, in a file the author only finds out about when
    they run it. The CLIO lexer cannot produce a `$` in an identifier
    (lexer.py:126-142), so `flow_$x` is a namespace no step name can reach. That
    also makes js_identifier() unnecessary here: the prefix already lands the name
    outside every JS reserved word.
    """
    return f"flow_${flow_name}"


# The `phase` argument of an inlined sub-flow function. A sub-flow is called from a
# phase it cannot know at emit time (the same function may be called from two
# sites), so the phase travels as a parameter — see render_subflow_js.
#
# `$` again, and for the same reason as above: the parameter is in scope over a
# body that CALLS step functions, so a step named `phaseName` would be shadowed by
# it and `await phaseName(...)` would try to call a string. `phase$` is a name no
# CLIO step can have. It is not `phase` either — that one is a host global.
PHASE_PARAM = "phase$"


def called_flow_names(items: tuple[object, ...]) -> list[str]:
    """Every FLOW invoked from this chain, in source order, duplicates kept.

    Nested bodies are walked: a sub-flow call is legal inside IF / MATCH / WHILE /
    FOR EACH (builder.py:946-964 collects the same edges for its own cycle check).
    Ordered, unlike the builder's set — the emitted functions must land in a
    deterministic order or the output churns between runs.
    """
    out: list[str] = []
    for item in items:
        if isinstance(item, FlowCallIR):
            out.append(item.flow_name)
        elif isinstance(item, (ForEachIR, WhileBlockIR)):
            out += called_flow_names(item.body)
        elif isinstance(item, IfBlockIR):
            out += called_flow_names(item.then_body)
            out += called_flow_names(item.else_body)
        elif isinstance(item, MatchBlockIR):
            for case in item.cases:
                out += called_flow_names(case.body)
    return out


def _edges(flow: FlowIR) -> list[str]:
    """The flows this one calls — chain and RESCUE bodies alike. A rescue body's
    call sites are emitted (Task 10), so the functions they name must exist."""
    out = called_flow_names(flow.chain)
    for rescue in flow.rescues:
        out += called_flow_names(rescue.body)
    return out


def reachable_flows(
    entry: FlowIR, flows_by_name: Mapping[str, FlowIR]
) -> list[FlowIR]:
    """The sub-flows to inline: everything `entry` calls, transitively, in
    first-seen order, without duplicates. The entry flow itself is NOT in the list
    — it is emitted as the script body, not as a function.

    Reachability, not `graph.flows`: a FLOW nothing calls has no call site in this
    script (this target emits exactly one flow, E_WF_006), so emitting a function
    for it would leave dead code in a file the author has to read and fill in. Same
    rule the step collector applies, for the same reason.

    Raises E_WF_007 on a back edge. The IR builder already refuses flow recursion
    for every source it parses (`_detect_flow_call_cycles`, builder.py:976-1009),
    so this is not the first line of defence — it is this module's own contract:
    the emitter is reachable without the builder (every hand-built FlowGraph in the
    test suite goes straight to it), and it is the INLINING that recursion breaks.
    An unguarded walk here would recurse forever at compile time, and a walk that
    merely deduped would emit a function that calls itself and blow the stack at
    run time — a failure in the user's session, far from the cause.

    A cycle among flows the entry never reaches is not refused: those flows are not
    inlined, so they cannot overflow anything.
    """
    ordered: list[FlowIR] = []
    seen: set[str] = set()

    def visit(flow: FlowIR, path: tuple[str, ...]) -> None:
        for name in _edges(flow):
            if name in path:
                cycle = " -> ".join([*path, name])
                raise ValueError(f"{E_WF_007} (cycle: {cycle})")
            sub = flows_by_name.get(name)
            if sub is None:
                raise AssertionError(
                    f"unreachable: FLOW {name!r} is called but not declared — the "
                    "IR builder resolves every FlowCallIR before the emitter runs"
                )
            if name in seen:
                continue
            seen.add(name)
            ordered.append(sub)
            visit(sub, (*path, name))

    visit(entry, (entry.name,))
    return ordered
