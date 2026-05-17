"""Per-step Go-source renderers.

Each renderer produces the body of one `steps/NN_<name>.go` file for a
single STEP IR node. The orchestrator that strings them together lives
in _go_flow_renderer.py; the runtime helpers (validate, cache) live in
_go_runtime_templates.py.

Layout convention mirrors the python target's clio/emitters/_python_helpers.py
where per-shape rendering is grouped together rather than interleaved
with cross-cutting graph walkers.
"""
from __future__ import annotations

from clio.emitters._shared_utils import _to_class_name, _to_go_field_name, _type_to_go
from clio.ir.graph import ContractIR, StepIR


def _step_in_out_struct(
    step: StepIR, contracts: dict[str, ContractIR]
) -> tuple[str, str]:
    """Return (in_struct_body, out_struct_body) for the step's typed stubs.

    Each body is the multi-line content between `struct {` and `}` — the
    caller wraps them in `type <Step>In struct { ... }` declarations.

    In struct: one field per entry in step.takes.
    Out struct: one field for step.gives (singular), or empty when None.
    """
    in_lines: list[str] = []
    for field in step.takes:
        go_field = _to_go_field_name(field.name)
        go_type = _type_to_go(field.type, contracts)
        in_lines.append(f'\t{go_field} {go_type} `json:"{field.name}"`')
    in_body = "\n".join(in_lines)

    out_body = ""
    if step.gives is not None:
        go_field = _to_go_field_name(step.gives.name)
        go_type = _type_to_go(step.gives.type, contracts)
        out_body = f'\t{go_field} {go_type} `json:"{step.gives.name}"`'

    return in_body, out_body


def render_exact_step_go(
    step: StepIR, contracts: dict[str, ContractIR]
) -> str:
    """Render a single exact step as a Go source file in the `steps` package.

    Emits:
      - package steps
      - import ("context")
      - type <Step>In struct { ... }
      - type <Step>Out struct { ... }
      - func <Step>(ctx context.Context, in <Step>In) (<Step>Out, error)
          with panic("fill me in: <step_name>") body
    """
    step_name_go = _to_class_name(step.name)
    in_body, out_body = _step_in_out_struct(step, contracts)

    lines: list[str] = [
        "package steps",
        "",
        "import (",
        '\t"context"',
        ")",
        "",
        f"type {step_name_go}In struct {{",
    ]
    if in_body:
        lines.append(in_body)
    lines.append("}")
    lines.append("")
    lines.append(f"type {step_name_go}Out struct {{")
    if out_body:
        lines.append(out_body)
    lines.append("}")
    lines.append("")
    lines.append(f"// {step_name_go} implements the '{step.name}' step.")
    lines.append(
        f"func {step_name_go}(ctx context.Context, in {step_name_go}In)"
        f" ({step_name_go}Out, error) {{"
    )
    lines.append(f'\tpanic("fill me in: {step.name}")')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
