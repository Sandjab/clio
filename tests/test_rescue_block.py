"""Tests for the RESCUE primitive (top-level handler attached to a STEP).
See docs/superpowers/specs/2026-05-10-rescue-handler-design.md."""

import pytest

from clio.ir.builder import IRBuildError, build_ir
from clio.ir.graph import ErrorAccessIR, RescueBlockIR, ResumeIR
from clio.keywords import Keyword
from clio.parser.ast_nodes import ErrorAccessExpr, FlowDecl, RescueBlock, ResumeAst, StepCall
from clio.parser.parser import parse


def _parse(src: str):
    return parse(src)


def test_rescue_keyword_present():
    """RESCUE must be registered as a closed keyword of the lexer."""
    assert Keyword.RESCUE.value == "RESCUE"


def test_resume_keyword_present():
    """RESUME must be registered as a closed keyword of the lexer."""
    assert Keyword.RESUME.value == "RESUME"


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
    """Rescue body's last top-level item must be a call to abort.

    Rescue is on `b` (so `a.gives = y` is in scope before `b` runs);
    the body calls `b(y=y)` which is well-typed but lacks a terminal
    abort — the IR builder must reject it on that ground."""
    src = _src(
        "  a(x=1) -> b(y=y)\n\n"
        "  RESCUE b:\n"
        "    -> b(y=y)\n"  # body ends with `b`, not `abort`
    )
    with pytest.raises(IRBuildError, match="must end with abort"):
        build_ir(_parse(src))


# (5b) Rescue body with abort only in branches (not at top level)
def test_rescue_abort_in_branch_not_enough():
    """An IF/ELSE block at the rescue body's tail counts as a non-abort
    top-level item even if every branch terminates with abort.

    `setup` runs BEFORE `a`, so `result` is in scope when the rescue on
    `a` fires; the body's IF reads `result.ok` legitimately. The error
    we expect is solely about the missing top-level terminal abort.
    """
    src = """
CONTRACT report
  SHAPE: { ok: bool }

STEP setup
  TAKES: x: int
  GIVES: result: report
  MODE:  exact

STEP a
  TAKES: r: report
  GIVES: y: int
  MODE:  exact

FLOW p
  setup(x=1) -> a(r=result)

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
    nesting-parallel error as anywhere else in the FLOW.

    `seed` runs BEFORE `process` in the top-level chain, so `items` is in
    scope when the rescue on `process` fires."""
    src = """
STEP seed
  TAKES: x: int
  GIVES: items: List<int>
  MODE:  exact

STEP process
  TAKES: items: List<int>
  GIVES: out: int
  MODE:  exact

STEP work
  TAKES: i: int
  GIVES: r: int
  MODE:  exact

FLOW p
  seed(x=1) -> process(items=items)

  RESCUE process:
    -> FOR EACH a IN items PARALLEL AS A:
         FOR EACH b IN items PARALLEL AS B:
           work(i=b)
    -> abort("nested parallel forbidden")
"""
    with pytest.raises(IRBuildError, match="nested inside another"):
        build_ir(_parse(src))


def test_rescue_cannot_reference_field_from_later_step():
    """A RESCUE for step_a must not validate against fields produced
    by steps that come after step_a in the top-level chain — those
    steps haven't run when the rescue fires.

    Without proper scoping, this would pass IR validation and produce
    a runtime KeyError on state['yb']."""
    src = """
STEP a
  TAKES: x: int
  GIVES: ya: int
  MODE:  exact

STEP b
  TAKES: ya: int
  GIVES: yb: int
  MODE:  exact

STEP cleanup
  TAKES: yb: int
  GIVES: ok: bool
  MODE:  exact

FLOW p
  a(x=1) -> b(ya=ya)

  RESCUE a:
    -> cleanup(yb=yb)
    -> abort("see cleanup")
"""
    # `yb` is produced by `b`, which runs AFTER `a`. If `a` raises,
    # `b` never runs, so the rescue body cannot legitimately reference
    # `yb`. The IR builder must reject this at compile time.
    with pytest.raises(IRBuildError, match="not produced|unknown"):
        build_ir(_parse(src))


def test_rescue_can_reference_earlier_field():
    """Sanity check the inverse: a RESCUE for step_b CAN reference
    `ya` (produced by step_a, which runs BEFORE step_b)."""
    src = """
STEP a
  TAKES: x: int
  GIVES: ya: int
  MODE:  exact

STEP b
  TAKES: ya: int
  GIVES: yb: int
  MODE:  exact

STEP cleanup
  TAKES: ya: int
  GIVES: ok: bool
  MODE:  exact

FLOW p
  a(x=1) -> b(ya=ya)

  RESCUE b:
    -> cleanup(ya=ya)
    -> abort("see cleanup")
"""
    graph = build_ir(_parse(src))
    rescues = graph.flow.rescues
    assert len(rescues) == 1
    assert rescues[0].step_name == "b"


ERROR_ACCESS_SRC = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP notify
  TAKES: channel: str, reason: str, err_type: str
  GIVES: sent: bool
  MODE:  exact

FLOW pipeline
  load(path="x") -> detect(rows=rows)

  RESCUE detect:
    -> notify(channel="#a", reason=detect.error.message, err_type=detect.error.type)
    -> abort("detection failed")

RESOURCES
  target: python
  models: [haiku]
"""


def test_parse_error_access_in_kwarg():
    """`step.error.message` and `step.error.type` parse as ErrorAccessExpr kwarg values."""
    program = _parse(ERROR_ACCESS_SRC)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    rescue = flow.rescues[0]
    notify_call = rescue.body[0]
    kwargs = dict(notify_call.kwargs)
    assert isinstance(kwargs["reason"], ErrorAccessExpr)
    assert kwargs["reason"].step_name == "detect"
    assert kwargs["reason"].field == "message"
    assert isinstance(kwargs["err_type"], ErrorAccessExpr)
    assert kwargs["err_type"].field == "type"


# ---------------------------------------------------------------------------
# RESUME(<step>.<field>) terminator (Task 4)
# ---------------------------------------------------------------------------

RESUME_SRC = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP recover
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  exact

FLOW pipeline
  load(path="data.csv")
    -> detect(rows=rows)

  RESCUE detect:
    -> recover(rows=rows)
    -> RESUME(recover.report)

RESOURCES
  target: python
  models: [haiku]
"""


def test_ir_builds_error_access():
    """ErrorAccessExpr kwargs in a RESCUE body become ErrorAccessIR in the IR."""
    program = _parse(ERROR_ACCESS_SRC)
    graph = build_ir(program)
    rescue = graph.flow.rescues[0]
    notify_ir = rescue.body[0]
    kwargs = dict(notify_ir.kwargs)
    assert isinstance(kwargs["reason"], ErrorAccessIR)
    assert kwargs["reason"].rescued_step == "detect"
    assert kwargs["reason"].field == "message"
    assert isinstance(kwargs["err_type"], ErrorAccessIR)
    assert kwargs["err_type"].field == "type"


def test_parse_resume_terminator():
    """RESUME(<step>.<field>) parses as a ResumeAst at the end of the rescue body."""
    program = _parse(RESUME_SRC)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    rescue = flow.rescues[0]
    last = rescue.body[-1]
    assert isinstance(last, ResumeAst)
    assert last.fallback_step == "recover"
    assert last.field_name == "report"


# ---------------------------------------------------------------------------
# T7: ErrorAccessIR validation rules
# ---------------------------------------------------------------------------


def test_ir_rejects_error_access_cross_step():
    """`<other_step>.error.X` where other_step is not the rescued step → reject."""
    src = ERROR_ACCESS_SRC.replace(
        "reason=detect.error.message",
        "reason=load.error.message",
    )
    program = _parse(src)
    with pytest.raises(IRBuildError) as exc:
        build_ir(program)
    assert "can only reference the step protected by this RESCUE" in str(exc.value)


def test_ir_rejects_error_access_unknown_field():
    src = ERROR_ACCESS_SRC.replace(
        "reason=detect.error.message",
        "reason=detect.error.stacktrace",
    )
    program = _parse(src)
    with pytest.raises(IRBuildError) as exc:
        build_ir(program)
    assert "unknown error field 'stacktrace'" in str(exc.value)
    assert "'message'" in str(exc.value) and "'type'" in str(exc.value)


def test_ir_rejects_error_access_outside_rescue():
    """ErrorAccessExpr in the main flow chain (not inside a RESCUE body) → reject."""
    src = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE: exact

STEP b
  TAKES: msg: str
  GIVES: z: int
  MODE: exact

FLOW f
  a(x=1) -> b(msg=a.error.message)

RESOURCES
  target: python
  models: [haiku]
"""
    program = _parse(src)
    with pytest.raises(IRBuildError) as exc:
        build_ir(program)
    assert "step.error.<field> is only valid inside a RESCUE handler" in str(exc.value)


def test_ir_builds_resume_terminator():
    """RESUME at the end of a rescue body becomes a ResumeIR in the IR."""
    program = _parse(RESUME_SRC)
    graph = build_ir(program)
    rescue = graph.flow.rescues[0]
    last = rescue.body[-1]
    assert isinstance(last, ResumeIR)
    assert last.fallback_step == "recover"
    assert last.field_name == "report"


# ---------------------------------------------------------------------------
# T9: ResumeIR semantic validations
# ---------------------------------------------------------------------------


def test_ir_rejects_resume_fallback_not_in_chain():
    src = RESUME_SRC.replace(
        "RESUME(recover.report)",
        "RESUME(ghost.report)",
    )
    program = _parse(src)
    with pytest.raises(IRBuildError) as exc:
        build_ir(program)
    assert "RESUME(ghost.report)" in str(exc.value)
    assert "is not called in this RESCUE handler" in str(exc.value)


def test_ir_rejects_resume_field_not_in_gives():
    src = RESUME_SRC.replace(
        "RESUME(recover.report)",
        "RESUME(recover.nope)",
    )
    program = _parse(src)
    with pytest.raises(IRBuildError) as exc:
        build_ir(program)
    assert "is not a field of step 'recover'" in str(exc.value)


def test_ir_rejects_resume_type_mismatch():
    # detect.gives: report: str  /  recover.gives: report: int  → type mismatch
    src = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP recover
  TAKES: rows: List<int>
  GIVES: report: int
  MODE:  exact

FLOW pipeline
  load(path="data.csv")
    -> detect(rows=rows)

  RESCUE detect:
    -> recover(rows=rows)
    -> RESUME(recover.report)

RESOURCES
  target: python
  models: [haiku]
"""
    program = _parse(src)
    with pytest.raises(IRBuildError) as exc:
        build_ir(program)
    assert "is incompatible with rescued step's GIVES type" in str(exc.value)


def test_ir_rejects_rescue_without_terminator():
    src = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP notify
  TAKES: channel: str
  GIVES: sent: bool
  MODE:  exact

FLOW pipeline
  load(path="data.csv")
    -> detect(rows=rows)

  RESCUE detect:
    -> notify(channel="#a")

RESOURCES
  target: python
  models: [haiku]
"""
    program = _parse(src)
    with pytest.raises(IRBuildError) as exc:
        build_ir(program)
    assert "must end with abort(...) or RESUME(...)" in str(exc.value)


def test_ir_accepts_resume_contract_ref_type_match():
    """RESUME where both fallback and rescued GIVES are the same CONTRACT ref must succeed.

    This is a regression test for the bug where ContractRef instances with
    different source locations (line/col) compared unequal via raw !=, causing
    a false type-mismatch rejection.
    """
    src = """
CONTRACT churn_report
  SHAPE: {risks: List<{client: str, score: float}>}

STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: churn_report
  MODE:  judgment

STEP recover
  TAKES: rows: List<int>
  GIVES: report: churn_report
  MODE:  exact

FLOW pipeline
  load(path="data.csv")
    -> detect(rows=rows)

  RESCUE detect:
    -> recover(rows=rows)
    -> RESUME(recover.report)

RESOURCES
  target: python
  models: [haiku]
"""
    program = _parse(src)
    graph = build_ir(program)
    rescue = graph.flow.rescues[0]
    last = rescue.body[-1]
    # No exception means the type-equality check accepted the two ContractRefs
    # despite their different line/col.
    assert isinstance(last, ResumeIR)
    assert last.fallback_step == "recover"
    assert last.field_name == "report"
