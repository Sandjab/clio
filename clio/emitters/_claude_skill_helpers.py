"""Pure rendering helpers for the claude-skill emitter.

Functions in this module take IR nodes and produce strings or dicts.
No filesystem I/O. No imports from other emitter modules.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    BoolOpIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    OnFailChainIR,
    ResourcesIR,
    StepIR,
    WhileBlockIR,
)
from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    EnumType,
    ListType,
    PrimitiveType,
    RecordType,
    TypeExpr,
)


def _type_to_display(t: TypeExpr) -> str:
    """Human-readable representation of a TypeExpr for prompt templates."""
    if isinstance(t, PrimitiveType):
        return t.name
    if isinstance(t, ConstrainedType):
        constraints = ", ".join(f"{k}={v}" for k, v in t.constraints)
        return f"{_type_to_display(t.base)}({constraints})"
    if isinstance(t, ListType):
        return f"List<{_type_to_display(t.inner)}>"
    if isinstance(t, RecordType):
        fields = ", ".join(f"{n}: {_type_to_display(ty)}" for n, ty in t.fields)
        return "{" + fields + "}"
    if isinstance(t, EnumType):
        return "enum(" + "|".join(t.values) + ")"
    if isinstance(t, ContractRef):
        return t.name
    return str(t)


def _flow_name(graph: FlowGraph) -> str:
    """Derive the canonical flow name from the IR.

    FlowGraph exposes the name via ``graph.flow.name`` when a FLOW block is
    present.  For files that declare only STEPs (no FLOW), fall back to the
    first step's name.
    """
    if graph.flow is not None:
        return graph.flow.name
    if graph.steps:
        return graph.steps[0].name
    return "unnamed"


def _allowed_tools(graph: FlowGraph) -> list[str]:
    """Static set for v1: every emitted skill uses the same tool surface.

    Read for state.json, Write for state mutations, Bash for exact scripts
    and validation, TodoWrite for the orchestration checklist.
    """
    return ["Bash", "Read", "Write", "TodoWrite"]


def render_frontmatter(
    graph: FlowGraph,
    *,
    warn: Callable[[str], None] | None = None,
) -> str:
    """Render the YAML frontmatter block for SKILL.md (between '---' fences).

    If the flow has no description, emit a warning via ``warn`` (a callable
    that takes a single string — typically
    ``lambda m: print(m, file=sys.stderr)``).

    Returns a string starting with '---\\n' and ending with '---\\n'.
    """
    raw_name = _flow_name(graph)
    name = raw_name.replace("_", "-")

    # TODO(post-v0.14): wire FLOW.description.
    # The parser (clio/parser/parser.py::parse_flow) currently does not capture
    # a description from the .clio source. To enable this:
    #   1. Add `description: str = ""` to FlowDecl in clio/parser/ast_nodes.py.
    #   2. Add `description: str = ""` to FlowIR in clio/ir/graph.py.
    #   3. Thread the value through clio/ir/builder.py::build_ir.
    # Once any of those is non-empty, the lookup below will pick it up.
    description = (getattr(getattr(graph, "flow", None), "description", "") or "").strip()
    if not description:
        description = f"Execute flow {raw_name}"
        if warn is not None:
            warn(
                f"claude-skill warning: FLOW {raw_name} has no description; "
                f"frontmatter description defaulted to '{description}'. "
                f"Auto-trigger of the emitted skill will be weak."
            )

    tools = _allowed_tools(graph)
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"allowed-tools: {', '.join(tools)}\n"
        f"---\n"
    )


def detect_skill_language(graph: FlowGraph) -> str:
    """Heuristic: FR if any common French diacritic appears in flow description
    or step docstrings; otherwise EN."""
    samples = []
    flow = getattr(graph, "flow", None)
    if flow is not None:
        samples.append(getattr(flow, "description", "") or "")
    for step in graph.steps:
        samples.append(getattr(step, "description", "") or "")
    text = " ".join(samples)
    fr_markers = set("éèàçôîêûïü")
    return "fr" if any(c in fr_markers for c in text) else "en"


def render_exact_script(step: StepIR, contracts_by_name: dict, idx: int) -> str:
    """Build a standalone Python script for an exact STEP.

    Reads state JSON from stdin, calls the step's body function, merges the
    output back into state under state[step.name], writes updated state to
    stdout.  If the step has no impl (no CODE block), the script is a trivial
    pass-through that echoes state unchanged.
    """
    takes_doc = (
        "\n    ".join(f"{t.name}: {t.type}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {step.gives.type}"
        if step.gives is not None else "(no GIVES)"
    )

    # Build the parameter unpacking lines for the step function call.
    if step.takes:
        param_lines = "\n".join(
            f"    {t.name} = state.get({step.name!r}, {{}}).get({t.name!r})"
            for t in step.takes
        )
        call_kwargs = ", ".join(f"{t.name}={t.name}" for t in step.takes)
        call_expr = f"result = {step.name}({call_kwargs})"
    else:
        param_lines = "    # no TAKES"
        call_expr = f"result = {step.name}()"

    # Merge result back into state.
    if step.gives is not None:
        merge_line = f"    state.setdefault({step.name!r}, {{}})[{step.gives.name!r}] = result"
    else:
        merge_line = f"    # no GIVES — state unchanged by {step.name}"

    return (
        f'"""Standalone script for STEP {step.name} (exact)\n'
        f"\n"
        f"TAKES:\n"
        f"    {takes_doc}\n"
        f"GIVES:\n"
        f"    {gives_doc}\n"
        f"\n"
        f"Usage:\n"
        f"    python scripts/{idx:02d}_{step.name}.py < state.json > state.next.json\n"
        f'"""\n'
        f"from __future__ import annotations\n"
        f"\n"
        f"import json\n"
        f"import sys\n"
        f"\n"
        f"\n"
        f"def {step.name}({', '.join(t.name for t in step.takes)}):\n"
        f'    """Implement the body of STEP {step.name} here.\n'
        f"\n"
        f"    TAKES:\n"
        f"        {takes_doc}\n"
        f"    GIVES:\n"
        f"        {gives_doc}\n"
        f'    """\n'
        f"    raise NotImplementedError(\n"
        f'        "Implement {step.name}: this is an exact (deterministic) step."\n'
        f"    )\n"
        f"\n"
        f"\n"
        f'if __name__ == "__main__":\n'
        f"    state = json.load(sys.stdin)\n"
        f"{param_lines}\n"
        f"    {call_expr}\n"
        f"{merge_line}\n"
        f"    json.dump(state, sys.stdout, indent=2)\n"
        f"    sys.stdout.write('\\n')\n"
    )


def render_exact_step_section(step: StepIR, idx: int, lang: str = "en") -> str:
    """Markdown section for an exact STEP.

    ``lang``: "en" → "Step NN", "fr" → "Étape NN".
    """
    label = {"en": "Step", "fr": "Étape"}[lang]
    title = f"## {label} {idx:02d} — {step.name} (MODE: exact)\n"
    doc = (getattr(step, "description", "") or "").strip()
    doc_block = f"\n{doc}\n" if doc else ""
    cmd = (
        f"\nRun:\n\n"
        f"    python scripts/{idx:02d}_{step.name}.py < state.json > state.next.json "
        f"&& mv state.next.json state.json\n\n"
    )
    tail = (
        "Tick the corresponding TodoWrite todo. "
        "Do not advance until the script exited 0.\n\n"
    )
    return title + doc_block + cmd + tail + render_cache_block(step, lang=lang) + render_retry_block(step, lang=lang)


def render_judgment_prompt(step: StepIR) -> str:
    """Markdown prompt template for a judgment STEP.

    Judgment steps in CLIO source have no free-text prompt body — their
    semantics are expressed by TAKES/GIVES. We emit a structured template
    that instructs the LLM: substitute state values for placeholders, then
    produce output matching the declared GIVES contract.
    """
    takes_lines = "\n".join(
        f"- `{{{{state.{t.name}}}}}` ({_type_to_display(t.type)})" for t in step.takes
    ) if step.takes else "*(no TAKES)*"
    gives_line = (
        f"`{step.gives.name}` ({_type_to_display(step.gives.type)})"
        if step.gives is not None else "*(no GIVES)*"
    )
    return (
        f"# Prompt template — {step.name}\n\n"
        "Substitute `{{state.x}}` placeholders from `state.json` before sending.\n\n"
        "---\n\n"
        f"## Task\n\n"
        f"You are executing STEP `{step.name}` (MODE: judgment).\n\n"
        f"### Inputs (from state)\n\n"
        f"{takes_lines}\n\n"
        f"### Required output\n\n"
        f"Produce a JSON object with one key: {gives_line}.\n\n"
        "Respond with **only** the JSON object — no prose, no markdown fences.\n"
    )


def render_input_schema(step: StepIR, contracts_by_name: dict | None = None) -> str | None:
    """JSON Schema for the step's input contract (TAKES).

    Returns None if the step has no TAKES fields (caller skips file emission).
    When present, emits an object schema with one property per TAKES field,
    all required. Same inlining strategy as render_output_schema — no external
    $refs survive into the output.
    """
    if not step.takes:
        return None
    cb = contracts_by_name or {}
    properties = {}
    for field in step.takes:
        raw = type_to_json_schema(field.type)
        properties[field.name] = _inline_contract_refs(raw, cb)
    schema = {
        "type": "object",
        "properties": properties,
        "required": [f.name for f in step.takes],
    }
    return json.dumps(schema, indent=2) + "\n"


def render_output_schema(step: StepIR, contracts_by_name: dict | None = None) -> str:
    """Build a self-contained JSON Schema for the step's output contract.

    Calls type_to_json_schema (which may emit $ref pointers to ../contracts/*)
    then inlines all $ref-ed contract schemas so the result has zero external
    file dependencies. The claude-skill output layout has no `contracts/`
    sibling — the LLM host validates against schemas/NN_*.output.json directly
    via scripts/_validate.py, which doesn't resolve relative $ref.
    """
    if step.gives is None:
        # No declared output — open schema.
        return json.dumps({"type": "object"}, indent=2) + "\n"
    raw = type_to_json_schema(step.gives.type)
    inlined = _inline_contract_refs(raw, contracts_by_name or {})
    schema = {
        "type": "object",
        "properties": {step.gives.name: inlined},
        "required": [step.gives.name],
    }
    return json.dumps(schema, indent=2) + "\n"


def _contract_schema(contract) -> dict:
    """Return a JSON Schema dict for a ContractIR.

    ContractIR stores the pre-built schema as the `json_schema` attribute (a
    plain dict, set by clio.ir.builder.build_ir). Use it directly — no
    re-derivation needed.
    """
    return dict(contract.json_schema)


def _inline_contract_refs(
    node: object,
    contracts_by_name: dict,
    seen: frozenset = frozenset(),
) -> object:
    """Recursively replace {"$ref": "../contracts/<name>.schema.json"} with the
    inlined contract schema.

    `seen` tracks contract names already inlined in the current ancestor chain
    to detect cycles (which would cause infinite recursion). Cycles are replaced
    with {"type": "object"} as a safety net (CLIO IR validation should reject
    cyclic contracts before we reach this point).
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if (
            isinstance(ref, str)
            and ref.startswith("../contracts/")
            and ref.endswith(".schema.json")
        ):
            name = ref[len("../contracts/"):-len(".schema.json")]
            if name in seen:
                return {"type": "object", "description": f"(cycle: {name})"}
            contract = contracts_by_name.get(name)
            if contract is None:
                return {"type": "object", "description": f"(unknown contract: {name})"}
            inner = _contract_schema(contract)
            return _inline_contract_refs(inner, contracts_by_name, seen | {name})
        return {k: _inline_contract_refs(v, contracts_by_name, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_contract_refs(item, contracts_by_name, seen) for item in node]
    return node


def render_judgment_step_section(step: StepIR, idx: int, lang: str = "en") -> str:
    """Markdown section for a judgment STEP in SKILL.md."""
    label = {"en": "Step", "fr": "Étape"}[lang]
    title = f"## {label} {idx:02d} — {step.name} (MODE: judgment)\n"
    doc = (getattr(step, "description", "") or "").strip()
    doc_block = f"\n{doc}\n" if doc else ""
    body = (
        f"\n**Reads from state**: see prompt template `prompts/{idx:02d}_{step.name}.md`\n"
        f"**Writes to state**: `state.{step.name}` validated by "
        f"`schemas/{idx:02d}_{step.name}.output.json`\n\n"
        f"Steps:\n"
        f"1. Read `prompts/{idx:02d}_{step.name}.md`, substitute `{{{{state.x}}}}` "
        f"placeholders from `state.json`.\n"
        f"2. Generate an output as the assistant, save verbatim to `out.json`.\n"
        f"3. Validate using the bundled helper:\n\n"
        f"        python scripts/_validate.py out.json schemas/{idx:02d}_{step.name}.output.json\n\n"
        f"4. If exit 0 (valid): merge into `state.json` under `state.{step.name}`.\n"
        f"5. If exit ≠ 0 (invalid): see RESCUE/RETRY section below if present, "
        f"otherwise stop.\n\n"
        "Tick the corresponding TodoWrite todo.\n\n"
    )
    return title + doc_block + body + render_cache_block(step, lang=lang) + render_retry_block(step, lang=lang)


def _render_condition(cond) -> str:
    """Render a ConditionIR leaf or BoolOpIR tree to a human-readable string.

    Mirrors the ``_format_condition_label`` helper in ``graph_render.py``.
    """
    if isinstance(cond, BoolOpIR):
        left = _render_condition(cond.left)
        right = _render_condition(cond.right)
        return f"({left}) {cond.op} ({right})"
    # ConditionIR leaf
    lit_repr = repr(cond.literal_value)
    return f"{cond.step_name}.{cond.field} {cond.op} {lit_repr}"


def render_if_section(if_node: IfBlockIR, idx_label: str, lang: str = "en") -> str:
    """Render an IF/ELSE block as a SKILL.md sub-section.

    Sub-steps inside then_body / else_body have already been emitted (via the
    flat graph.steps walk). This section narrates the control flow only.
    """
    title = {"en": "IF", "fr": "Si"}[lang]
    cond = _render_condition(if_node.condition)
    a_label = {"en": "True branch", "fr": "Branche Vrai"}[lang]
    b_label = {"en": "False branch", "fr": "Branche Faux"}[lang]
    n_then = len(if_node.then_body)
    n_else = len(if_node.else_body)
    head = (
        f"### {title} {cond}  (source line {if_node.line})\n\n"
        f"Evaluate the condition. If true, proceed with branch A. "
        f"Otherwise, branch B.\n\n"
        f"- **{a_label}**: {n_then} sub-step(s) (see ordinal sections above/below)\n"
    )
    if n_else:
        head += f"- **{b_label}**: {n_else} sub-step(s)\n"
    head += "\n"
    return head


def render_match_section(match_node: MatchBlockIR, idx_label: str, lang: str = "en") -> str:
    """Render a MATCH/CASE block as a SKILL.md sub-section."""
    title = {"en": "MATCH", "fr": "Cas"}[lang]
    discriminator = f"{match_node.state_field}.{match_node.sub_field}"
    head = (
        f"### {title} {discriminator}  (source line {match_node.line})\n\n"
        f"Evaluate the discriminator and follow the matching case below.\n\n"
    )
    for case in match_node.cases:
        pattern = case.value if case.value is not None else "DEFAULT"
        n_body = len(case.body)
        head += f"- **Case `{pattern}`**: {n_body} sub-step(s)\n"
    head += "\n"
    return head


def render_for_each_section(node: ForEachIR, idx_label: str, lang: str = "en") -> str:
    """Render a FOR EACH block as a SKILL.md sub-section.

    The per-iteration TodoWrite instruction is the primary drift anchor: without
    it the LLM host has no structural checkpoint and may process items in a
    single unchecked sweep.
    """
    title = {"en": "FOR EACH", "fr": "Pour chaque"}[lang]
    var = node.loop_var
    coll = node.collection
    n_body = len(node.body)
    parallel_note = ""
    if node.parallel and node.collector:
        parallel_note = (
            f" Results are accumulated into `state.{node.collector}` (PARALLEL mode)."
        )
    return (
        f"### {title} `{var}` IN `{coll}`  (source line {node.line})\n\n"
        f"For each element of `{coll}`:\n"
        f"- Create a TodoWrite sub-todo \"Iteration {var}=<value>\".\n"
        f"- Run the {n_body} sub-step(s) in the loop body.\n"
        f"- Mark the sub-todo done, append the result to `state.<accumulator>`.\n"
        f"{parallel_note}\n\n"
    )


def render_while_section(node: WhileBlockIR, idx_label: str, lang: str = "en") -> str:
    """Render a WHILE block as a SKILL.md sub-section.

    Instructs the LLM host to create a per-iteration TodoWrite sub-todo and
    re-evaluate the condition after each body execution, hard-capping at
    max_iters.
    """
    title = {"en": "WHILE", "fr": "Tant que"}[lang]
    cond = _render_condition(node.condition)
    n_body = len(node.body)
    return (
        f"### {title} `{cond}` MAX {node.max_iters}  (source line {node.line})\n\n"
        f"Loop while the condition holds (hard cap: {node.max_iters} iterations):\n"
        f"- Before each iteration, create a TodoWrite sub-todo \"Iteration #N\".\n"
        f"- Run the {n_body} sub-step(s) in the loop body.\n"
        f"- Re-evaluate the condition. Mark the iteration's todo done.\n"
        f"- Stop if the condition turns false or {node.max_iters} iterations have completed.\n\n"
    )


def render_cache_block(step: StepIR, lang: str = "en") -> str:
    """Cache sub-block instructing the LLM to use the bundled cache-key helper.

    CacheConfigIR has two fields: `.mode` ("on" | "off" | "ttl") and
    `.ttl_seconds`. There is no `.key_fields` in the v0.14 IR — cache is
    mode-driven, not field-driven. The block passes an empty key_fields array to
    _cache_key.py, which hashes the entire step namespace in state.json.

    If `step.cache` is None or mode is "off", returns "".
    """
    cache = getattr(step, "cache", None)
    if cache is None:
        return ""
    mode = getattr(cache, "mode", "off") or "off"
    if mode == "off":
        return ""
    label = {"en": "Cache", "fr": "Mise en cache"}[lang]
    ttl_seconds = getattr(cache, "ttl_seconds", None)
    ttl_note = ""
    if ttl_seconds is not None:
        ttl_note = f" (TTL: {ttl_seconds}s)"
    # TODO(post-v0.14): CacheConfigIR has no `.key_fields`. When the IR adds
    # field-level cache keys, replace the empty list below with the actual
    # key_fields tuple serialised as a JSON array.
    key_fields_json = "[]"
    body = (
        f"\n**{label}**{ttl_note}: before executing, compute the cache key:\n\n"
        f"    KEY=$(python scripts/_cache_key.py state.json '{step.name}' '{key_fields_json}')\n\n"
        f"If `.cache/{step.name}_${{KEY}}.json` exists, skip execution and merge its "
        f"contents into `state.json` under `state.{step.name}`. Otherwise run the step "
        f"normally and write the output to `.cache/{step.name}_${{KEY}}.json` after success.\n\n"
    )
    return body


def render_retry_block(step: StepIR, lang: str = "en") -> str:
    """Retry sub-block if ON_FAIL has a retry strategy.

    TODO(post-v0.14): StepIR has no dedicated `.retry` field. Retry config is
    currently expressed via ON_FAIL: retry(N) inside OnFailChainIR. This
    function reads the first retry strategy from on_fail.strategies and renders
    it. A dedicated StepIR.retry shorthand is deferred to post-v0.14.

    Returns "" when no retry strategy is found.
    """
    on_fail: OnFailChainIR | None = getattr(step, "on_fail", None)
    if on_fail is None:
        return ""
    retry_strategy = None
    for s in on_fail.strategies:
        if getattr(s, "kind", None) == "retry":
            retry_strategy = s
            break
    if retry_strategy is None:
        return ""
    label = {"en": "Retry", "fr": "Réessayer"}[lang]
    budget = getattr(retry_strategy, "max_retries", 1) or 1
    body = (
        f"\n**{label}**: on failure, regenerate up to {budget} time(s). "
        f"After the budget is exhausted, see RESCUE section if present, otherwise stop.\n\n"
    )
    return body


def render_resources_annex(graph: FlowGraph, lang: str = "en") -> str:
    """Annex at the end of SKILL.md listing flow-level RESOURCES.

    ResourcesIR has: .target (str), .models (tuple[str, ...]),
    .mcp_servers (tuple[McpServerSpecIR, ...]), .databases (tuple[DatabaseSpecIR, ...]).
    Returns "" when graph.resources is None or all collections are empty.
    """
    resources: ResourcesIR | None = getattr(graph, "resources", None)
    if resources is None:
        return ""
    has_content = bool(
        getattr(resources, "models", ())
        or getattr(resources, "mcp_servers", ())
        or getattr(resources, "databases", ())
    )
    if not has_content:
        return ""
    label = {"en": "Resources", "fr": "Ressources"}[lang]
    lines = [f"\n## {label}\n\n"]
    target = getattr(resources, "target", None)
    if target:
        target_label = {"en": "Target", "fr": "Cible"}[lang]
        lines.append(f"**{target_label}**: `{target}`\n\n")
    models = getattr(resources, "models", ())
    if models:
        models_label = {"en": "Models", "fr": "Modèles"}[lang]
        lines.append(f"**{models_label}**: {', '.join(f'`{m}`' for m in models)}\n\n")
    mcp_servers = getattr(resources, "mcp_servers", ())
    if mcp_servers:
        mcp_label = {"en": "MCP servers", "fr": "Serveurs MCP"}[lang]
        lines.append(f"**{mcp_label}**:\n\n")
        for srv in mcp_servers:
            lines.append(f"- `{srv.name}`\n")
        lines.append("\n")
    databases = getattr(resources, "databases", ())
    if databases:
        db_label = {"en": "Databases", "fr": "Bases de données"}[lang]
        lines.append(f"**{db_label}**:\n\n")
        for db in databases:
            lines.append(f"- `{db.name}` ({db.driver})\n")
        lines.append("\n")
    return "".join(lines)


def render_skill_md(
    graph: FlowGraph,
    *,
    warn: Callable[[str], None] | None = None,
) -> str:
    """Render the full SKILL.md content for a flow.

    Strategy: emit flat step sections first (preserving Tasks 4/5 behaviour),
    then append narrative control-structure sections from graph.flow.chain.
    The flat graph.steps list already contains every STEP regardless of nesting;
    the chain walk adds IF/MATCH headings that describe the branching logic and
    reference the steps by count. This keeps the two concerns separate and
    avoids rewriting the step-enumeration logic.
    """
    lang = detect_skill_language(graph)
    parts = [render_frontmatter(graph, warn=warn), f"\n# {_flow_name(graph)}\n"]
    for idx, step in enumerate(graph.steps, start=1):
        if step.mode == "exact":
            parts.append(render_exact_step_section(step, idx, lang=lang))
        elif step.mode == "judgment":
            parts.append(render_judgment_step_section(step, idx, lang=lang))

    # Append control-structure narrative from the structured chain.
    flow = getattr(graph, "flow", None)
    if flow is not None:
        chain = getattr(flow, "chain", None) or ()
        for item_idx, item in enumerate(chain, start=1):
            if isinstance(item, IfBlockIR):
                parts.append(render_if_section(item, f"{item_idx:02d}", lang=lang))
            elif isinstance(item, MatchBlockIR):
                parts.append(render_match_section(item, f"{item_idx:02d}", lang=lang))
            elif isinstance(item, ForEachIR):
                parts.append(render_for_each_section(item, f"{item_idx:02d}", lang=lang))
            elif isinstance(item, WhileBlockIR):
                parts.append(render_while_section(item, f"{item_idx:02d}", lang=lang))
            # RescueBlockIR: deferred to later tasks.

    parts.append(render_resources_annex(graph, lang=lang))
    return "".join(parts)


BUNDLED_VALIDATE_PY = '''\
#!/usr/bin/env python3
"""Bundled JSON Schema validator for CLIO-emitted skills.

Usage: python _validate.py <instance.json> <schema.json>
Exits 0 if valid, non-zero with a human-readable message otherwise.

Prefers the `jsonschema` PyPI package when available; falls back to a
minimal stdlib check (type + required + property types) so the skill
remains usable on bare Python installs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _stdlib_validate(instance, schema, path="$"):
    t = schema.get("type")
    if t == "object":
        if not isinstance(instance, dict):
            raise ValueError(f"{path}: expected object, got {type(instance).__name__}")
        for req in schema.get("required", []):
            if req not in instance:
                raise ValueError(f"{path}: missing required field \'{req}\'")
        for k, sub in schema.get("properties", {}).items():
            if k in instance:
                _stdlib_validate(instance[k], sub, f"{path}.{k}")
    elif t == "array":
        if not isinstance(instance, list):
            raise ValueError(f"{path}: expected array, got {type(instance).__name__}")
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(instance):
                _stdlib_validate(item, items_schema, f"{path}[{i}]")
    elif t == "string":
        if not isinstance(instance, str):
            raise ValueError(f"{path}: expected string")
    elif t == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise ValueError(f"{path}: expected integer")
    elif t == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            raise ValueError(f"{path}: expected number")
    elif t == "boolean":
        if not isinstance(instance, bool):
            raise ValueError(f"{path}: expected boolean")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: _validate.py <instance.json> <schema.json>", file=sys.stderr)
        return 2
    instance = json.loads(Path(sys.argv[1]).read_text())
    schema = json.loads(Path(sys.argv[2]).read_text())
    try:
        import jsonschema  # type: ignore
        jsonschema.validate(instance, schema)
    except ImportError:
        try:
            _stdlib_validate(instance, schema)
        except ValueError as e:
            print(f"validation error: {e}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"validation error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def render_bundled_validate_script() -> str:
    return BUNDLED_VALIDATE_PY


BUNDLED_CACHE_KEY_PY = '''\
#!/usr/bin/env python3
"""Bundled deterministic cache-key generator for CLIO-emitted skills.

Usage: python _cache_key.py <state.json> <step_name> <key_fields_json>
Emits SHA256 hex on stdout.

`key_fields_json` is a JSON array of dotted paths into <state.json>
(e.g. \'["customer.id", "order.items"]\'). Missing paths are treated as
null, which deterministically participates in the hash.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _get(state, dotted_path):
    cur = state
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: _cache_key.py <state.json> <step_name> <key_fields_json>", file=sys.stderr)
        return 2
    state = json.loads(Path(sys.argv[1]).read_text())
    step_name = sys.argv[2]
    key_fields = json.loads(sys.argv[3])
    payload = {"step": step_name, "inputs": {p: _get(state, p) for p in key_fields}}
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    print(hashlib.sha256(canon).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def render_bundled_cache_key_script() -> str:
    return BUNDLED_CACHE_KEY_PY


def render_process_flow_dot(graph: FlowGraph) -> str:
    """Render the flow as DOT (reuses the existing `clio graph --format dot` renderer)."""
    from clio.graph_render import to_dot

    return to_dot(graph)


def render_state_example(graph: FlowGraph) -> str:
    """Initial-state template. One empty namespace per top-level STEP.

    Format: {"step01": {}, "step02": {}, ...} — keyed by top-level step name,
    in source order.
    """
    state = {step.name: {} for step in graph.steps}
    return json.dumps(state, indent=2) + "\n"


def render_readme(graph: FlowGraph) -> str:
    """Render a brief README.md for the emitted skill directory."""
    raw_name = _flow_name(graph)
    return (
        f"# {raw_name} — claude-skill\n\n"
        f"Compiled from a CLIO `.clio` source for the `claude-skill` target.\n\n"
        "## How to install\n\n"
        "Copy this directory to `~/.claude/skills/<name>/`, then invoke from any Claude Code session.\n\n"
        "## Caveats\n\n"
        "This skill is executed by the LLM host. Fidelity of execution is conditioned on the "
        "rigor of the host — the TodoWrite checklist in `SKILL.md` provides the main anchor "
        "against drift.\n"
    )
