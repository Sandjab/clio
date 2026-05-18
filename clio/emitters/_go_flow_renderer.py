"""Renderer for the top-level flow orchestrator (`flow/flow.go`).

Emits `func Run(ctx, kwargs) (state, error)` that chains every item in
graph.flow.chain.  v0.20.0 scope: sequential chain + IF/ELSE (T12).
T13-T15 will extend `_render_chain_item` with MATCH/WHILE/FOR EACH.
"""
from __future__ import annotations

from clio.emitters._go_helpers import _go_module_name
from clio.emitters._shared_utils import (
    _go_condition_expr,
    _to_class_name,
    _to_go_field_name,
)
from clio.ir.graph import CallIR, ContractIR, FlowGraph, IfBlockIR, MatchBlockIR, StepIR


def _go_kwarg_value(
    value: object,
    contracts: dict[str, ContractIR],
    state_field_to_step: dict[str, StepIR],
) -> str:
    """Render one CallIR kwarg value as a Go expression.

    Two cases, mirroring the python emitter's logic in python.py:
    - Reference ``@<field>`` — the field name is the GIVES name of some prior
      step.  We emit a state-based type assertion:
          state["<field>"].(steps.<StepCls>Out).<GoField>
      This is the same pattern used by `_go_condition_expr` for IF conditions,
      so the two readers are consistent with the writer.
    - Literal (str / int / float / bool) — rendered as a Go literal.
      Plain strings that do not start with ``@`` are string literals.
    """
    if isinstance(value, str) and value.startswith("@"):
        ref = value[1:]  # the state-dict key (= the prior step's GIVES name)
        step = state_field_to_step.get(ref)
        if step is not None:
            cls = _to_class_name(step.name)
            gf = _to_go_field_name(ref)
            return f'state["{ref}"].(steps.{cls}Out).{gf}'
        # Unknown ref — fall back to untyped any access (should not happen
        # after IR validation, but guards against future call-site bugs).
        return f'state["{ref}"]'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"int64({value})"
    if isinstance(value, float):
        return f"float64({value!r})"
    # str literal — emit as a Go interpreted string literal (double-quoted).
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _kwargs_to_step_input(
    call: CallIR,
    step: StepIR,
    contracts: dict[str, ContractIR],
    state_field_to_step: dict[str, StepIR],
) -> str:
    """Render the ``steps.<Step>In{...}`` initialisation from CallIR.kwargs.

    Each kwarg pair binds a TAKES field name to either a literal value or a
    reference into a prior step's typed output (``@<field>`` syntax).

    Iterates ``call.kwargs`` directly — no assumptions about GIVES/TAKES field
    name alignment between adjacent steps.  Mirrors the python emitter's
    ``_emit_step_call`` logic.
    """
    cls = _to_class_name(step.name)
    parts: list[str] = []
    for name, value in call.kwargs:
        gf = _to_go_field_name(name)
        rendered = _go_kwarg_value(value, contracts, state_field_to_step)
        parts.append(f"{gf}: {rendered}")
    return f"steps.{cls}In{{ {', '.join(parts)} }}"


def _render_chain_item(
    item: object,
    prev_var: str,
    indent: str,
    *,
    steps_by_name: dict[str, StepIR],
    state_field_to_step: dict[str, StepIR],
    contracts_by_name: dict[str, ContractIR],
    scope_local: set[str],
) -> tuple[list[str], str]:
    """Render one chain item.  Returns (rendered_lines, new_prev_var).

    `prev_var` is the Go identifier holding the previous step's typed output
    (or "kwargs" for the very first step).  For control-flow blocks (IF, MATCH,
    WHILE, FOR EACH) `prev_var` passes through unchanged because branches may
    diverge and do not share a single continuation variable.

    `state_field_to_step` maps each state-dict key (a step's GIVES field name)
    to the StepIR that produced it.  Used by `_go_condition_expr` to resolve
    the Go type assertion for `state[<key>].(steps.<Cls>Out)`.

    v0.20.0 supports: `CallIR`, `IfBlockIR`.  Other block kinds raise
    `NotImplementedError`; T13-T15 will add MATCH/WHILE/FOR EACH.
    """
    if isinstance(item, CallIR):
        step = steps_by_name.get(item.step_name)
        if step is None or step.mode not in ("exact", "judgment"):
            # Unknown or unsupported step — skip silently (mirrors prior behaviour)
            return [], prev_var
        cls = _to_class_name(step.name)
        input_init = _kwargs_to_step_input(
            item,
            step,
            contracts_by_name,
            state_field_to_step,
        )
        out_var = f"{step.name}Out"
        rendered: list[str] = [
            f"{indent}{out_var}, err := steps.{cls}(ctx, {input_init})",
            f"{indent}if err != nil {{",
            f"{indent}\treturn nil, err",
            f"{indent}}}",
        ]
        # Write the result into state under the GIVES field name (= the state-dict
        # key used by _go_condition_expr and _go_kwarg_value when reading back).
        # Skip the write entirely for steps with no GIVES (side-effect-only).
        if step.gives is not None:
            rendered.append(f'{indent}state["{step.gives.name}"] = {out_var}')
        rendered.append("")
        return rendered, out_var

    if isinstance(item, IfBlockIR):
        cond = _go_condition_expr(item.condition, scope_local, state_field_to_step)
        lines: list[str] = [f"{indent}if {cond} {{"]
        inner_indent = indent + "\t"
        # then branch
        cur = prev_var
        for sub in item.then_body:
            sub_lines, cur = _render_chain_item(
                sub, cur, inner_indent,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                scope_local=scope_local,
            )
            lines.extend(sub_lines)
        # else branch
        if item.else_body:
            lines.append(f"{indent}}} else {{")
            cur_else = prev_var
            for sub in item.else_body:
                sub_lines, cur_else = _render_chain_item(
                    sub, cur_else, inner_indent,
                    steps_by_name=steps_by_name,
                    state_field_to_step=state_field_to_step,
                    contracts_by_name=contracts_by_name,
                    scope_local=scope_local,
                )
                lines.extend(sub_lines)
        lines.append(f"{indent}}}")
        lines.append("")
        # prev_var after a branch block stays the pre-branch value; the two
        # branches may each update state under different keys.
        return lines, prev_var

    if isinstance(item, MatchBlockIR):
        # Render the scrutinee as a typed state-field access, mirroring the
        # pattern used by _go_condition_expr for IF conditions.
        state_field = item.state_field
        step = state_field_to_step.get(state_field)
        if step is not None:
            cls = _to_class_name(step.name)
            type_assert = f"(steps.{cls}Out)"
        else:
            type_assert = "(any)"
        if state_field in scope_local:
            base = f"{state_field}.{type_assert}"
        else:
            base = f'state["{state_field}"].{type_assert}'
        gf = _to_go_field_name(item.sub_field)
        subject_expr = f"{base}.{gf}"
        match_lines: list[str] = [f"{indent}switch {subject_expr} {{"]
        inner_indent = indent + "\t"
        for arm in item.cases:
            if arm.value is None:
                # DEFAULT arm — Go uses `default:`.
                match_lines.append(f"{inner_indent}default:")
            else:
                # Enum idents and string literals both render as double-quoted
                # Go string constants (same convention as _go_condition_expr's
                # "ident" / "str" literal rendering).
                escaped = arm.value.replace("\\", "\\\\").replace('"', '\\"')
                match_lines.append(f'{inner_indent}case "{escaped}":')
            cur = prev_var
            for sub in arm.body:
                sub_lines, cur = _render_chain_item(
                    sub, cur, inner_indent + "\t",
                    steps_by_name=steps_by_name,
                    state_field_to_step=state_field_to_step,
                    contracts_by_name=contracts_by_name,
                    scope_local=scope_local,
                )
                match_lines.extend(sub_lines)
        match_lines.append(f"{indent}}}")
        match_lines.append("")
        return match_lines, prev_var

    raise NotImplementedError(
        f"chain item kind not yet supported in v0.20.0: {type(item).__name__}"
    )


def render_flow_go(graph: FlowGraph) -> str:
    """Render flow/flow.go — top-level orchestrator."""
    pkg = _go_module_name(graph)  # NOT _safe_package_name — Go requires lowercase
    if graph.flow is None:
        # No entry flow — emit an empty orchestrator. T19 should reject earlier.
        return (
            "package flow\n\n"
            "// Auto-generated by CLIO.\n\n"
            "import \"context\"\n\n"
            "func Run(ctx context.Context, kwargs map[string]any) "
            "(map[string]any, error) {\n"
            "\treturn map[string]any{}, nil\n"
            "}\n"
        )

    contracts_by_name = {c.name: c for c in graph.contracts}
    steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}
    # Maps each state-dict key (= step's GIVES field name) to the step that
    # produced it.  Used by _go_condition_expr to derive type assertions.
    state_field_to_step: dict[str, StepIR] = {}
    for s in graph.steps:
        if isinstance(s, StepIR) and s.gives is not None:
            state_field_to_step[s.gives.name] = s

    lines: list[str] = [
        "package flow",
        "",
        "// Auto-generated by CLIO. Do not edit by hand.",
        "",
        "import (",
        '\t"context"',
        "",
        f'\t"{pkg}/steps"',
        ")",
        "",
        "func Run(ctx context.Context, kwargs map[string]any) (map[string]any, error) {",
        "\tstate := map[string]any{}",
        "",
    ]
    prev_var = "kwargs"
    for elem in graph.flow.chain:
        elem_lines, prev_var = _render_chain_item(
            elem,
            prev_var,
            "\t",
            steps_by_name=steps_by_name,
            state_field_to_step=state_field_to_step,
            contracts_by_name=contracts_by_name,
            scope_local=set(),
        )
        lines.extend(elem_lines)
    lines.append("\treturn state, nil")
    lines.append("}")
    return "\n".join(lines) + "\n"
