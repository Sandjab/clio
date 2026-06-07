"""Renderer for the top-level Flow orchestrator (Sources/ClioFlow/Flow.swift).

Phase 1: linear chain of exact CallIR items only.
"""
from __future__ import annotations

from clio.emitters._swift_helpers import _type_to_swift
from clio.emitters._swift_step_renderers import _step_struct_prefix
from clio.ir.graph import CallIR, ContractIR, FlowGraph, StepIR


def _build_state_field_to_step(graph: FlowGraph) -> dict[str, StepIR]:
    """Map each state-dict key (GIVES field name) to the StepIR that produced it."""
    result: dict[str, StepIR] = {}
    if graph.flow is None:
        return result
    steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}
    for item in graph.flow.chain:
        if isinstance(item, CallIR):
            step = steps_by_name.get(item.step_name)
            if step is not None and step.gives is not None:
                result[step.gives.name] = step
    return result


def _swift_kwarg_value(
    value: object,
    contracts: dict[str, ContractIR],
    state_field_to_step: dict[str, StepIR],
    take_types: dict[str, str],
) -> str:
    """Render one CallIR kwarg value as a Swift expression."""
    if isinstance(value, str) and value.startswith("@"):
        ref = value[1:]
        step = state_field_to_step.get(ref)
        if step is not None and step.gives is not None:
            swift_type = _type_to_swift(step.gives.type, contracts)
            return f'state["{ref}"] as! {swift_type}'
        if ref in take_types:
            return f'state["{ref}"] as! {take_types[ref]}'
        # Unknown ref — untyped fallback (should not occur after IR validation).
        return f'state["{ref}"]'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    # String literal
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_chain_item(
    item: object,
    call_idx: list[int],
    *,
    steps_by_name: dict[str, StepIR],
    state_field_to_step: dict[str, StepIR],
    contracts_by_name: dict[str, ContractIR],
    take_types: dict[str, str],
    step_to_idx: dict[str, int],
) -> list[str]:
    """Render one chain item. Phase 1 supports CallIR only."""
    if not isinstance(item, CallIR):
        raise ValueError(
            f"E_SWIFT: {type(item).__name__} not yet supported (phase 1)"
        )
    step = steps_by_name.get(item.step_name)
    if step is None:
        return []

    call_idx[0] += 1
    n = call_idx[0]
    idx = step_to_idx[step.name]
    prefix = _step_struct_prefix(idx, step.name)

    in_args: list[str] = []
    for name, val in item.kwargs:
        swift_val = _swift_kwarg_value(
            val, contracts_by_name, state_field_to_step, take_types
        )
        in_args.append(f"{name}: {swift_val}")

    lines: list[str] = [
        f"        let in{n} = {prefix}_In({', '.join(in_args)})",
        f"        let out{n} = try await step_{step.name}(in{n})",
    ]
    if step.gives is not None:
        lines.append(f'        state["{step.gives.name}"] = out{n}.{step.gives.name}')
    lines.append("")
    return lines


def render_flow_swift(graph: FlowGraph, step_to_idx: dict[str, int]) -> str:
    """Render Sources/ClioFlow/Flow.swift — top-level orchestrator."""
    assert graph.flow is not None
    flow = graph.flow
    contracts_by_name = {c.name: c for c in graph.contracts}
    steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}
    state_field_to_step = _build_state_field_to_step(graph)
    take_types: dict[str, str] = {
        f.name: _type_to_swift(f.type, contracts_by_name) for f in flow.takes
    }

    lines: list[str] = [
        "import Foundation",
        "",
        "public enum Flow {",
        "    @MainActor",
        "    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {",
        "        var state = kwargs",
        "",
    ]

    call_idx = [0]
    for item in flow.chain:
        lines.extend(
            _render_chain_item(
                item,
                call_idx,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                take_types=take_types,
                step_to_idx=step_to_idx,
            )
        )

    lines.append("        return state")
    lines.append("    }")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)
