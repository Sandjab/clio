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

from clio.emitters._shared_utils import (
    _model_id,
    _to_class_name,
    _to_go_field_name,
    _type_to_go,
)
from clio.ir.graph import CacheConfigIR, ContractIR, FlowGraph, StepIR


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


# ---------------------------------------------------------------------------
# Judgment step renderer (Anthropic SDK path)


def _cache_ttl_seconds(cache: CacheConfigIR | None) -> int | None:
    """Resolve a CacheConfigIR into a TTL in seconds, or None for permanent.

    Returns:
      None  — CACHE: on  (permanent, nil ttl pointer in Go)
      0     — no cache or CACHE: off (skip cache blocks)
      int   — CACHE: ttl(Xh/Xm/Xs) converted to seconds
    """
    if cache is None or cache.mode == "off":
        return 0
    if cache.mode == "on":
        return None  # permanent — nil ttl pointer
    # mode == "ttl": ttl_seconds is already parsed by the IR builder
    if cache.ttl_seconds is not None:
        return cache.ttl_seconds
    return 0


def render_judgment_step_go(step: StepIR, graph: FlowGraph) -> str:
    """Render steps/NN_<name>.go for a judgment step using anthropic-sdk-go.

    Body shape:
      1. Build prompt from inputs (JSON-marshal In struct).
      2. Cache lookup if CACHE is configured (with ttl pointer when ttl(Xh)).
      3. anthropic.NewClient → Messages.New with system=JSON-only directive,
         user=prompt.
      4. Extract text from resp.Content[0].Text.
      5. json.Unmarshal into typed Out struct.
      6. .Validate(ctx) via interface assertion (works for both ContractRef
         outputs and plain structs — plain structs have no Validate method
         so the assertion is skipped at runtime).
      7. cache.Store if cache configured.
    """
    cls = _to_class_name(step.name)
    pkg = graph.flow.name.lower() if graph.flow is not None else "flow"
    contracts_by_name = {c.name: c for c in graph.contracts}
    in_body, out_body = _step_in_out_struct(step, contracts_by_name)

    # Resolve model: first declared in RESOURCES
    model_short = (
        graph.resources.models[0]
        if graph.resources is not None and graph.resources.models
        else "haiku"
    )
    model = _model_id(model_short)

    cache_ttl = _cache_ttl_seconds(step.cache)
    has_cache = cache_ttl != 0  # None (permanent) or positive int → has cache

    cache_block_pre = ""
    cache_block_post = ""
    if cache_ttl is None:
        # CACHE: on — permanent, nil ttl pointer
        cache_block_pre = (
            f'\tcacheDir := cache.CacheDirFromEnv()\n'
            f'\tkey := cache.Key("{step.name}", "{model}", prompt, "")\n'
            f'\tif v, ok := cache.Lookup(cacheDir, "{step.name}", key, nil); ok {{\n'
            f'\t\tvar cached {cls}Out\n'
            f'\t\tif err := json.Unmarshal([]byte(v), &cached); err == nil {{\n'
            f'\t\t\treturn cached, nil\n'
            f'\t\t}}\n'
            f'\t}}\n'
        )
        cache_block_post = (
            f'\tif rawBytes, err := json.Marshal(out); err == nil {{\n'
            f'\t\t_ = cache.Store(cacheDir, "{step.name}", key, "{model}", string(rawBytes))\n'
            f'\t}}\n'
        )
    elif cache_ttl > 0:
        # CACHE: ttl(Xh/Xm/Xs) — positive seconds
        cache_block_pre = (
            f'\tttl := int64({cache_ttl})\n'
            f'\tttlPtr := &ttl\n'
            f'\tcacheDir := cache.CacheDirFromEnv()\n'
            f'\tkey := cache.Key("{step.name}", "{model}", prompt, "")\n'
            f'\tif v, ok := cache.Lookup(cacheDir, "{step.name}", key, ttlPtr); ok {{\n'
            f'\t\tvar cached {cls}Out\n'
            f'\t\tif err := json.Unmarshal([]byte(v), &cached); err == nil {{\n'
            f'\t\t\treturn cached, nil\n'
            f'\t\t}}\n'
            f'\t}}\n'
        )
        cache_block_post = (
            f'\tif rawBytes, err := json.Marshal(out); err == nil {{\n'
            f'\t\t_ = cache.Store(cacheDir, "{step.name}", key, "{model}", string(rawBytes))\n'
            f'\t}}\n'
        )

    imports: list[str] = [
        '\t"context"',
        '\t"encoding/json"',
        '\t"fmt"',
        '\t"os"',
        "",
        '\t"github.com/anthropics/anthropic-sdk-go"',
        '\t"github.com/anthropics/anthropic-sdk-go/option"',
    ]
    if has_cache:
        imports.append(f'\t"{pkg}/clio_runtime/cache"')

    lines: list[str] = [
        "package steps",
        "",
        "// Auto-generated by CLIO. Do not edit by hand.",
        "",
        "import (",
        "\n".join(imports),
        ")",
        "",
        f"type {cls}In struct {{",
    ]
    if in_body:
        lines.append(in_body)
    lines.append("}")
    lines.append("")
    lines.append(f"type {cls}Out struct {{")
    if out_body:
        lines.append(out_body)
    lines.append("}")
    lines.append("")
    lines.append(f"// {cls} implements the '{step.name}' judgment step (Anthropic SDK).")
    lines.append(
        f"func {cls}(ctx context.Context, in {cls}In) ({cls}Out, error) {{"
    )
    lines.append("\t// 1. Build prompt from input.")
    lines.append("\tinJSON, err := json.Marshal(in)")
    lines.append("\tif err != nil {")
    lines.append(f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: marshal input: %w", err)')
    lines.append("\t}")
    lines.append(
        '\tprompt := fmt.Sprintf('
        '"Process this input and return JSON matching the output schema.\\n\\nInput:\\n%s",'
        " string(inJSON))"
    )
    lines.append("")
    if cache_block_pre:
        lines.append(cache_block_pre.rstrip("\n"))
        lines.append("")
    lines.append(
        "\tclient := anthropic.NewClient(option.WithAPIKey(os.Getenv(\"ANTHROPIC_API_KEY\")))"
    )
    lines.append("\tresp, err := client.Messages.New(ctx, anthropic.MessageNewParams{")
    lines.append(f'\t\tModel:     anthropic.F("{model}"),')
    lines.append("\t\tMaxTokens: anthropic.F(int64(8192)),")
    lines.append(
        '\t\tSystem:    anthropic.F('
        '"You are a precise function. Return only valid JSON matching the requested output schema. No prose."),'
    )
    lines.append(
        "\t\tMessages:  anthropic.F([]anthropic.MessageParam{"
        "anthropic.NewUserMessage(anthropic.NewTextBlock(prompt))}),"
    )
    lines.append("\t})")
    lines.append("\tif err != nil {")
    lines.append(f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: anthropic: %w", err)')
    lines.append("\t}")
    lines.append("\tif len(resp.Content) == 0 {")
    lines.append(f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: empty response")')
    lines.append("\t}")
    lines.append("\traw := resp.Content[0].Text")
    lines.append(f"\tvar out {cls}Out")
    lines.append("\tif err := json.Unmarshal([]byte(raw), &out); err != nil {")
    lines.append(f'\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: unmarshal: %w", err)')
    lines.append("\t}")
    lines.append("\t// Validate per contract.")
    lines.append(
        "\tif validatable, ok := any(&out)"
        '.(interface{ Validate(context.Context) error }); ok {'
    )
    lines.append("\t\tif err := validatable.Validate(ctx); err != nil {")
    lines.append(f'\t\t\treturn {cls}Out{{}}, fmt.Errorf("{step.name}: validate: %w", err)')
    lines.append("\t\t}")
    lines.append("\t}")
    if cache_block_post:
        lines.append(cache_block_post.rstrip("\n"))
    lines.append("\treturn out, nil")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
