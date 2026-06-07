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


def _swift_str_literal_escape(s: str) -> str:
    """Escape a string for embedding inside a Swift double-quoted literal.

    Backslash first, then double-quote — an author writing
    `abort("can't parse \\"x\\"")` would otherwise emit Swift that does not
    compile. Order matters so the inserted backslashes are not re-escaped.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _on_fail_chain_parts(
    step: StepIR,
) -> tuple[int, str | None, str | None]:
    """Extract (retry_count, fallback_step_name, abort_msg) from the ON_FAIL chain.

    Returns (0, None, None) when there is no chain.
    escalate is a no-op for the Swift target (single model per emission).
    """
    if step.on_fail is None:
        return 0, None, None

    retry_count = 0
    fallback_step_name: str | None = None
    abort_msg: str | None = None

    for s in step.on_fail.strategies:
        if s.kind == "retry" and s.max_retries is not None:
            retry_count = s.max_retries
        elif s.kind == "fallback":
            # Prefer the resolved StepIR name; fall back to the name string.
            if s.fallback_step is not None:
                fallback_step_name = s.fallback_step.name
            else:
                fallback_step_name = s.fallback_step_name
        elif s.kind == "abort":
            abort_msg = s.abort_message or ""
        # escalate: no-op for the Swift target

    return retry_count, fallback_step_name, abort_msg


def render_judgment_step_swift(
    step: StepIR,
    contracts: dict[str, ContractIR],
    graph: FlowGraph,
    idx: int,
) -> str:
    """Render a single judgment step as a Swift source file in ClioFlow/Steps/.

    Body shape (Phase 2 — with optional CACHE and ON_FAIL chain):
      1. JSON-encode the In struct to build the user-turn prompt.
      2. Cache lookup (if CACHE: on or CACHE: ttl(…)) — return cached Out on hit.
      3. Call Anthropic.complete(model:system:prompt:maxTokens:) — the
         zero-dep URLSession client in Sources/ClioFlow/Runtime/Anthropic.swift.
      4. Decode the returned text into the typed Out struct via JSONDecoder.
      5. If GIVES is a direct ContractRef, call .validate() on the field.
      6. Cache store (if CACHE configured) — atomic write to disk.
      7. Return Out.

    With ON_FAIL chain (retry(N) [then escalate] [then fallback(step)] [then abort(msg)]):
      - retry(N): wraps the Anthropic call in a `for attempt in 0..<N` loop with
        exponential backoff via Task.sleep before retries after the first.
        On any failure (API error / UTF-8 decode / JSON parse / validation),
        sets lastError and continues.  On success, stores cache and returns.
      - escalate: no-op (single model per emission).
      - fallback(step): after retry exhaustion, calls the named step with the
        same inputs and propagates its output.
      - abort(msg): throws AnthropicError carrying the configured message.

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

    # --- ON_FAIL chain analysis -------------------------------------------
    retry_count, fallback_step_name, abort_msg = _on_fail_chain_parts(step)
    has_on_fail = retry_count > 0
    # When the chain is retry-only (no fallback/abort), the post-loop path is
    # `throw lastError`, so lastError must be tracked + written. With a
    # fallback/abort post-loop, lastError is never read — tracking it would
    # trigger a Swift "written to, but never read" warning, so we drop it
    # and use bare `continue` on failure.
    retry_only = fallback_step_name is None and abort_msg is None

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
    ]

    if has_on_fail:
        # ----------------------------------------------------------------
        # ON_FAIL path: wrap API call in a retry loop with exponential
        # backoff.  On success (decode + optional validate), store cache
        # and return.  After exhaustion: fallback step or abort.
        # ----------------------------------------------------------------
        if retry_only:
            lines.append(
                f'    var lastError: Error = AnthropicError(message: "{step.name}: all retries exhausted")'
            )
        lines += [
            f"    for attempt in 0..<{retry_count} {{",
            "        if attempt > 0 {",
            "            try await Task.sleep(nanoseconds: UInt64(1 << (attempt - 1)) * 1_000_000_000)",
            "        }",
            "        do {",
            "            // 4. Call Anthropic.",
            "            let raw = try await Anthropic.complete(",
            f'                model: "{model}",',
            "                system: system,",
            "                prompt: prompt,",
            "                maxTokens: 8192",
            "            )",
            "            guard let rawData = raw.data(using: .utf8) else {",
        ]
        if retry_only:
            lines.append(
                f'                lastError = AnthropicError(message: "{step.name}: response is not valid UTF-8")'
            )
        lines += [
            "                continue",
            "            }",
            "            // 5. Decode response into typed Out struct.",
            f"            let out = try JSONDecoder().decode({prefix}_Out.self, from: rawData)",
        ]

        if needs_validate:
            assert step.gives is not None
            lines += [
                "            // 6. Validate contract.",
                f"            try out.{step.gives.name}.validate()",
            ]

        if has_cache:
            lines += [
                "            // 7. Store in cache.",
                "            if let storeData = try? JSONEncoder().encode(out),",
                "               let storeStr = String(data: storeData, encoding: .utf8) {",
                (
                    f'                Cache.store(cacheDir: cacheDir, stepName: "{step.name}",'
                    f' key: cacheKey, model: "{model}", response: storeStr)'
                ),
                "            }",
            ]

        lines.append("            return out")
        if retry_only:
            lines += [
                "        } catch {",
                "            lastError = error",
                "            continue",
                "        }",
            ]
        else:
            # No post-loop read of the error → bare catch avoids a Swift
            # "written to, but never read" warning on the fallback/abort path.
            lines += [
                "        } catch {",
                "            continue",
                "        }",
            ]
        lines += [
            "    }",
            "",
        ]

        # Post-loop: fallback then abort, or just abort, or rethrow lastError.
        if fallback_step_name is not None:
            # Lazy import to avoid circular dependency: swift.py imports this module.
            from clio.emitters.swift import _collect_reachable_steps
            reachable = _collect_reachable_steps(graph)
            step_to_idx = {s.name: i + 1 for i, s in enumerate(reachable)}
            fb_idx = step_to_idx.get(fallback_step_name, 0)
            fb_prefix = _step_struct_prefix(fb_idx, fallback_step_name)
            in_args = ", ".join(f"{f.name}: input.{f.name}" for f in step.takes)
            err_msg = abort_msg if abort_msg is not None else f"{step.name}: fallback failed"
            err_msg = _swift_str_literal_escape(err_msg)
            lines += [
                f"    // ON_FAIL fallback: {fallback_step_name}",
                f"    let fbIn = {fb_prefix}_In({in_args})",
                "    do {",
            ]
            if step.gives is not None:
                lines.append(f"        let fbOut = try await step_{fallback_step_name}(fbIn)")
                lines.append(
                    f"        return {prefix}_Out({step.gives.name}: fbOut.{step.gives.name})"
                )
            else:
                # fbOut would be unused → bind with _ to avoid a Swift warning.
                lines.append(f"        _ = try await step_{fallback_step_name}(fbIn)")
                lines.append(f"        return {prefix}_Out()")
            lines += [
                "    } catch {",
                f'        throw AnthropicError(message: "{err_msg}")',
                "    }",
            ]
        elif abort_msg is not None:
            abort_lit = _swift_str_literal_escape(abort_msg)
            lines.append(f'    throw AnthropicError(message: "{abort_lit}")')
        else:
            # retry only, no fallback/abort: rethrow so all paths return/throw
            lines.append("    throw lastError")

    else:
        # ----------------------------------------------------------------
        # No ON_FAIL chain — original simple path.
        # ----------------------------------------------------------------
        lines += [
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
        ]

    lines += [
        "}",
        "",
    ]

    return "\n".join(lines)
