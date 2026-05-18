"""Renderer for the top-level flow orchestrator (`flow/flow.go`).

Emits `func Run(ctx, kwargs) (state, error)` that chains every item in
graph.flow.chain.  v0.20.0 scope: sequential chain + IF/ELSE (T12),
MATCH (T13), WHILE (T14), sequential FOR EACH (T15).
"""
from __future__ import annotations

from clio.emitters._go_helpers import _go_module_name
from clio.emitters._shared_utils import (
    _go_condition_expr,
    _to_class_name,
    _to_go_field_name,
)
from clio.ir.graph import (
    CallIR,
    ContractIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    RescueBlockIR,
    ResumeIR,
    StepIR,
    WhileBlockIR,
)


def _go_kwarg_value(
    value: object,
    contracts: dict[str, ContractIR],
    state_field_to_step: dict[str, StepIR],
    scope_local: set[str] | None = None,
) -> str:
    """Render one CallIR kwarg value as a Go expression.

    Two cases, mirroring the python emitter's logic in python.py:
    - Reference ``@<field>`` — the field name is the GIVES name of some prior
      step.  We emit a state-based type assertion:
          state["<field>"].(steps.<StepCls>Out).<GoField>
      When <field> is in `scope_local` (a FOR EACH loop variable), we render
      it as the bare identifier instead: just `<field>` (no state lookup).
      This is the same pattern used by `_go_condition_expr` for IF conditions,
      so the two readers are consistent with the writer.
    - Literal (str / int / float / bool) — rendered as a Go literal.
      Plain strings that do not start with ``@`` are string literals.
    """
    _scope = scope_local or set()
    if isinstance(value, str) and value.startswith("@"):
        ref = value[1:]  # the state-dict key (= the prior step's GIVES name)
        if ref in _scope:
            # Loop variable — use bare identifier (no state lookup needed).
            return ref
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
    scope_local: set[str] | None = None,
) -> str:
    """Render the ``steps.<Step>In{...}`` initialisation from CallIR.kwargs.

    Each kwarg pair binds a TAKES field name to either a literal value or a
    reference into a prior step's typed output (``@<field>`` syntax).

    `scope_local` is forwarded to `_go_kwarg_value` so that FOR EACH loop
    variables are rendered as bare identifiers rather than state lookups.

    Iterates ``call.kwargs`` directly — no assumptions about GIVES/TAKES field
    name alignment between adjacent steps.  Mirrors the python emitter's
    ``_emit_step_call`` logic.
    """
    cls = _to_class_name(step.name)
    parts: list[str] = []
    for name, value in call.kwargs:
        gf = _to_go_field_name(name)
        rendered = _go_kwarg_value(value, contracts, state_field_to_step, scope_local)
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
    rescues_by_step: dict[str, RescueBlockIR] | None = None,
) -> tuple[list[str], str]:
    """Render one chain item.  Returns (rendered_lines, new_prev_var).

    `prev_var` is the Go identifier holding the previous step's typed output
    (or "kwargs" for the very first step).  For control-flow blocks (IF, MATCH,
    WHILE, FOR EACH) `prev_var` passes through unchanged because branches may
    diverge and do not share a single continuation variable.

    `state_field_to_step` maps each state-dict key (a step's GIVES field name)
    to the StepIR that produced it.  Used by `_go_condition_expr` to resolve
    the Go type assertion for `state[<key>].(steps.<Cls>Out)`.

    v0.20.0 supports: `CallIR`, `IfBlockIR`, `MatchBlockIR`, `WhileBlockIR`,
    and sequential `ForEachIR` (T15).  Parallel `ForEachIR` raises
    `NotImplementedError` and is handled in T17.  RESCUE wrapping (T16) is
    applied at the `CallIR` level when `rescues_by_step` contains the step.
    """
    _rescues = rescues_by_step or {}
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
            scope_local,
        )
        out_var = f"{step.name}Out"

        # RESCUE: if this step has a rescue handler and we are at the top-level
        # scope (not inside FOR EACH / IF / etc.), wrap the call in an IIFE with
        # a deferred recover so the handler body runs on panic.
        if item.step_name in _rescues and not scope_local:
            rb = _rescues[item.step_name]
            body_indent = indent + "\t"
            on_fail_indent = indent + "\t\t\t"
            # Build the deferred recover block first (inserted before the protected call).
            defer_lines: list[str] = [
                f"{body_indent}defer func() {{",
                f"{body_indent}\tif r := recover(); r != nil {{",
            ]
            # Render rescue body steps.  Each non-terminal CallIR uses a
            # simplified error path (no `return nil, err` — deferred funcs
            # cannot propagate errors to the outer caller; on-fail errors are
            # discarded here, matching the python emitter's semantics).
            for sub in rb.body:
                if isinstance(sub, ResumeIR):
                    # RESUME(<fallback_step>.<field>) — update state with the
                    # fallback result so the outer flow can continue.
                    # The fallback step was already called above and its typed
                    # output is in <fallback_step>Out.
                    fallback_out_var = f"{sub.fallback_step}Out"
                    if step.gives is not None:
                        gf = _to_go_field_name(sub.field_name)
                        defer_lines.append(
                            f"{on_fail_indent}state[\"{step.gives.name}\"] = "
                            f"{fallback_out_var}.{gf}"
                        )
                elif isinstance(sub, CallIR):
                    sub_step = steps_by_name.get(sub.step_name)
                    if sub_step is None or sub_step.mode not in ("exact", "judgment"):
                        continue
                    sub_cls = _to_class_name(sub_step.name)
                    sub_input = _kwargs_to_step_input(
                        sub, sub_step, contracts_by_name, state_field_to_step, scope_local,
                    )
                    sub_out_var = f"{sub.step_name}Out"
                    defer_lines.append(
                        f"{on_fail_indent}{sub_out_var}, _ := steps.{sub_cls}(ctx, {sub_input})"
                    )
                    # Do NOT write to state here: if the rescue body ends with
                    # RESUME(<this_step>.<field>), the RESUME terminal writes
                    # the extracted field value into state.  For abort-terminated
                    # rescues there is no continuation, so state writes are moot.
                # Other node types inside rescue body are not supported in v0.20.0.
            defer_lines.extend([
                f"{body_indent}\t}}",
                f"{body_indent}}}()",
            ])
            # Build the IIFE: opener → defer block → protected call → closer.
            rescue_lines: list[str] = [f"{indent}func() {{"]
            rescue_lines.extend(defer_lines)
            # Protected step call — uses panic(err) instead of return nil, err
            # so the deferred recover catches it.
            rescue_lines.append(
                f"{body_indent}{out_var}, err := steps.{cls}(ctx, {input_init})"
            )
            rescue_lines.append(f"{body_indent}if err != nil {{")
            rescue_lines.append(f"{body_indent}\tpanic(err)")
            rescue_lines.append(f"{body_indent}}}")
            if step.gives is not None:
                rescue_lines.append(
                    f'{body_indent}state["{step.gives.name}"] = {out_var}'
                )
            rescue_lines.append(f"{indent}}}()")
            rescue_lines.append("")
            return rescue_lines, out_var

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
                rescues_by_step=_rescues,
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
                    rescues_by_step=_rescues,
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
                    rescues_by_step=_rescues,
                )
                match_lines.extend(sub_lines)
        match_lines.append(f"{indent}}}")
        match_lines.append("")
        return match_lines, prev_var

    if isinstance(item, WhileBlockIR):
        cond = _go_condition_expr(item.condition, scope_local, state_field_to_step)
        while_lines: list[str] = [f"{indent}for {cond} {{"]
        inner_indent = indent + "\t"
        cur_while = prev_var
        for sub in item.body:
            sub_lines, cur_while = _render_chain_item(
                sub, cur_while, inner_indent,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                scope_local=scope_local,
                rescues_by_step=_rescues,
            )
            while_lines.extend(sub_lines)
        while_lines.append(f"{indent}}}")
        while_lines.append("")
        return while_lines, prev_var

    if isinstance(item, ForEachIR) and not item.parallel:
        # Render the collection expression from the state dict.  The collection
        # is a slice stored under `item.collection` (the GIVES field name of the
        # step that produced it).  We need to type-assert to the producing step's
        # Out struct and then access the field by its Go name so that `range`
        # iterates over a typed slice rather than `any`.
        coll_name = item.collection
        coll_step = state_field_to_step.get(coll_name)
        if coll_step is not None:
            coll_cls = _to_class_name(coll_step.name)
            coll_gf = _to_go_field_name(coll_name)
            coll_expr = f'state["{coll_name}"].(steps.{coll_cls}Out).{coll_gf}'
        else:
            # Unknown collection source — fall back to untyped (should not
            # happen after IR validation).
            coll_expr = f'state["{coll_name}"].([]any)'
        var = item.loop_var
        for_lines: list[str] = [f"{indent}for _, {var} := range {coll_expr} {{"]
        inner_indent = indent + "\t"
        inner_scope = scope_local | {var}
        cur_fe = prev_var
        for sub in item.body:
            sub_lines, cur_fe = _render_chain_item(
                sub, cur_fe, inner_indent,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                scope_local=inner_scope,
                rescues_by_step=_rescues,
            )
            for_lines.extend(sub_lines)
        for_lines.append(f"{indent}}}")
        for_lines.append("")
        return for_lines, prev_var

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
    # Maps protected step name → RescueBlockIR for RESCUE handlers (T16).
    rescues_by_step: dict[str, RescueBlockIR] = {
        rb.step_name: rb for rb in graph.flow.rescues
    }

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
            rescues_by_step=rescues_by_step,
        )
        lines.extend(elem_lines)
    lines.append("\treturn state, nil")
    lines.append("}")
    return "\n".join(lines) + "\n"
