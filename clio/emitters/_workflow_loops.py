"""FOR EACH for target: claude-workflow — the sequential loop, and the fan-out.

This is the module that justifies the target. `FOR EACH … PARALLEL` is degraded to
sequential by claude-skill (with a warning) and refused outright by claude-cli;
here it becomes real concurrency, because the Workflow host gives us `parallel()`
and `pipeline()` over live subagents.

It renders a STATEMENT, so it needs to render its own body — and the body may
contain anything, including another FOR EACH. Rather than import the dispatcher
(which imports this module: a cycle), it takes `render_body` as a parameter. That
one callback is the whole coupling between the two modules.
"""
from __future__ import annotations

from collections.abc import Callable

from clio.emitters._workflow_expressions import (
    Bindings,
    call_js,
    flow_input,
    gives_of,
    loop_binding,
    read,
)
from clio.emitters._workflow_helpers import js_identifier, js_string
from clio.emitters._workflow_subflows import subflow_fn_name
from clio.ir.graph import CallIR, FlowCallIR, ForEachIR, StepIR

# `_render_body` of the flow renderer: (body, steps_by_name, phase_js, indent, bindings).
RenderBody = Callable[
    [tuple[object, ...], dict[str, StepIR], str, str, Bindings], list[str]
]

# A thunk that throws resolves to `null` in the result array of parallel() /
# pipeline() — the call itself never rejects. Unfiltered, those nulls land in
# state[collector] and blow up later, somewhere else.
#
# Not `.filter(Boolean)`: a step that GIVES a bool, an int or a str legitimately
# produces `false` / `0` / `''`, and Boolean would drop those SUCCESSFUL items
# along with the failed ones — silent data loss that no syntax check and no
# text assertion would catch. Only the failure sentinel is filtered.
_DROP_FAILED = ".filter((r) => r !== null && r !== undefined)"


def render_foreach(
    item: ForEachIR,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    bindings: Bindings,
    render_body: RenderBody,
) -> list[str]:
    """A sequential FOR EACH: a native `for…of` over the collection.

    The loop variable enters `bindings` for the body — every read of it (a kwarg
    `@doc`, a `MATCH doc.level`) then resolves to the JS binding instead of to a
    state key that does not exist. The collection itself is read through the OUTER
    bindings: in a nested `FOR EACH b IN a`, `a` is the enclosing loop's variable.

    Results are discarded, and that is the language's rule rather than a shortcut
    here: the parser refuses `AS` without `PARALLEL` (parser.py:2371-2375).
    """
    if item.parallel:
        return _render_parallel(item, steps_by_name, phase_js, indent, bindings)

    var = js_identifier(item.loop_var)
    inner = loop_binding(item.loop_var, bindings)
    lines = [f"{indent}for (const {var} of {read(item.collection, bindings)}) {{"]
    lines += render_body(item.body, steps_by_name, phase_js, indent, inner)
    lines.append(f"{indent}}}")
    return lines


def _render_parallel(
    item: ForEachIR,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    """The payoff: real fan-out, collected into `state[<collector>]`.

    One call in the body -> `parallel(items.map(x => () => step(x)))`. The inner
    arrow is load-bearing: parallel() takes THUNKS, and `.map(x => step(x))` would
    start every call during the map and hand it promises already in flight —
    bypassing the very concurrency limit it exists to enforce.

    N calls -> `pipeline(items, stage1, …, stageN)`, NOT parallel(). A multi-call
    body is a per-item stage chain, and pipeline() runs each item through all the
    stages with no barrier between them; parallel() would impose one, idling fast
    items behind the slowest of each stage. The Workflow tool's own guidance is
    "DEFAULT TO pipeline()", and the two are equivalent for a single stage anyway.

    The body renders no nested statement, so this branch needs no `render_body`:
    the IR builder allows only calls here (builder.py:2043-2079).
    """
    if item.collector is None:
        raise AssertionError(
            "unreachable: PARALLEL requires an AS binding (parser.py:2362-2366)"
        )

    var = js_identifier(item.loop_var)
    items = read(item.collection, bindings)
    inner = loop_binding(item.loop_var, bindings)
    target = f"{indent}state[{js_string(item.collector)}] ="

    if len(item.body) == 1 and isinstance(item.body[0], FlowCallIR):
        return _render_parallel_subflow(
            item.body[0], var, items, target, indent, phase_js, inner
        )

    calls = [b for b in item.body if isinstance(b, CallIR)]
    if len(calls) != len(item.body):
        raise NotImplementedError(
            f"claude-workflow: a PARALLEL FOR EACH body that MIXES step calls and "
            f"sub-flow calls (line {item.line}) is not rendered. A pipeline() stage "
            f"receives the previous stage's return value, and the two kinds return "
            f"different shapes — a step gives its GIVES value, an inlined sub-flow "
            f"gives an object of its GIVES — so `prev` would mean two things in one "
            f"chain. Split the FOR EACH, or wrap the steps in a sub-flow."
        )

    if len(calls) == 1:
        thunk = call_js(calls[0], steps_by_name, phase_js, inner).removeprefix("await ")
        return [
            f"{target} (await parallel(",
            f"{indent}  {items}.map(({var}) => () => {thunk}),",
            f"{indent})){_DROP_FAILED}",
        ]

    lines = [f"{target} (await pipeline(", f"{indent}  {items},"]
    for i, call in enumerate(calls):
        stage = _stage_bindings(calls, i, steps_by_name, inner)
        params = f"({var})" if i == 0 else f"(prev, {var})"
        body = call_js(call, steps_by_name, phase_js, stage).removeprefix("await ")
        lines.append(f"{indent}  {params} => {body},")
    lines.append(f"{indent})){_DROP_FAILED}")
    return lines


def _render_parallel_subflow(
    call: FlowCallIR,
    var: str,
    items: str,
    target: str,
    indent: str,
    phase_js: str,
    inner: Bindings,
) -> list[str]:
    """A sub-flow call as the PARALLEL body: `parallel(items.map(x => () => flow_$f(…)))`.

    The IR builder allows exactly this (builder.py:2057-2069) and states what the
    collector then holds: "a list of the sub-flow's GIVES dicts at runtime". The
    inlined function returns precisely that object, so the thunk IS the call — no
    field is extracted. (go extracts the lone GIVES field instead, because a Go
    slice must have one static element type; JS has no such constraint, and
    extracting here would silently drop the other fields of a multi-GIVES sub-flow.)

    parallel(), not pipeline(): the body is a single stage. The thunk arrow is the
    same load-bearing one as for a step call — parallel() takes THUNKS, and mapping
    straight to the promise would start every sub-flow during the map, bypassing the
    concurrency limit.
    """
    fn = subflow_fn_name(call.flow_name)
    return [
        f"{target} (await parallel(",
        f"{indent}  {items}.map(({var}) => () => {fn}({flow_input(call.kwargs, inner)}, {phase_js})),",
        f"{indent})){_DROP_FAILED}",
    ]


def _stage_bindings(
    calls: list[CallIR], i: int, steps_by_name: dict[str, StepIR], inner: Bindings
) -> Bindings:
    """What stage `i` of a pipeline() can see: the original item, and the result of
    the stage immediately before it.

    A stage callback receives `(prevResult, originalItem, index)` — that is the
    whole contract. So the predecessor's GIVES field is bound to `prev`, and a late
    stage takes the item from `originalItem` rather than having it threaded through
    the previous stage's return value, which must stay that step's GIVES: the
    collector holds the LAST stage's results.

    A read of an EARLIER stage's output has nowhere to come from: it is not a
    parameter, and it is not in state either — nothing writes state inside a
    parallel body, because concurrent items would race on the key. Falling back to
    `state[…]` would emit JS that parses and reads `undefined` at run time. Refused
    instead, naming the step and the line.
    """
    if i == 0:
        return inner

    prev = gives_of(calls[i - 1], steps_by_name)
    earlier = {
        g
        for c in calls[: i - 1]
        if (g := gives_of(c, steps_by_name)) is not None and g != prev
    }
    call = calls[i]
    refs = {v[1:] for _, v in call.kwargs if isinstance(v, str) and v.startswith("@")}

    blocked = sorted(refs & earlier)
    if blocked:
        raise NotImplementedError(
            f"claude-workflow: step {call.step_name!r} (line {call.line}) reads "
            f"{', '.join(blocked)} from a stage that is not the one right before "
            "it. A pipeline() stage receives only (prevResult, originalItem), and a "
            "parallel body never writes state (concurrent items would race), so "
            "there is nowhere to read it from. Split the FOR EACH, or fold the "
            "steps into one."
        )
    return inner if prev is None else {**inner, prev: "prev"}
