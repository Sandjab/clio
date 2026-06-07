"""Per-step Swift-source renderers.

Each renderer produces the body of one `Sources/ClioFlow/Steps/NN_<name>.swift`
file for a single STEP IR node. The orchestrator that strings them together
lives in _swift_flow_renderer.py.
"""
from __future__ import annotations

from clio.emitters._shared_utils import _model_id
from clio.emitters._swift_helpers import _type_to_swift
from clio.ir.graph import ApiInvokeIR, ContractIR, FlowGraph, StepIR
from clio.parser.ast_nodes import ContractRef


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


# ---------------------------------------------------------------------------
# Judgment step renderer (Anthropic URLSession path)
# ---------------------------------------------------------------------------

_SWIFT_SYSTEM_PROMPT = (
    "You are a strict JSON-only API. Output exactly one JSON document matching "
    "the requested schema, with no prose, no markdown code fences, no commentary, "
    "and no leading or trailing whitespace beyond the JSON itself."
)


def render_judgment_step_swift(
    step: StepIR,
    contracts: dict[str, ContractIR],
    graph: FlowGraph,
    idx: int,
) -> str:
    """Render a single judgment step as a Swift source file in ClioFlow/Steps/.

    Body shape (Phase 2 — no cache, no ON_FAIL chain):
      1. JSON-encode the In struct to build the user-turn prompt.
      2. Call Anthropic.complete(model:system:prompt:maxTokens:) — the
         zero-dep URLSession client in Sources/ClioFlow/Runtime/Anthropic.swift.
      3. Decode the returned text into the typed Out struct via JSONDecoder.
      4. If GIVES is a direct ContractRef, call .validate() on the field.
      5. Return Out.

    Model resolution (mirrors render_judgment_step_go):
      step.invoke.model (ApiInvokeIR) > graph.resources.models[0] > "haiku"

    The system prompt text is byte-identical to the Python/Go targets so
    model behaviour is consistent across compilation targets.
    """
    prefix = _step_struct_prefix(idx, step.name)
    in_src, out_src = _step_in_out_struct(step, contracts, idx)

    # --- model resolution -------------------------------------------------
    if isinstance(step.invoke, ApiInvokeIR) and step.invoke.model:
        model_short = step.invoke.model
    elif graph.resources is not None and graph.resources.models:
        model_short = graph.resources.models[0]
    else:
        model_short = "haiku"
    model = _model_id(model_short)

    # --- contract validation path -----------------------------------------
    # Validate only when GIVES is a *direct* ContractRef — the ContractIR
    # struct emitted in Contracts.swift has a .validate() method. List<C>,
    # Optional<C>, etc. are skipped in Phase 2 (cache/ON_FAIL scope).
    needs_validate = step.gives is not None and isinstance(
        step.gives.type, ContractRef
    )

    lines: list[str] = [
        "import Foundation",
        "",
        f"// {step.name} — judgment step (Anthropic API)",
        in_src,
        "",
        out_src,
        "",
        f"func step_{step.name}(_ input: {prefix}_In) async throws -> {prefix}_Out {{",
        "    // 1. Build prompt from input.",
        "    let encoder = JSONEncoder()",
        "    let inData = try encoder.encode(input)",
        '    let inJSON = String(data: inData, encoding: .utf8) ?? "{}"',
        (
            "    let prompt = "
            '"Process this input and return JSON matching the output schema.'
            "\\n\\nInput:\\n\\(inJSON)\""
        ),
        "    // 2. JSON-only system prompt (matches python/go targets).",
        f'    let system = "{_SWIFT_SYSTEM_PROMPT}"',
        "    // 3. Call Anthropic.",
        "    let raw = try await Anthropic.complete(",
        f'        model: "{model}",',
        "        system: system,",
        "        prompt: prompt,",
        "        maxTokens: 8192",
        "    )",
        "    // 4. Decode response into typed Out struct.",
        "    guard let rawData = raw.data(using: .utf8) else {",
        f'        throw AnthropicError(message: "{step.name}: response is not valid UTF-8")',
        "    }",
        f"    let out = try JSONDecoder().decode({prefix}_Out.self, from: rawData)",
    ]

    if needs_validate:
        assert step.gives is not None
        lines += [
            "    // 5. Validate contract.",
            f"    try out.{step.gives.name}.validate()",
        ]

    lines += [
        "    return out",
        "}",
        "",
    ]

    return "\n".join(lines)
