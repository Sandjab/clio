"""Tests for WHILE MAX parsing, IR build, and emission across targets.

LangGraph rejects WHILE at compile time in v0.7 (cyclic edges + state
reducers planned for v0.8). Python and mcp-server emit a bounded
`for _i in range(MAX): if not cond: break; body` loop.
"""
from __future__ import annotations

import pytest

from clio.ir.builder import IRBuildError, build_ir
from clio.parser.parser import ParseError, parse

_BASE_DECLS = (
    "CONTRACT draft_score\n"
    "  SHAPE: {text: str(max=2000), score: float}\n"
    "\n"
    "STEP draft_initial\n"
    "  TAKES: brief: str\n"
    "  GIVES: draft: draft_score\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP refine_draft\n"
    "  TAKES: draft: draft_score\n"
    "  GIVES: draft: draft_score\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP publish\n"
    "  TAKES: draft: draft_score\n"
    "  GIVES: published_id: str\n"
    "  MODE:  exact\n"
)

_BASIC_FLOW = (
    'FLOW main\n'
    '    draft_initial(brief="brief...")\n'
    '    -> WHILE draft.score < 0.9 MAX 3:\n'
    '        refine_draft(draft=draft)\n'
    '    -> publish(draft=draft)\n'
)


# ----- Parser ----------------------------------------------------------------


def test_parser_recognises_while_in_flow():
    program = parse(_BASE_DECLS + _BASIC_FLOW)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    assert [type(x).__name__ for x in flow.chain] == [
        "StepCall", "WhileBlock", "StepCall",
    ]


def test_parser_while_max_iters_required():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    draft_initial(brief="x")\n'
        '    -> WHILE draft.score < 0.9:\n'
        '        refine_draft(draft=draft)\n'
    )
    with pytest.raises(ParseError, match="MAX"):
        parse(bad)


def test_parser_while_max_must_be_positive_int():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    draft_initial(brief="x")\n'
        '    -> WHILE draft.score < 0.9 MAX 0:\n'
        '        refine_draft(draft=draft)\n'
    )
    with pytest.raises(ParseError, match="> 0"):
        parse(bad)


def test_parser_while_rejects_float_max():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    draft_initial(brief="x")\n'
        '    -> WHILE draft.score < 0.9 MAX 3.5:\n'
        '        refine_draft(draft=draft)\n'
    )
    with pytest.raises(ParseError, match="integer"):
        parse(bad)


# ----- IR builder ------------------------------------------------------------


def test_ir_builder_produces_while_block_ir():
    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    wir = graph.flow.chain[1]
    assert wir.__class__.__name__ == "WhileBlockIR"
    assert wir.condition.step_name == "draft"
    assert wir.condition.field == "score"
    assert wir.condition.op == "<"
    assert wir.condition.literal_value == 0.9
    assert wir.max_iters == 3
    assert len(wir.body) == 1


def test_ir_builder_rejects_while_on_primitive_field_via_dot_access():
    """Condition `<state_field>.<sub_field>` requires state_field to be a
    contract; bare primitives (str/int/float) have no nested fields."""
    bad = (
        "STEP load_int\n"
        "  TAKES: x: str\n"
        "  GIVES: counter: int\n"
        "  MODE:  exact\n"
        + 'FLOW main\n'
        '    load_int(x="hi")\n'
        '    -> WHILE counter.foo < 5 MAX 3:\n'
        '        load_int(x="hi")\n'
    )
    with pytest.raises(IRBuildError, match="not a CONTRACT"):
        build_ir(parse(bad))


# ----- Python emitter --------------------------------------------------------


def test_python_emitter_emits_while_loop(tmp_path):
    from clio.emitters.python import PythonEmitter

    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    PythonEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "main" / "flow.py").read_text()
    assert "for _i in range(3):" in flow_py
    assert "if not (state['draft'].score < 0.9):" in flow_py
    assert "break" in flow_py
    assert "state['draft'] = refine_draft_mod.refine_draft(draft=state['draft'])" in flow_py


# ----- mcp-server emitter ----------------------------------------------------


def test_mcp_server_emitter_emits_while_loop(tmp_path):
    from clio.emitters.mcp_server import MCPServerEmitter

    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    MCPServerEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "main" / "flow.py").read_text()
    assert "for _i in range(3):" in flow_py
    assert "if not (state['draft'].score < 0.9):" in flow_py
    assert "await refine_draft_mod.refine_draft(" in flow_py


# ----- LangGraph emitter (must reject) ---------------------------------------


def test_langgraph_emitter_rejects_while(tmp_path):
    from clio.emitters.langgraph import LangGraphEmitter

    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    with pytest.raises(ValueError, match="WHILE is not supported"):
        LangGraphEmitter().emit(graph, tmp_path)


# ----- Graph render ----------------------------------------------------------


def test_html_viewer_exposes_while_meta_and_subgraph():
    from clio.graph_render import to_html

    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    html = to_html(graph)
    assert '"while_1": {"state_field": "draft"' in html
    assert '"max_iters": 3' in html
    assert 'subgraph while_1[\\"WHILE draft.score < 0.9 MAX 3\\"]' in html
