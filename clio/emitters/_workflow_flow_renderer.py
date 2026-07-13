"""Chain → JS for target: claude-workflow — the flow body of the emitted script.

Statements only: calls, branches, loops. How a node READS its inputs — state key
vs. loop variable vs. a pipeline stage's `prev` — lives in _workflow_expressions,
which this module threads a `Bindings` map through. Sub-flows (T9) and ON_FAIL /
RESCUE (T10) hang off `_render_item`, the single dispatch point.

FOR EACH … PARALLEL is why this target exists: claude-skill degrades it to
sequential, claude-cli refuses it, and here it becomes real fan-out.
"""
from __future__ import annotations

from clio.emitters._workflow_expressions import (
    NO_BINDINGS,
    Bindings,
    condition_js,
    loop_binding,
    read,
    state_access,
    step_input,
)
from clio.emitters._workflow_helpers import js_identifier, js_string, phase_titles
from clio.ir.graph import (
    CallIR,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    StepIR,
    WhileBlockIR,
)

# A chain node — the union of FlowIR.chain. Widened to `object` at the dispatch
# seam so a node type this task does not render fails loudly rather than silently.
ChainItem = object


def _call_js(
    call: CallIR, steps_by_name: dict[str, StepIR], phase: str, bindings: Bindings
) -> str:
    """`await <step>(<input>, '<phase>')` — the call expression, unbound.

    `await` even on an exact step, whose stub is a plain `function`: awaiting a
    non-promise yields the value, and the day an author makes a stub async the
    call site is already right.

    The phase travels as an ARGUMENT (the step wrapper passes it to agent({phase}))
    and never as a `phase()` call from inside a block: that global is racy under
    parallel() / pipeline() — last writer wins (§4.3).
    """
    step = steps_by_name[call.step_name]
    return (
        f"await {js_identifier(step.name)}"
        f"({step_input(call.kwargs, bindings)}, {js_string(phase)})"
    )


def _render_call(
    call: CallIR, steps_by_name: dict[str, StepIR], phase: str, bindings: Bindings
) -> list[str]:
    """`state['<gives>'] = await <step>(…)`.

    The state key is the GIVES *field* name, not the step name — that is the key
    every reader uses: a kwarg ref (`@rows`), an IF condition (`r.score`), a MATCH
    scrutinee. python.py:635 and _swift_flow_renderer.py:264 key state the same way.
    """
    step = steps_by_name[call.step_name]
    invocation = _call_js(call, steps_by_name, phase, bindings)
    if step.gives is None:
        return [invocation]  # a side-effect step: nothing to bind
    return [f"state[{js_string(step.gives.name)}] = {invocation}"]


def _render_body(
    body: tuple[object, ...],
    steps_by_name: dict[str, StepIR],
    phase: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    """The lines of a block body, one indent level deeper. `phase` is passed
    through unchanged: §4.3 moves the global only at the top level."""
    lines: list[str] = []
    for sub in body:
        lines += _render_item(sub, steps_by_name, phase, indent + "  ", bindings)
    return lines


def _render_if(
    item: IfBlockIR,
    steps_by_name: dict[str, StepIR],
    phase: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    lines = [f"{indent}if ({condition_js(item.condition, bindings)}) {{"]
    lines += _render_body(item.then_body, steps_by_name, phase, indent, bindings)
    if item.else_body:
        lines.append(f"{indent}}} else {{")
        lines += _render_body(item.else_body, steps_by_name, phase, indent, bindings)
    lines.append(f"{indent}}}")
    return lines


def _render_match(
    item: MatchBlockIR,
    steps_by_name: dict[str, StepIR],
    phase: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    """A native `switch`, whose case comparison is already strict in JS.

    Every arm ends in an explicit `break`. Without it JS falls through into the
    NEXT arm's body: `CASE low` would run `archive` and then `flag`. That is a
    behaviour change, not a style nit — and it is invisible to any test that only
    reads the emitted text for `case`. The DEFAULT arm (`MatchCaseIR.value is
    None`, graph.py:336) becomes `default:`, and breaks too: the builder puts it
    last today, and a break costs nothing if that ever stops being true.
    """
    scrutinee = state_access(item.state_field, item.sub_field, bindings)
    lines = [f"{indent}switch ({scrutinee}) {{"]
    for arm in item.cases:
        label = "default:" if arm.value is None else f"case {js_string(arm.value)}:"
        lines.append(f"{indent}  {label}")
        lines += _render_body(arm.body, steps_by_name, phase, indent + "  ", bindings)
        lines.append(f"{indent}    break")
    lines.append(f"{indent}}}")
    return lines


def _render_while(
    item: WhileBlockIR,
    steps_by_name: dict[str, StepIR],
    phase: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    """A native `while`, bounded by the MAX the source declares.

    `max_iters` is not advisory (graph.py:355): a loop whose only exit is its
    condition never terminates when a judgment step keeps returning the same
    answer — the counter is what makes that a bounded failure instead of a hung
    session. The counter is named after the node's SOURCE LINE, so two nested
    WHILEs cannot share one: with a shared name the inner `let` would shadow the
    outer counter, the outer would stop incrementing, and its bound would never
    be reached. (A latent bug of exactly this shape is on file against the `go`
    target.) The increment leads the body so no path through it can be skipped.
    """
    counter = f"_i_{item.line}"
    cond = condition_js(item.condition, bindings)
    lines = [
        f"{indent}let {counter} = 0",
        f"{indent}while (({cond}) && {counter} < {item.max_iters}) {{",
        f"{indent}  {counter}++",
    ]
    lines += _render_body(item.body, steps_by_name, phase, indent, bindings)
    lines.append(f"{indent}}}")
    return lines


# A thunk that throws resolves to `null` in the result array of parallel() /
# pipeline() — the call itself never rejects. Unfiltered, those nulls land in
# state[collector] and fail later, somewhere else.
#
# Not `.filter(Boolean)`: a step that GIVES a bool or a str legitimately produces
# `false` / `''` / `0`, and Boolean would drop those SUCCESSFUL items along with
# the failed ones — a data-loss bug that no syntax check and no text assertion
# would catch. Only the failure sentinel is filtered.
_DROP_FAILED = ".filter((r) => r !== null && r !== undefined)"


def _render_foreach(
    item: ForEachIR,
    steps_by_name: dict[str, StepIR],
    phase: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    """A sequential FOR EACH: a native `for…of` over the collection.

    The loop variable enters `bindings` for the body — every read of it (a kwarg
    `@doc`, a `MATCH doc.level`) then resolves to the JS binding instead of a state
    key that does not exist. The collection itself is read through the OUTER
    bindings: in a nested `FOR EACH b IN a`, `a` is the enclosing loop's variable.

    Results are discarded, and that is the language's own rule, not a shortcut:
    the parser refuses `AS` without `PARALLEL` (parser.py:2371-2375).
    """
    if item.parallel:
        return _render_parallel_foreach(item, steps_by_name, phase, indent, bindings)

    var = js_identifier(item.loop_var)
    lines = [f"{indent}for (const {var} of {read(item.collection, bindings)}) {{"]
    lines += _render_body(
        item.body, steps_by_name, phase, indent, loop_binding(item.loop_var, bindings)
    )
    lines.append(f"{indent}}}")
    return lines


def _render_parallel_foreach(
    item: ForEachIR,
    steps_by_name: dict[str, StepIR],
    phase: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    """The payoff of this target: real fan-out.

    One call in the body -> `parallel(items.map(x => () => step(x)))`. The inner
    arrow matters: parallel() takes THUNKS, and `.map(x => step(x))` would start
    every call during the map, handing it promises already in flight — bypassing
    the very concurrency limit it exists to enforce.

    N calls -> `pipeline(items, stage1, …, stageN)`, NOT parallel(). A multi-call
    body is a per-item stage chain, and pipeline() runs each item through all the
    stages with no barrier between them; parallel() would impose one, idling fast
    items behind the slowest of each stage. The Workflow tool's own guidance is
    "DEFAULT TO pipeline()".
    """
    if item.collector is None:
        raise AssertionError(
            "unreachable: PARALLEL requires an AS binding (parser.py:2362-2366)"
        )
    calls = [b for b in item.body if isinstance(b, CallIR)]
    if len(calls) != len(item.body):
        raise NotImplementedError(
            f"claude-workflow: a PARALLEL FOR EACH body of sub-flow calls "
            f"(line {item.line}) is rendered in Task 9"
        )

    var = js_identifier(item.loop_var)
    items = read(item.collection, bindings)
    inner = loop_binding(item.loop_var, bindings)
    target = f"{indent}state[{js_string(item.collector)}] ="

    if len(calls) == 1:
        thunk = _call_js(calls[0], steps_by_name, phase, inner).removeprefix("await ")
        return [
            f"{target} (await parallel(",
            f"{indent}  {items}.map(({var}) => () => {thunk}),",
            f"{indent})){_DROP_FAILED}",
        ]

    lines = [f"{target} (await pipeline(", f"{indent}  {items},"]
    for i, call in enumerate(calls):
        stage = _stage_bindings(calls, i, steps_by_name, inner)
        params = f"({var})" if i == 0 else f"(prev, {var})"
        body = _call_js(call, steps_by_name, phase, stage).removeprefix("await ")
        lines.append(f"{indent}  {params} => {body},")
    lines.append(f"{indent})){_DROP_FAILED}")
    return lines


def _gives_of(call: CallIR, steps_by_name: dict[str, StepIR]) -> str | None:
    """The state-field name a call produces. A step's NAME and its GIVES FIELD
    differ — `review` GIVES `verdict`, and a downstream kwarg reads `@verdict` —
    and it is the field name that every reader keys on."""
    step = steps_by_name[call.step_name]
    return step.gives.name if step.gives is not None else None


def _stage_bindings(
    calls: list[CallIR], i: int, steps_by_name: dict[str, StepIR], inner: Bindings
) -> Bindings:
    """What stage `i` of a pipeline() can see: the original item, and the result of
    the stage immediately before it.

    A stage callback receives `(prevResult, originalItem, index)` — that is the
    whole contract. So the predecessor's GIVES field is bound to `prev`, and a late
    stage takes the item from `originalItem` rather than having it threaded through
    the previous stage's return value (which must stay that step's GIVES: the
    collector holds the LAST stage's results).

    A read of an EARLIER stage's output has nowhere to come from: it is not a
    parameter, and it is not in state either — nothing writes state inside a
    parallel body, because concurrent items would race on the key. Falling back to
    `state[…]` would emit JS that parses and reads `undefined` at run time. Refused
    instead, naming the step and the line.
    """
    if i == 0:
        return inner

    prev = _gives_of(calls[i - 1], steps_by_name)
    earlier = {
        g
        for c in calls[: i - 1]
        if (g := _gives_of(c, steps_by_name)) is not None and g != prev
    }
    call = calls[i]
    refs = {v[1:] for _, v in call.kwargs if isinstance(v, str) and v.startswith("@")}

    blocked = sorted(refs & earlier)
    if blocked:
        raise NotImplementedError(
            f"claude-workflow: step {call.step_name!r} (line {call.line}) reads "
            f"{', '.join(blocked)} from a stage that is not the one right before "
            "it. A pipeline() stage receives only (prevResult, originalItem), and "
            "a parallel body never writes state (concurrent items would race), so "
            "there is nowhere to read it from. Split the FOR EACH, or fold the "
            "steps into one."
        )
    return inner if prev is None else {**inner, prev: "prev"}


def _render_item(
    item: ChainItem,
    steps_by_name: dict[str, StepIR],
    phase: str,
    indent: str,
    bindings: Bindings = NO_BINDINGS,
) -> list[str]:
    """Dispatch one chain node. Tasks 9-10 add their branches here; `phase` is
    threaded down so an agent spawned inside a block carries the block's phase
    (§4.3) instead of moving the racy global."""
    if isinstance(item, CallIR):
        return [
            indent + line
            for line in _render_call(item, steps_by_name, phase, bindings)
        ]
    if isinstance(item, IfBlockIR):
        return _render_if(item, steps_by_name, phase, indent, bindings)
    if isinstance(item, MatchBlockIR):
        return _render_match(item, steps_by_name, phase, indent, bindings)
    if isinstance(item, WhileBlockIR):
        return _render_while(item, steps_by_name, phase, indent, bindings)
    if isinstance(item, ForEachIR):
        return _render_foreach(item, steps_by_name, phase, indent, bindings)
    raise NotImplementedError(
        f"claude-workflow: {type(item).__name__} is not rendered yet "
        "(sub-flows: Task 9 — ON_FAIL / RESCUE: Task 10)"
    )


def _render_preamble(flow: FlowIR) -> list[str]:
    """`const state = {}`, then the flow's TAKES bound from the `args` global.

    A flow with no TAKES never mentions `args`: the runtime is free to hand the
    script nothing, and a guard emitted anyway would throw on a legitimate run.
    `typeof args` rather than `args === undefined` because an undeclared global is
    a ReferenceError, not undefined — the guard must survive its own failure case.
    """
    lines = ["const state = {}"]
    if not flow.takes:
        return lines

    declared = ", ".join(f.name for f in flow.takes)
    missing_all = js_string(f"clio: flow '{flow.name}' requires args: {declared}")
    lines += [
        "if (typeof args === 'undefined' || args === null) {",
        f"  throw new Error({missing_all})",
        "}",
    ]
    for field in flow.takes:
        key = js_string(field.name)
        missing = js_string(f"clio: flow '{flow.name}' requires args[{field.name!r}]")
        lines += [
            f"if (args[{key}] === undefined) {{",
            f"  throw new Error({missing})",
            "}",
            f"state[{key}] = args[{key}]",
        ]
    return lines


def render_flow_js(flow: FlowIR, steps_by_name: dict[str, StepIR]) -> str:
    """The flow body: preamble, then one `phase()` + one rendered element per
    top-level chain node.

    `strict=True` on the zip is the guard for §4.3: phase_titles and the chain walk
    must stay in lockstep, so a chain node a later task adds without giving it a
    title fails here — loudly, at compile time — instead of shifting every phase
    label by one.
    """
    lines = _render_preamble(flow)
    for title, item in zip(phase_titles(flow), flow.chain, strict=True):
        lines.append("")
        lines.append(f"phase({js_string(title)})")
        lines += _render_item(item, steps_by_name, title, "")
    return "\n".join(lines) + "\n"
