"""Helpers for target: claude-workflow — diagnostics, literals, meta, README."""
from __future__ import annotations

import json
from collections.abc import Callable

from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    ApiInvokeIR,
    CodeImplIR,
    ContractIR,
    FlowGraph,
    FlowIR,
    McpToolImplIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
)
from clio.parser.ast_nodes import TypeExpr

# ---------------------------------------------------------------------------
# Compile-time validation error codes (permanent — stable across releases)
# ---------------------------------------------------------------------------

E_WF_001 = "E_WF_001: source declares no FLOW; nothing to orchestrate."
E_WF_002 = (
    "E_WF_002: target: claude-workflow runs steps as Claude Code subagents and "
    "cannot call non-Anthropic providers. Use --target python for "
    "openai / bedrock / vertex."
)
E_WF_003 = (
    "E_WF_003: target: claude-workflow cannot execute IO from an exact step: the "
    "workflow sandbox has no process, no network and no filesystem. Move the IO "
    "out of the flow, or use --target python / go / swift."
)
E_WF_004 = (
    "E_WF_004: target: claude-workflow can only embed exact step bodies in "
    "JavaScript (LANG: node or LANG: auto). Use --target python / go / swift for "
    "other languages."
)
E_WF_005 = (
    "E_WF_005: CONTRACT reference cycle — cannot inline a self-referential schema "
    "for target claude-workflow (the sandbox cannot resolve a $ref at run time)."
)

# ---------------------------------------------------------------------------
# Compile-time degradation warnings (the feature still compiles, with less)
# ---------------------------------------------------------------------------

W_WF_001 = (
    "W_WF_001: `cache:` is ignored by target claude-workflow — the sandbox has no "
    "filesystem and no clock. A cache miss is slower, never wrong."
)
W_WF_002 = (
    "W_WF_002: ON_FAIL retries run WITHOUT backoff under target claude-workflow — "
    "the sandbox has no clock (`Date.now()` throws)."
)
W_WF_003 = (
    "W_WF_003: `CONTRACT … ASSERT` is not enforced by target claude-workflow. The "
    "JSON Schema (types, ranges, enums) IS enforced by the host; the ASSERT "
    "predicate is not."
)

# Langs whose exact bodies this target can emit (no LANG = None = auto-detect).
_WF_OK_LANGS: frozenset[str | None] = frozenset({"node", "auto", None})

# Impls that need a process, a socket or a filesystem — none of which exist here.
_IO_IMPLS = (ShellImplIR, RestImplIR, SqlImplIR, McpToolImplIR)


def js_string(s: str) -> str:
    """A single-quoted JS string literal.

    json.dumps gives correct escaping for backslashes, control chars and
    non-ASCII; we then convert the double-quoted form to single quotes to match
    the house style of the emitted script.
    """
    inner = json.dumps(s, ensure_ascii=False)[1:-1].replace("'", "\\'")
    return f"'{inner}'"


_REF_PREFIX = "../contracts/"
_REF_SUFFIX = ".schema.json"


def _deref(schema: dict, contracts: dict[str, ContractIR], seen: frozenset[str]) -> dict:
    """Recursively replace every {"$ref": "../contracts/X.schema.json"} with X's
    own (recursively inlined) schema. `seen` carries the ancestor chain so a cycle
    raises instead of recursing forever, and an unresolvable name raises rather
    than leaving a dangling $ref the sandbox cannot read.

    `x-clio-assert` is dropped from an inlined contract: the host validates the
    agent's output against this schema and does not evaluate CLIO's assert AST
    (W_WF_003). The claude-cli target strips it for the same reason.
    """
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        name = ref[len(_REF_PREFIX):-len(_REF_SUFFIX)]
        if name in seen:
            raise ValueError(f"{E_WF_005} (contract={name!r})")
        target = contracts.get(name)
        if target is None:
            raise ValueError(f"{E_WF_005} (unknown contract {name!r})")
        inlined = {
            k: v for k, v in target.json_schema.items() if k != "x-clio-assert"
        }
        return _deref(inlined, contracts, seen | {name})

    out: dict = {}
    for k, v in schema.items():
        if isinstance(v, dict):
            out[k] = _deref(v, contracts, seen)
        elif isinstance(v, list):
            out[k] = [
                _deref(i, contracts, seen) if isinstance(i, dict) else i for i in v
            ]
        else:
            out[k] = v
    return out


def inline_schema(t: TypeExpr, contracts: dict[str, ContractIR]) -> dict:
    """A fully self-contained JSON Schema for `t` — every $ref inlined."""
    return _deref(type_to_json_schema(t), contracts, frozenset())


def schema_literal(
    t: TypeExpr, contracts: dict[str, ContractIR], field_name: str
) -> str:
    """The JS object literal for a step's GIVES schema.

    A step's GIVES is a single NAMED field, and conditions read it as
    state[step].<field> — so the agent must return an OBJECT wrapping that one
    field, not the bare value. `field_name` is StepIR.gives.name (not the step
    name: they differ).
    """
    obj = {
        "type": "object",
        "properties": {field_name: inline_schema(t, contracts)},
        "required": [field_name],
        "additionalProperties": False,
    }
    return json.dumps(obj, indent=2, ensure_ascii=False)


def workflow_name(graph: FlowGraph) -> str:
    """kebab-case name of the entry flow — used for meta.name and the filename."""
    flow = graph.flow or (graph.flows[0] if graph.flows else None)
    if flow is None:
        raise ValueError(E_WF_001)
    return flow.name.replace("_", "-")


def phase_titles(flow: FlowIR) -> list[str]:
    """One phase per top-level element of the flow chain. Task 6 implements the
    non-empty cases; an empty chain has no phases."""
    return []


def render_meta(graph: FlowGraph) -> str:
    """The `export const meta` block. It MUST be a pure literal — no variables,
    no calls, no interpolation — or the Workflow runtime rejects the script."""
    flow = graph.flow or graph.flows[0]
    name = workflow_name(graph)
    desc = flow.description or f"CLIO flow {flow.name}"
    lines = [
        "export const meta = {",
        f"  name: {js_string(name)},",
        f"  description: {js_string(desc)},",
        "  phases: [",
    ]
    for title in phase_titles(flow):
        lines.append(f"    {{ title: {js_string(title)} }},")
    lines.append("  ],")
    lines.append("}")
    return "\n".join(lines)


def _noop_warn(_msg: str) -> None:
    return None


def validate_graph_for_workflow(
    graph: FlowGraph, warn: Callable[[str], None] = _noop_warn
) -> None:
    """Raise ValueError with a stable E_WF_NNN code for anything this target
    cannot honor; call `warn` for anything it degrades. Called as the first
    statement of emit().

    `warn` is injected (rather than printing directly) so tests can capture it —
    same seam as ClaudeSkillEmitter._validate.

    TEST blocks are deliberately NOT refused: they are inert here (only the
    python target emits pytest files), and refusing them would reject a source
    over a block this target simply ignores.
    """
    if len(graph.flows) == 0:
        raise ValueError(E_WF_001)

    for step in graph.steps:
        where = f"(step={step.name!r}, line {step.line})"

        # E_WF_003 first: an IO impl is refused whatever LANG it carries.
        if isinstance(step.impl, _IO_IMPLS):
            raise ValueError(f"{E_WF_003} {where}")

        # E_WF_004: a non-JS exact body, in either spelling — the `LANG:`
        # directive (StepIR.lang) or impl.lang (CodeImplIR.lang).
        if step.mode == "exact" and step.lang not in _WF_OK_LANGS:
            raise ValueError(f"{E_WF_004} {where} lang={step.lang!r}")
        if isinstance(step.impl, CodeImplIR) and step.impl.lang not in _WF_OK_LANGS:
            raise ValueError(f"{E_WF_004} {where} lang={step.impl.lang!r}")

        # E_WF_002: an agent() cannot reach a non-Anthropic provider.
        # CliInvokeIR needs no mapping: here the agent() call IS the Claude Code
        # invocation. It is accepted as-is.
        if isinstance(step.invoke, ApiInvokeIR) and step.invoke.protocol != "anthropic":
            raise ValueError(
                f"{E_WF_002} {where} protocol={step.invoke.protocol!r}"
            )

        if step.cache is not None and step.cache.mode != "off":
            warn(f"{W_WF_001} {where}")
        if step.on_fail is not None and any(
            s.kind == "retry" for s in step.on_fail.strategies
        ):
            warn(f"{W_WF_002} {where}")

    for contract in graph.contracts:
        if contract.assert_json_ast is not None:
            warn(f"{W_WF_003} (contract={contract.name!r}, line {contract.line})")
