"""Render a FlowGraph as Mermaid, Graphviz DOT, or a self-contained HTML viewer.

Output is meant to be embedded in GitHub PRs (Mermaid), piped to a graphviz
tool (DOT), or opened in a browser (HTML). None of these emitters writes a
project — each returns one source string per call.
"""
from __future__ import annotations

import html as _html
import json

from clio.ir.graph import (
    ApiInvokeIR,
    CacheConfigIR,
    CallIR,
    CliInvokeIR,
    CodeImplIR,
    ContractIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    ImplIR,
    InvokeIR,
    MatchBlockIR,
    OnFailChainIR,
    RescueBlockIR,
    RestImplIR,
    ShellImplIR,
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


_MERMAID_CLASSDEFS = (
    "    classDef judgment fill:#e3f2fd,stroke:#1976d2,color:#0d47a1",
    "    classDef exact fill:#fff3e0,stroke:#f57c00,color:#bf360c",
)

_BLUEPRINT_CLASSDEFS = (
    "    classDef judgment fill:#ffffff,stroke:#1a3550,color:#0e2236,stroke-width:1.25px",
    "    classDef exact fill:#ffffff,stroke:#1a3550,color:#0e2236,stroke-width:1.25px",
)


def _mermaid_node(step: StepIR) -> str:
    label = f"{step.name}<br/>{step.mode}"
    if step.mode == "judgment":
        return f'{step.name}[/"{label}"/]:::judgment'
    return f'{step.name}["{label}"]:::exact'


def to_mermaid(
    graph: FlowGraph,
    classdefs: tuple[str, ...] = _MERMAID_CLASSDEFS,
) -> str:
    """Render a FlowGraph as a Mermaid `flowchart TD` source string.

    EXACT steps render as rectangles, JUDGMENT steps as parallelograms.
    FOR EACH blocks render as labelled subgraphs containing their body;
    edges from a previous step land on the subgraph border.

    `classdefs` lets the HTML viewer inject a blueprint-themed palette
    without changing the GitHub-rendered Mermaid output.
    """
    steps_by_name = {s.name: s for s in graph.steps}
    lines: list[str] = ["flowchart TD"]
    declared: set[str] = set()
    state = {"foreach_idx": 0}

    def declare(step_name: str, indent: str) -> None:
        if step_name in declared:
            return
        step = steps_by_name.get(step_name)
        if step is not None:
            lines.append(f"{indent}{_mermaid_node(step)}")
        else:
            lines.append(f'{indent}{step_name}["{step_name}<br/>?"]')
        declared.add(step_name)

    def walk(chain, indent: str, prev_id: str | None) -> str | None:
        for elem in chain:
            if isinstance(elem, CallIR):
                declare(elem.step_name, indent)
                if prev_id is not None:
                    lines.append(f"{indent}{prev_id} --> {elem.step_name}")
                prev_id = elem.step_name
            elif isinstance(elem, ForEachIR):
                state["foreach_idx"] += 1
                sg_id = f"foreach_{state['foreach_idx']}"
                if elem.parallel:
                    label = f"FOR EACH {elem.loop_var} IN {elem.collection} [parallel]"
                else:
                    label = f"FOR EACH {elem.loop_var} IN {elem.collection}"
                lines.append(f'{indent}subgraph {sg_id}["{label}"]')
                walk(elem.body, indent + "    ", None)
                lines.append(f"{indent}end")
                if prev_id is not None:
                    lines.append(f"{indent}{prev_id} --> {sg_id}")
                prev_id = sg_id
        return prev_id

    if graph.flow is None:
        for s in graph.steps:
            lines.append(f"    {_mermaid_node(s)}")
    else:
        walk(graph.flow.chain, "    ", None)

    lines.extend(classdefs)
    return "\n".join(lines) + "\n"


def _dot_node(step: StepIR) -> str:
    shape = "parallelogram" if step.mode == "judgment" else "box"
    label = f"{step.name}\\n{step.mode}"
    return f'{step.name} [label="{label}", shape={shape}];'


def to_dot(graph: FlowGraph) -> str:
    """Render a FlowGraph as Graphviz DOT.

    FOR EACH is represented by a label on the entering edge rather than a
    cluster — the latter requires `lhead`/invisible nodes that aren't worth
    the complexity for a v0 visualization.
    """
    steps_by_name = {s.name: s for s in graph.steps}
    lines: list[str] = [
        "digraph clio {",
        "    rankdir=TB;",
        '    node [fontname="Helvetica"];',
    ]
    declared: set[str] = set()

    def declare(step_name: str) -> None:
        if step_name in declared:
            return
        step = steps_by_name.get(step_name)
        if step is not None:
            lines.append(f"    {_dot_node(step)}")
        else:
            lines.append(f'    {step_name} [label="{step_name}\\n?", shape=box];')
        declared.add(step_name)

    def first_call(chain) -> str | None:
        for elem in chain:
            if isinstance(elem, CallIR):
                return elem.step_name
            if isinstance(elem, ForEachIR):
                inner = first_call(elem.body)
                if inner is not None:
                    return inner
        return None

    def last_call(chain) -> str | None:
        for elem in reversed(chain):
            if isinstance(elem, CallIR):
                return elem.step_name
            if isinstance(elem, ForEachIR):
                inner = last_call(elem.body)
                if inner is not None:
                    return inner
        return None

    def walk(chain, prev_id: str | None) -> str | None:
        for elem in chain:
            if isinstance(elem, CallIR):
                declare(elem.step_name)
                if prev_id is not None:
                    lines.append(f"    {prev_id} -> {elem.step_name};")
                prev_id = elem.step_name
            elif isinstance(elem, ForEachIR):
                target = first_call(elem.body)
                if target is None:
                    continue
                walk(elem.body, None)
                if prev_id is not None:
                    if elem.parallel:
                        edge_label = f"for each {elem.loop_var} in {elem.collection} [parallel]"
                    else:
                        edge_label = f"for each {elem.loop_var} in {elem.collection}"
                    lines.append(
                        f'    {prev_id} -> {target} [label="{edge_label}", style=dashed];'
                    )
                prev_id = last_call(elem.body) or prev_id
        return prev_id

    if graph.flow is None:
        for s in graph.steps:
            declare(s.name)
    else:
        walk(graph.flow.chain, None)

    lines.append("}")
    return "\n".join(lines) + "\n"


def _type_to_str(t: TypeExpr) -> str:
    """Render a TypeExpr in the same surface notation as `.clio` source."""
    if isinstance(t, PrimitiveType):
        return t.name
    if isinstance(t, ListType):
        return f"List<{_type_to_str(t.inner)}>"
    if isinstance(t, RecordType):
        return "{" + ", ".join(f"{n}: {_type_to_str(ty)}" for n, ty in t.fields) + "}"
    if isinstance(t, EnumType):
        return "enum(" + "|".join(t.values) + ")"
    if isinstance(t, ContractRef):
        return t.name
    if isinstance(t, ConstrainedType):
        base = _type_to_str(t.base)
        constraints = ", ".join(f"{k}={v}" for k, v in t.constraints)
        return f"{base}({constraints})"
    return type(t).__name__


def _collect_contract_refs(t: TypeExpr) -> list[str]:
    seen: list[str] = []

    def walk(node: TypeExpr) -> None:
        if isinstance(node, ContractRef):
            if node.name not in seen:
                seen.append(node.name)
        elif isinstance(node, ListType):
            walk(node.inner)
        elif isinstance(node, RecordType):
            for _, ty in node.fields:
                walk(ty)
        elif isinstance(node, ConstrainedType):
            walk(node.base)

    walk(t)
    return seen


def _cache_to_str(c: CacheConfigIR) -> str:
    if c.mode in ("on", "off"):
        return c.mode
    if c.mode == "ttl" and c.ttl_seconds is not None:
        s = c.ttl_seconds
        if s % 86400 == 0:
            return f"ttl({s // 86400}d)"
        if s % 3600 == 0:
            return f"ttl({s // 3600}h)"
        if s % 60 == 0:
            return f"ttl({s // 60}m)"
        return f"ttl({s}s)"
    return c.mode


def _on_fail_to_str(of: OnFailChainIR) -> str:
    parts: list[str] = []
    for s in of.strategies:
        if s.kind == "retry":
            parts.append(f"retry({s.max_retries})")
        elif s.kind == "escalate":
            parts.append("escalate")
        elif s.kind == "fallback":
            parts.append(f"fallback({s.fallback_step_name})")
        elif s.kind == "abort":
            msg = (s.abort_message or "").replace('"', '\\"')
            parts.append(f'abort("{msg}")')
        else:
            parts.append(s.kind)
    return " then ".join(parts)


def _impl_to_dict(i: ImplIR) -> dict:
    if isinstance(i, CodeImplIR):
        return {"mode": "code", "lang": i.lang}
    if isinstance(i, ShellImplIR):
        d: dict = {"mode": "shell", "argv": list(i.argv), "parse": i.parse}
        if i.timeout_seconds is not None:
            d["timeout"] = i.timeout_seconds
        return d
    if isinstance(i, RestImplIR):
        d = {"mode": "rest", "method": i.method, "url": i.url}
        if i.response_path:
            d["response_path"] = i.response_path
        if i.timeout_seconds is not None:
            d["timeout"] = i.timeout_seconds
        if i.retries is not None:
            d["retries"] = i.retries
        return d
    return {"mode": type(i).__name__}


def _invoke_to_dict(i: InvokeIR) -> dict:
    if isinstance(i, CliInvokeIR):
        d: dict = {"mode": "cli"}
        if i.cli is not None:
            d["cli"] = i.cli
        if i.model is not None:
            d["model"] = i.model
        if i.output_format is not None:
            d["output_format"] = i.output_format
        if i.max_turns is not None:
            d["max_turns"] = i.max_turns
        return d
    if isinstance(i, ApiInvokeIR):
        d = {"mode": "api", "protocol": i.protocol, "model": i.model}
        if i.base_url is not None:
            d["base_url"] = i.base_url
        if i.auth is not None:
            d["auth"] = i.auth
        if i.temperature is not None:
            d["temperature"] = i.temperature
        if i.max_tokens is not None:
            d["max_tokens"] = i.max_tokens
        if i.timeout_seconds is not None:
            d["timeout"] = i.timeout_seconds
        if i.retries is not None:
            d["retries"] = i.retries
        return d
    return {"mode": type(i).__name__}


def _step_mode_class(step: StepIR) -> str:
    """Map a step to its visual mode class for the HTML viewer.

    judgment      → 'judgment'   (LLM call, blue)
    exact + shell → 'exact-shell' (orange)
    exact + rest  → 'exact-rest'  (teal)
    exact + code  → 'exact-code'  (slate)  — also default for stub steps
    """
    if step.mode == "judgment":
        return "judgment"
    if isinstance(step.impl, ShellImplIR):
        return "exact-shell"
    if isinstance(step.impl, RestImplIR):
        return "exact-rest"
    return "exact-code"


# Recognised model nicknames in priority order. The first match in the model
# string wins. Keep sorted from most specific to least specific.
_MODEL_NICKNAMES = (
    "haiku", "sonnet", "opus",
    "gpt-5", "gpt-4o", "gpt-4", "gpt-3.5",
    "gemini-pro", "gemini",
    "mistral", "llama", "qwen", "deepseek",
)


def _step_kicker(step: StepIR) -> str | None:
    """The 'next-level' detail to show in the card head, beyond the icon's
    semantics. For judgment: the model nickname or 'cli'. For exact: the
    invoked command (shell), HTTP method (rest), or language (code).
    Returns None when nothing distinctive can be said."""
    if step.mode == "judgment":
        if step.invoke is None:
            return "cli"  # default = invoke.cli (Claude CLI)
        if isinstance(step.invoke, CliInvokeIR):
            return "cli"
        if isinstance(step.invoke, ApiInvokeIR):
            model = (step.invoke.model or "").lower()
            for nick in _MODEL_NICKNAMES:
                if nick in model:
                    return nick
            # truncate long model names so they fit the chip
            return (step.invoke.model or "api")[:14]
        return "api"
    # exact mode
    if isinstance(step.impl, ShellImplIR):
        return step.impl.argv[0] if step.impl.argv else "shell"
    if isinstance(step.impl, RestImplIR):
        return step.impl.method or "GET"
    if isinstance(step.impl, CodeImplIR):
        return step.impl.lang or "code"
    return None


def _step_meta_rows(step: StepIR) -> list[tuple[str, str]]:
    """Up to two key/value rows shown in the meta footer of the node card.
    Picks the most informative attributes per step. Truncates long values."""
    rows: list[tuple[str, str]] = []
    if step.cache is not None:
        rows.append(("cache", _cache_to_str(step.cache)))
    if step.on_fail is not None and len(rows) < 2:
        of = _on_fail_to_str(step.on_fail)
        if len(of) > 28:
            of = of[:26] + "…"
        rows.append(("retry", of))
    if isinstance(step.impl, ShellImplIR) and step.impl.parse != "none" and len(rows) < 2:
        rows.append(("parse", step.impl.parse))
    if len(rows) < 2 and step.gives is not None:
        gv = _type_to_str(step.gives.type)
        if len(gv) > 28:
            gv = gv[:26] + "…"
        rows.append(("gives", gv))
    return rows[:2]


# Lucide-style stroke icons per mode. Same width=24 viewBox; rendered at
# 20px in production via CSS. Stroke-width set on the SVG itself (1.5).
_MODE_ICONS = {
    "judgment": (
        '<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936'
        'A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5'
        'A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063'
        'a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>'
        '<path d="M20 3v4"/><path d="M22 5h-4"/>'
        '<path d="M4 17v2"/><path d="M5 18H3"/>'
        '</svg>'
    ),
    "exact-shell": (
        '<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="4 17 10 11 4 5"/>'
        '<line x1="12" y1="19" x2="20" y2="19"/>'
        '</svg>'
    ),
    "exact-rest": (
        '<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M5 9l-3 3 3 3"/>'
        '<path d="M2 12h20"/>'
        '<path d="M19 15l3-3-3-3"/>'
        '</svg>'
    ),
    "exact-code": (
        '<svg viewBox="0 0 24 24" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="16 18 22 12 16 6"/>'
        '<polyline points="8 6 2 12 8 18"/>'
        '</svg>'
    ),
}


def _render_node_card_html(step: StepIR) -> str:
    """Render the rich Tabloid-style HTML card for a single step. Used as
    the inline label in the Mermaid source for the HTML viewer.

    HTML attributes are quoted with single quotes so the surrounding Mermaid
    `node["..."]` double-quoted form stays intact. Dynamic values are
    HTML-escaped to handle types like `List<...>` safely."""
    mode_class = _step_mode_class(step)
    icon_svg = _MODE_ICONS.get(mode_class, "")
    kicker = _step_kicker(step)
    meta_rows = _step_meta_rows(step)

    # Use single-quoted HTML attrs throughout. html.escape with quote=True
    # turns " into &quot; and ' into &#x27;, both safe in either delimiter.
    def esc(s: str) -> str:
        return _html.escape(s, quote=True)

    parts = [
        f"<div class='node-card {mode_class}'>",
        "<div class='head'>",
        f"<span class='icon'>{icon_svg}</span>",
        f"<span class='name'>{esc(step.name)}</span>",
    ]
    if kicker:
        parts.append(f"<span class='kicker'>{esc(kicker)}</span>")
    parts.append("</div>")
    if meta_rows:
        parts.append("<div class='meta'>")
        for key, val in meta_rows:
            parts.append(
                "<div class='row'>"
                f"<span class='key'>{esc(key)}</span>"
                f"<span class='val'>{esc(val)}</span>"
                "</div>"
            )
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def _to_mermaid_rich_labels(
    graph: FlowGraph,
) -> tuple[
    str,
    dict[str, dict],
    dict[str, dict],
    dict[str, dict],
    dict[str, dict],
    dict[str, dict],
]:
    """Mermaid source where each node label is the rich Tabloid-style HTML
    card. The viewer's CSS hides the underlying SVG rect so only the HTML
    label shows.

    Returns (mermaid_source, foreach_meta, if_meta, match_meta, while_meta,
    rescue_meta). Each meta dict maps a node/cluster id to control-flow
    specifics; the viewer JS uses them to enrich the Mermaid output with
    chip-pills / decision cards / rescue side-panel enrichment.
    """
    steps_by_name = {s.name: s for s in graph.steps}
    lines: list[str] = ["flowchart TD"]
    declared: set[str] = set()
    state = {"foreach_idx": 0, "if_idx": 0, "match_idx": 0, "while_idx": 0}
    foreach_meta: dict[str, dict] = {}
    if_meta: dict[str, dict] = {}
    match_meta: dict[str, dict] = {}
    while_meta: dict[str, dict] = {}
    rescue_meta: dict[str, dict] = {}

    def declare(step_name: str, indent: str) -> None:
        if step_name in declared:
            return
        step = steps_by_name.get(step_name)
        if step is not None:
            card = _render_node_card_html(step)
            lines.append(f'{indent}{step_name}["{card}"]')
        else:
            lines.append(f'{indent}{step_name}["{step_name}<br/>?"]')
        declared.add(step_name)

    def _first_id(chain) -> str | None:
        for elem in chain:
            if isinstance(elem, CallIR):
                return elem.step_name
            if isinstance(elem, ForEachIR):
                return _first_id(elem.body)
            if isinstance(elem, IfBlockIR):
                return _first_id(elem.then_body)
            if isinstance(elem, MatchBlockIR):
                if elem.cases:
                    return _first_id(elem.cases[0].body)
        return None

    def walk(chain, indent: str, prev_ids: list[str]) -> list[str]:
        for elem in chain:
            if isinstance(elem, CallIR):
                declare(elem.step_name, indent)
                for p in prev_ids:
                    lines.append(f"{indent}{p} --> {elem.step_name}")
                prev_ids = [elem.step_name]
            elif isinstance(elem, ForEachIR):
                state["foreach_idx"] += 1
                sg_id = f"foreach_{state['foreach_idx']}"
                if elem.parallel:
                    label = f"FOR EACH {elem.loop_var} IN {elem.collection} [parallel]"
                else:
                    label = f"FOR EACH {elem.loop_var} IN {elem.collection}"
                foreach_meta[sg_id] = {
                    "loop_var": elem.loop_var,
                    "collection": elem.collection,
                    "parallel": elem.parallel,
                }
                lines.append(f'{indent}subgraph {sg_id}["{label}"]')
                walk(elem.body, indent + "    ", [])
                lines.append(f"{indent}end")
                for p in prev_ids:
                    lines.append(f"{indent}{p} --> {sg_id}")
                prev_ids = [sg_id]
            elif isinstance(elem, IfBlockIR):
                state["if_idx"] += 1
                dec_id = f"if_{state['if_idx']}"
                cond = elem.condition
                if_meta[dec_id] = {
                    "state_field": cond.step_name,
                    "sub_field": cond.field,
                    "op": cond.op,
                    "literal": cond.literal_value,
                    "literal_kind": cond.literal_kind,
                }
                # Decision node — diamond shape via {{...}} (hexagon-ish)
                # Mermaid renders it without a foreignObject; the viewer's
                # JS swaps in a small chip-pill via if_meta.
                lit_repr = repr(cond.literal_value)
                cond_label = (
                    f"{cond.step_name}.{cond.field} {cond.op} {lit_repr}"
                )
                lines.append(f'{indent}{dec_id}{{"IF {cond_label}"}}')
                for p in prev_ids:
                    lines.append(f"{indent}{p} --> {dec_id}")
                # Then branch — connect with a labelled edge.
                if elem.then_body:
                    first_then = _first_id(elem.then_body)
                    if first_then is not None:
                        lines.append(f'{indent}{dec_id} -- "yes" --> {first_then}')
                    then_terminals = walk(elem.then_body, indent, [])
                else:
                    then_terminals = [dec_id]
                # Else branch
                if elem.else_body:
                    first_else = _first_id(elem.else_body)
                    if first_else is not None:
                        lines.append(f'{indent}{dec_id} -- "no" --> {first_else}')
                    else_terminals = walk(elem.else_body, indent, [])
                else:
                    else_terminals = [dec_id]
                prev_ids = then_terminals + else_terminals
            elif isinstance(elem, WhileBlockIR):
                state["while_idx"] += 1
                w_id = f"while_{state['while_idx']}"
                cond = elem.condition
                while_meta[w_id] = {
                    "state_field": cond.step_name,
                    "sub_field": cond.field,
                    "op": cond.op,
                    "literal": cond.literal_value,
                    "literal_kind": cond.literal_kind,
                    "max_iters": elem.max_iters,
                }
                lit_repr = repr(cond.literal_value)
                label = (
                    f"WHILE {cond.step_name}.{cond.field} {cond.op} {lit_repr} "
                    f"MAX {elem.max_iters}"
                )
                lines.append(f'{indent}subgraph {w_id}["{label}"]')
                walk(elem.body, indent + "    ", [])
                lines.append(f"{indent}end")
                for p in prev_ids:
                    lines.append(f"{indent}{p} --> {w_id}")
                prev_ids = [w_id]
            elif isinstance(elem, MatchBlockIR):
                state["match_idx"] += 1
                m_id = f"match_{state['match_idx']}"
                match_meta[m_id] = {
                    "state_field": elem.state_field,
                    "sub_field": elem.sub_field,
                    "cases": [
                        {"value": arm.value} for arm in elem.cases
                    ],
                }
                cond_label = f"MATCH {elem.state_field}.{elem.sub_field}"
                lines.append(f'{indent}{m_id}{{"{cond_label}"}}')
                for p in prev_ids:
                    lines.append(f"{indent}{p} --> {m_id}")
                arm_terminals: list[str] = []
                for arm in elem.cases:
                    label = "default" if arm.value is None else arm.value
                    if arm.body:
                        first_arm = _first_id(arm.body)
                        if first_arm is not None:
                            lines.append(
                                f'{indent}{m_id} -- "{label}" --> {first_arm}'
                            )
                        arm_terms = walk(arm.body, indent, [])
                        arm_terminals.extend(arm_terms)
                    else:
                        arm_terminals.append(m_id)
                prev_ids = arm_terminals or [m_id]
        return prev_ids

    if graph.flow is None:
        for s in graph.steps:
            card = _render_node_card_html(s)
            lines.append(f'    {s.name}["{card}"]')
            declared.add(s.name)
    else:
        walk(graph.flow.chain, "    ", [])

    # RESCUE clusters — one red-tinted node per RescueBlockIR with a dotted
    # "fails" edge from the protected step and a body sub-flow ending in an
    # abort circle. Populates rescue_meta for the side-panel JS.
    if graph.flow is not None and graph.flow.rescues:
        for rb in graph.flow.rescues:
            target_id = rb.step_name
            rescue_id = f"rescue_{rb.step_name}"
            lines.append(f'    {rescue_id}["RESCUE<br/>{rb.step_name}"]')
            lines.append(f"    class {rescue_id} rescueClass")
            lines.append(f"    {target_id} -. fails .-> {rescue_id}")
            prev_id = rescue_id
            body_summary: list[dict] = []
            for item in rb.body:
                if isinstance(item, CallIR):
                    if item.step_name == "abort":
                        abort_id = f"abort_{rb.step_name}"
                        msg = next(
                            (v for k, v in item.kwargs if k == "message"), ""
                        )
                        msg_str = str(msg).replace('"', '\\"')
                        lines.append(f'    {abort_id}(("abort: {msg_str}"))')
                        lines.append(f"    {prev_id} --> {abort_id}")
                        prev_id = abort_id
                        body_summary.append(
                            {"step_name": "abort", "message": msg}
                        )
                    else:
                        item_id = item.step_name
                        # Declare the body step so it has a card if it isn't
                        # already part of the main chain.
                        declare(item.step_name, "    ")
                        lines.append(f"    {prev_id} --> {item_id}")
                        prev_id = item_id
                        body_summary.append(
                            {
                                "step_name": item.step_name,
                                "kwargs": list(item.kwargs),
                            }
                        )
                # IF/MATCH/WHILE/FOR EACH inside a rescue body could be
                # expanded later; v0.8 ships the call+abort path only.
            rescue_meta[rescue_id] = {
                "step_name": rb.step_name,
                "body": body_summary,
            }
        # Red accent style for rescue nodes.
        lines.append(
            "    classDef rescueClass fill:#fce4e4,stroke:#d73a49,"
            "stroke-width:2px,color:#7b1d1f"
        )

    return (
        "\n".join(lines) + "\n",
        foreach_meta,
        if_meta,
        match_meta,
        while_meta,
        rescue_meta,
    )


def _step_to_dict(step: StepIR) -> dict:
    refs: list[str] = []
    for f in step.takes:
        for r in _collect_contract_refs(f.type):
            if r not in refs:
                refs.append(r)
    if step.gives is not None:
        for r in _collect_contract_refs(step.gives.type):
            if r not in refs:
                refs.append(r)
    d: dict = {
        "name": step.name,
        "mode": step.mode,
        "mode_class": _step_mode_class(step),
        "kicker": _step_kicker(step),
        "line": step.line,
        "takes": [{"name": f.name, "type": _type_to_str(f.type)} for f in step.takes],
        "gives": (
            {"name": step.gives.name, "type": _type_to_str(step.gives.type)}
            if step.gives is not None else None
        ),
        "contracts": refs,
    }
    if step.lang is not None:
        d["lang"] = step.lang
    if step.cache is not None:
        d["cache"] = _cache_to_str(step.cache)
    if step.on_fail is not None:
        d["on_fail"] = _on_fail_to_str(step.on_fail)
    if step.impl is not None:
        d["impl"] = _impl_to_dict(step.impl)
    if step.invoke is not None:
        d["invoke"] = _invoke_to_dict(step.invoke)
    return d


def _contract_to_dict(c: ContractIR) -> dict:
    return {
        "name": c.name,
        "line": c.line,
        "json_schema": c.json_schema,
        "has_assert": c.assert_json_ast is not None,
    }


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CLIO graph &mdash; __TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
html { color-scheme: light; }
body {
  margin: 0;
  font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.viewer {
  --bg: oklch(96.5% 0.022 78);
  --surface: oklch(99% 0.012 78);
  --ink: oklch(20% 0.012 50);
  --ink-soft: oklch(32% 0.014 50);
  --muted: oklch(50% 0.015 50);
  --rule: oklch(86% 0.018 60);
  --rule-soft: oklch(91% 0.015 60);
  --grid-dot: oklch(45% 0.014 50 / 0.18);
  --neutral-strong: oklch(38% 0.012 240);
  --neutral-med: oklch(50% 0.014 240);
  --jdg-strong: oklch(35% 0.16 260);
  --jdg-tint:   oklch(91% 0.045 260);
  --jdg-icon:   oklch(38% 0.14 260);
  --shl-strong: oklch(45% 0.155 38);
  --shl-tint:   oklch(91% 0.045 38);
  --shl-icon:   oklch(45% 0.14 38);
  --rst-strong: oklch(45% 0.105 210);
  --rst-tint:   oklch(91% 0.04 210);
  --rst-icon:   oklch(40% 0.1 210);
  --cod-strong: oklch(40% 0.025 240);
  --cod-tint:   oklch(92% 0.012 240);
  --cod-icon:   oklch(38% 0.025 240);
  /* PARALLEL/FOR EACH cluster — amber/rust accent (distinct from mode hues) */
  --par-strong: oklch(48% 0.155 60);
  --par-tint:   oklch(93% 0.045 60);
  --par-icon:   oklch(45% 0.14 60);
  --shadow-card:  0 1px 2px rgba(82, 38, 19, 0.06), 0 4px 12px rgba(82, 38, 19, 0.04);
  --shadow-hover: 0 2px 6px rgba(82, 38, 19, 0.10), 0 12px 28px rgba(82, 38, 19, 0.08);
  font-size: 13.5px;
  line-height: 1.55;
  background: var(--bg);
  color: var(--ink);
  min-height: 100vh;
  display: grid;
  grid-template-rows: auto 1fr auto;
}

.viewer .v-toolbar {
  background: var(--surface);
  border-bottom: 1px solid var(--rule);
  padding: 12px 28px;
  display: flex; align-items: center; gap: 10px;
}
.viewer .v-toolbar .title { font-weight: 600; font-size: 16px; letter-spacing: -0.02em; color: var(--ink); margin-right: 6px; }
.viewer .v-toolbar .pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; font-size: 12px; font-weight: 500;
  background: var(--bg);
  border: 1px solid var(--rule);
  color: var(--neutral-strong);
  border-radius: 999px;
}
.viewer .v-toolbar .pill .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--neutral-med); }
.viewer .v-toolbar .spacer { flex: 1; }
.viewer .v-toolbar .credit { font-family: 'Geist Mono', monospace; font-size: 11px; color: var(--neutral-med); letter-spacing: 0.02em; }

.viewer .layout { display: grid; grid-template-columns: 1fr 380px; }
@media (max-width: 1100px) { .viewer .layout { grid-template-columns: 1fr; } }

.viewer .v-graph {
  padding: 32px 28px;
  background:
    radial-gradient(circle, var(--grid-dot) 1px, transparent 1.5px) 0 0 / 18px 18px,
    var(--bg);
  position: relative;
  min-height: 380px;
  overflow: auto;
}
.viewer .v-graph .axis-meta {
  position: absolute;
  top: 14px; left: 18px;
  font-family: 'Geist Mono', monospace;
  font-size: 10px;
  color: var(--muted);
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

/* Mermaid wrappers — collapse padding/margin around HTML labels */
.viewer .mermaid svg { background: transparent !important; max-width: 100%; overflow: visible !important; }
.viewer .mermaid svg .node rect, .viewer .mermaid svg .node polygon { fill: transparent !important; stroke: transparent !important; }
.viewer .mermaid svg foreignObject { overflow: visible !important; }
.viewer .mermaid svg foreignObject > div,
.viewer .mermaid svg foreignObject .label,
.viewer .mermaid svg foreignObject .nodeLabel,
.viewer .mermaid svg .nodeLabel,
.viewer .mermaid svg .label {
  padding: 0 !important;
  margin: 0 !important;
  line-height: 1 !important;
  white-space: normal !important;
}
.viewer .mermaid svg foreignObject .node-card { display: flex !important; }
.viewer .mermaid svg .edgePath path { stroke-linecap: round; stroke-linejoin: round; }

/* ============== Node cards (rich Tabloid-style HTML labels) ============== */
.node-card {
  display: flex; flex-direction: column;
  background: var(--surface);
  border-radius: 8px;
  border: 1px solid var(--rule);
  box-shadow: var(--shadow-card);
  overflow: hidden;
  min-width: 220px;
  font-family: 'Geist', sans-serif;
  transition: box-shadow 180ms ease-out, transform 180ms ease-out, border-color 180ms ease-out;
  cursor: pointer;
}
.node-card:hover { box-shadow: var(--shadow-hover); transform: translateY(-2px); border-color: var(--accent-strong); }
g.node.selected .node-card,
g.node:focus-visible .node-card {
  border-color: var(--accent-strong);
  box-shadow: 0 0 0 2px var(--accent-strong), var(--shadow-hover);
  outline: none;
}

.node-card .head {
  display: flex; align-items: center; gap: 9px;
  padding: 8px 12px;
  background: var(--accent-tint);
}
.node-card .head .icon {
  width: 20px; height: 20px;
  flex-shrink: 0;
  color: var(--accent-icon) !important;
  display: inline-flex;
}
.node-card .head .icon svg {
  width: 100%; height: 100%;
  fill: none !important;
  stroke: currentColor !important;
}
.node-card .head .icon svg path,
.node-card .head .icon svg polyline,
.node-card .head .icon svg line,
.node-card .head .icon svg circle,
.node-card .head .icon svg rect,
.node-card .head .icon svg ellipse {
  fill: none !important;
  stroke: currentColor !important;
}
.node-card .head .name { font-weight: 600; font-size: 14px; letter-spacing: -0.015em; color: var(--ink); white-space: nowrap; }
.node-card .head .kicker {
  margin-left: auto;
  font-family: 'Geist Mono', monospace;
  font-size: 10px; font-weight: 600;
  letter-spacing: 0.06em; text-transform: uppercase;
  padding: 1px 7px; border-radius: 3px;
  background: white;
  color: var(--accent-strong);
}
.node-card .meta {
  padding: 6px 12px 8px;
  font-family: 'Geist Mono', monospace;
  font-size: 11px;
  display: flex; flex-direction: column; gap: 2px;
  color: var(--muted);
}
.node-card .meta .row { display: inline-flex; gap: 8px; align-items: baseline; }
.node-card .meta .key { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; min-width: 38px; opacity: 0.85; }
.node-card .meta .val { color: var(--ink-soft); font-weight: 500; }
.node-card .meta:empty { display: none; }

.node-card.judgment    { --accent-strong: var(--jdg-strong); --accent-tint: var(--jdg-tint); --accent-icon: var(--jdg-icon); }
.node-card.exact-shell { --accent-strong: var(--shl-strong); --accent-tint: var(--shl-tint); --accent-icon: var(--shl-icon); }
.node-card.exact-rest  { --accent-strong: var(--rst-strong); --accent-tint: var(--rst-tint); --accent-icon: var(--rst-icon); }
.node-card.exact-code  { --accent-strong: var(--cod-strong); --accent-tint: var(--cod-tint); --accent-icon: var(--cod-icon); }

/* ============== FOR EACH PARALLEL cluster (variant C: soft fill + chip pill) ============ */
.viewer .mermaid svg .cluster rect {
  fill: oklch(94% 0.025 60) !important;
  stroke: var(--rule) !important;
  stroke-width: 1px !important;
  rx: 8 !important; ry: 8 !important;
}
.viewer .mermaid svg .cluster-label foreignObject { overflow: visible !important; }
.viewer .mermaid svg .cluster-label foreignObject > * { padding: 0 !important; margin: 0 !important; }

/* Override Mermaid's transparent `color` cascade so banner text stays visible */
.viewer .mermaid svg .par-banner,
.viewer .mermaid svg .par-banner * { color: inherit !important; }
.viewer .mermaid svg .par-banner .par-icon svg,
.viewer .mermaid svg .par-banner .par-icon svg * {
  stroke: currentColor !important;
  fill: none !important;
}

.par-banner {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: 'Geist', sans-serif;
  background: white;
  padding: 4px 10px 4px 8px;
  border: 1px solid var(--par-strong);
  border-radius: 999px;
  color: var(--ink);
  box-shadow: 0 1px 3px rgba(82, 38, 19, 0.08);
}
.par-banner .par-icon {
  width: 16px; height: 16px;
  display: inline-flex;
  flex-shrink: 0;
  color: var(--par-icon);
}
.par-banner .par-icon svg {
  width: 100%; height: 100%;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.par-banner .par-text {
  font-family: 'Geist Mono', monospace;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: -0.005em;
  white-space: nowrap;
}
.par-banner .par-kicker {
  font-family: 'Geist Mono', monospace;
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--par-strong);
  margin-left: 4px;
}

/* ============== Detail panel (editorial, not card-replay) ============== */
.viewer .v-panel {
  border-left: 1px solid var(--rule);
  background: var(--surface);
  padding: 28px 28px 32px;
  overflow-y: auto;
  position: relative;
  max-height: calc(100vh - 60px);
}
.viewer .v-panel::before {
  content: '';
  position: absolute;
  left: 0; top: 32px;
  width: 3px; height: 56px;
  background: var(--accent-strong, var(--neutral-strong));
}
.viewer .v-panel.judgment    { --accent-strong: var(--jdg-strong); }
.viewer .v-panel.exact-shell { --accent-strong: var(--shl-strong); }
.viewer .v-panel.exact-rest  { --accent-strong: var(--rst-strong); }
.viewer .v-panel.exact-code  { --accent-strong: var(--cod-strong); }
.viewer .v-panel.empty { color: var(--muted); }
.viewer .v-panel.empty::before { background: var(--neutral-med); height: 32px; }

.viewer .v-panel h2.step-name {
  margin: 0 0 4px 0;
  font-weight: 700;
  font-size: 26px;
  letter-spacing: -0.025em;
  color: var(--ink);
  word-break: break-word;
}
.viewer .v-panel .step-meta {
  display: flex; gap: 10px; align-items: center;
  font-family: 'Geist Mono', monospace;
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 18px;
  letter-spacing: 0.02em;
}
.viewer .v-panel .step-meta .accent {
  color: var(--accent-strong);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.viewer .v-panel h3 {
  font-weight: 600;
  font-size: 11px;
  margin: 22px 0 6px 0;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.viewer .v-panel ul { list-style: none; padding: 0; margin: 0; }
.viewer .v-panel li {
  padding: 6px 0;
  font-size: 13px;
  display: flex; gap: 10px; align-items: baseline;
  border-bottom: 1px dashed var(--rule-soft);
}
.viewer .v-panel li:last-child { border-bottom: none; }
.viewer .v-panel .field { font-weight: 600; color: var(--ink); }
.viewer .v-panel .sep { color: var(--muted); opacity: 0.5; }
.viewer .v-panel code {
  font-family: 'Geist Mono', monospace;
  font-size: 12px;
  color: var(--accent-strong, var(--ink));
  font-weight: 500;
}
.viewer .v-panel .pair {
  display: flex; gap: 14px; padding: 6px 0; font-size: 13px;
  align-items: baseline;
  border-bottom: 1px dashed var(--rule-soft);
}
.viewer .v-panel .pair:last-child { border-bottom: none; }
.viewer .v-panel .pair .l {
  font-family: 'Geist Mono', monospace;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  min-width: 92px; flex-shrink: 0;
}
.viewer .v-panel .pair .v { color: var(--ink); font-family: 'Geist Mono', monospace; font-size: 12px; font-weight: 500; }
.viewer .v-panel details { margin: 6px 0; }
.viewer .v-panel details + details { border-top: 1px dashed var(--rule-soft); padding-top: 8px; }
.viewer .v-panel details > summary {
  cursor: pointer; font-size: 13px;
  list-style: none; padding: 4px 0;
  display: flex; align-items: center; gap: 8px;
}
.viewer .v-panel details > summary::-webkit-details-marker { display: none; }
.viewer .v-panel details > summary::before {
  content: '+';
  color: var(--accent-strong, var(--ink));
  font-family: 'Geist Mono', monospace;
  font-size: 13px;
  width: 12px;
  display: inline-block;
}
.viewer .v-panel details[open] > summary::before { content: '−'; }
.viewer .v-panel details > summary code { color: var(--ink); background: none; padding: 0; }
.viewer .v-panel details .asserts {
  font-family: 'Geist Mono', monospace;
  font-size: 10px;
  color: var(--accent-strong);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 600;
}
.viewer .v-panel pre {
  font-family: 'Geist Mono', monospace;
  font-size: 11px;
  background: var(--bg);
  border: 1px solid var(--rule-soft);
  border-radius: 4px;
  padding: 10px 12px;
  overflow-x: auto;
  line-height: 1.5;
  color: var(--ink-soft);
  margin: 6px 0 0 0;
}
.viewer .empty-hint {
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

/* Footer */
.viewer .v-credit {
  text-align: right;
  font-family: 'Geist Mono', monospace;
  font-size: 11px;
  color: var(--muted);
  padding: 10px 28px;
  border-top: 1px solid var(--rule-soft);
  letter-spacing: 0.04em;
}
.viewer .v-credit em { color: var(--ink-soft); font-style: normal; font-weight: 500; }

:focus-visible { outline: 2px solid var(--neutral-strong); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation-duration: 0.001ms !important; }
}
</style>
</head>
<body>
<div class="viewer">
  <div class="v-toolbar">
    <span class="title" id="flow-title"></span>
    <span class="pill" id="flow-target"><span class="dot"></span><span id="flow-target-text"></span></span>
    <span class="pill" id="flow-counts"></span>
    <span class="spacer"></span>
    <span class="credit">CLIO viewer</span>
  </div>
  <main class="layout">
    <div class="v-graph" role="img" aria-label="Pipeline diagram">
      <div class="axis-meta">flow &middot; 01</div>
      <pre class="mermaid" id="mermaid-src"></pre>
    </div>
    <aside class="v-panel empty" id="panel" aria-live="polite" aria-atomic="false" aria-label="Step details"></aside>
  </main>
  <div class="v-credit">Compiled with <em>CLIO</em></div>
</div>
<template id="banner-foreach">
  <div class="par-banner">
    <span class="par-icon"><svg viewBox="0 0 24 24"><line x1="6" y1="3" x2="6" y2="15"></line><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></svg></span>
    <span class="par-text"></span>
    <span class="par-kicker"></span>
  </div>
</template>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
  mermaid.initialize({
    startOnLoad: true,
    securityLevel: 'loose',
    flowchart: { htmlLabels: true, curve: 'basis', padding: 4, nodeSpacing: 40, rankSpacing: 42 },
    theme: 'base',
    themeVariables: {
      background: 'transparent',
      mainBkg: 'transparent',
      primaryColor: 'transparent',
      primaryBorderColor: 'transparent',
      primaryTextColor: '#1f2937',
      lineColor: '#9a8470',
      secondaryColor: 'transparent',
      tertiaryColor: 'transparent',
      clusterBkg: 'rgba(154, 132, 112, 0.04)',
      clusterBorder: '#9a8470',
      titleColor: '#1f2937',
      edgeLabelBackground: '#f5efe6',
      fontFamily: '"Geist", -apple-system, sans-serif',
      fontSize: '13px',
    }
  });
  /* After Mermaid renders, make nodes keyboard-accessible. */
  window.addEventListener('load', () => {
    setTimeout(() => {
      document.querySelectorAll('g.node').forEach(n => {
        n.setAttribute('tabindex', '0');
        n.setAttribute('role', 'button');
        const id = n.id || '';
        const m = id.match(/^flowchart-(.+?)-\\d+$/);
        if (m) n.setAttribute('aria-label', 'Inspect step ' + m[1]);
      });
    }, 250);
  });
</script>
<script>
const STEPS = __STEPS_JSON__;
const CONTRACTS = __CONTRACTS_JSON__;
const TITLE = __TITLE_JSON__;
const SUBTITLE = __SUBTITLE_JSON__;
const TARGET = __TARGET_JSON__;
const COUNTS = __COUNTS_JSON__;
const MERMAID_SRC = __MERMAID_JSON__;
const FOREACH_META = __FOREACH_META_JSON__;
const IF_META = __IF_META_JSON__;
const MATCH_META = __MATCH_META_JSON__;
const WHILE_META = __WHILE_META_JSON__;
const RESCUE_META = __RESCUE_META_JSON__;

document.title = 'CLIO graph — ' + TITLE;
document.getElementById('flow-title').textContent = TITLE;
const targetPill = document.getElementById('flow-target');
if (TARGET) {
  document.getElementById('flow-target-text').textContent = TARGET;
} else {
  targetPill.style.display = 'none';
}
const countsPill = document.getElementById('flow-counts');
if (COUNTS) {
  countsPill.textContent = COUNTS;
} else {
  countsPill.style.display = 'none';
}
document.getElementById('mermaid-src').textContent = MERMAID_SRC;

function el(tag, opts) {
  const n = document.createElement(tag);
  if (opts) {
    if (opts.cls) n.className = opts.cls;
    if (opts.text != null) n.textContent = opts.text;
    if (opts.children) for (const c of opts.children) if (c) n.appendChild(c);
  }
  return n;
}

function fieldList(items) {
  const ul = el('ul');
  for (const f of items) {
    const li = el('li');
    li.appendChild(el('span', { cls: 'field', text: f.name }));
    li.appendChild(el('span', { cls: 'sep', text: ':' }));
    li.appendChild(el('code', { text: f.type }));
    ul.appendChild(li);
  }
  return ul;
}

function section(title, body) {
  const sec = el('section');
  sec.appendChild(el('h3', { text: title }));
  if (body) sec.appendChild(body);
  return sec;
}

function inlinePair(label, value) {
  const div = el('div', { cls: 'pair' });
  div.appendChild(el('span', { cls: 'l', text: label }));
  div.appendChild(el('span', { cls: 'v', text: value }));
  return div;
}

function preJson(obj) {
  return el('pre', { text: JSON.stringify(obj, null, 2) });
}

const PANEL_MODE_CLASSES = ['judgment', 'exact-shell', 'exact-rest', 'exact-code', 'empty'];

function showStep(name) {
  const s = STEPS[name];
  const panel = document.getElementById('panel');
  panel.replaceChildren();
  for (const c of PANEL_MODE_CLASSES) panel.classList.remove(c);
  if (!s) {
    panel.classList.add('empty');
    panel.appendChild(el('p', { cls: 'empty-hint', text: 'Unknown step: ' + name }));
    return;
  }
  panel.classList.add(s.mode_class);

  panel.appendChild(el('h2', { cls: 'step-name', text: s.name }));

  const stepMeta = el('div', { cls: 'step-meta' });
  const accentBits = [s.mode];
  if (s.kicker) accentBits.push(s.kicker);
  stepMeta.appendChild(el('span', { cls: 'accent', text: accentBits.join(' · ') }));
  stepMeta.appendChild(el('span', { text: '·' }));
  stepMeta.appendChild(el('span', { text: 'line ' + s.line }));
  panel.appendChild(stepMeta);

  if (s.takes && s.takes.length) panel.appendChild(section('Takes', fieldList(s.takes)));
  if (s.gives) panel.appendChild(section('Gives', fieldList([s.gives])));

  const inline = [];
  if (s.cache) inline.push(['Cache', s.cache]);
  if (s.on_fail) inline.push(['On fail', s.on_fail]);
  if (s.lang) inline.push(['Lang', s.lang]);
  if (inline.length) {
    const sec = el('section');
    sec.appendChild(el('h3', { text: 'Policy' }));
    for (const [label, value] of inline) sec.appendChild(inlinePair(label, value));
    panel.appendChild(sec);
  }

  if (s.impl) panel.appendChild(section('Impl', preJson(s.impl)));
  if (s.invoke) panel.appendChild(section('Invoke', preJson(s.invoke)));

  if (s.contracts && s.contracts.length) {
    const sec = el('section');
    sec.appendChild(el('h3', { text: 'Contracts referenced' }));
    for (const cname of s.contracts) {
      const c = CONTRACTS[cname];
      const det = el('details');
      const sum = el('summary');
      sum.appendChild(el('code', { text: cname }));
      if (c && c.has_assert) {
        sum.appendChild(el('span', { cls: 'asserts', text: 'asserts' }));
      }
      det.appendChild(sum);
      if (c) det.appendChild(preJson(c.json_schema));
      sec.appendChild(det);
    }
    panel.appendChild(sec);
  }

  document.querySelectorAll('g.node.selected').forEach(n => n.classList.remove('selected'));
  document.querySelectorAll('g.node').forEach(n => {
    const id = n.id || '';
    const m = id.match(/^flowchart-(.+?)-\\d+$/);
    if (m && m[1] === name) n.classList.add('selected');
  });
}

function showEmpty() {
  const panel = document.getElementById('panel');
  panel.replaceChildren();
  for (const c of PANEL_MODE_CLASSES) panel.classList.remove(c);
  panel.classList.add('empty');
  panel.appendChild(el('p', {
    cls: 'empty-hint',
    text: 'Pick a step from the diagram to inspect its takes, gives, contracts, cache, retry policy, and exec details.'
  }));
}

showEmpty();

function activateNode(node) {
  const id = node.id || '';
  const m = id.match(/^flowchart-(.+?)-\\d+$/);
  if (m) showStep(m[1]);
}

document.addEventListener('click', (e) => {
  const node = e.target.closest('g.node');
  if (node) activateNode(node);
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const node = document.activeElement && document.activeElement.closest('g.node');
  if (!node) return;
  e.preventDefault();
  activateNode(node);
});

/* ============== FOR EACH PARALLEL banner injection ============== */
/* Mermaid renders cluster labels as foreignObjects sized for the source
 * label string. We pass a placeholder text label and inject a rich chip-pill
 * banner per cluster id, then resize the foreignObject + recenter its
 * parent .cluster-label group so the chip floats at the cluster's top. */
function injectForeachBanners() {
  const meta = FOREACH_META || {};
  if (!Object.keys(meta).length) return;
  const tpl = document.getElementById('banner-foreach');
  if (!tpl) return;
  document.querySelectorAll('g.cluster').forEach(cluster => {
    const id = cluster.id;
    const data = meta[id];
    if (!data) return;
    const fo = cluster.querySelector('.cluster-label foreignObject');
    if (!fo) return;
    const host = fo.querySelector('div, span') || fo;

    const node = tpl.content.cloneNode(true);
    const text = node.querySelector('.par-text');
    const kicker = node.querySelector('.par-kicker');
    if (text) text.textContent = 'FOR EACH ' + data.loop_var + ' IN ' + data.collection;
    if (kicker) {
      if (data.parallel) kicker.textContent = 'parallel';
      else kicker.remove();
    }
    while (host.firstChild) host.removeChild(host.firstChild);
    host.appendChild(node);

    requestAnimationFrame(() => requestAnimationFrame(() => {
      const banner = host.querySelector('.par-banner');
      if (!banner) return;
      const rect = banner.getBoundingClientRect();
      const newW = Math.ceil(rect.width) + 6;
      const newH = Math.ceil(rect.height) + 6;
      const oldW = parseFloat(fo.getAttribute('width')) || 0;
      fo.setAttribute('width', newW);
      fo.setAttribute('height', newH);
      /* Position the banner astride the cluster's top border (fieldset
       * legend style) so it doesn't overlap inner cards. The label-group's
       * x is recentered on its original midpoint; y is fixed at -newH/2
       * so half the chip sits above the rect, half below. */
      const parentG = fo.parentElement;
      const tform = parentG && parentG.getAttribute('transform');
      if (tform) {
        const m = tform.match(/translate\\(([^,]+),\\s*([^)]+)\\)/);
        if (m) {
          const tx = parseFloat(m[1]) - (newW - oldW) / 2;
          const ty = -newH / 2;
          parentG.setAttribute('transform', 'translate(' + tx + ', ' + ty + ')');
        }
      }
    }));
  });
}

window.addEventListener('load', () => {
  setTimeout(injectForeachBanners, 300);
});
</script>
</body>
</html>
"""


def to_html(graph: FlowGraph) -> str:
    """Render a FlowGraph as a single self-contained HTML viewer.

    The page embeds a Mermaid source where every node is rendered as a rich
    Tabloid-style HTML card (icon, name, kicker, key/val meta footer), plus
    a per-step JSON catalog and a click handler that populates a side panel
    with TAKES / GIVES / mode / referenced contracts / cache / on_fail /
    impl / invoke for the selected step. No build step, no server — open
    the HTML in any browser with internet access (mermaid.js + Geist fonts
    are loaded from CDN).
    """
    (
        mermaid_source,
        foreach_meta,
        if_meta,
        match_meta,
        while_meta,
        rescue_meta,
    ) = _to_mermaid_rich_labels(graph)
    mermaid_source = mermaid_source.rstrip("\n")
    steps_data = {s.name: _step_to_dict(s) for s in graph.steps}
    contracts_data = {c.name: _contract_to_dict(c) for c in graph.contracts}

    flow_name = graph.flow.name if graph.flow is not None else "(no FLOW)"
    n_steps = len(graph.steps)
    n_contracts = len(graph.contracts)

    target = graph.resources.target if graph.resources is not None else None

    counts_parts: list[str] = []
    if n_contracts:
        counts_parts.append(f"{n_contracts} contract" + ("s" if n_contracts != 1 else ""))
    counts_parts.append(f"{n_steps} step" + ("s" if n_steps != 1 else ""))
    counts = " · ".join(counts_parts)

    subtitle_parts = list(counts_parts)
    if target is not None:
        subtitle_parts.insert(0, f"target: {target}")
    subtitle = " · ".join(subtitle_parts)

    return (
        _HTML_TEMPLATE
        .replace("__TITLE__", flow_name)
        .replace("__STEPS_JSON__", json.dumps(steps_data, ensure_ascii=False))
        .replace("__CONTRACTS_JSON__", json.dumps(contracts_data, ensure_ascii=False))
        .replace("__TITLE_JSON__", json.dumps(flow_name, ensure_ascii=False))
        .replace("__SUBTITLE_JSON__", json.dumps(subtitle, ensure_ascii=False))
        .replace("__TARGET_JSON__", json.dumps(target, ensure_ascii=False))
        .replace("__COUNTS_JSON__", json.dumps(counts, ensure_ascii=False))
        .replace("__MERMAID_JSON__", json.dumps(mermaid_source, ensure_ascii=False))
        .replace("__FOREACH_META_JSON__", json.dumps(foreach_meta, ensure_ascii=False))
        .replace("__IF_META_JSON__", json.dumps(if_meta, ensure_ascii=False))
        .replace("__MATCH_META_JSON__", json.dumps(match_meta, ensure_ascii=False))
        .replace("__WHILE_META_JSON__", json.dumps(while_meta, ensure_ascii=False))
        .replace("__RESCUE_META_JSON__", json.dumps(rescue_meta, ensure_ascii=False))
    )
