"""Tests for the RESCUE primitive (top-level handler attached to a STEP).
See docs/superpowers/specs/2026-05-10-rescue-handler-design.md."""

from clio.keywords import Keyword
from clio.parser.ast_nodes import FlowDecl, RescueBlock, StepCall
from clio.parser.parser import parse


def _parse(src: str):
    return parse(src)


def test_rescue_keyword_present():
    """RESCUE must be registered as a closed keyword of the lexer."""
    assert Keyword.RESCUE.value == "RESCUE"


def test_rescue_block_ast_shape():
    """RescueBlock must be a frozen dataclass with step_name / body / line / col."""
    rb = RescueBlock(
        step_name="detect_churn",
        body=(StepCall(name="abort", kwargs=(("message", "boom"),), line=2, col=2),),
        line=1, col=0,
    )
    assert rb.step_name == "detect_churn"
    assert len(rb.body) == 1
    assert rb.line == 1


def test_flow_decl_has_rescues_field():
    """FlowDecl must accept a rescues tuple (empty default allowed)."""
    fd = FlowDecl(name="f", chain=(), rescues=(), line=1, col=0)
    assert fd.rescues == ()


SINGLE_RESCUE_SRC = """
STEP load
  TAKES: path: str
  GIVES: data: List<int>
  MODE:  exact

STEP detect
  TAKES: data: List<int>
  GIVES: result: int
  MODE:  exact

FLOW pipeline
  load(path="x.csv")
    -> detect(data=load)

  RESCUE detect:
    -> abort("detection failed")
"""


def test_parse_single_rescue_block():
    """RESCUE after the main chain must produce a RescueBlock in flow.rescues."""
    program = _parse(SINGLE_RESCUE_SRC)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    assert len(flow.chain) == 2  # load -> detect
    assert len(flow.rescues) == 1
    rb = flow.rescues[0]
    assert rb.step_name == "detect"
    assert len(rb.body) == 1
    abort_call = rb.body[0]
    assert abort_call.name == "abort"
    assert abort_call.kwargs == (("message", "detection failed"),)


TWO_RESCUES_SRC = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

STEP b
  TAKES: y: int
  GIVES: z: int
  MODE:  exact

FLOW pipe
  a(x=1) -> b(y=a)

  RESCUE a:
    -> abort("a failed")

  RESCUE b:
    -> abort("b failed")
"""


def test_parse_multiple_rescues():
    """Multiple RESCUE blocks (one per STEP) all collected into flow.rescues."""
    program = _parse(TWO_RESCUES_SRC)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    assert len(flow.rescues) == 2
    assert {r.step_name for r in flow.rescues} == {"a", "b"}


RESCUE_BEFORE_RESOURCES_SRC = """
STEP s
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

FLOW p
  s(x=1)

  RESCUE s:
    -> abort("boom")

RESOURCES
  target: python
"""


def test_rescue_compatible_with_resources():
    """The RESCUE collection loop must NOT consume the DEDENT that lets RESOURCES parse next."""
    program = _parse(RESCUE_BEFORE_RESOURCES_SRC)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    res = next(d for d in program.decls if d.__class__.__name__ == "ResourcesDecl")
    assert len(flow.rescues) == 1
    assert res.target == "python"
