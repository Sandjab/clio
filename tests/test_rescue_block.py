"""Tests for the RESCUE primitive (top-level handler attached to a STEP).
See docs/superpowers/specs/2026-05-10-rescue-handler-design.md."""

from clio.keywords import Keyword
from clio.parser.ast_nodes import FlowDecl, RescueBlock, StepCall


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
