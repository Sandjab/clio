"""Per-step Swift-source renderers.

Each renderer produces the body of one `Sources/ClioFlow/Steps/NN_<name>.swift`
file for a single STEP IR node. The orchestrator that strings them together
lives in _swift_flow_renderer.py.
"""
from __future__ import annotations

from clio.emitters._shared_utils import _model_id
from clio.emitters._swift_helpers import _cache_ttl_seconds, _type_to_swift
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

    Body shape (Phase 2 — with optional CACHE, no ON_FAIL chain):
      1. JSON-encode the In struct to build the user-turn prompt.
      2. Cache lookup (if CACHE: on or CACHE: ttl(…)) — return cached Out on hit.
      3. Call Anthropic.complete(model:system:prompt:maxTokens:) — the
         zero-dep URLSession client in Sources/ClioFlow/Runtime/Anthropic.swift.
      4. Decode the returned text into the typed Out struct via JSONDecoder.
      5. If GIVES is a direct ContractRef, call .validate() on the field.
      6. Cache store (if CACHE configured) — atomic write to disk.
      7. Return Out.

    Model resolution (mirrors render_judgment_step_go):
      step.invoke.model (ApiInvokeIR) > graph.resources.models[0] > "haiku"

    Cache key derivation is byte-identical to clio/runtime/cache.py and
    clio_runtime/cache/cache.go: SHA256(step + "\\n" + model + "\\n" + prompt + "\\n" + "").
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

    # --- cache configuration ----------------------------------------------
    cache_ttl = _cache_ttl_seconds(step.cache)
    has_cache = cache_ttl != 0  # None (permanent) or positive int → has cache

    # --- contract validation path -----------------------------------------
    needs_validate = step.gives is not None and isinstance(
        step.gives.type, ContractRef
    )

    # --- cache blocks -------------------------------------------------------
    # ttlSeconds argument in Swift: nil for permanent, Int literal for TTL.
    if cache_ttl is None:
        ttl_arg = "nil"
    elif has_cache:
        ttl_arg = str(cache_ttl)
    else:
        ttl_arg = ""  # unused when has_cache is False

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
    ]

    if has_cache:
        lines += [
            "    // 2. Cache lookup.",
            "    let cacheDir = Cache.cacheDirFromEnv()",
            (
                f'    let cacheKey = Cache.key(step: "{step.name}", model: "{model}",'
                f" prompt: prompt, schema: \"\")"
            ),
            (
                "    if let hit = Cache.lookup("
                f'cacheDir: cacheDir, stepName: "{step.name}", '
                f"key: cacheKey, ttlSeconds: {ttl_arg}),"
            ),
            "       let hitData = hit.data(using: .utf8),",
            f"       let cached = try? JSONDecoder().decode({prefix}_Out.self, from: hitData) {{",
            "        return cached",
            "    }",
        ]

    lines += [
        "    // 3. JSON-only system prompt (matches python/go targets).",
        f'    let system = "{_SWIFT_SYSTEM_PROMPT}"',
        "    // 4. Call Anthropic.",
        "    let raw = try await Anthropic.complete(",
        f'        model: "{model}",',
        "        system: system,",
        "        prompt: prompt,",
        "        maxTokens: 8192",
        "    )",
        "    // 5. Decode response into typed Out struct.",
        "    guard let rawData = raw.data(using: .utf8) else {",
        f'        throw AnthropicError(message: "{step.name}: response is not valid UTF-8")',
        "    }",
        f"    let out = try JSONDecoder().decode({prefix}_Out.self, from: rawData)",
    ]

    if needs_validate:
        assert step.gives is not None
        lines += [
            "    // 6. Validate contract.",
            f"    try out.{step.gives.name}.validate()",
        ]

    if has_cache:
        lines += [
            "    // 7. Store in cache.",
            "    if let storeData = try? JSONEncoder().encode(out),",
            "       let storeStr = String(data: storeData, encoding: .utf8) {",
            (
                f'        Cache.store(cacheDir: cacheDir, stepName: "{step.name}",'
                f' key: cacheKey, model: "{model}", response: storeStr)'
            ),
            "    }",
        ]

    lines += [
        "    return out",
        "}",
        "",
    ]

    return "\n".join(lines)
