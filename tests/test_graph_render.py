import json
import re
from pathlib import Path

from clio.graph_render import to_dot, to_html, to_mermaid
from clio.ir.builder import build_ir
from clio.parser.parser import parse


_FLOW_SRC = (
    "STEP detect_topic\n"
    "  TAKES: text: str\n"
    "  GIVES: topic: str\n"
    "  MODE:  judgment\n"
    "STEP summarize\n"
    "  TAKES: topic: str\n"
    "  GIVES: summary: str\n"
    "  MODE:  exact\n"
    "FLOW classify\n"
    '  detect_topic(text="hello")\n'
    "    -> summarize(topic)\n"
)


_FOREACH_SRC = (
    "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
    "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
    "STEP report\n  GIVES: r: str\n  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH item IN items:\n"
    "         process(x=item)\n"
    "    -> report()\n"
)


def _mermaid(src: str) -> str:
    return to_mermaid(build_ir(parse(src)))


def _dot(src: str) -> str:
    return to_dot(build_ir(parse(src)))


def test_mermaid_emits_flowchart_header():
    out = _mermaid(_FLOW_SRC)
    assert out.startswith("flowchart TD\n")


def test_mermaid_distinguishes_exact_and_judgment():
    out = _mermaid(_FLOW_SRC)
    assert 'detect_topic[/"detect_topic<br/>judgment"/]:::judgment' in out
    assert 'summarize["summarize<br/>exact"]:::exact' in out


def test_mermaid_renders_chain_edges():
    out = _mermaid(_FLOW_SRC)
    assert "detect_topic --> summarize" in out


def test_mermaid_renders_classdefs():
    out = _mermaid(_FLOW_SRC)
    assert "classDef judgment" in out
    assert "classDef exact" in out


def test_mermaid_renders_for_each_as_subgraph():
    out = _mermaid(_FOREACH_SRC)
    assert 'subgraph foreach_1["FOR EACH item IN items"]' in out
    assert "load --> foreach_1" in out
    assert "foreach_1 --> report" in out
    # body step is declared inside the subgraph
    sg_start = out.index('subgraph foreach_1["FOR EACH item IN items"]')
    sg_end = out.index("end", sg_start)
    assert "process" in out[sg_start:sg_end]


def test_mermaid_no_flow_emits_isolated_nodes():
    src = "STEP a\n  MODE: exact\nSTEP b\n  MODE: judgment\n"
    out = _mermaid(src)
    assert "flowchart TD" in out
    assert 'a["a<br/>exact"]:::exact' in out
    assert 'b[/"b<br/>judgment"/]:::judgment' in out
    assert "-->" not in out


def test_dot_emits_digraph_header():
    out = _dot(_FLOW_SRC)
    assert out.startswith("digraph clio {")
    assert out.rstrip().endswith("}")


def test_dot_distinguishes_shapes_by_mode():
    out = _dot(_FLOW_SRC)
    assert 'detect_topic [label="detect_topic\\njudgment", shape=parallelogram];' in out
    assert 'summarize [label="summarize\\nexact", shape=box];' in out
    assert "detect_topic -> summarize;" in out


def test_dot_renders_for_each_as_dashed_edge_label():
    out = _dot(_FOREACH_SRC)
    assert 'load -> process [label="for each item in items", style=dashed];' in out
    assert "process -> report;" in out
    # No cluster machinery
    assert "subgraph cluster" not in out
    assert "lhead=" not in out


def test_cli_graph_prints_mermaid_to_stdout(tmp_path, capsys):
    from clio.cli import main
    src = tmp_path / "f.clio"
    src.write_text(_FLOW_SRC)
    rc = main(["graph", str(src)])
    assert rc == 0
    captured = capsys.readouterr().out
    assert captured.startswith("flowchart TD\n")
    assert "detect_topic --> summarize" in captured


def test_cli_graph_writes_to_output_file(tmp_path):
    from clio.cli import main
    src = tmp_path / "f.clio"
    src.write_text(_FLOW_SRC)
    out = tmp_path / "graph.mmd"
    rc = main(["graph", str(src), "--output", str(out)])
    assert rc == 0
    body = out.read_text()
    assert body.startswith("flowchart TD\n")


def test_cli_graph_dot_format(tmp_path, capsys):
    from clio.cli import main
    src = tmp_path / "f.clio"
    src.write_text(_FLOW_SRC)
    rc = main(["graph", str(src), "--format", "dot"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert captured.startswith("digraph clio {")


def test_cli_graph_missing_source_returns_2(tmp_path, capsys):
    from clio.cli import main
    rc = main(["graph", str(tmp_path / "nope.clio")])
    assert rc == 2
    err = capsys.readouterr().out
    assert "source file not found" in err


_PARALLEL_FOREACH_SRC = (
    "STEP load\n  GIVES: docs: List<str>\n  MODE: exact\n"
    "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH doc IN docs PARALLEL AS labels:\n"
    "         classify(text=doc)\n"
)


def test_graph_mermaid_marks_parallel_for_each():
    """Parallel FOR EACH nodes should be visually distinguished from sequential ones."""
    out = _mermaid(_PARALLEL_FOREACH_SRC)
    assert "[parallel]" in out


def test_graph_dot_marks_parallel_for_each():
    """DOT edge label for parallel FOR EACH should include '[parallel]'."""
    out = _dot(_PARALLEL_FOREACH_SRC)
    assert "[parallel]" in out


# --------------------------------------------------------------------------
# HTML viewer (`to_html`)
# --------------------------------------------------------------------------


def _html(src: str) -> str:
    return to_html(build_ir(parse(src)))


def _extract_js_const(html: str, name: str) -> object:
    """Pull `const NAME = <json>;` from the embedded <script> and parse it."""
    m = re.search(rf"const\s+{re.escape(name)}\s*=\s*(.+?);\s*\n", html)
    assert m is not None, f"const {name} not found in HTML"
    return json.loads(m.group(1))


def test_html_emits_well_formed_document():
    out = _html(_FLOW_SRC)
    assert out.startswith("<!DOCTYPE html>\n")
    assert out.rstrip().endswith("</html>")
    # Single Mermaid <pre>, single <aside id=panel>, single mermaid CDN import.
    assert out.count('<pre class="mermaid"') == 1
    assert out.count('id="panel"') == 1
    assert "cdn.jsdelivr.net/npm/mermaid@" in out


def test_html_embeds_mermaid_source_as_textcontent():
    """The Mermaid source is injected via .textContent (JSON-encoded), not as
    raw HTML inside the <pre> — keeps it XSS-safe and arbitrary-string-safe."""
    out = _html(_FLOW_SRC)
    mermaid_src = _extract_js_const(out, "MERMAID_SRC")
    assert isinstance(mermaid_src, str)
    assert mermaid_src.startswith("flowchart TD")
    assert "detect_topic --> summarize" in mermaid_src


def test_html_steps_catalog_shapes():
    """STEPS exposes per-step name/mode/takes/gives/line/contracts to the panel JS."""
    out = _html(_FLOW_SRC)
    steps = _extract_js_const(out, "STEPS")
    assert set(steps.keys()) == {"detect_topic", "summarize"}
    assert steps["detect_topic"]["mode"] == "judgment"
    assert steps["summarize"]["mode"] == "exact"
    assert steps["detect_topic"]["gives"] == {"name": "topic", "type": "str"}
    assert steps["summarize"]["takes"] == [{"name": "topic", "type": "str"}]


def test_html_contracts_catalog_includes_json_schema_and_assert_flag():
    src = (
        "CONTRACT entity\n"
        "  SHAPE: {name: str, kind: enum(person|other)}\n"
        "  ASSERT: len(name) > 0\n"
        "CONTRACT plain_summary\n"
        "  SHAPE: {total: int}\n"
        "STEP extract\n  TAKES: text: str\n  GIVES: items: List<entity>\n  MODE: judgment\n"
        "STEP digest\n  TAKES: items: List<entity>\n  GIVES: out: plain_summary\n  MODE: exact\n"
        "FLOW pipe\n  extract(text=\"x\") -> digest(items)\n"
    )
    out = _html(src)
    contracts = _extract_js_const(out, "CONTRACTS")
    assert set(contracts.keys()) == {"entity", "plain_summary"}
    assert contracts["entity"]["has_assert"] is True
    assert contracts["plain_summary"]["has_assert"] is False
    # json_schema is the same dict the emitters use → must contain properties.
    assert "properties" in contracts["entity"]["json_schema"]


def test_html_step_collects_referenced_contracts_in_order():
    src = (
        "CONTRACT a\n  SHAPE: {x: int}\n"
        "CONTRACT b\n  SHAPE: {y: int}\n"
        "STEP s\n  TAKES: foo: a\n  GIVES: bar: b\n  MODE: judgment\n"
    )
    out = _html(src)
    steps = _extract_js_const(out, "STEPS")
    assert steps["s"]["contracts"] == ["a", "b"]


def test_html_serializes_cache_and_on_fail_as_clio_surface_strings():
    src = (
        "STEP s\n"
        "  TAKES: text: str\n  GIVES: out: str\n  MODE: judgment\n"
        "  CACHE: ttl(24h)\n"
        "  ON_FAIL: retry(3) then escalate then abort(\"give up\")\n"
    )
    out = _html(src)
    steps = _extract_js_const(out, "STEPS")
    assert steps["s"]["cache"] == "ttl(1d)"
    assert steps["s"]["on_fail"] == 'retry(3) then escalate then abort("give up")'


def test_html_serializes_impl_shell_with_parse_json():
    src = (
        "STEP load\n"
        "  TAKES: file: str\n  GIVES: rows: List<{id: int}>\n  MODE: exact\n"
        "  impl:\n    mode: shell\n    cmd: \"cat ${file}\"\n    parse: json\n"
    )
    out = _html(src)
    steps = _extract_js_const(out, "STEPS")
    impl = steps["load"]["impl"]
    assert impl == {"mode": "shell", "argv": ["cat", "${file}"], "parse": "json"}


def test_html_uses_dom_api_not_innerhtml():
    """Defensive: the panel must populate via DOM API (textContent /
    appendChild), never innerHTML — the injected step/contract names come
    from user .clio files and could include HTML metacharacters."""
    out = _html(_FLOW_SRC)
    assert "innerHTML" not in out


def test_cli_graph_html_format_to_stdout(tmp_path, capsys):
    from clio.cli import main
    src = tmp_path / "f.clio"
    src.write_text(_FLOW_SRC)
    rc = main(["graph", str(src), "--format", "html"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert captured.startswith("<!DOCTYPE html>\n")
    assert "const STEPS" in captured


def test_cli_graph_html_writes_to_output_file(tmp_path):
    from clio.cli import main
    src = tmp_path / "f.clio"
    src.write_text(_FLOW_SRC)
    out = tmp_path / "graph.html"
    rc = main(["graph", str(src), "--format", "html", "--output", str(out)])
    assert rc == 0
    body = out.read_text()
    assert body.startswith("<!DOCTYPE html>\n")
    assert "flowchart TD" in body
