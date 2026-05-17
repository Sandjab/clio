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
    _type_to_go,
)
from clio.ir.graph import CallIR, ContractIR, FlowGraph, IfBlockIR, StepIR


def _kwargs_to_step_input(
    step: StepIR,
    prev_state_var: str,
    contracts: dict[str, ContractIR],
    *,
    is_first_step: bool,
) -> str:
    """Render the literal `<Step>In{...}` initialisation pulling fields from
    kwargs (first step) or the previous step's typed output (subsequent steps).

    v0.20.0 assumes 1:1 GIVES/TAKES field name alignment between adjacent
    steps. If a later spec adds explicit field remapping at chain time,
    revisit here.

    TODO(T12): kwargs["x"].(SomeStruct) panics at runtime for ContractRef
    inputs on the first step. Skip for v0.20.0 (no fixture exercises this
    path); add type-assertion helper when ContractRef first-step inputs land.
    """
    cls = _to_class_name(step.name)
    parts: list[str] = []
    for field in step.takes:
        gf = _to_go_field_name(field.name)
        if is_first_step:
            parts.append(
                f'{gf}: {prev_state_var}["{field.name}"].'
                f'({_type_to_go(field.type, contracts)})'
            )
        else:
            parts.append(f"{gf}: {prev_state_var}.{gf}")
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
    is_first_step: bool,
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
            step,
            prev_var,
            contracts_by_name,
            is_first_step=is_first_step,
        )
        out_var = f"{step.name}Out"
        rendered = [
            f"{indent}{out_var}, err := steps.{cls}(ctx, {input_init})",
            f"{indent}if err != nil {{",
            f"{indent}\treturn nil, err",
            f"{indent}}}",
            f'{indent}state["{step.name}"] = {out_var}',
            "",
        ]
        return rendered, out_var

    if isinstance(item, IfBlockIR):
        cond = _go_condition_expr(item.condition, scope_local, state_field_to_step)
        lines: list[str] = [f"{indent}if {cond} {{"]
        inner_indent = indent + "\t"
        # then branch
        cur = prev_var
        first_in_then = is_first_step
        for sub in item.then_body:
            sub_lines, cur = _render_chain_item(
                sub, cur, inner_indent,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                scope_local=scope_local,
                is_first_step=first_in_then,
            )
            lines.extend(sub_lines)
            if sub_lines:
                first_in_then = False
        # else branch
        if item.else_body:
            lines.append(f"{indent}}} else {{")
            cur_else = prev_var
            first_in_else = is_first_step
            for sub in item.else_body:
                sub_lines, cur_else = _render_chain_item(
                    sub, cur_else, inner_indent,
                    steps_by_name=steps_by_name,
                    state_field_to_step=state_field_to_step,
                    contracts_by_name=contracts_by_name,
                    scope_local=scope_local,
                    is_first_step=first_in_else,
                )
                lines.extend(sub_lines)
                if sub_lines:
                    first_in_else = False
        lines.append(f"{indent}}}")
        lines.append("")
        # prev_var after a branch block stays the pre-branch value; the two
        # branches may each update state under different keys.
        return lines, prev_var

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
    is_first = True
    for elem in graph.flow.chain:
        elem_lines, prev_var = _render_chain_item(
            elem,
            prev_var,
            "\t",
            steps_by_name=steps_by_name,
            state_field_to_step=state_field_to_step,
            contracts_by_name=contracts_by_name,
            scope_local=set(),
            is_first_step=is_first,
        )
        lines.extend(elem_lines)
        if elem_lines and is_first and isinstance(elem, CallIR):
            is_first = False
    lines.append("\treturn state, nil")
    lines.append("}")
    return "\n".join(lines) + "\n"
