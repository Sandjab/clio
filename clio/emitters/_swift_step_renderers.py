"""Per-step Swift-source renderers.

Each renderer produces the body of one `Sources/ClioFlow/Steps/NN_<name>.swift`
file for a single STEP IR node. The orchestrator that strings them together
lives in _swift_flow_renderer.py.
"""
from __future__ import annotations

from clio.emitters._swift_helpers import _type_to_swift
from clio.ir.graph import ContractIR, StepIR


def _step_struct_prefix(idx: int, step_name: str) -> str:
    """Return the struct name prefix, e.g. 'Step01_load'."""
    return f"Step{idx:02d}_{step_name}"


def _step_in_out_struct(
    step: StepIR, contracts: dict[str, ContractIR], idx: int
) -> tuple[str, str]:
    """Return (in_struct_src, out_struct_src) for the step's typed Codable stubs."""
    prefix = _step_struct_prefix(idx, step.name)

    in_lines: list[str] = [f"struct {prefix}_In: Codable, Sendable {{"]
    for field in step.takes:
        swift_type = _type_to_swift(field.type, contracts)
        in_lines.append(f"    var {field.name}: {swift_type}")
    in_lines.append("}")
    in_src = "\n".join(in_lines)

    out_lines: list[str] = [f"struct {prefix}_Out: Codable, Sendable {{"]
    if step.gives is not None:
        swift_type = _type_to_swift(step.gives.type, contracts)
        out_lines.append(f"    var {step.gives.name}: {swift_type}")
    out_lines.append("}")
    out_src = "\n".join(out_lines)

    return in_src, out_src


def render_exact_step_swift(
    step: StepIR, contracts: dict[str, ContractIR], idx: int
) -> str:
    """Render a single exact step as a Swift source file in ClioFlow/Steps/."""
    prefix = _step_struct_prefix(idx, step.name)
    in_src, out_src = _step_in_out_struct(step, contracts, idx)

    lines: list[str] = [
        "import Foundation",
        "",
        f"// {step.name} — exact step (fill in the body)",
        in_src,
        "",
        out_src,
        "",
        f"func step_{step.name}(_ input: {prefix}_In) async throws -> {prefix}_Out {{",
        f'    fatalError("fill me in: {step.name}")',
        "}",
        "",
    ]
    return "\n".join(lines)
