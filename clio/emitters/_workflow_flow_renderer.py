"""Chain → JS for target: claude-workflow — the flow body of the emitted script.

The dispatcher, and the statements that are one node deep: the call, the three
branches, the preamble. The other two thirds of the job live next door, and the
split is by responsibility rather than by size:

  * _workflow_expressions — how a node READS a value (a state key, a loop
    variable). Expressions never render a statement,
    so the dependency is one-way.
  * _workflow_loops — FOR EACH, sequential and parallel. It renders its own body,
    which may contain anything, so it takes `_render_body` as a parameter rather
    than importing this module back.
  * _workflow_subflows — the FLOW→FLOW call graph: which flows this script must
    inline, in what order, and the refusal (E_WF_007) that inlining a recursive
    flow would otherwise defer to a stack overflow at run time. Analysis only, so
    it renders nothing and this module can import it.

A sub-flow is emitted here (render_subflow_js) because it is a body like any
other. ON_FAIL / RESCUE (T10) hangs off `_render_item`, the single dispatch point.
"""
from __future__ import annotations

from clio.emitters._workflow_errors import abort_js, render_guarded, resume_js
from clio.emitters._workflow_expressions import (
    NO_BINDINGS,
    Bindings,
    call_js,
    condition_js,
    flow_input,
    state_access,
)
from clio.emitters._workflow_helpers import js_string, phase_titles
from clio.emitters._workflow_loops import render_foreach
from clio.emitters._workflow_subflows import PHASE_PARAM, subflow_fn_name
from clio.ir.graph import (
    CallIR,
    FlowCallIR,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    RescueBlockIR,
    ResumeIR,
    StepIR,
    WhileBlockIR,
)

# A chain node — the union of FlowIR.chain. Widened to `object` at the dispatch
# seam so a node type this task does not render fails loudly rather than silently.
ChainItem = object

# The preamble's own bindings: the normalized `args` (see _render_preamble) and the
# error it catches from JSON.parse. `$`-prefixed, so no step name can collide with
# either — the same namespace trick as `flow_$x`.
_ARGS = "$args"
_ERR = "$err"


def _render_call(
    call: CallIR, steps_by_name: dict[str, StepIR], phase_js: str, bindings: Bindings
) -> list[str]:
    """`state['<gives>'] = await <step>(…)`.

    The state key is the GIVES *field* name, not the step name — that is the key
    every reader uses: a kwarg ref (`@rows`), an IF condition (`r.score`), a MATCH
    scrutinee. python.py:635 and _swift_flow_renderer.py:264 key state the same way.
    """
    step = steps_by_name[call.step_name]
    invocation = call_js(call, steps_by_name, phase_js, bindings)
    if step.gives is None:
        return [invocation]  # a side-effect step: nothing to bind
    return [f"state[{js_string(step.gives.name)}] = {invocation}"]


def _render_flow_call(call: FlowCallIR, phase_js: str, bindings: Bindings) -> list[str]:
    """`Object.assign(state, await flow_$<name>(<input>, <phase>))`.

    The merge, and not `state['<call site>'] = …`, because a sub-flow's declared
    GIVES are published as TOP-LEVEL keys of the parent state — the convention
    python (`state.update(run_x(...))`, python.py:686-693) and go already emit, and
    the one the IR builder itself assumes when it resolves a downstream `@field`
    against the sub-flow's signature. Binding the whole result under the call-site
    name instead would leave every one of those reads on `undefined`: JS returns it
    silently, so `s2(b=b) -> level_c(c=c)` would run with `c` unset rather than
    fail.

    `flow_input` and not `step_input`: the callee writes into the object it is
    handed, so it must never be the parent's own state.
    """
    fn = subflow_fn_name(call.flow_name)
    return [f"Object.assign(state, await {fn}({flow_input(call.kwargs, bindings)}, {phase_js}))"]


def _render_body(
    body: tuple[object, ...],
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    bindings: Bindings,
    resume_key: str | None = None,
) -> list[str]:
    """The lines of a block body, one indent level deeper. `phase_js` is passed
    through unchanged: §4.3 moves the global only at the top level.

    `resume_key` is the GIVES field of the step a RESCUE handler protects — the key
    a `RESUME` in this body binds its value under. It is None everywhere else, which
    is why a RESUME reached outside a handler fails loudly (_render_item) instead of
    binding nothing. FOR EACH takes this function as a callback and calls it with
    five positional arguments, so a body nested in a loop drops the key: a RESUME
    there is exactly the case that must not be guessed at.
    """
    lines: list[str] = []
    for sub in body:
        lines += _render_item(
            sub, steps_by_name, phase_js, indent + "  ", bindings, resume_key
        )
    return lines


def _render_if(
    item: IfBlockIR,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    lines = [f"{indent}if ({condition_js(item.condition, bindings)}) {{"]
    lines += _render_body(item.then_body, steps_by_name, phase_js, indent, bindings)
    if item.else_body:
        lines.append(f"{indent}}} else {{")
        lines += _render_body(item.else_body, steps_by_name, phase_js, indent, bindings)
    lines.append(f"{indent}}}")
    return lines


def _render_match(
    item: MatchBlockIR,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
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
        lines += _render_body(arm.body, steps_by_name, phase_js, indent + "  ", bindings)
        lines.append(f"{indent}    break")
    lines.append(f"{indent}}}")
    return lines


def _render_while(
    item: WhileBlockIR,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
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
    lines += _render_body(item.body, steps_by_name, phase_js, indent, bindings)
    lines.append(f"{indent}}}")
    return lines


def _render_item(
    item: ChainItem,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    bindings: Bindings = NO_BINDINGS,
    resume_key: str | None = None,
) -> list[str]:
    """Dispatch one chain node. `phase_js` is threaded down so an agent spawned
    inside a block carries the block's phase (§4.3) instead of moving the racy
    global; `resume_key` only ever has a value inside a RESCUE handler."""
    if isinstance(item, CallIR) and item.step_name == "abort":
        # Before the CallIR branch, which would look 'abort' up in steps_by_name and
        # KeyError: it is a synthetic terminator, not a step (_workflow_errors).
        # Here rather than in _render_call because a rescue body may nest it inside
        # an IF, and every recursion lands on this dispatcher.
        return [indent + abort_js(item)]
    if isinstance(item, ResumeIR):
        if resume_key is None:
            raise NotImplementedError(
                f"claude-workflow: RESUME({item.fallback_step}.{item.field_name}) "
                f"(line {item.line}) is not rendered outside a RESCUE handler whose "
                "step has a GIVES — there is no key to bind the value under, and "
                "emitting nothing would let the flow continue on a stale value."
            )
        return [indent + line for line in resume_js(item, resume_key)]
    if isinstance(item, CallIR):
        return [
            indent + line for line in _render_call(item, steps_by_name, phase_js, bindings)
        ]
    if isinstance(item, FlowCallIR):
        return [
            indent + line for line in _render_flow_call(item, phase_js, bindings)
        ]
    if isinstance(item, IfBlockIR):
        return _render_if(item, steps_by_name, phase_js, indent, bindings)
    if isinstance(item, MatchBlockIR):
        return _render_match(item, steps_by_name, phase_js, indent, bindings)
    if isinstance(item, WhileBlockIR):
        return _render_while(item, steps_by_name, phase_js, indent, bindings)
    if isinstance(item, ForEachIR):
        return render_foreach(
            item, steps_by_name, phase_js, indent, bindings, _render_body
        )
    raise NotImplementedError(
        f"claude-workflow: {type(item).__name__} is not rendered"
    )


def _render_top_item(
    item: ChainItem,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    rescues: dict[str, RescueBlockIR],
) -> list[str]:
    """A top-level chain node — the only place a RESCUE handler may wrap one."""
    return render_guarded(
        item, steps_by_name, phase_js, indent, rescues, _render_item, _render_body
    )


def _render_preamble(flow: FlowIR) -> list[str]:
    """`const state = {}`, then the flow's TAKES bound from the `args` global.

    A flow with no TAKES never mentions `args`: the runtime is free to hand the
    script nothing, and a guard emitted anyway would throw on a legitimate run.
    `typeof args` rather than `args === undefined` because an undeclared global is
    a ReferenceError, not undefined — the guard must survive its own failure case.
    That is also why the normalization below reads the global once, AFTER that
    guard, and never before it.

    The runtime delivers `args` as a JSON **string**, not as the object the shape
    of the invocation suggests (measured with a probe workflow: `typeof args` came
    back `'string'`). Indexing a string by a field name yields `undefined`, so a
    preamble that read `args[key]` directly threw `requires args[…]` on every flow
    that declared a TAKES — before the first step ran. Both shapes are accepted
    here: nothing in the runtime's contract makes the string form permanent, and
    tolerating the object costs one `typeof`.

    `$args` and not `_args`: a step may legally be named `_args`, and it is emitted
    as `async function _args(…)` in this same module scope — a duplicate
    declaration, which is a SyntaxError. A `$` cannot occur in a CLIO identifier
    (lexer.py:126-142) and js_identifier only ever *appends* one, so a leading `$`
    is a namespace no step name can reach — the same reasoning as `flow_$x`
    (_workflow_subflows.subflow_fn_name).
    """
    lines = ["const state = {}"]
    if not flow.takes:
        return lines

    declared = ", ".join(f.name for f in flow.takes)
    missing_all = js_string(f"clio: flow '{flow.name}' requires args: {declared}")
    bad_json = js_string(
        f"clio: flow '{flow.name}' received args as a string that is not valid JSON: "
    )
    lines += [
        "if (typeof args === 'undefined' || args === null) {",
        f"  throw new Error({missing_all})",
        "}",
        f"let {_ARGS} = args",
        # The runtime hands the script a JSON string; a host that hands it an object
        # falls straight through. The parse is guarded: unguarded, a non-JSON string
        # raises a bare `SyntaxError: Unexpected token`, which names neither the flow
        # nor `args` and reads like a bug in the sandbox.
        f"if (typeof {_ARGS} === 'string') {{",
        "  try {",
        f"    {_ARGS} = JSON.parse({_ARGS})",
        f"  }} catch ({_ERR}) {{",
        f"    throw new Error({bad_json} + {_ERR}.message)",
        "  }",
        "}",
        # After the parse, `args` may be any JSON value: 'null' parses to null and
        # '3' to a number. Indexing null throws a TypeError from inside the guard
        # that exists to prevent exactly that.
        f"if ({_ARGS} === null || typeof {_ARGS} !== 'object') {{",
        f"  throw new Error({missing_all})",
        "}",
    ]
    for field in flow.takes:
        key = js_string(field.name)
        missing = js_string(f"clio: flow '{flow.name}' requires args[{field.name!r}]")
        lines += [
            f"if ({_ARGS}[{key}] === undefined) {{",
            f"  throw new Error({missing})",
            "}",
            f"state[{key}] = {_ARGS}[{key}]",
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
    rescues = {rb.step_name: rb for rb in flow.rescues}
    lines = _render_preamble(flow)
    for title, item in zip(phase_titles(flow), flow.chain, strict=True):
        lines.append("")
        lines.append(f"phase({js_string(title)})")
        lines += _render_top_item(item, steps_by_name, js_string(title), "", rescues)
    return "\n".join(lines) + "\n"


def render_subflow_js(flow: FlowIR, steps_by_name: dict[str, StepIR]) -> str:
    """A sub-flow, inlined as a local `async function` (§4.2).

    Not `workflow({scriptPath})`: that call caps nesting at one level and CLIO
    nests arbitrarily — see _workflow_subflows for the full reasoning.

    Three things the signature encodes:

      * `state` is the caller's COPY, never its object (flow_input), so the writes
        this body makes — every step binds its GIVES into `state` — stay local;
      * `phase$` is a PARAMETER, so the agents spawned in here report the phase of
        the CALL SITE. A sub-flow does not know that phase at emit time (the same
        function can be called from two sites), and only the top level of the entry
        flow may move the `phase()` global (§4.3) — so no phase() call is emitted
        here, and none of the titles in meta.phases is ever invented;
      * the return value is exactly the flow's declared GIVES, read back out of the
        local state. The call site merges those into the parent state
        (`Object.assign`), which is how a downstream `@field` read resolves — the
        same convention python (`state.update(run_x(...))`) and go emit.
    """
    rescues = {rb.step_name: rb for rb in flow.rescues}
    lines = [f"async function {subflow_fn_name(flow.name)}(state, {PHASE_PARAM}) {{"]
    for item in flow.chain:
        lines += _render_top_item(item, steps_by_name, PHASE_PARAM, "  ", rescues)
    fields = ", ".join(
        f"{js_string(g.name)}: state[{js_string(g.name)}]" for g in flow.gives
    )
    lines.append(f"  return {{ {fields} }}" if fields else "  return {}")
    lines.append("}")
    return "\n".join(lines) + "\n"
