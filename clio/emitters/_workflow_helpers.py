"""Helpers for target: claude-workflow — diagnostics, literals, meta, README."""
from __future__ import annotations

import json
from collections.abc import Callable

from clio.ir.graph import (
    ApiInvokeIR,
    CodeImplIR,
    FlowGraph,
    FlowIR,
    McpToolImplIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
)

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
