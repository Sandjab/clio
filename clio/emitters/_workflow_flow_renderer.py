"""Chain → JS for target: claude-workflow — the flow body of the emitted script.

Task 6 covers the linear chain: `args` binding, state threading, one phase per
top-level element. Task 7 adds IF/MATCH/WHILE — the payoff of this target, where
control flow is native JS rather than scaffolding. FOR EACH (T8), sub-flows (T9)
and ON_FAIL/RESCUE (T10) hang off `_render_item`, the single dispatch point.
"""
from __future__ import annotations

from clio.emitters._workflow_helpers import js_identifier, js_string, phase_titles
from clio.ir.graph import (
    BoolOpIR,
    CallIR,
    ConditionIR,
    FlowIR,
    IfBlockIR,
    MatchBlockIR,
    StepIR,
    WhileBlockIR,
)

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


# CLIO compares by value; JS `==` does not. `0 == false`, `'' == 0` and
# `'1' == 1` are all true under it, so an int/bool comparison the author wrote
# would quietly change meaning. Only the strict forms are ever emitted — the
# ordering operators are the same token in both languages and carry no such trap.
_JS_OPS = {"==": "===", "!=": "!==", ">": ">", ">=": ">=", "<": "<", "<=": "<="}


def _state_access(state_field: str, field: str) -> str:
    """`state['<state field>'].<field>` — the read side of _render_call's write.

    `ConditionIR.step_name` and `MatchBlockIR.state_field` name the state KEY,
    which is the producing step's GIVES *field* name and not the step's name
    (_swift_flow_renderer.py:89-90 documents the same field the same way): in
    swift_control_flow.clio `assess` GIVES `r`, and `IF r.score > 0.5` reads
    state['r'].score. Keying on the step name instead yields `undefined`, and
    `undefined > 0.5` is silently false rather than an error.
    """
    return f"state[{js_string(state_field)}].{field}"


def _condition_literal(condition: ConditionIR) -> str:
    """The right-hand side of a comparison.

    `ident` is the one kind _js_value cannot take as-is: its value is a bare word
    from the source, which the builder resolves to an enum member — `true`/`false`
    are already folded to bools there (builder.py:1772-1783), so what reaches here
    is an enum value, a string at run time. Rendered as a JS string, as the go
    target does (_shared_utils.py:535). Swift maps a bare `null` to `nil` instead;
    that spelling is in no fixture and in no line of LANGUAGE_SPEC, so this follows
    the builder's own documented reading rather than inventing a third.
    """
    if condition.literal_kind == "ident":
        return js_string(str(condition.literal_value))
    return _js_value(condition.literal_value)


def _condition_js(condition: ConditionIR | BoolOpIR) -> str:
    """A condition as a JS boolean expression, recursively.

    Both operands of a BoolOpIR are parenthesized unconditionally: JS binds `&&`
    tighter than `||`, so an unparenthesized `a || b && c` would re-associate the
    tree the author actually wrote. Same reason _swift_flow_renderer.py:86 does it.
    """
    if isinstance(condition, BoolOpIR):
        left = _condition_js(condition.left)
        right = _condition_js(condition.right)
        js_op = "&&" if condition.op == "and" else "||"
        return f"({left}) {js_op} ({right})"

    op = _JS_OPS.get(condition.op)
    if op is None:
        raise NotImplementedError(
            f"claude-workflow: comparison operator {condition.op!r} is not rendered"
        )
    access = _state_access(condition.step_name, condition.field)
    return f"{access} {op} {_condition_literal(condition)}"


def _render_body(
    body: tuple[object, ...], steps_by_name: dict[str, StepIR], phase: str, indent: str
) -> list[str]:
    """The lines of a block body, one indent level deeper. `phase` is passed
    through unchanged: §4.3 moves the global only at the top level."""
    lines: list[str] = []
    for sub in body:
        lines += _render_item(sub, steps_by_name, phase, indent + "  ")
    return lines


def _render_if(
    item: IfBlockIR, steps_by_name: dict[str, StepIR], phase: str, indent: str
) -> list[str]:
    lines = [f"{indent}if ({_condition_js(item.condition)}) {{"]
    lines += _render_body(item.then_body, steps_by_name, phase, indent)
    if item.else_body:
        lines.append(f"{indent}}} else {{")
        lines += _render_body(item.else_body, steps_by_name, phase, indent)
    lines.append(f"{indent}}}")
    return lines


def _render_match(
    item: MatchBlockIR, steps_by_name: dict[str, StepIR], phase: str, indent: str
) -> list[str]:
    """A native `switch`, whose case comparison is already strict in JS.

    Every arm ends in an explicit `break`. Without it JS falls through into the
    NEXT arm's body: `CASE low` would run `archive` and then `flag`. That is a
    behaviour change, not a style nit — and it is invisible to any test that only
    reads the emitted text for `case`. The DEFAULT arm (`MatchCaseIR.value is
    None`, graph.py:336) becomes `default:`, and breaks too: the builder puts it
    last today, and a break costs nothing if that ever stops being true.
    """
    scrutinee = _state_access(item.state_field, item.sub_field)
    lines = [f"{indent}switch ({scrutinee}) {{"]
    for arm in item.cases:
        label = "default:" if arm.value is None else f"case {js_string(arm.value)}:"
        lines.append(f"{indent}  {label}")
        lines += _render_body(arm.body, steps_by_name, phase, indent + "  ")
        lines.append(f"{indent}    break")
    lines.append(f"{indent}}}")
    return lines


def _render_while(
    item: WhileBlockIR, steps_by_name: dict[str, StepIR], phase: str, indent: str
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
    cond = _condition_js(item.condition)
    lines = [
        f"{indent}let {counter} = 0",
        f"{indent}while (({cond}) && {counter} < {item.max_iters}) {{",
        f"{indent}  {counter}++",
    ]
    lines += _render_body(item.body, steps_by_name, phase, indent)
    lines.append(f"{indent}}}")
    return lines


def _render_item(
    item: ChainItem, steps_by_name: dict[str, StepIR], phase: str, indent: str
) -> list[str]:
    """Dispatch one chain node. Tasks 8-10 add their branches here; `phase` is
    threaded down so an agent spawned inside a block carries the block's phase
    (§4.3) instead of moving the racy global."""
    if isinstance(item, CallIR):
        return [indent + line for line in _render_call(item, steps_by_name, phase)]
    if isinstance(item, IfBlockIR):
        return _render_if(item, steps_by_name, phase, indent)
    if isinstance(item, MatchBlockIR):
        return _render_match(item, steps_by_name, phase, indent)
    if isinstance(item, WhileBlockIR):
        return _render_while(item, steps_by_name, phase, indent)
    raise NotImplementedError(
        f"claude-workflow: {type(item).__name__} is not rendered yet "
        "(FOR EACH: Task 8 — sub-flows: Task 9)"
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
