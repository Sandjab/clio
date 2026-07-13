"""Failure handling for target: claude-workflow — ON_FAIL, RESCUE, RESUME.

The four IR nodes of failure (`OnFailChainIR`, `RescueBlockIR`, `ErrorAccessIR`,
`ResumeIR`) render to plain JS `try` / `catch` / `throw`. Every one of them rests
on a single line emitted elsewhere:

    // _workflow_step_renderers, render_judgment_step_js
    if (result === null || result === undefined) { throw new Error(…) }

`agent()` returns **null** on terminal failure — it does not throw (§6.1). Remove
that conversion and nothing in this module ever fires: the retry loop below would
see a successful call returning null, and a RESCUE handler would be dead code that
passes every text assertion in the suite and never runs once in a real session.

**No backoff.** The sandbox has no clock: `Date.now()` and `new Date()` throw, and
there are no timers. Retries therefore run back-to-back. The compiler says so at
compile time (W_WF_002, _workflow_helpers) and the emitted code must not quietly
contradict it — `tests/test_emitters/test_workflow.py` sweeps every fixture's
output for `Date.now(` / `new Date(` / `Math.random(` / `setTimeout(`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from clio.emitters._workflow_helpers import js_identifier, js_string
from clio.ir.graph import CallIR, ErrorAccessIR, RescueBlockIR, ResumeIR, StepIR

# The flow renderer's two dispatchers, passed in rather than imported: they import
# this module, so importing them back would be a cycle. `_workflow_loops` takes its
# body renderer the same way and for the same reason.
RenderItem = Callable[[object, dict[str, StepIR], str, str], list[str]]
RenderBody = Callable[
    [tuple[object, ...], dict[str, StepIR], str, str, Mapping[str, str], str | None],
    list[str],
]

# The binding of a RESCUE handler's `catch`. A `_`-prefixed name cannot collide
# with a step function (a CLIO identifier reaching js_identifier keeps its name,
# and the lexer's `_`-leading names would still have to be *called* to shadow),
# and it is the name ErrorAccessIR reads its fields off — the two must agree, so
# they read the same constant.
ERR_VAR = "_err"


def attempt_fn_name(step: StepIR) -> str:
    """The inner function holding the step's one attempt, when an ON_FAIL chain
    wraps it. `$` cannot occur in a CLIO identifier (lexer.py:126-142), so this
    can never collide with a step the author declared."""
    return f"{js_identifier(step.name)}$attempt"


def _chain_parts(step: StepIR) -> tuple[int, str | None, str | None, bool]:
    """(retries, fallback step name, abort message, escalate present).

    Mirrors _swift_step_renderers._on_fail_chain_parts, including its reading of
    `escalate` as a no-op: the model tier is chosen once, at compile time, from
    the step's own `INVOKE` (_workflow_step_renderers._model_tier), and there is
    no second tier to escalate to at run time.
    """
    retries, fallback, abort_msg, escalate = 0, None, None, False
    if step.on_fail is None:
        return retries, fallback, abort_msg, escalate

    for strategy in step.on_fail.strategies:
        if strategy.kind == "retry" and strategy.max_retries is not None:
            retries = strategy.max_retries
        elif strategy.kind == "fallback":
            fallback = (
                strategy.fallback_step.name
                if strategy.fallback_step is not None
                else strategy.fallback_step_name
            )
        elif strategy.kind == "abort":
            abort_msg = strategy.abort_message or ""
        elif strategy.kind == "escalate":
            escalate = True
    return retries, fallback, abort_msg, escalate


def _chain_source(step: StepIR) -> str:
    """The declared chain, echoed into the emitted comment so the author can read
    what the loop below is a rendering of."""
    parts = []
    for s in step.on_fail.strategies if step.on_fail is not None else ():
        if s.kind == "retry":
            parts.append(f"retry({s.max_retries})")
        elif s.kind == "fallback":
            parts.append(f"fallback({s.fallback_step_name})")
        elif s.kind == "abort":
            parts.append(f"abort({(s.abort_message or '')!r})")
        else:
            parts.append(s.kind)
    return " then ".join(parts)


def render_on_fail_wrapper(step: StepIR) -> str:
    """The ON_FAIL chain, as the function the flow actually calls.

    The chain wraps the STEP, not the call site, because that is what the language
    declares: `ON_FAIL` is a STEP block field, so it must hold at *every* call site.
    A call-site wrapper would be silently dropped inside a `parallel()` /
    `pipeline()` body — that path builds a thunk EXPRESSION out of `call_js`
    (_workflow_loops) and never walks the statement dispatcher. python, go and
    swift all put the chain inside the step for the same reason.

    Shape (retry(N) [then escalate] [then fallback(s)] [then abort(msg)]):

      * the attempt loop runs `max(1, N)` times — at least once, so an abort-only
        or fallback-only chain still reaches its post-loop handler;
      * a failed attempt is caught and retried IMMEDIATELY: no backoff, no jitter,
        no clock (W_WF_002);
      * after exhaustion: the fallback step, called with the same `state` the
        wrapper was handed (so it reads the same TAKES); then the abort message;
      * with neither, the last error is rethrown. Falling through instead would
        return `undefined` into the next step, and the failure would surface on
        some later step that is not the broken one.
    """
    retries, fallback, abort_msg, escalate = _chain_parts(step)
    attempts = max(1, retries)
    inner = attempt_fn_name(step)

    lines = [
        f"// ON_FAIL: {_chain_source(step)}",
        "// Retries run back-to-back: the sandbox has no clock, so there is no",
        "// backoff and no jitter (W_WF_002 says so at compile time).",
    ]
    if escalate:
        lines.append(
            "// `escalate` is a no-op here: the model tier is fixed at compile time."
        )
    lines += [
        f"async function {js_identifier(step.name)}(state, phaseName) {{",
        "  let lastError = null",
        f"  for (let attempt = 0; attempt < {attempts}; attempt++) {{",
        "    try {",
        f"      return await {inner}(state, phaseName)",
        "    } catch (err) {",
        "      lastError = err",
        "    }",
        "  }",
    ]

    if fallback is not None and abort_msg is not None:
        lines += [
            f"  // ON_FAIL fallback: {fallback} — same state, so the same TAKES.",
            "  try {",
            f"    return await {js_identifier(fallback)}(state, phaseName)",
            "  } catch (err) {",
            f"    throw new Error({js_string(abort_msg)})",
            "  }",
        ]
    elif fallback is not None:
        lines += [
            f"  // ON_FAIL fallback: {fallback} — same state, so the same TAKES.",
            "  // No abort clause: the fallback's own failure propagates, cause intact.",
            f"  return await {js_identifier(fallback)}(state, phaseName)",
        ]
    elif abort_msg is not None:
        lines.append(f"  throw new Error({js_string(abort_msg)})")
    else:
        lines.append("  throw lastError")

    lines.append("}")
    return "\n".join(lines) + "\n"


def error_access_js(access: ErrorAccessIR) -> str:
    """`<rescued step>.error.message|type` — read off the error the catch bound.

    `err.name` for `.type`: it is the JS analog of python's
    `type(_err).__name__` (python.py:608-616). The IR builder restricts the field
    to exactly these two (graph.py:259-266), so a third value is a builder bug and
    says so rather than emitting `undefined`.
    """
    if access.field == "message":
        return f"{ERR_VAR}.message"
    if access.field == "type":
        return f"{ERR_VAR}.name"
    raise AssertionError(
        f"unreachable: ErrorAccessIR field {access.field!r} (builder validates "
        "message|type)"
    )


def abort_js(call: CallIR) -> str:
    """`abort("msg")` — a synthetic CallIR the IR builder injects into RESCUE
    bodies only (builder.py:1530-1533). It names no STEP, so every dispatcher must
    catch it *before* it looks the name up in `steps_by_name`.

    A bare `throw` and not a `return`: the handler runs inside a `catch` block in
    the middle of the flow body, and returning from there would resume the chain
    with the failed step's output unset — which is the one thing an abort exists to
    prevent."""
    message = next((v for k, v in call.kwargs if k == "message"), "")
    return f"throw new Error({js_string(str(message))})"


def resume_js(resume: ResumeIR, rescued_gives: str) -> list[str]:
    """`RESUME(<fallback step>.<field>)` — bind the fallback's value under the key
    the rescued step would have written, and let the chain continue.

    `state['<rescued gives>'] = state['<field>']`, and NOT
    `state['<fallback step>']['<field>']`: this target keys state by the GIVES
    FIELD name and stores the UNWRAPPED value (_workflow_flow_renderer._render_call
    binds `state[gives.name]`, and the judgment wrapper returns `result[gives.name]`,
    not the wrapper object). There is no `state['recover']`, and no nesting — the
    step-name form would read `undefined`, silently, at run time.

    The IR builder guarantees `field` is the fallback step's GIVES field name and
    that its type matches the rescued step's GIVES (builder.py:1421-1455), so the
    key on the right always exists by the time this line runs: the fallback step is
    called earlier in the same handler body.
    """
    return [
        f"// RESUME({resume.fallback_step}.{resume.field_name})",
        f"state[{js_string(rescued_gives)}] = state[{js_string(resume.field_name)}]",
    ]


def render_guarded(
    item: object,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    rescues: dict[str, RescueBlockIR],
    render_item: RenderItem,
    render_body: RenderBody,
) -> list[str]:
    """A TOP-LEVEL chain node, wrapped in its RESCUE handler when one protects it:

        try { <the rescued call> } catch (_err) { <handler body> }

    Top-level only, because that is where the IR builder allows a RESCUE target to
    be called (python.py:637-643 documents the same restriction, and gates on it
    too). The handler renders inside the `catch`, so a `RESUME` in it lands right
    after the failed step and the chain continues from the next node — which is the
    point of RESUME: a handler that ended the flow would be an abort.

    The two renderers arrive as callbacks, the way _workflow_loops takes its own
    body renderer: the dispatcher they belong to imports this module, so importing
    it back would be a cycle. `resume_key` — the GIVES field of the protected step —
    is the one piece of context a RESUME cannot get any other way, and this is the
    only function that knows it.

    The `catch` sees a failed judgment step only because the step wrapper converts
    agent()'s null into a throw (§6.1). That is the whole contract between this
    module and the step renderer, and it is why there is no null check here.
    """
    rescue = rescues.get(item.step_name) if isinstance(item, CallIR) else None
    if rescue is None:
        return render_item(item, steps_by_name, phase_js, indent)

    step = steps_by_name[rescue.step_name]
    protected = render_item(item, steps_by_name, phase_js, indent + "  ")
    handler = render_body(
        rescue.body,
        steps_by_name,
        phase_js,
        indent,
        {},
        step.gives.name if step.gives is not None else None,
    )
    return [
        f"{indent}try {{",
        *protected,
        f"{indent}}} catch ({ERR_VAR}) {{",
        *handler,
        f"{indent}}}",
    ]
