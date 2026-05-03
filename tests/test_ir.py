from clio.parser.parser import parse
from clio.ir.builder import build_ir


def test_build_ir_from_minimal_step():
    program = parse("STEP foo\n  MODE: exact\n")
    graph = build_ir(program)
    assert len(graph.steps) == 1
    step = graph.steps[0]
    assert step.name == "foo"
    assert step.mode == "exact"
