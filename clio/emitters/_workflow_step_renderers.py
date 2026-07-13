"""Step renderers for target: claude-workflow — judgment steps and exact stubs."""
from __future__ import annotations

import textwrap

from clio.emitters._workflow_helpers import js_identifier, js_string, schema_literal
from clio.ir.graph import ApiInvokeIR, CliInvokeIR, ContractIR, StepIR

_MODEL_TIERS = (
    ("claude-opus", "opus"),
    ("claude-sonnet", "sonnet"),
    ("claude-haiku", "haiku"),
)


def _model_tier(step: StepIR) -> str | None:
    """Map a declared Anthropic model id to the tier enum agent() accepts.

    Returns None when the source declares nothing — then we omit `model` and the
    subagent inherits the session model, which the Workflow tool documents as
    almost always the right call.
    """
    declared: str | None = None
    if isinstance(step.invoke, ApiInvokeIR):
        declared = step.invoke.model
    elif isinstance(step.invoke, CliInvokeIR):
        declared = step.invoke.model
    if not declared:
        return None
    for prefix, tier in _MODEL_TIERS:
        if declared.startswith(prefix):
            return tier
    return None  # unknown Anthropic id: inherit rather than guess


def _tpl_text(s: str) -> str:
    """Escape a static fragment for inclusion in a JS template literal.

    The prompt must be a template literal (the TAKES interpolate `${JSON.stringify
    (state[...])}`), so a backtick or a `${` coming from CLIO prose — DESCRIPTION
    and STRATEGIES are free text, and markdown backticks are the norm there —
    would close the literal early and emit a script that does not parse.
    Backslash first, or the escapes we add get re-escaped.
    """
    return s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def _prompt(step: StepIR) -> str:
    """The subagent prompt: intent, inputs, and the required output shape.

    Every static fragment is escaped; the `${…}` interpolations are the only raw
    JS in the literal, and this function is the only place that writes them.
    """
    parts = [_tpl_text(f"You are executing step `{step.name}` of a CLIO flow.")]
    if step.description:
        parts.append(_tpl_text(step.description))
    if step.strategies:
        parts.append(_tpl_text(f"Strategies for edge cases:\n{step.strategies}"))
    for field in step.takes:
        parts.append(
            _tpl_text(f"Input `{field.name}`:")
            + f"\n${{JSON.stringify(state[{js_string(field.name)}])}}"
        )
    if step.gives is not None:
        parts.append(_tpl_text(
            f"Return a JSON object with the single key `{step.gives.name}`, "
            "conforming to the provided schema."
        ))
    return "\n\n".join(parts)


def render_judgment_step_js(step: StepIR, contracts: dict[str, ContractIR]) -> str:
    """A judgment step delegates to a subagent. The host forces it through a
    structured-output tool and validates the result against `schema`, so contract
    validation costs no emitted code."""
    schema = (
        schema_literal(step.gives.type, contracts, step.gives.name)
        if step.gives is not None
        else "{ type: 'object' }"
    )
    opts = [f"label: {js_string(f'judgment:{step.name}')}"]
    tier = _model_tier(step)
    if tier is not None:
        opts.append(f"model: {js_string(tier)}")
    opts.append(f"schema: {textwrap.indent(schema, '      ').lstrip()}")
    opts.append("phase: phaseName")
    opts_js = "".join(f"      {o},\n" for o in opts)

    # The agent returns the schema object — { <gives.name>: value } (§4.1). What
    # the flow stores is the VALUE: downstream reads are state['<gives>'] (a kwarg
    # ref) and state['<gives>'].<field> (a condition), the same shape python
    # (state[gives.name]) and swift emit. Returning the wrapper would nest it
    # twice and every read would come back undefined, at run time, far from here.
    unwrap = (
        f"result[{js_string(step.gives.name)}]" if step.gives is not None else "result"
    )

    return f"""\
async function {js_identifier(step.name)}(state, phaseName) {{
  const result = await agent(
    `{_prompt(step)}`,
    {{
{opts_js}    }},
  )
  // agent() returns null on terminal failure — it does NOT throw. Convert, or
  // ON_FAIL / RESCUE would never fire and `undefined` would flow onward.
  if (result === null || result === undefined) {{
    throw new Error({js_string(f"clio: step '{step.name}' failed (agent returned no result)")})
  }}
  return {unwrap}
}}
"""


def render_exact_step_js(step: StepIR, contracts: dict[str, ContractIR]) -> str:
    """An exact `code` step is a stub the author fills in.

    The compiler emits the signature, the state keys to read and the field to
    return; it never invents the body. The stub throws until filled — returning
    `undefined` instead would flow silently into the next step and fail far from
    the cause.

    `contracts` is unused: the body is the author's, so there is nothing to
    validate against a schema here. It stays in the signature so the flow renderer
    can call either renderer the same way.
    """
    reads = ", ".join(f"state[{js_string(f.name)}]" for f in step.takes) or "(none)"
    gives = step.gives.name if step.gives is not None else "(none)"
    todo = f"TODO: implement exact step '{step.name}' (pure, no IO)"
    return f"""\
// STEP {step.name} — MODE: exact
// Reads:   {reads}
// Returns: {gives}
//
// This body must be a PURE function: the workflow sandbox has no filesystem, no
// network, no process and no clock — Date.now(), new Date() and Math.random()
// throw. Anything IO-shaped belongs in --target python / go / swift.
function {js_identifier(step.name)}(state) {{
  throw new Error({js_string(todo)})
}}
"""
