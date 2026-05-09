"""Tests for MATCH / CASE / DEFAULT parsing, IR build, and emission."""
from __future__ import annotations

import pytest

from clio.ir.builder import IRBuildError, build_ir
from clio.parser.parser import ParseError, parse


_BASE_DECLS = (
    "CONTRACT classification\n"
    "  SHAPE: {category: enum(spam|support|sales), confidence: float}\n"
    "\n"
    "CONTRACT routing_decision\n"
    "  SHAPE: {dest: str(max=40)}\n"
    "\n"
    "STEP classify\n"
    "  TAKES: email: str\n"
    "  GIVES: report: classification\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP archive\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP route_support\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP route_sales\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
    "\n"
    "STEP route_general\n"
    "  TAKES: report: classification\n"
    "  GIVES: decision: routing_decision\n"
    "  MODE:  judgment\n"
)

_FLOW_WITH_DEFAULT = (
    'FLOW main\n'
    '    classify(email="hi")\n'
    '    -> MATCH report.category:\n'
    '        CASE spam:    archive(report)\n'
    '        CASE support: route_support(report)\n'
    '        CASE sales:   route_sales(report)\n'
    '        DEFAULT:      route_general(report)\n'
)


# ----- Parser ----------------------------------------------------------------


def test_parser_recognises_match_in_flow():
    program = parse(_BASE_DECLS + _FLOW_WITH_DEFAULT)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    assert [type(x).__name__ for x in flow.chain] == ["StepCall", "MatchBlock"]


def test_parser_match_scrutinee_and_arms():
    program = parse(_BASE_DECLS + _FLOW_WITH_DEFAULT)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    mb = flow.chain[1]
    assert mb.scrutinee.step_name == "report"
    assert mb.scrutinee.field == "category"
    arm_values = [c.value for c in mb.cases]
    assert arm_values == ["spam", "support", "sales", None]


def test_parser_default_must_come_last():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> MATCH report.category:\n'
        '        DEFAULT:      route_general(report)\n'
        '        CASE spam:    archive(report)\n'
    )
    with pytest.raises(ParseError, match="CASE arm must come before DEFAULT"):
        parse(bad)


def test_parser_match_requires_at_least_one_case():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> MATCH report.category:\n'
    )
    with pytest.raises(ParseError):
        parse(bad)


# ----- IR builder ------------------------------------------------------------


def test_ir_builder_produces_match_block_ir():
    graph = build_ir(parse(_BASE_DECLS + _FLOW_WITH_DEFAULT))
    mir = graph.flow.chain[1]
    assert mir.__class__.__name__ == "MatchBlockIR"
    assert mir.state_field == "report"
    assert mir.sub_field == "category"
    assert [c.value for c in mir.cases] == ["spam", "support", "sales", None]


def test_ir_builder_rejects_unknown_enum_variant():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> MATCH report.category:\n'
        '        CASE wat:     archive(report)\n'
        '        DEFAULT:      route_general(report)\n'
    )
    with pytest.raises(IRBuildError, match="not one of the enum variants"):
        build_ir(parse(bad))


def test_ir_builder_rejects_match_on_non_enum_field():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> MATCH report.confidence:\n'
        '        CASE high: archive(report)\n'
        '        DEFAULT:   route_general(report)\n'
    )
    with pytest.raises(IRBuildError, match="requires that field to be an enum"):
        build_ir(parse(bad))


def test_ir_builder_rejects_duplicate_case():
    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> MATCH report.category:\n'
        '        CASE spam: archive(report)\n'
        '        CASE spam: route_support(report)\n'
        '        DEFAULT:   route_general(report)\n'
    )
    with pytest.raises(IRBuildError, match="duplicate CASE"):
        build_ir(parse(bad))


# ----- Python emitter --------------------------------------------------------


def test_python_emitter_emits_match(tmp_path):
    from clio.emitters.python import PythonEmitter

    graph = build_ir(parse(_BASE_DECLS + _FLOW_WITH_DEFAULT))
    PythonEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "main" / "flow.py").read_text()
    assert "match state['report'].category:" in flow_py
    assert "case 'spam':" in flow_py
    assert "case 'support':" in flow_py
    assert "case 'sales':" in flow_py
    assert "case _:" in flow_py


# ----- mcp-server emitter ----------------------------------------------------


def test_mcp_server_emitter_emits_match(tmp_path):
    from clio.emitters.mcp_server import MCPServerEmitter

    graph = build_ir(parse(_BASE_DECLS + _FLOW_WITH_DEFAULT))
    MCPServerEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "main" / "flow.py").read_text()
    assert "match state['report'].category:" in flow_py
    assert "case _:" in flow_py
    assert "await archive_mod.archive(" in flow_py


# ----- LangGraph emitter -----------------------------------------------------


def test_langgraph_emitter_emits_match_router_and_conditional_edges(tmp_path):
    from clio.emitters.langgraph import LangGraphEmitter

    graph = build_ir(parse(_BASE_DECLS + _FLOW_WITH_DEFAULT))
    LangGraphEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "main" / "flow.py").read_text()
    assert "def _match_report_category(state: State) -> str:" in flow_py
    assert "_scrutinee = state['report'].category" in flow_py
    assert "if _scrutinee == 'spam':" in flow_py
    assert "return 'route_general'" in flow_py
    assert "workflow.add_conditional_edges('classify', _match_report_category" in flow_py


def test_langgraph_emitter_rejects_match_without_default(tmp_path):
    from clio.emitters.langgraph import LangGraphEmitter

    bad = (
        _BASE_DECLS
        + 'FLOW main\n'
        '    classify(email="hi")\n'
        '    -> MATCH report.category:\n'
        '        CASE spam:    archive(report)\n'
        '        CASE support: route_support(report)\n'
        '        CASE sales:   route_sales(report)\n'
    )
    graph = build_ir(parse(bad))
    with pytest.raises(ValueError, match="DEFAULT"):
        LangGraphEmitter().emit(graph, tmp_path)


# ----- Graph render ----------------------------------------------------------


def test_html_viewer_exposes_match_meta_and_diamond():
    from clio.graph_render import to_html

    graph = build_ir(parse(_BASE_DECLS + _FLOW_WITH_DEFAULT))
    html = to_html(graph)
    assert '"match_1": {"state_field": "report"' in html
    assert 'match_1{\\"MATCH report.category\\"}' in html
    assert 'match_1 -- \\"spam\\" --> archive' in html
    assert 'match_1 -- \\"default\\" --> route_general' in html
