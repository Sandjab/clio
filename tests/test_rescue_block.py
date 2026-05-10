"""Tests for the RESCUE primitive (top-level handler attached to a STEP).
See docs/superpowers/specs/2026-05-10-rescue-handler-design.md."""

import pytest

from clio.ir.builder import IRBuildError, build_ir
from clio.ir.graph import RescueBlockIR
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
    -> detect(data=data)

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


def test_build_ir_single_rescue():
    """build_ir must produce a RescueBlockIR in flow.rescues."""
    program = _parse(SINGLE_RESCUE_SRC)
    graph = build_ir(program)
    assert graph.flow is not None
    assert len(graph.flow.rescues) == 1
    rb = graph.flow.rescues[0]
    assert isinstance(rb, RescueBlockIR)
    assert rb.step_name == "detect"
    assert len(rb.body) == 1
    call = rb.body[0]
    assert call.step_name == "abort"
    assert call.kwargs == (("message", "detection failed"),)


# ---------------------------------------------------------------------------
# IR validation rules on RescueBlockIR (Task 4)
# ---------------------------------------------------------------------------

_BASE_STEPS = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

STEP b
  TAKES: y: int
  GIVES: z: int
  MODE:  exact
"""


def _src(flow_body: str) -> str:
    return _BASE_STEPS + "\nFLOW p\n" + flow_body


# (1) RESCUE for unknown step
def test_rescue_unknown_step():
    src = _src("  a(x=1)\n\n  RESCUE inexistant:\n    -> abort(\"x\")\n")
    with pytest.raises(IRBuildError, match="unknown step 'inexistant'"):
        build_ir(_parse(src))


# (2) RESCUE for nested step (top-level only)
def test_rescue_nested_step_rejected():
    """Step 'a' lives inside FOR EACH; RESCUE must reject this in v0.8.

    The FOR EACH iterates over `values: List<int>` produced by a
    preceding `source` step so the chain is otherwise valid — the only
    expected failure is the rescue top-level-only rule.
    """
    src = (
        _BASE_STEPS
        + "\nSTEP source\n"
        + "  TAKES: x: int\n"
        + "  GIVES: values: List<int>\n"
        + "  MODE:  exact\n"
        + "\nFLOW p\n"
        + "  source(x=1)\n"
        + "    -> FOR EACH item IN values:\n"
        + "      a(x=item)\n"
        + "\n  RESCUE a:\n    -> abort(\"x\")\n"
    )
    with pytest.raises(IRBuildError, match="must appear in the top-level FLOW chain"):
        build_ir(_parse(src))


# (3) Duplicate RESCUE
def test_duplicate_rescue_rejected():
    src = _src(
        "  a(x=1) -> b(y=y)\n\n"
        "  RESCUE a:\n    -> abort(\"x\")\n\n"
        "  RESCUE a:\n    -> abort(\"y\")\n"
    )
    with pytest.raises(IRBuildError, match="already has a RESCUE handler"):
        build_ir(_parse(src))


# (4) ON_FAIL.abort + RESCUE conflict
def test_rescue_with_on_fail_abort_rejected():
    src = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  judgment
  ON_FAIL: abort("aborted in on_fail")

FLOW p
  a(x=1)

  RESCUE a:
    -> abort("from rescue")
"""
    with pytest.raises(IRBuildError, match="redundant when RESCUE"):
        build_ir(_parse(src))


# (5) Rescue body without terminal abort
def test_rescue_body_must_end_with_abort():
    """Rescue body's last top-level item must be a call to abort."""
    src = _src(
        "  a(x=1)\n\n"
        "  RESCUE a:\n"
        "    -> b(y=y)\n"  # body ends with `b`, not `abort`
    )
    with pytest.raises(IRBuildError, match="must end with abort"):
        build_ir(_parse(src))


# (5b) Rescue body with abort only in branches (not at top level)
def test_rescue_abort_in_branch_not_enough():
    """An IF/ELSE block at the rescue body's tail counts as a non-abort
    top-level item even if every branch terminates with abort.
    """
    src = """
CONTRACT report
  SHAPE: { ok: bool }

STEP a
  TAKES: x: int
  GIVES: result: report
  MODE:  exact

FLOW p
  a(x=1)

  RESCUE a:
    IF result.ok == true:
      abort("ok-branch")
    ELSE:
      abort("ko-branch")
"""
    with pytest.raises(IRBuildError, match="must end with abort"):
        build_ir(_parse(src))


# (6) abort outside a rescue body must be rejected (closes Task 3 deferred passthrough)
def test_abort_outside_rescue_rejected():
    """abort(...) is only valid inside a rescue body. Using it in the
    main FLOW chain (or in any FOR EACH/IF/MATCH/WHILE body OUTSIDE a
    rescue) must be rejected by the IR builder."""
    src = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

FLOW p
  a(x=1)
    -> abort("from main chain")
"""
    with pytest.raises(IRBuildError, match="abort.* only valid inside a RESCUE"):
        build_ir(_parse(src))


def test_walker_descends_into_rescue_body():
    """_validate_parallel_for_each must descend into rescue.body. A nested
    FOR EACH PARALLEL inside a rescue body must be rejected with the same
    nesting-parallel error as anywhere else in the FLOW."""
    src = """
STEP load
  TAKES: x: int
  GIVES: items: List<int>
  MODE:  exact

STEP work
  TAKES: i: int
  GIVES: r: int
  MODE:  exact

FLOW p
  load(x=1)

  RESCUE load:
    -> FOR EACH a IN items PARALLEL AS A:
         FOR EACH b IN items PARALLEL AS B:
           work(i=b)
    -> abort("nested parallel forbidden")
"""
    with pytest.raises(IRBuildError, match="nested inside another"):
        build_ir(_parse(src))
