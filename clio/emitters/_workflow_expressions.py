"""Expressions for target: claude-workflow — how a chain node READS its inputs.

Split out of _workflow_flow_renderer, which renders STATEMENTS (calls, branches,
loops): the two were one module until FOR EACH (Task 8) pushed it past the
~300-line budget, with Tasks 9-10 still to land. The seam is one-way — an
expression never renders a statement — so there is no import cycle, and it is the
seam the loop work actually needed: every function here takes the same `Bindings`
that FOR EACH introduces.

`Bindings` is the whole point of the module. A CLIO flow reads its values from one
of two places, and the renderer must never confuse them:

  * a **state key** — `state['rows']`, written by an earlier step's GIVES;
  * a **local binding** — the `for (const doc of …)` variable, or a `pipeline()`
    stage's `(prevResult, originalItem)` parameters. These are JS bindings. They
    are NOT in state, and inside a parallel body they must not be: concurrent
    items writing the loop variable into the shared object would race.

Getting that backwards emits JS that parses, runs, and silently reads `undefined`
— `undefined > 0.5` is false, and a `switch (undefined)` takes no arm. Which is
why the mapping is a parameter threaded everywhere rather than a guess made twice.
"""
from __future__ import annotations

from collections.abc import Mapping

from clio.emitters._workflow_helpers import js_identifier, js_string
from clio.ir.graph import BoolOpIR, CallIR, ConditionIR, StepIR

# CLIO name -> the JS expression that shadows it at the point being rendered.
# Empty at the top level of a flow: there, every name is a state key.
Bindings = Mapping[str, str]

NO_BINDINGS: Bindings = {}


def js_value(value: object) -> str:
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


def read(name: str, bindings: Bindings) -> str:
    """The JS expression for a CLIO state-field name: a local binding if one
    shadows it, `state['<name>']` otherwise."""
    return bindings.get(name) or f"state[{js_string(name)}]"


def state_access(state_field: str, field: str, bindings: Bindings) -> str:
    """`state['<state field>'].<field>` — the read side of a step's GIVES write.

    `ConditionIR.step_name` and `MatchBlockIR.state_field` name the state KEY,
    which is the producing step's GIVES *field* name and not the step's name
    (_swift_flow_renderer.py:89-90 documents the same field the same way): in
    swift_control_flow.clio `assess` GIVES `r`, and `IF r.score > 0.5` reads
    state['r'].score. Keying on the step name instead yields `undefined`, and
    `undefined > 0.5` is silently false rather than an error.

    Inside a FOR EACH the same field names the LOOP VARIABLE instead —
    swift_foreach_seq.clio's `FOR EACH a IN assessments: MATCH a.level` builds
    MatchBlockIR(state_field='a'), and `a` is a JS binding, never a state key.
    Hence `bindings`.
    """
    return f"{read(state_field, bindings)}.{field}"


def _condition_literal(condition: ConditionIR) -> str:
    """The right-hand side of a comparison.

    `ident` is the one kind js_value cannot take as-is: its value is a bare word
    from the source, which the builder resolves to an enum member — `true`/`false`
    are already folded to bools there (builder.py:1772-1783), so what reaches here
    is an enum value, a string at run time. Rendered as a JS string, as the go
    target does (_shared_utils.py:535). Swift maps a bare `null` to `nil` instead;
    that spelling is in no fixture and in no line of LANGUAGE_SPEC, so this follows
    the builder's own documented reading rather than inventing a third.
    """
    if condition.literal_kind == "ident":
        return js_string(str(condition.literal_value))
    return js_value(condition.literal_value)


# CLIO compares by value; JS `==` does not. `0 == false`, `'' == 0` and
# `'1' == 1` are all true under it, so an int/bool comparison the author wrote
# would quietly change meaning. Only the strict forms are ever emitted — the
# ordering operators are the same token in both languages and carry no such trap.
_JS_OPS = {"==": "===", "!=": "!==", ">": ">", ">=": ">=", "<": "<", "<=": "<="}


def condition_js(condition: ConditionIR | BoolOpIR, bindings: Bindings) -> str:
    """A condition as a JS boolean expression, recursively.

    Both operands of a BoolOpIR are parenthesized unconditionally: JS binds `&&`
    tighter than `||`, so an unparenthesized `a || b && c` would re-associate the
    tree the author actually wrote. Same reason _swift_flow_renderer.py:86 does it.
    """
    if isinstance(condition, BoolOpIR):
        left = condition_js(condition.left, bindings)
        right = condition_js(condition.right, bindings)
        js_op = "&&" if condition.op == "and" else "||"
        return f"({left}) {js_op} ({right})"

    op = _JS_OPS.get(condition.op)
    if op is None:
        raise NotImplementedError(
            f"claude-workflow: comparison operator {condition.op!r} is not rendered"
        )
    access = state_access(condition.step_name, condition.field, bindings)
    return f"{access} {op} {_condition_literal(condition)}"


def step_input(kwargs: tuple[tuple[str, object], ...], bindings: Bindings) -> str:
    """The object a step reads its TAKES from.

    An emitted step reads `state['<take>']` (_workflow_step_renderers), so every
    TAKES has to be bound under its own name before the call. The call site does
    that with a shadowed COPY — `{ ...state, x: … }` — and never by writing into
    state, for two reasons that both bite at run time:

      * a literal TAKES named like some step's GIVES would clobber that output
        (`assess(x="in")` in swift_control_flow.clio binds `x` from a literal —
        nothing in state holds it, and nothing may be overwritten to put it there);
      * inside parallel() / pipeline(), concurrent items writing the loop variable
        into the shared state would race.

    When every kwarg is an identity ref (`@x` bound to TAKES `x` — what the `->`
    pipe sugar produces, and the common case), the copy would be a no-op: pass
    `state` itself and keep the emitted line readable. That shortcut is exactly
    what a local binding must NOT take: swift_parallel.clio's `classify(item=item)`
    is an identity ref to the LOOP VARIABLE, which is not in state at all — passing
    `state` untouched there would classify every item on `undefined`.
    """
    overlays = _overlays(kwargs, bindings)
    if not overlays:
        return "state"
    return "{ ...state, " + ", ".join(overlays) + " }"


def _overlays(kwargs: tuple[tuple[str, object], ...], bindings: Bindings) -> list[str]:
    """The `k: v` fragments that shadow state for one call site."""
    overlays: list[str] = []
    for name, value in kwargs:
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            local = bindings.get(ref)
            if local is not None:
                overlays.append(f"{js_string(name)}: {local}")
            elif ref != name:
                overlays.append(f"{js_string(name)}: state[{js_string(ref)}]")
            # else: an identity ref to a state key — already in state under `name`.
        else:
            overlays.append(f"{js_string(name)}: {js_value(value)}")
    return overlays


def flow_input(kwargs: tuple[tuple[str, object], ...], bindings: Bindings) -> str:
    """The object an INLINED SUB-FLOW reads its TAKES from — always a fresh copy.

    This is step_input without its shortcut, and the difference is load-bearing. A
    step function only READS the object it is handed, so passing `state` itself
    when no kwarg shadows anything is free. A sub-flow function WRITES into it:
    every step in its chain binds its GIVES there. Handing it the parent's own
    state would

      * leak the sub-flow's intermediate keys into the parent, clobbering a parent
        key that happens to share a name — silently, since JS just overwrites;
      * race inside parallel() / pipeline(), where concurrent items would each be
        writing into that one shared object.

    So the copy is emitted even when it looks like a no-op (`{ ...state }`). The
    sub-flow's declared GIVES come back through its return value, and only those:
    render_subflow_js builds the returned object from FlowIR.gives.
    """
    overlays = _overlays(kwargs, bindings)
    if not overlays:
        return "{ ...state }"
    return "{ ...state, " + ", ".join(overlays) + " }"


def loop_binding(loop_var: str, bindings: Bindings) -> dict[str, str]:
    """`bindings` extended with the loop variable, bound to its own JS name.

    js_identifier because a CLIO loop variable is any `[a-zA-Z_][a-zA-Z0-9_]*` —
    `FOR EACH class IN rows` parses, and `for (const class of …)` is a SyntaxError.
    """
    return {**bindings, loop_var: js_identifier(loop_var)}


def call_js(
    call: CallIR, steps_by_name: dict[str, StepIR], phase_js: str, bindings: Bindings
) -> str:
    """`await <step>(<input>, <phase>)` — the call expression, unbound.

    `await` even on an exact step, whose stub is a plain `function`: awaiting a
    non-promise yields the value, and the day an author makes a stub async the call
    site is already right.

    The phase travels as an ARGUMENT — the step wrapper hands it to agent({phase})
    — and never as a `phase()` call from inside a block: that global is racy under
    parallel() / pipeline(), where the last writer wins (§4.3).

    `phase_js` is a JS EXPRESSION, not a title: a string literal at the top level of
    the entry flow, and the `phase$` PARAMETER inside an inlined sub-flow (Task 9),
    which is called from a phase it cannot know at emit time. Freezing a literal
    there would name a phase `meta.phases` never declared.
    """
    step = steps_by_name[call.step_name]
    return (
        f"await {js_identifier(step.name)}"
        f"({step_input(call.kwargs, bindings)}, {phase_js})"
    )


def gives_of(call: CallIR, steps_by_name: dict[str, StepIR]) -> str | None:
    """The state-field name a call produces. A step's NAME and its GIVES FIELD
    differ — `review` GIVES `verdict`, and a downstream kwarg reads `@verdict` —
    and it is the field name that every reader keys on."""
    step = steps_by_name[call.step_name]
    return step.gives.name if step.gives is not None else None
