"""Tests for IF / ELSE parsing, IR build, and emission across targets."""
from __future__ import annotations

import pytest

from clio.ir.builder import IRBuildError, build_ir
from clio.parser.parser import ParseError, parse


_BASE_DECLS = (
    "CONTRACT classification\n"
    "  SHAPE: {category: str(max=20), confidence: float}\n"
    "\n"
    "CONTRACT routing_decision\n"
    "  SHAPE: {dest: str(max=40)}\n"
    "\n"
    "STEP classify\n"
    "  TAKES: email: str\n"
    "  GIVES: report: classification\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP human_review\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP auto_route\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
)

_BASIC_FLOW = (
    'FLOW main\n'
    '    classify(email="hi")\n'
    '    -> IF report.confidence < 0.7:\n'
    '        human_review(report)\n'
    '    ELSE:\n'
    '        auto_route(report)\n'
)


# ----- Parser ----------------------------------------------------------------


def test_parser_recognises_if_else_in_flow():
    program = parse(_BASE_DECLS + _BASIC_FLOW)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    assert [type(x).__name__ for x in flow.chain] == ["StepCall", "IfBlock"]


def test_parser_if_condition_compare_expr_shape():
    program = parse(_BASE_DECLS + _BASIC_FLOW)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    ifb = flow.chain[1]
    assert ifb.condition.op == "<"
    assert ifb.condition.left.step_name == "report"
    assert ifb.condition.left.field == "confidence"
    assert ifb.condition.right.value == 0.7


def test_parser_if_branch_bodies_are_step_calls():
    program = parse(_BASE_DECLS + _BASIC_FLOW)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    ifb = flow.chain[1]
    assert [c.name for c in ifb.then_body] == ["human_review"]
    assert [c.name for c in ifb.else_body] == ["auto_route"]


def test_parser_if_without_else_has_empty_else_tuple():
    src = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> IF report.confidence < 0.7:\n'
        '        human_review(report)\n'
    )
    program = parse(src)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    ifb = flow.chain[1]
    assert ifb.else_body == ()


def test_parser_rejects_condition_missing_op():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> IF report.confidence:\n'
        '        human_review(report)\n'
    )
    with pytest.raises(ParseError):
        parse(bad)


def test_parser_rejects_condition_missing_dot():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> IF report < 0.7:\n'
        '        human_review(report)\n'
    )
    with pytest.raises(ParseError):
        parse(bad)


# ----- IR builder ------------------------------------------------------------


def test_ir_builder_produces_if_block_ir():
    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    ifir = graph.flow.chain[1]
    assert ifir.__class__.__name__ == "IfBlockIR"
    assert ifir.condition.step_name == "report"
    assert ifir.condition.field == "confidence"
    assert ifir.condition.op == "<"
    assert ifir.condition.literal_value == 0.7
    assert ifir.condition.literal_kind == "float"


def test_ir_builder_rejects_unknown_state_field():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> IF mystery.confidence < 0.7:\n'
        '        human_review(report)\n'
        '    ELSE:\n'
        '        auto_route(report)\n'
    )
    with pytest.raises(IRBuildError, match="not produced by any previous step"):
        build_ir(parse(bad))


def test_ir_builder_rejects_unknown_sub_field():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> IF report.no_such_field < 0.7:\n'
        '        human_review(report)\n'
        '    ELSE:\n'
        '        auto_route(report)\n'
    )
    with pytest.raises(IRBuildError, match="no field"):
        build_ir(parse(bad))


# ----- Python emitter --------------------------------------------------------


def test_python_emitter_emits_if_else(tmp_path):
    from clio.emitters.python import PythonEmitter

    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    PythonEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "main" / "flow.py").read_text()
    assert "if state['report'].confidence < 0.7:" in flow_py
    assert "human_review_mod.human_review(report=state['report'])" in flow_py
    assert "else:" in flow_py
    assert "auto_route_mod.auto_route(report=state['report'])" in flow_py


# ----- mcp-server emitter ----------------------------------------------------


def test_mcp_server_emitter_emits_if_else(tmp_path):
    from clio.emitters.mcp_server import MCPServerEmitter

    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    MCPServerEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "main" / "flow.py").read_text()
    assert "if state['report'].confidence < 0.7:" in flow_py
    assert "await human_review_mod.human_review(" in flow_py
    assert "await auto_route_mod.auto_route(" in flow_py


# ----- LangGraph emitter -----------------------------------------------------


def test_langgraph_emitter_emits_router_and_conditional_edges(tmp_path):
    from clio.emitters.langgraph import LangGraphEmitter

    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    LangGraphEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "main" / "flow.py").read_text()
    assert "def _route_to_human_review_or_auto_route(state: State) -> str:" in flow_py
    assert "if state['report'].confidence < 0.7:" in flow_py
    assert "return 'human_review'" in flow_py
    assert "return 'auto_route'" in flow_py
    assert "workflow.add_conditional_edges('classify', _route_to_human_review_or_auto_route" in flow_py


def test_langgraph_emitter_rejects_if_without_else(tmp_path):
    from clio.emitters.langgraph import LangGraphEmitter

    src = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> IF report.confidence < 0.7:\n'
        '        human_review(report)\n'
    )
    graph = build_ir(parse(src))
    with pytest.raises(ValueError, match="ELSE"):
        LangGraphEmitter().emit(graph, tmp_path)


def test_langgraph_emitter_rejects_multi_step_branch(tmp_path):
    from clio.emitters.langgraph import LangGraphEmitter

    src = (
        _BASE_DECLS
        + "STEP archive\n"
        "  TAKES: report: classification\n"
        "  GIVES: decision: routing_decision\n"
        "  MODE:  judgment\n"
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> IF report.confidence < 0.7:\n'
        '        human_review(report)\n'
        '        -> archive(report)\n'
        '    ELSE:\n'
        '        auto_route(report)\n'
    )
    graph = build_ir(parse(src))
    with pytest.raises(ValueError, match="exactly one step call"):
        LangGraphEmitter().emit(graph, tmp_path)


# ----- Graph render ----------------------------------------------------------


def test_html_viewer_exposes_if_meta_and_decision_diamond():
    from clio.graph_render import to_html

    graph = build_ir(parse(_BASE_DECLS + _BASIC_FLOW))
    html = to_html(graph)
    assert '"if_1": {"state_field": "report"' in html
    # Mermaid decision diamond uses the {"..."} syntax
    assert 'if_1{\\"IF report.confidence < 0.7\\"}' in html
    assert 'if_1 -- \\"yes\\" --> human_review' in html
    assert 'if_1 -- \\"no\\" --> auto_route' in html
