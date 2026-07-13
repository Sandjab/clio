"""Chain → JS for target: claude-workflow — the flow body of the emitted script.

Task 6 covers the linear chain: `args` binding, state threading, one phase per
top-level element. IF/MATCH/WHILE (T7), FOR EACH (T8), sub-flows (T9) and
ON_FAIL/RESCUE (T10) hang off `_render_item`, which is the single dispatch point.
"""
from __future__ import annotations

from clio.emitters._workflow_helpers import js_identifier, js_string, phase_titles
from clio.ir.graph import CallIR, FlowIR, StepIR

# A chain node — the union of FlowIR.chain. Widened to `object` at the dispatch
# seam so a node type this task does not render fails loudly rather than silently.
ChainItem = object


def _js_value(value: object) -> str:
    """A literal kwarg as a JS literal.

    bool before int: in Python `isinstance(True, int)` is True, so the int branch
    would render `true` as `1` — and `1` is not `true` under the strict equality
    the conditions use.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return js_string(value)
    if isinstance(value, int | float):
        return repr(value)
    if value is None:
        return "null"
    raise NotImplementedError(
        f"claude-workflow: kwarg value of type {type(value).__name__} is not "
        "rendered yet (ErrorAccessIR rides on ON_FAIL / RESCUE — Task 10)"
    )


def _step_input(kwargs: tuple[tuple[str, object], ...]) -> str:
    """The object a step reads its TAKES from.

    An emitted step reads `state['<take>']` (_workflow_step_renderers), so every
    TAKES has to be bound under its own name before the call. The call site does
    that with a shadowed COPY — `{ ...state, x: … }` — and never by writing into
    state, for two reasons that both bite at run time:

      * a literal TAKES named like some step's GIVES would clobber that output
        (`assess(x="in")` in swift_control_flow.clio binds `x` from a literal —
        nothing in state holds it, and nothing may be overwritten to put it there);
      * inside parallel() / pipeline() (Task 8), concurrent items writing the loop
        variable into the shared state would race.

    When every kwarg is an identity ref (`@x` bound to TAKES `x` — what the `->`
    pipe sugar produces, and the common case), the copy would be a no-op: pass
    `state` itself and keep the emitted line readable.
    """
    overlays: list[str] = []
    for name, value in kwargs:
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            if ref == name:
                continue  # already in state under that key
            overlays.append(f"{js_string(name)}: state[{js_string(ref)}]")
        else:
            overlays.append(f"{js_string(name)}: {_js_value(value)}")
    if not overlays:
        return "state"
    return "{ ...state, " + ", ".join(overlays) + " }"


def _render_call(call: CallIR, steps_by_name: dict[str, StepIR], phase: str) -> list[str]:
    """`state['<gives>'] = await <step>(<input>, '<phase>')`.

    The state key is the GIVES *field* name, not the step name — that is the key
    every reader uses: a kwarg ref (`@rows`), an IF condition (`r.score`), a MATCH
    scrutinee. python.py:635 and _swift_flow_renderer.py:264 key state the same way.

    `await` even on an exact step, whose stub is a plain `function`: awaiting a
    non-promise yields the value, and the day an author makes a stub async the
    call site is already right.
    """
    step = steps_by_name[call.step_name]
    invocation = (
        f"await {js_identifier(step.name)}({_step_input(call.kwargs)}, {js_string(phase)})"
    )
    if step.gives is None:
        return [invocation]  # a side-effect step: nothing to bind
    return [f"state[{js_string(step.gives.name)}] = {invocation}"]


def _render_item(
    item: ChainItem, steps_by_name: dict[str, StepIR], phase: str, indent: str
) -> list[str]:
    """Dispatch one chain node. Tasks 7-10 add their branches here; `phase` is
    threaded down so an agent spawned inside a block carries the block's phase
    (§4.3) instead of moving the racy global."""
    if isinstance(item, CallIR):
        return [indent + line for line in _render_call(item, steps_by_name, phase)]
    raise NotImplementedError(
        f"claude-workflow: {type(item).__name__} is not rendered yet "
        "(IF/MATCH/WHILE: Task 7 — FOR EACH: Task 8 — sub-flows: Task 9)"
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
