"""Chain → JS for target: claude-workflow — the flow body of the emitted script.

The dispatcher, and the statements that are one node deep: the call, the three
branches, the preamble. The other two thirds of the job live next door, and the
split is by responsibility rather than by size:

  * _workflow_expressions — how a node READS a value (a state key, a loop
    variable, a pipeline stage's `prev`). Expressions never render a statement,
    so the dependency is one-way.
  * _workflow_loops — FOR EACH, sequential and parallel. It renders its own body,
    which may contain anything, so it takes `_render_body` as a parameter rather
    than importing this module back.

Sub-flows (T9) and ON_FAIL / RESCUE (T10) hang off `_render_item`, the single
dispatch point.
"""
from __future__ import annotations

from clio.emitters._workflow_expressions import (
    NO_BINDINGS,
    Bindings,
    call_js,
    condition_js,
    state_access,
)
from clio.emitters._workflow_helpers import js_string, phase_titles
from clio.emitters._workflow_loops import render_foreach
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


def _render_call(
    call: CallIR, steps_by_name: dict[str, StepIR], phase: str, bindings: Bindings
) -> list[str]:
    """`state['<gives>'] = await <step>(…)`.

    The state key is the GIVES *field* name, not the step name — that is the key
    every reader uses: a kwarg ref (`@rows`), an IF condition (`r.score`), a MATCH
    scrutinee. python.py:635 and _swift_flow_renderer.py:264 key state the same way.
    """
    step = steps_by_name[call.step_name]
    invocation = call_js(call, steps_by_name, phase, bindings)
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
            indent + line for line in _render_call(item, steps_by_name, phase, bindings)
        ]
    if isinstance(item, IfBlockIR):
        return _render_if(item, steps_by_name, phase, indent, bindings)
    if isinstance(item, MatchBlockIR):
        return _render_match(item, steps_by_name, phase, indent, bindings)
    if isinstance(item, WhileBlockIR):
        return _render_while(item, steps_by_name, phase, indent, bindings)
    if isinstance(item, ForEachIR):
        return render_foreach(
            item, steps_by_name, phase, indent, bindings, _render_body
        )
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
