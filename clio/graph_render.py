"""Render a FlowGraph as Mermaid, Graphviz DOT, or a self-contained HTML viewer.

Output is meant to be embedded in GitHub PRs (Mermaid), piped to a graphviz
tool (DOT), or opened in a browser (HTML). None of these emitters writes a
project — each returns one source string per call.
"""
from __future__ import annotations

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
    ImplIR,
    InvokeIR,
    OnFailChainIR,
    RestImplIR,
    ShellImplIR,
    StepIR,
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


def _mermaid_node(step: StepIR) -> str:
    label = f"{step.name}<br/>{step.mode}"
    if step.mode == "judgment":
        return f'{step.name}[/"{label}"/]:::judgment'
    return f'{step.name}["{label}"]:::exact'


def to_mermaid(graph: FlowGraph) -> str:
    """Render a FlowGraph as a Mermaid `flowchart TD` source string.

    EXACT steps render as rectangles, JUDGMENT steps as parallelograms.
    FOR EACH blocks render as labelled subgraphs containing their body;
    edges from a previous step land on the subgraph border.
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

    lines.extend(_MERMAID_CLASSDEFS)
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
<style>
:root { --fg: #1f2933; --muted: #6b7280; --border: #e5e7eb; --bg: #fafafa; --judgment: #0d47a1; --judgment-bg: #e3f2fd; --exact: #bf360c; --exact-bg: #fff3e0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; color: var(--fg); background: white; }
header.top { padding: 16px 24px; border-bottom: 1px solid var(--border); }
header.top h1 { margin: 0; font-size: 18px; font-weight: 600; }
header.top .meta { color: var(--muted); font-size: 12px; margin-top: 4px; }
.layout { display: grid; grid-template-columns: 1fr 380px; min-height: calc(100vh - 56px); }
.graph { padding: 24px; overflow: auto; background: var(--bg); }
.panel { padding: 20px; border-left: 1px solid var(--border); overflow-y: auto; max-height: calc(100vh - 56px); }
.panel.empty { color: var(--muted); font-size: 13px; }
.panel h2 { margin: 0 0 4px 0; font-size: 16px; font-family: "SF Mono", Menlo, Consolas, monospace; }
.panel h3 { margin: 16px 0 6px 0; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); font-weight: 600; }
.panel section { margin-top: 12px; }
.panel ul { list-style: none; padding: 0; margin: 0; }
.panel li { padding: 4px 0; font-size: 13px; }
.panel code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px; background: #f3f4f6; padding: 1px 5px; border-radius: 3px; }
.panel pre { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 11px; background: #f3f4f6; padding: 8px 10px; border-radius: 4px; overflow-x: auto; margin: 6px 0; line-height: 1.5; }
.panel details { margin: 6px 0; }
.panel details > summary { cursor: pointer; font-size: 13px; padding: 4px 0; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; vertical-align: middle; margin-left: 6px; }
.badge.judgment { color: var(--judgment); background: var(--judgment-bg); }
.badge.exact { color: var(--exact); background: var(--exact-bg); }
.empty-hint { color: var(--muted); font-size: 13px; line-height: 1.6; }
g.node { cursor: pointer; }
g.node.selected rect, g.node.selected polygon { stroke-width: 3px !important; }
.line { color: var(--muted); font-size: 11px; }
</style>
</head>
<body>
<header class="top">
  <h1 id="flow-title"></h1>
  <div class="meta" id="flow-subtitle"></div>
</header>
<div class="layout">
  <div class="graph">
    <pre class="mermaid" id="mermaid-src"></pre>
  </div>
  <aside class="panel empty" id="panel"></aside>
</div>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
  mermaid.initialize({ startOnLoad: true, securityLevel: 'loose', flowchart: { htmlLabels: true } });
</script>
<script>
const STEPS = __STEPS_JSON__;
const CONTRACTS = __CONTRACTS_JSON__;
const TITLE = __TITLE_JSON__;
const SUBTITLE = __SUBTITLE_JSON__;
const MERMAID_SRC = __MERMAID_JSON__;

document.getElementById('flow-title').textContent = TITLE;
document.getElementById('flow-subtitle').textContent = SUBTITLE;
document.getElementById('mermaid-src').textContent = MERMAID_SRC;

function el(tag, opts) {
  const n = document.createElement(tag);
  if (opts) {
    if (opts.cls) n.className = opts.cls;
    if (opts.text != null) n.textContent = opts.text;
    if (opts.html != null) {
      // intentionally not used; we never inject HTML strings
    }
    if (opts.children) for (const c of opts.children) if (c) n.appendChild(c);
  }
  return n;
}

function fieldList(items) {
  const ul = el('ul');
  for (const f of items) {
    const li = el('li');
    const nameCode = el('code', { text: f.name });
    const typeCode = el('code', { text: f.type });
    li.appendChild(nameCode);
    li.appendChild(document.createTextNode(' : '));
    li.appendChild(typeCode);
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

function codeLine(text) {
  const div = el('div');
  div.appendChild(el('code', { text: text }));
  return div;
}

function preJson(obj) {
  return el('pre', { text: JSON.stringify(obj, null, 2) });
}

function showStep(name) {
  const s = STEPS[name];
  const panel = document.getElementById('panel');
  panel.replaceChildren();
  if (!s) {
    panel.classList.add('empty');
    panel.appendChild(el('p', { cls: 'empty-hint', text: 'Unknown step: ' + name }));
    return;
  }
  panel.classList.remove('empty');

  const header = el('header');
  header.style.border = 'none';
  header.style.padding = '0';
  const h2 = el('h2', { text: s.name });
  const badge = el('span', { cls: 'badge ' + s.mode, text: s.mode });
  h2.appendChild(badge);
  header.appendChild(h2);
  header.appendChild(el('div', { cls: 'line', text: 'defined at line ' + s.line }));
  panel.appendChild(header);

  if (s.takes && s.takes.length) panel.appendChild(section('TAKES', fieldList(s.takes)));
  if (s.gives) panel.appendChild(section('GIVES', fieldList([s.gives])));
  if (s.cache) panel.appendChild(section('CACHE', codeLine(s.cache)));
  if (s.on_fail) panel.appendChild(section('ON_FAIL', codeLine(s.on_fail)));
  if (s.impl) panel.appendChild(section('IMPL', preJson(s.impl)));
  if (s.invoke) panel.appendChild(section('INVOKE', preJson(s.invoke)));
  if (s.lang) panel.appendChild(section('LANG', codeLine(s.lang)));

  if (s.contracts && s.contracts.length) {
    const sec = el('section');
    sec.appendChild(el('h3', { text: 'CONTRACTS REFERENCED' }));
    for (const cname of s.contracts) {
      const c = CONTRACTS[cname];
      const det = el('details');
      const sum = el('summary');
      sum.appendChild(el('code', { text: cname }));
      if (c && c.has_assert) {
        sum.appendChild(document.createTextNode(' '));
        sum.appendChild(el('span', { cls: 'line', text: '(has ASSERT)' }));
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
  panel.classList.add('empty');
  panel.appendChild(el('p', {
    cls: 'empty-hint',
    text: 'Click a step in the graph to inspect its TAKES / GIVES, mode, contracts, cache, retry policy, and exec details.'
  }));
}

showEmpty();

document.addEventListener('click', (e) => {
  const node = e.target.closest('g.node');
  if (!node) return;
  const id = node.id || '';
  const m = id.match(/^flowchart-(.+?)-\\d+$/);
  if (m) showStep(m[1]);
});
</script>
</body>
</html>
"""


def to_html(graph: FlowGraph) -> str:
    """Render a FlowGraph as a single self-contained HTML viewer.

    The page embeds the Mermaid source (rendered client-side via the
    mermaid.js ESM CDN module), a per-step JSON catalog, and a click
    handler that populates a side panel with TAKES / GIVES / mode /
    referenced contracts / cache / on_fail / impl / invoke for the
    selected step. No build step, no server — open the HTML in any
    browser with internet access.
    """
    mermaid_source = to_mermaid(graph).rstrip("\n")
    steps_data = {s.name: _step_to_dict(s) for s in graph.steps}
    contracts_data = {c.name: _contract_to_dict(c) for c in graph.contracts}

    flow_name = graph.flow.name if graph.flow is not None else "(no FLOW)"
    n_steps = len(graph.steps)
    n_contracts = len(graph.contracts)
    subtitle_parts = [f"{n_steps} step" + ("s" if n_steps != 1 else "")]
    if n_contracts:
        subtitle_parts.append(f"{n_contracts} contract" + ("s" if n_contracts != 1 else ""))
    if graph.resources is not None:
        subtitle_parts.append(f"target: {graph.resources.target}")
    subtitle = " · ".join(subtitle_parts)

    return (
        _HTML_TEMPLATE
        .replace("__TITLE__", flow_name)
        .replace("__STEPS_JSON__", json.dumps(steps_data, ensure_ascii=False))
        .replace("__CONTRACTS_JSON__", json.dumps(contracts_data, ensure_ascii=False))
        .replace("__TITLE_JSON__", json.dumps(flow_name, ensure_ascii=False))
        .replace("__SUBTITLE_JSON__", json.dumps(subtitle, ensure_ascii=False))
        .replace("__MERMAID_JSON__", json.dumps(mermaid_source, ensure_ascii=False))
    )
