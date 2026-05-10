import json
import re

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


def test_html_step_exposes_mode_class_and_kicker():
    """Each step in STEPS gets a mode_class (judgment | exact-shell | exact-rest |
    exact-code) used to colour the panel + node card, and a kicker (next-level
    detail like 'cli', 'haiku', 'cat', 'GET', 'python') shown beside the icon."""
    src = (
        "STEP detect\n  TAKES: text: str\n  GIVES: topic: str\n  MODE: judgment\n"
        "STEP load\n"
        "  TAKES: file: str\n  GIVES: lines: List<str>\n  MODE: exact\n"
        "  impl:\n    mode: shell\n    cmd: \"cat ${file}\"\n"
    )
    out = _html(src)
    steps = _extract_js_const(out, "STEPS")
    # judgment with no invoke block defaults to 'cli' (Claude CLI)
    assert steps["detect"]["mode_class"] == "judgment"
    assert steps["detect"]["kicker"] == "cli"
    # exact + impl.shell → 'exact-shell' class, kicker = first argv token
    assert steps["load"]["mode_class"] == "exact-shell"
    assert steps["load"]["kicker"] == "cat"


def test_html_kicker_extracts_model_nickname():
    """For judgment + invoke.api with a known provider model, the kicker is
    the model's nickname (haiku, sonnet, gpt-4o, ...) — much more useful than
    repeating 'LLM' or the full model id."""
    src = (
        "STEP classify\n"
        "  TAKES: text: str\n  GIVES: label: str\n  MODE: judgment\n"
        "  invoke:\n    mode: api\n    protocol: anthropic\n    model: \"claude-haiku-4-5\"\n"
    )
    out = _html(src)
    steps = _extract_js_const(out, "STEPS")
    assert steps["classify"]["kicker"] == "haiku"


def test_html_mermaid_source_uses_rich_html_labels():
    """The HTML viewer's Mermaid source carries a Tabloid-style rich card per
    node, so the graph itself shows mode/icon/meta even before the user clicks.
    Vanilla `to_mermaid()` (used for --format mermaid on GitHub) is unchanged."""
    out = _html(_FLOW_SRC)
    mermaid_src = _extract_js_const(out, "MERMAID_SRC")
    # Each step is rendered as a div.node-card with its mode class.
    assert "<div class='node-card judgment'>" in mermaid_src
    assert "<div class='node-card exact-code'>" in mermaid_src
    # Step name appears inside a span.name (HTML-escaped form)
    assert "<span class='name'>detect_topic</span>" in mermaid_src
    assert "<span class='name'>summarize</span>" in mermaid_src
    # Vanilla Mermaid source (no rich labels) used for --format mermaid is
    # untouched: should still produce the simple "name<br/>mode" labels.
    vanilla = _mermaid(_FLOW_SRC)
    assert "<div class='node-card" not in vanilla
    assert "detect_topic<br/>judgment" in vanilla


def test_html_panel_has_mode_class_hooks():
    """The panel applies a class derived from STEPS[name].mode_class so the
    CSS can theme borders/text per mode without baking the mode into HTML."""
    out = _html(_FLOW_SRC)
    # The JS uses PANEL_MODE_CLASSES to add/remove panel mode classes.
    assert "PANEL_MODE_CLASSES" in out
    # The mode tokens themselves appear in the CSS rules.
    for cls in ("judgment", "exact-shell", "exact-rest", "exact-code"):
        assert f".v-panel.{cls}" in out
        assert f".node-card.{cls}" in out


# --------------------------------------------------------------------------
# RESCUE viewer cluster (mermaid + html)
# --------------------------------------------------------------------------


_RESCUE_VIEWER_SRC = (
    "STEP a\n"
    "  TAKES: x: int\n"
    "  GIVES: y: int\n"
    "  MODE:  exact\n"
    "FLOW p\n"
    "  a(x=1)\n"
    "\n"
    "  RESCUE a:\n"
    '    -> abort("boom")\n'
)


def test_mermaid_renders_rescue_cluster():
    """Mermaid output for a flow with a RESCUE block must contain a rescue
    node, a dotted 'fails' edge from the protected step, and an abort node."""
    out = _html(_RESCUE_VIEWER_SRC)
    # The HTML viewer embeds the mermaid source inside a JSON string; we
    # extract it and assert against the rich-label mermaid produced by
    # `_to_mermaid_rich_labels` (the same source `to_html` uses).
    mermaid_src = _extract_js_const(out, "MERMAID_SRC")
    assert isinstance(mermaid_src, str)
    # The rescue node id appears.
    assert "rescue_a" in mermaid_src
    # Dotted edge from the protected step `a` to `rescue_a` with label "fails".
    assert "a -. fails .-> rescue_a" in mermaid_src
    # Class for red accent.
    assert "classDef rescueClass" in mermaid_src
    # Abort node renders with the message.
    assert "abort_a" in mermaid_src
    assert "boom" in mermaid_src


def test_html_exposes_rescue_meta():
    """HTML viewer must expose rescue_meta to the JS via the placeholder."""
    out = _html(_RESCUE_VIEWER_SRC)
    # The JS const declaration is present.
    assert "const RESCUE_META =" in out
    # The placeholder is replaced with a JSON object containing the rescue.
    rescue_meta = _extract_js_const(out, "RESCUE_META")
    assert "rescue_a" in rescue_meta
    entry = rescue_meta["rescue_a"]
    assert entry["step_name"] == "a"
    body = entry["body"]
    assert any(item.get("step_name") == "abort" and item.get("message") == "boom"
               for item in body)


# --------------------------------------------------------------------------
# Replay UI (drag-drop events.jsonl)
# --------------------------------------------------------------------------


def test_html_emits_replay_dropzone_in_toolbar():
    """The toolbar must contain a labelled drop target with a hidden file
    input so users can pick or drop an events.jsonl trace."""
    out = _html(_FLOW_SRC)
    assert 'id="replay-drop"' in out
    assert 'id="replay-file"' in out
    assert 'accept=".jsonl' in out
    assert 'Drop events.jsonl' in out


def test_html_emits_replay_control_bar_hidden_by_default():
    """The control bar starts hidden (display: none in CSS) and exposes
    play/prev/next/restart, a speed slider (0.1x .. 10x, default 2x), a
    progress strip, a follow checkbox, and a stats area."""
    out = _html(_FLOW_SRC)
    assert 'id="replay-bar"' in out
    assert 'class="replay-bar"' in out
    # Controls
    for elem_id in (
        "replay-play", "replay-prev", "replay-next", "replay-restart",
        "replay-speed", "replay-speed-value", "replay-follow",
        "replay-progress-text", "replay-fill", "replay-current-step",
        "replay-stats",
    ):
        assert f'id="{elem_id}"' in out, f"missing replay element id={elem_id!r}"
    # Speed slider attributes (range, default, step)
    assert 'min="0.1"' in out
    assert 'max="10"' in out
    assert 'value="2"' in out


def test_html_replay_css_classes_present():
    """CSS rules for active/done/fail node states must be present so the
    JS can drive node decoration via classList."""
    out = _html(_FLOW_SRC)
    assert "g.node.replay-active" in out
    assert "g.node.replay-done" in out
    assert "g.node.replay-fail" in out
    assert "@keyframes clio-replay-pulse" in out


def test_html_replay_js_engine_is_embedded():
    """The Replay JS module is embedded with all the expected entry points
    (load, render, step, play, restart, setSpeed, setFollow,
    noteManualSelection)."""
    out = _html(_FLOW_SRC)
    assert "const Replay =" in out
    for entry in (
        "load(text)",
        "render()",
        "step(delta)",
        "play()",
        "pause()",
        "restart()",
        "setSpeed(v)",
        "setFollow(on)",
        "noteManualSelection()",
    ):
        assert entry in out, f"missing Replay entry point: {entry!r}"


def test_html_replay_handles_step_start_step_end_events():
    """The render() loop classifies node states from `step_start` /
    `step_end` events with the `success` field."""
    out = _html(_FLOW_SRC)
    assert "'step_start'" in out
    assert "'step_end'" in out
    assert "success === false" in out


def test_html_replay_disables_autofollow_on_manual_click():
    """When the user clicks a node directly during a replay, the engine
    must stop fighting them by disabling auto-follow until restart.
    This is implemented by wrapping `activateNode`."""
    out = _html(_FLOW_SRC)
    assert "Replay.noteManualSelection()" in out
    assert "_origActivateNode" in out
