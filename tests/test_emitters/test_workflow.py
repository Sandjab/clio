"""target: claude-workflow — emitter tests."""
import json
import re
from pathlib import Path

import pytest

from clio.emitters._workflow_helpers import (
    inline_schema,
    js_identifier,
    schema_literal,
    validate_graph_for_workflow,
)
from clio.emitters._workflow_step_renderers import (
    render_exact_step_js,
    render_judgment_step_js,
)
from clio.emitters.workflow import WorkflowEmitter
from clio.ir.graph import (
    ApiInvokeIR,
    BoolOpIR,
    CacheConfigIR,
    CallIR,
    CliInvokeIR,
    CodeImplIR,
    ConditionIR,
    ContractIR,
    FieldIR,
    FlowCallIR,
    FlowGraph,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    ImplIR,
    MatchBlockIR,
    MatchCaseIR,
    McpToolImplIR,
    OnFailChainIR,
    OnFailStrategyIR,
    RescueBlockIR,
    RestImplIR,
    ResumeIR,
    ShellImplIR,
    SqlImplIR,
    StepIR,
    WhileBlockIR,
)
from clio.parser.ast_nodes import ContractRef, ListType, PrimitiveType, RecordType
from tests.conftest import assert_valid_js


def _emit(graph: FlowGraph, tmp_path: Path) -> str:
    """Emit a hand-built graph and return the script text."""
    WorkflowEmitter().emit(graph, tmp_path)
    scripts = list(tmp_path.glob("*.workflow.js"))
    assert len(scripts) == 1, f"expected 1 script, got {scripts}"
    return scripts[0].read_text()


def _emit_fixture(name: str, tmp_path: Path, flow: str | None = None) -> str:
    """Compile a real fixture from tests/fixtures/ and return the script text.

    Later tasks use this rather than hand-building IR: the `.clio` grammar has
    traps, and these fixtures already parse. Assert on structure, not on step
    names you have not read.

    `flow` selects the entry FLOW. A multi-FLOW source needs it: the IR builder
    leaves `graph.flow` at None there, and this target refuses to guess
    (E_WF_006). Every sub-flow fixture is multi-FLOW by construction.
    """
    from clio.cli import main

    out = tmp_path / "out"
    argv = ["compile", f"tests/fixtures/{name}",
            "--target", "claude-workflow", "--output", str(out)]
    if flow is not None:
        argv += ["--flow", flow]
    rc = main(argv)
    assert rc == 0, f"{name} failed to compile"
    script = next(iter(out.glob("*.workflow.js")))
    return script.read_text()


def test_empty_flow_emits_valid_meta(tmp_path):
    flow = FlowIR(name="triage", chain=(), rescues=(), line=1,
                  description="Triage incoming reports")
    graph = FlowGraph(steps=(), flow=flow, flows=(flow,))

    src = _emit(graph, tmp_path)

    assert "export const meta = {" in src
    assert "name: 'triage'" in src
    assert "description: 'Triage incoming reports'" in src
    assert_valid_js(src, tmp_path)


def test_cli_registers_claude_workflow_target(tmp_path):
    """swift_minimal.clio, not go_minimal.clio: its exact steps declare no LANG,
    so they are target-agnostic. go_minimal declares `LANG: go`, which this
    target refuses (E_WF_004) rather than silently emitting a JS stub for a body
    the author declared in Go — see test_go_lang_fixture_is_refused_end_to_end."""
    from clio.cli import main

    rc = main(["compile", "tests/fixtures/swift_minimal.clio",
               "--target", "claude-workflow", "--output", str(tmp_path / "out")])
    assert rc == 0
    assert list((tmp_path / "out").glob("*.workflow.js")), "no script emitted"


# ---------------------------------------------------------------------------
# Task 2 — refusals (E_WF_001..004) and degradation warnings (W_WF_001..003)
# ---------------------------------------------------------------------------


def _step(name="s", mode="judgment", **kw) -> StepIR:
    defaults = dict(
        takes=(), gives=FieldIR(name="out", type=PrimitiveType(name="str")),
        cache=None, on_fail=None, lang=None, impl=None, invoke=None, line=7,
    )
    defaults.update(kw)
    return StepIR(name=name, mode=mode, **defaults)


def _graph(*steps: StepIR, **kw) -> FlowGraph:
    flow = FlowIR(name="f", chain=(), rescues=(), line=1)
    return FlowGraph(steps=steps, flow=flow, flows=(flow,), **kw)


def _rest_impl() -> RestImplIR:
    return RestImplIR(method="GET", url="https://example.com", query=None,
                      headers=None, body=None, response_path=None,
                      timeout_seconds=None, retry=None)


def test_no_flow_is_refused():
    with pytest.raises(ValueError, match="E_WF_001"):
        validate_graph_for_workflow(FlowGraph(steps=(), flow=None, flows=()))


@pytest.mark.parametrize("impl", [
    ShellImplIR(argv=("ls",), timeout_seconds=None),
    _rest_impl(),
    SqlImplIR(db="d", query="SELECT 1"),
    McpToolImplIR(server="s", tool="t", args=(), timeout_seconds=5, parse="json"),
])
def test_io_exact_steps_are_refused(impl: ImplIR):
    """The workflow sandbox has no process, no network and no filesystem: an
    exact step that does IO cannot run there, so it is refused rather than
    degraded into something that fails at run time."""
    graph = _graph(_step(mode="exact", impl=impl))
    with pytest.raises(ValueError, match="E_WF_003"):
        validate_graph_for_workflow(graph)


def test_refusal_names_the_step_and_the_source_line():
    graph = _graph(_step(name="fetch_page", mode="exact",
                         impl=ShellImplIR(argv=("curl",), timeout_seconds=None)))
    with pytest.raises(ValueError, match=r"fetch_page.*line 7|line 7.*fetch_page"):
        validate_graph_for_workflow(graph)


@pytest.mark.parametrize("protocol", ["openai", "bedrock", "vertex"])
def test_non_anthropic_api_is_refused(protocol: str):
    inv = ApiInvokeIR(protocol=protocol, model="gpt-4o", base_url=None, auth=None,
                      temperature=None, max_tokens=None, timeout_seconds=None,
                      retries=None)
    with pytest.raises(ValueError, match="E_WF_002"):
        validate_graph_for_workflow(_graph(_step(invoke=inv)))


def test_anthropic_api_and_cli_invokes_are_accepted():
    """The refusal must not over-fire: an agent() IS the Claude Code invocation,
    so invoke.mode: cli needs no mapping and an Anthropic api invoke is fine."""
    api = ApiInvokeIR(protocol="anthropic", model="claude-opus-4-8", base_url=None,
                      auth=None, temperature=None, max_tokens=None,
                      timeout_seconds=None, retries=None)
    cli = CliInvokeIR(cli="claude", model="claude-sonnet-4-6", output_format=None,
                      max_turns=None)
    validate_graph_for_workflow(_graph(_step(name="a", invoke=api),
                                       _step(name="b", invoke=cli)))


@pytest.mark.parametrize("lang", ["python", "go", "rust", "bash"])
def test_non_js_exact_lang_is_refused(lang: str):
    """A LANG the sandbox cannot run is refused, never degraded into a JS stub:
    that would silently discard the body language the author declared. Both
    spellings are checked — the `LANG:` directive and impl.lang."""
    with pytest.raises(ValueError, match="E_WF_004"):
        validate_graph_for_workflow(_graph(_step(mode="exact", lang=lang)))
    with pytest.raises(ValueError, match="E_WF_004"):
        validate_graph_for_workflow(
            _graph(_step(mode="exact", impl=CodeImplIR(lang=lang)))
        )


@pytest.mark.parametrize("lang", ["node", "auto", None])
def test_js_exact_lang_is_accepted(lang: str | None):
    validate_graph_for_workflow(_graph(_step(mode="exact", lang=lang)))
    validate_graph_for_workflow(_graph(_step(mode="exact", impl=CodeImplIR(lang=lang))))


def test_cache_is_a_noop_with_a_warning():
    graph = _graph(_step(cache=CacheConfigIR(mode="ttl", ttl_seconds=3600)))
    warnings: list[str] = []
    validate_graph_for_workflow(graph, warn=warnings.append)
    assert any("W_WF_001" in w for w in warnings)
    assert any("line 7" in w for w in warnings)


def test_cache_off_does_not_warn():
    graph = _graph(_step(cache=CacheConfigIR(mode="off", ttl_seconds=None)))
    warnings: list[str] = []
    validate_graph_for_workflow(graph, warn=warnings.append)
    assert warnings == []


def test_on_fail_retry_warns_about_the_missing_backoff():
    chain = OnFailChainIR(strategies=(OnFailStrategyIR(kind="retry", max_retries=3),))
    warnings: list[str] = []
    validate_graph_for_workflow(_graph(_step(on_fail=chain)), warn=warnings.append)
    assert any("W_WF_002" in w for w in warnings)
    assert any("line 7" in w for w in warnings)


def test_on_fail_without_retry_does_not_warn_about_backoff():
    """Only the retry strategy loses something (the delay). A fallback or abort
    chain is honored in full — warning on it would be noise."""
    chain = OnFailChainIR(strategies=(OnFailStrategyIR(kind="abort",
                                                       abort_message="stop"),))
    warnings: list[str] = []
    validate_graph_for_workflow(_graph(_step(on_fail=chain)), warn=warnings.append)
    assert warnings == []


def test_contract_assert_is_not_enforced_and_says_so():
    """The JSON Schema (types, ranges, enums) IS enforced by the host; the ASSERT
    predicate is not rendered. This target WARNS rather than dropping it in
    silence — the author's `ASSERT` is the one guarantee they wrote by hand, and a
    guarantee that quietly stops holding is worse than one that was never offered.
    (python, go and swift do enforce it, through an `x-clio-assert` walker in their
    emitted validators; there is no validator here to carry one.)"""
    contract = ContractIR(
        name="Verdict",
        json_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        assert_json_ast={"op": ">", "left": "len(reason)", "right": 0},
        line=3,
    )
    warnings: list[str] = []
    validate_graph_for_workflow(_graph(_step(), contracts=(contract,)),
                                warn=warnings.append)
    assert any("W_WF_003" in w for w in warnings)
    assert any("Verdict" in w and "line 3" in w for w in warnings)


def test_contract_without_assert_does_not_warn():
    contract = ContractIR(name="Verdict", json_schema={"type": "object"},
                          assert_json_ast=None, line=3)
    warnings: list[str] = []
    validate_graph_for_workflow(_graph(_step(), contracts=(contract,)),
                                warn=warnings.append)
    assert warnings == []


def test_test_blocks_are_ignored_not_refused():
    """Only the `python` target emits pytest files. A TEST block is inert here —
    it must not refuse the compile (swift raises E_SWIFT_012; we deliberately
    do not copy that)."""
    from clio.ir.graph import TestIR  # imported here: pytest would collect it as a class

    graph = _graph(
        _step(),
        tests=(TestIR(name="t", flow_name="f", with_kwargs=(), expects=(),
                      expects_not=(), line=20),),
    )
    validate_graph_for_workflow(graph)  # must not raise


def test_emitter_wires_warnings_to_stderr(tmp_path, capsys):
    """The emitter injects the real `warn` — same seam as ClaudeSkillEmitter.
    Without the wiring, validate_graph_for_workflow's default is a no-op and
    every degradation would be silent."""
    graph = _graph(_step(cache=CacheConfigIR(mode="ttl", ttl_seconds=3600)))

    WorkflowEmitter().emit(graph, tmp_path)

    assert "W_WF_001" in capsys.readouterr().err


def test_shell_fixture_is_refused_end_to_end(tmp_path):
    """The negative fixture, through the CLI: a refusal must print a message and
    exit 1 — not raise a traceback. That try/except in cli.py is load-bearing."""
    from clio.cli import main

    rc = main(["compile", "tests/fixtures/go_shell.clio",
               "--target", "claude-workflow", "--output", str(tmp_path)])
    assert rc == 1


def test_go_lang_fixture_is_refused_end_to_end(tmp_path, capsys):
    """`LANG: go` cannot be emitted as JavaScript. Refuse it and point at a target
    that can — do not hand the author a JS stub in place of the Go body."""
    from clio.cli import main

    rc = main(["compile", "tests/fixtures/go_minimal.clio",
               "--target", "claude-workflow", "--output", str(tmp_path)])
    assert rc == 1
    assert "E_WF_004" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Task 3 — self-contained JSON Schema (every $ref dereferenced, E_WF_005)
# ---------------------------------------------------------------------------


def _contract(name: str, schema: dict, line: int = 1) -> ContractIR:
    return ContractIR(name=name, json_schema=schema, assert_json_ast=None, line=line)


def _ref(name: str) -> ContractRef:
    """ContractRef carries its source position — the parser always has one."""
    return ContractRef(name=name, line=1, col=1)


def test_inline_schema_dereferences_contract_refs():
    """type_to_json_schema renders a ContractRef as a *file* $ref. The sandbox has
    no filesystem: nothing can resolve it at run time, so it must be inlined."""
    verdict = _contract("Verdict", {"type": "object",
                                    "properties": {"ok": {"type": "boolean"}},
                                    "required": ["ok"]}, line=3)
    t = ListType(inner=_ref("Verdict"))

    schema = inline_schema(t, {"Verdict": verdict})

    assert schema == {"type": "array", "items": verdict.json_schema}
    assert "$ref" not in str(schema), "no file $ref may survive into the sandbox"


def test_inline_schema_recurses_into_nested_contract_refs():
    """A contract whose own schema references another contract must be inlined all
    the way down — one level of dereferencing would leave a live $ref behind."""
    inner = _contract("Score", {"type": "object",
                                "properties": {"n": {"type": "integer"}},
                                "required": ["n"]})
    outer = _contract("Report", {
        "type": "object",
        "properties": {"score": {"$ref": "../contracts/Score.schema.json"}},
        "required": ["score"],
    })

    schema = inline_schema(_ref("Report"), {"Report": outer, "Score": inner})

    assert schema["properties"]["score"] == inner.json_schema
    assert "$ref" not in str(schema)


def test_inline_schema_rejects_a_reference_cycle():
    """A cycle cannot be inlined at all: the fixed point is infinite. Refuse it at
    compile time rather than recurse forever or emit a $ref the host cannot read."""
    a = _contract("A", {"$ref": "../contracts/B.schema.json"})
    b = _contract("B", {"$ref": "../contracts/A.schema.json"})

    with pytest.raises(ValueError, match="E_WF_005"):
        inline_schema(_ref("A"), {"A": a, "B": b})


def test_inline_schema_rejects_an_unknown_contract_ref():
    """Same code, other unresolvable case: a $ref to a contract that is not in the
    graph. Emitting the dangling $ref would fail inside the sandbox instead."""
    with pytest.raises(ValueError, match="E_WF_005"):
        inline_schema(_ref("Ghost"), {})


def test_inline_schema_strips_the_clio_assert_ast():
    """W_WF_003: the ASSERT predicate is not enforced by this target. Its CLIO AST
    has no meaning for the host validator, so it does not travel in the schema —
    same call as the claude-cli target, which embeds schemas in prompts."""
    verdict = _contract("Verdict", {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "x-clio-assert": {"op": ">", "left": "len(reason)", "right": 0},
    })

    schema = inline_schema(_ref("Verdict"), {"Verdict": verdict})

    assert "x-clio-assert" not in schema
    assert schema["properties"] == {"reason": {"type": "string"}}


def test_schema_literal_wraps_the_gives_field_not_the_step_name():
    """Conditions read a step's output as state[step].<gives.name>, so the agent
    must return an OBJECT wrapping that one named field — not the bare value."""
    t = RecordType(fields=(("ok", PrimitiveType(name="bool")),))

    literal = schema_literal(t, {}, "verdict")
    obj = json.loads(literal)

    assert obj["properties"]["verdict"]["type"] == "object"
    assert obj["required"] == ["verdict"]
    assert obj["additionalProperties"] is False


def test_schema_literal_of_a_real_fixture_contract_is_self_contained():
    """The hand-built IR above could drift from what the builder actually produces.
    swift_contract.clio declares a CONTRACT (with an ASSERT) and a step that GIVES
    it — compile it for real and inline that step's schema."""
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    graph = build_ir(parse(Path("tests/fixtures/swift_contract.clio").read_text()))
    contracts = {c.name: c for c in graph.contracts}
    score = next(s for s in graph.steps if s.name == "score")

    obj = json.loads(schema_literal(score.gives.type, contracts, score.gives.name))

    risk = obj["properties"]["risk"]          # GIVES: risk: customer_risk
    assert risk["properties"]["client"] == {"type": "string"}
    assert risk["properties"]["risk"] == {"enum": ["low", "mid", "high"]}
    assert "$ref" not in json.dumps(obj)
    assert "x-clio-assert" not in json.dumps(obj)


# ---------------------------------------------------------------------------
# Task 4 — judgment step -> agent(), and the null trap
# ---------------------------------------------------------------------------


def test_judgment_step_calls_agent_with_schema_and_label(tmp_path):
    step = _step(name="classify", mode="judgment",
                 gives=FieldIR(name="label", type=PrimitiveType(name="str")))
    js = render_judgment_step_js(step, contracts={})

    assert "async function classify(" in js
    assert "await agent(" in js
    assert "label: 'judgment:classify'" in js
    assert "schema:" in js
    assert_valid_js(js, tmp_path)


def test_judgment_step_throws_when_agent_returns_null():
    """agent() returns null on terminal failure instead of throwing. If this
    conversion is dropped, ON_FAIL and RESCUE become dead code and every failed
    agent silently yields `undefined` downstream. This test is the guard."""
    js = render_judgment_step_js(_step(name="classify"), contracts={})

    assert "=== null" in js or "== null" in js
    assert "throw new Error" in js


def test_judgment_step_omits_model_when_source_declares_none():
    """Omitting model lets the subagent inherit the session model — the behavior
    the Workflow tool documents as almost always correct."""
    js = render_judgment_step_js(_step(name="c"), contracts={})
    assert "model:" not in js


@pytest.mark.parametrize("model,tier", [
    ("claude-opus-4-8", "opus"),
    ("claude-sonnet-4-6", "sonnet"),
    ("claude-haiku-4-5-20251001", "haiku"),
])
def test_judgment_step_maps_declared_model_to_a_tier(model: str, tier: str):
    inv = ApiInvokeIR(protocol="anthropic", model=model, base_url=None,
                      auth=None, temperature=None, max_tokens=None,
                      timeout_seconds=None, retries=None)
    js = render_judgment_step_js(_step(name="c", invoke=inv), contracts={})
    assert f"model: '{tier}'" in js


def test_judgment_step_maps_a_cli_invoke_model_too():
    """invoke.mode: cli needs no protocol mapping — the agent() call IS the Claude
    Code invocation — but the model it names still selects the subagent's tier."""
    cli = CliInvokeIR(cli="claude", model="claude-haiku-4-5-20251001",
                      output_format=None, max_turns=None)
    js = render_judgment_step_js(_step(name="c", invoke=cli), contracts={})
    assert "model: 'haiku'" in js


def test_judgment_step_inherits_the_session_model_on_an_unknown_id():
    """An id we cannot map is not a guess-and-hope: omit `model` and inherit the
    session model, exactly as when the source declares nothing."""
    inv = ApiInvokeIR(protocol="anthropic", model="claude-next-9", base_url=None,
                      auth=None, temperature=None, max_tokens=None,
                      timeout_seconds=None, retries=None)
    js = render_judgment_step_js(_step(name="c", invoke=inv), contracts={})
    assert "model:" not in js


def test_judgment_prompt_carries_intent_inputs_and_output_shape(tmp_path):
    step = _step(
        name="triage",
        takes=(FieldIR(name="report", type=PrimitiveType(name="str")),),
        gives=FieldIR(name="severity", type=PrimitiveType(name="str")),
        description="Rank the report by severity.",
        strategies="When the report is empty, answer 'low'.",
    )
    js = render_judgment_step_js(step, contracts={})

    assert "Rank the report by severity." in js
    assert "When the report is empty" in js
    # TAKES are interpolated from run state, not hardcoded.
    assert "${JSON.stringify(state['report'])}" in js
    # GIVES names the single key the agent must return (state[step].<gives.name>).
    assert "severity" in js
    assert_valid_js(js, tmp_path)


def test_judgment_prompt_escapes_js_template_metacharacters(tmp_path):
    """The prompt is emitted as a template literal so ${JSON.stringify(state…)}
    interpolates. A backtick or a ${ in free text (DESCRIPTION/STRATEGIES are
    prose — markdown backticks are the norm) would otherwise close the literal
    early and emit a script that does not parse."""
    step = _step(
        name="c",
        description="Use `jq` on the payload.",
        strategies="Never emit ${danger} or a stray \\ backslash.",
    )
    js = render_judgment_step_js(step, contracts={})

    assert "\\`jq\\`" in js
    assert "\\${danger}" in js
    assert_valid_js(js, tmp_path)


def test_judgment_step_of_a_real_fixture_is_valid_js(tmp_path):
    """Hand-built IR can drift from what the builder produces. swift_judgment.clio
    declares a judgment step whose GIVES is a CONTRACT — render that step for real
    and check the schema landed inlined inside valid JS."""
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    graph = build_ir(parse(Path("tests/fixtures/swift_judgment.clio").read_text()))
    contracts = {c.name: c for c in graph.contracts}
    analyze = next(s for s in graph.steps if s.name == "analyze")

    js = render_judgment_step_js(analyze, contracts)

    assert "async function analyze(state, phaseName)" in js
    assert "label: 'judgment:analyze'" in js
    assert '"enum"' in js and '"positive"' in js   # the contract schema, inlined
    assert "$ref" not in js
    assert_valid_js(js, tmp_path)


def test_no_emitted_line_calls_a_sandbox_forbidden_global():
    """Date.now(), new Date() and Math.random() THROW in the workflow sandbox. No
    emitted line may call them — no timestamps, no jitter, no generated ids."""
    js = render_judgment_step_js(_step(name="c"), contracts={})

    for forbidden in ("Date.now(", "new Date(", "Math.random("):
        assert forbidden not in js


# ---------------------------------------------------------------------------
# Task 5 — exact `code` step -> a pure JS stub
# ---------------------------------------------------------------------------


def test_exact_code_step_emits_a_pure_stub():
    step = _step(name="parse_rows", mode="exact", impl=CodeImplIR(lang="node"),
                 takes=(FieldIR(name="raw", type=PrimitiveType(name="str")),),
                 gives=FieldIR(name="rows", type=PrimitiveType(name="str")))
    js = render_exact_step_js(step, contracts={})

    assert "function parse_rows(state)" in js
    assert "TODO" in js
    # The sandbox has no IO. The stub must say so, loudly, where the author types.
    assert "pure" in js.lower()
    assert "throw new Error" in js  # unfilled stub must fail loudly, not return undefined


def test_exact_stub_names_the_globals_that_throw_in_the_sandbox():
    """`no IO` is not enough guidance: Date.now() and Math.random() look pure and
    are not — they THROW in the sandbox. The author reads the stub, not the README,
    so the two traps are named at the point where the body gets typed."""
    js = render_exact_step_js(_step(name="c", mode="exact"), contracts={})

    assert "Date.now()" in js
    assert "Math.random()" in js


def test_exact_stub_documents_the_state_keys_it_reads_and_the_field_it_returns():
    """The body reads its inputs off `state`, not off named parameters. Without the
    keys spelled out, the author has to go back to the .clio to find them."""
    step = _step(name="merge", mode="exact",
                 takes=(FieldIR(name="left", type=PrimitiveType(name="str")),
                        FieldIR(name="right", type=PrimitiveType(name="str"))),
                 gives=FieldIR(name="merged", type=PrimitiveType(name="str")))
    js = render_exact_step_js(step, contracts={})

    assert "state['left']" in js and "state['right']" in js
    assert "merged" in js


def test_exact_stub_without_takes_or_gives_is_still_valid_js(tmp_path):
    """A step may declare neither (a side-effect step). The stub must degrade to
    valid JS, not to a dangling comment or an empty `state[]`."""
    js = render_exact_step_js(_step(name="c", mode="exact", takes=(), gives=None),
                              contracts={})

    assert_valid_js(js, tmp_path)


def test_exact_step_of_a_real_fixture_is_valid_js(tmp_path):
    """Hand-built IR can drift from what the builder produces. swift_minimal.clio
    declares exact steps with no LANG (target-agnostic bodies) — render one for
    real and syntax-check it."""
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    graph = build_ir(parse(Path("tests/fixtures/swift_minimal.clio").read_text()))
    load = next(s for s in graph.steps if s.name == "load")

    js = render_exact_step_js(load, {c.name: c for c in graph.contracts})

    assert "function load(state)" in js
    assert "state['file']" in js          # TAKES: file: str
    assert "throw new Error" in js
    assert_valid_js(js, tmp_path)


# ---------------------------------------------------------------------------
# E_WF_006 — a multi-FLOW source compiled without --flow is ambiguous
# ---------------------------------------------------------------------------


def _two_flows() -> tuple[FlowIR, FlowIR]:
    return (FlowIR(name="alpha", chain=(), rescues=(), line=1),
            FlowIR(name="beta", chain=(), rescues=(), line=9))


def test_multi_flow_without_a_selection_is_refused():
    """LANGUAGE_SPEC: `clio compile` requires --flow when the source declares more
    than one. The builder leaves graph.flow None in that case; picking flows[0]
    here would compile a flow the author never asked for and drop the others
    without a word — an exit-0 lie."""
    alpha, beta = _two_flows()
    graph = FlowGraph(steps=(), flow=None, flows=(alpha, beta))

    with pytest.raises(ValueError, match="E_WF_006"):
        validate_graph_for_workflow(graph)


def test_e_wf_006_lists_the_declared_flows_and_names_the_fix():
    """A refusal the author cannot act on is only half a refusal: it must name the
    candidates and the flag that resolves the ambiguity."""
    alpha, beta = _two_flows()

    with pytest.raises(ValueError) as excinfo:
        validate_graph_for_workflow(FlowGraph(steps=(), flow=None,
                                              flows=(alpha, beta)))

    msg = str(excinfo.value)
    assert "alpha" in msg and "beta" in msg
    assert "--flow" in msg


def test_multi_flow_with_an_explicit_selection_is_accepted():
    """The guard must not over-fire: --flow beta sets graph.flow, and a sub-flow
    called by the entry flow is a normal, supported shape."""
    alpha, beta = _two_flows()

    validate_graph_for_workflow(FlowGraph(steps=(), flow=beta,
                                          flows=(alpha, beta)))  # must not raise


def test_multi_flow_source_without_flag_refuses_end_to_end(tmp_path, capsys):
    """The reproduction, through the CLI. Before E_WF_006 this exited 0 and wrote
    alpha.workflow.js — the first FLOW, silently chosen, `beta` gone. Exit 1 and
    emit nothing instead: a wrong artifact is worse than no artifact."""
    from clio.cli import main

    rc = main(["compile", "tests/fixtures/workflow_two_flows.clio",
               "--target", "claude-workflow", "--output", str(tmp_path)])

    assert rc == 1
    assert "E_WF_006" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.workflow.js")), "refused, yet a script was written"


def test_selecting_the_flow_compiles_that_flow_end_to_end(tmp_path):
    """The other half: --flow beta compiles, and it compiles *beta* — not flows[0]."""
    from clio.cli import main

    rc = main(["compile", "tests/fixtures/workflow_two_flows.clio",
               "--target", "claude-workflow", "--flow", "beta",
               "--output", str(tmp_path)])

    assert rc == 0
    assert [p.name for p in tmp_path.glob("*.workflow.js")] == ["beta.workflow.js"]


# ---------------------------------------------------------------------------
# JS reserved words — a CLIO step name is not a legal JS identifier by default
# ---------------------------------------------------------------------------

# node --check rejects every one of these as a function name in module code
# (strict mode): reserved words, strict-mode reserved words, `await`/`enum`, the
# literals, and the two names strict mode refuses to bind (`eval`, `arguments`).
_JS_RESERVED_SAMPLE = ["delete", "new", "class", "default", "case", "return",
                       "switch", "try", "catch", "throw", "let", "const", "var",
                       "await", "enum", "export", "import", "static", "yield",
                       "true", "false", "null", "eval", "arguments"]


@pytest.mark.parametrize("name", _JS_RESERVED_SAMPLE)
def test_judgment_step_named_after_a_js_reserved_word_is_valid_js(name, tmp_path):
    """The CLIO lexer accepts any [a-zA-Z_][a-zA-Z0-9_]* as a STEP name and knows
    nothing of JS. `STEP delete` therefore reaches the emitter and used to produce
    `async function delete(state, phaseName)` — a SyntaxError. The name must be
    mangled into a legal identifier."""
    js = render_judgment_step_js(_step(name=name), contracts={})

    assert_valid_js(js, tmp_path)


@pytest.mark.parametrize("name", _JS_RESERVED_SAMPLE)
def test_exact_stub_named_after_a_js_reserved_word_is_valid_js(name, tmp_path):
    js = render_exact_step_js(_step(name=name, mode="exact"), contracts={})

    assert_valid_js(js, tmp_path)


def test_a_reserved_step_name_is_reachable_from_real_clio_source(tmp_path):
    """Hand-built IR could be accused of inventing an impossible step. It is not:
    `STEP delete` parses, builds, and reaches the renderer."""
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    graph = build_ir(parse(
        "STEP delete\n"
        "  TAKES: path: str\n"
        "  GIVES: gone: bool\n"
        "  MODE:  judgment\n"
        "\n"
        "FLOW purge\n"
        "  TAKES: path: str\n"
        "  delete(path=path)\n"
    ))
    step = next(s for s in graph.steps if s.name == "delete")

    js = render_judgment_step_js(step, {c.name: c for c in graph.contracts})

    assert "async function delete(" not in js, "reserved word emitted verbatim"
    assert_valid_js(js, tmp_path)


def test_js_identifier_leaves_ordinary_names_alone():
    """Mangling is not a rename: an ordinary step keeps its name, so the emitted
    function, the agent label and the prompt still read like the source."""
    assert js_identifier("classify") == "classify"
    assert js_identifier("parse_rows_2") == "parse_rows_2"


def test_js_identifier_is_collision_free():
    """The suffix is `$`, not `_`: the CLIO lexer cannot produce a `$` in an
    identifier, so no mangled name can ever equal another step's name. A `_`
    suffix — the Python convention in _to_field_name — would map `delete` and a
    real step named `delete_` onto the same JS function, and one would silently
    overwrite the other."""
    assert js_identifier("delete") == "delete$"
    assert js_identifier("delete") != js_identifier("delete_")
    assert js_identifier("delete_") == "delete_"


def test_a_step_named_undefined_does_not_shadow_the_null_guard(tmp_path):
    """`function undefined(...)` is legal JS — node accepts it — which makes it
    worse than a SyntaxError: the declaration hoists and shadows the global, so
    the `result === undefined` guard compares against a function object and never
    fires. Trap §6.1 (agent() returns null, it does not throw) would come back
    silently. Mangle it too."""
    js = render_judgment_step_js(_step(name="undefined"), contracts={})

    assert "function undefined(" not in js
    assert "result === undefined" in js, "the null guard must still be the guard"
    assert_valid_js(js, tmp_path)


# ---------------------------------------------------------------------------
# Task 6 — the linear chain: state, args, phases
# ---------------------------------------------------------------------------

_STR = PrimitiveType(name="str")


def _linear_graph() -> FlowGraph:
    """`fetch(url=@url) -> summarize(text=@text)`, entered with args.url.

    `state` is keyed by GIVES **field** name, not by step name: `fetch` GIVES
    `text`, so its output lands in state['text'] and summarize's `@text` reads it
    back. That is what the builder produces (swift_minimal.clio compiles the
    `-> summarize(rows)` sugar to `rows=@rows`, where `rows` is *load*'s GIVES)
    and what every other target emits — python.py:635 `state[gives.name] = …`,
    _swift_flow_renderer.py:264 `state["<gives.name>"] = …`. Conditions read the
    same key: ConditionIR(step_name='r') where `r` is assess's GIVES field.
    """
    fetch = _step(name="fetch", takes=(FieldIR(name="url", type=_STR),),
                  gives=FieldIR(name="text", type=_STR))
    summarize = _step(name="summarize", takes=(FieldIR(name="text", type=_STR),),
                      gives=FieldIR(name="summary", type=_STR))
    flow = FlowIR(
        name="brief",
        chain=(CallIR(step_name="fetch", kwargs=(("url", "@url"),), line=10),
               CallIR(step_name="summarize", kwargs=(("text", "@text"),), line=11)),
        rescues=(), line=1,
        takes=(FieldIR(name="url", type=_STR),),
    )
    return FlowGraph(steps=(fetch, summarize), flow=flow, flows=(flow,))


def test_linear_chain_threads_state_and_declares_phases(tmp_path):
    src = _emit(_linear_graph(), tmp_path)

    assert "const state = {}" in src
    assert "state['url'] = args['url']" in src
    assert "state['text'] = await fetch(state, 'fetch')" in src
    assert "state['summary'] = await summarize(state, 'summarize')" in src
    assert "phase('fetch')" in src and "phase('summarize')" in src
    assert "{ title: 'fetch' }" in src   # meta.phases mirrors the phase() calls
    # The body calls the functions the emitter also writes into the same file.
    assert "async function fetch(state, phaseName)" in src
    assert_valid_js(src, tmp_path)


def test_meta_phases_mirror_the_phase_calls_in_order(tmp_path):
    """§4.3: one phase per TOP-LEVEL chain element, and meta.phases lists exactly
    those titles, in order. Drift either way is a real defect — a phase() the meta
    never declared, or a declared phase the run never enters."""
    src = _emit(_linear_graph(), tmp_path)

    declared = re.findall(r"\{ title: '([^']+)' \}", src)
    called = re.findall(r"^phase\('([^']+)'\)", src, re.M)

    assert declared == called == ["fetch", "summarize"]


def test_a_literal_kwarg_is_bound_without_mutating_shared_state(tmp_path):
    """`analyze(text="Great product!")` (swift_judgment.clio) binds a TAKES from a
    literal: nothing in state holds it, yet the step body reads state['text'].
    The call site supplies it through a shadowed COPY, never by writing state:
    a literal TAKES named like some step's GIVES would clobber that output, and
    inside parallel()/pipeline() (Task 8) concurrent items would race on the key.
    """
    analyze = _step(name="analyze", takes=(FieldIR(name="text", type=_STR),),
                    gives=FieldIR(name="verdict", type=_STR))
    flow = FlowIR(name="c", rescues=(), line=1,
                  chain=(CallIR(step_name="analyze",
                                kwargs=(("text", "Great product!"),), line=3),))

    src = _emit(FlowGraph(steps=(analyze,), flow=flow, flows=(flow,)), tmp_path)

    assert "{ ...state, 'text': 'Great product!' }" in src
    assert "state['text'] =" not in src, "a literal TAKES must not mutate state"
    assert_valid_js(src, tmp_path)


def test_a_renamed_ref_kwarg_reads_the_source_key(tmp_path):
    """`summarize(text=@raw)`: the TAKES name and the state key differ, so the call
    site maps one onto the other. Passing `state` untouched would leave the step
    reading state['text'] — undefined."""
    load = _step(name="load", gives=FieldIR(name="raw", type=_STR))
    summarize = _step(name="summarize", takes=(FieldIR(name="text", type=_STR),),
                      gives=FieldIR(name="summary", type=_STR))
    flow = FlowIR(name="c", rescues=(), line=1, chain=(
        CallIR(step_name="load", kwargs=(), line=3),
        CallIR(step_name="summarize", kwargs=(("text", "@raw"),), line=4),
    ))

    src = _emit(FlowGraph(steps=(load, summarize), flow=flow, flows=(flow,)), tmp_path)

    assert "{ ...state, 'text': state['raw'] }" in src
    assert_valid_js(src, tmp_path)


def test_an_identity_ref_passes_state_untouched(tmp_path):
    """`@text` bound to TAKES `text` — what the `->` pipe sugar produces, and the
    common case. The key is already in state under that name: copying it onto
    itself would be noise in the file the author has to read."""
    src = _emit(_linear_graph(), tmp_path)

    assert "await summarize(state, 'summarize')" in src
    assert "...state" not in src


def test_judgment_result_is_unwrapped_into_state():
    """agent() returns the schema object — { <gives.name>: value } (§4.1). What
    lands in state must be the VALUE: downstream reads are state['r'] (a kwarg
    ref) and state['r'].score (a condition), exactly as in python and swift.
    Storing the wrapper would nest it twice and every read would come back
    undefined — at run time, far from here."""
    js = render_judgment_step_js(
        _step(name="assess", gives=FieldIR(name="r", type=_STR)), contracts={})

    assert "return result['r']" in js


def test_a_step_without_gives_is_called_for_its_effect(tmp_path):
    """A step may declare no GIVES. There is no state key to assign — but the call
    must still happen, and `state[undefined] = …` must not be emitted."""
    notify = _step(name="notify", takes=(), gives=None)
    flow = FlowIR(name="f", rescues=(), line=1,
                  chain=(CallIR(step_name="notify", kwargs=(), line=2),))

    src = _emit(FlowGraph(steps=(notify,), flow=flow, flows=(flow,)), tmp_path)

    assert "await notify(state, 'notify')" in src
    assert "= await notify" not in src
    assert_valid_js(src, tmp_path)


def test_a_flow_without_takes_never_references_args(tmp_path):
    """swift_judgment.clio declares no TAKES. An args guard emitted anyway would
    throw when the runtime legitimately hands the script nothing."""
    src = _emit_fixture("swift_judgment.clio", tmp_path)

    assert "args" not in src
    assert_valid_js(src, tmp_path)


def test_a_missing_required_arg_fails_loudly(tmp_path):
    """A declared TAKES that never arrives must stop the run at the top, naming the
    flow and the arg — not flow `undefined` into a prompt and produce a plausible
    answer to a question nobody asked."""
    src = _emit(_linear_graph(), tmp_path)

    assert "if (args['url'] === undefined)" in src
    assert "brief" in src and "url" in src
    assert "throw new Error" in src


def test_the_linear_fixture_compiles_end_to_end(tmp_path):
    """Hand-built IR can drift from what the builder produces. swift_minimal.clio
    is the real thing: `load(file=file) -> summarize(rows)`, two exact steps."""
    src = _emit_fixture("swift_minimal.clio", tmp_path)

    assert "state['file'] = args['file']" in src
    assert "state['rows'] = await load(state, 'load')" in src
    assert "state['summary'] = await summarize(state, 'summarize')" in src
    assert "function load(state)" in src and "function summarize(state)" in src
    assert_valid_js(src, tmp_path)


def test_each_reachable_step_is_emitted_exactly_once(tmp_path):
    """A step called twice in the chain is still ONE JS function. Emitting it twice
    would redeclare it — and module code is strict code, where a duplicate
    declaration is a SyntaxError, not a silent overwrite."""
    refine = _step(name="refine", gives=FieldIR(name="p", type=_STR))
    flow = FlowIR(name="f", rescues=(), line=1, chain=(
        CallIR(step_name="refine", kwargs=(), line=2),
        CallIR(step_name="refine", kwargs=(), line=3),
    ))

    src = _emit(FlowGraph(steps=(refine,), flow=flow, flows=(flow,)), tmp_path)

    assert src.count("async function refine(") == 1
    assert_valid_js(src, tmp_path)


def test_a_reserved_step_name_is_called_by_its_mangled_name(tmp_path):
    """The declaration is `delete$` (js_identifier). The CALL SITE must agree:
    `await delete(state, …)` is a SyntaxError, and a renderer that mangles one but
    not the other emits a file that never parses. The phase title keeps the source
    name — it is a label, not an identifier."""
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    graph = build_ir(parse(
        "STEP delete\n"
        "  TAKES: path: str\n"
        "  GIVES: gone: bool\n"
        "  MODE:  judgment\n"
        "\n"
        "FLOW purge\n"
        "  TAKES: path: str\n"
        "  delete(path=path)\n"
    ))

    src = _emit(graph, tmp_path)

    assert "await delete$(" in src
    assert "phase('delete')" in src
    assert_valid_js(src, tmp_path)


@pytest.mark.parametrize("name", ["state", "args", "meta", "agent", "phase", "log",
                                  "parallel", "pipeline"])
def test_a_step_named_after_a_script_global_does_not_shadow_it(name: str, tmp_path):
    """The script now declares `const state` and `export const meta`, and calls the
    agent() / phase() / log() globals. A step named after one of them is not a
    style problem: `function state(…)` beside `const state` is a duplicate
    declaration (SyntaxError), and `function agent(…)` would silently SHADOW the
    global — the judgment wrapper would then call itself, forever. Mangled for the
    same reason as the reserved words, and only reachable now that the flow body
    exists."""
    step = _step(name=name, gives=FieldIR(name="out", type=_STR))
    flow = FlowIR(name="f", rescues=(), line=1,
                  chain=(CallIR(step_name=name, kwargs=(), line=2),))

    src = _emit(FlowGraph(steps=(step,), flow=flow, flows=(flow,)), tmp_path)

    assert f"function {name}(" not in src, "the step shadows a script global"
    assert_valid_js(src, tmp_path)


def test_the_whole_script_calls_no_sandbox_forbidden_global(tmp_path):
    """Trap §6.2, checked on the assembled script and not only on one step:
    Date.now(), new Date() and Math.random() THROW in the sandbox.

    Comments are stripped first, and that is not a loophole — naming the three
    traps in the stub the author types into is *required*
    (test_exact_stub_names_the_globals_that_throw_in_the_sandbox). What must not
    exist is a line that CALLS them."""
    src = _emit(_linear_graph(), tmp_path)

    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("//"))

    for forbidden in ("Date.now(", "new Date(", "Math.random("):
        assert forbidden not in code


# ---------------------------------------------------------------------------
# Task 7 — IF / MATCH / WHILE render to native JS control flow
# ---------------------------------------------------------------------------

_BOOL = PrimitiveType(name="bool")


def _bool_step(name: str, field: str) -> StepIR:
    """A judgment step whose GIVES lands in state under `field`."""
    return _step(name=name, gives=FieldIR(name=field, type=_BOOL))


def test_control_flow_fixture_emits_valid_js(tmp_path):
    """swift_control_flow.clio is `assess -> MATCH r.level -> IF r.score > 0.5
    -> refine -> WHILE p.done != true MAX 3`. The three blocks become native JS."""
    src = _emit_fixture("swift_control_flow.clio", tmp_path)

    assert "if (" in src
    assert "switch (" in src
    assert "while (" in src
    assert_valid_js(src, tmp_path)


def test_condition_reads_the_state_key_not_the_step_name(tmp_path):
    """ConditionIR.step_name is the state key — the producing step's GIVES *field*
    name — despite what the attribute is called. In the fixture, `assess` GIVES
    `r`, so `IF r.score > 0.5` must read state['r'].score. Reading state['assess']
    would be `undefined` at run time, and `undefined > 0.5` is silently false: the
    ELSE branch would always win and no test comparing text alone would notice."""
    src = _emit_fixture("swift_control_flow.clio", tmp_path)

    assert "if (state['r'].score > 0.5) {" in src
    assert "switch (state['r'].level) {" in src
    assert "state['assess']" not in src


def test_conditions_use_strict_equality(tmp_path):
    """JS loose equality makes `0 == false` true. Emitting `==` would silently
    change flow semantics for int/bool comparisons."""
    cond = ConditionIR(step_name="done", field="value", op="==",
                       literal_value=False, literal_kind="bool")
    check = _bool_step("check", "done")
    flow = FlowIR(name="g",
                  chain=(IfBlockIR(condition=cond,
                                   then_body=(CallIR(step_name="check", kwargs=(), line=3),),
                                   else_body=(), line=2),),
                  rescues=(), line=1)

    src = _emit(FlowGraph(steps=(check,), flow=flow, flows=(flow,)), tmp_path)

    assert "state['done'].value === false" in src
    assert " == " not in src
    assert_valid_js(src, tmp_path)


def test_inequality_is_strict_too(tmp_path):
    """The fixture's `WHILE p.done != true` — `!=` must become `!==` for the same
    reason `==` becomes `===`; `null != true` and `null !== true` agree here, but
    `0 != false` is false while `0 !== false` is true."""
    src = _emit_fixture("swift_control_flow.clio", tmp_path)

    assert "state['p'].done !== true" in src
    assert " != " not in src


def test_boolop_renders_native_js_operators(tmp_path):
    """BoolOpIR nests, so the renderer must recurse and parenthesize: dropping the
    parens would let JS precedence (&& binds tighter than ||) re-associate the
    tree the author wrote."""
    left = ConditionIR(step_name="done", field="value", op="==",
                       literal_value=True, literal_kind="bool")
    right = ConditionIR(step_name="score", field="value", op=">",
                        literal_value=3, literal_kind="int")
    other = ConditionIR(step_name="done", field="value", op="!=",
                        literal_value=False, literal_kind="bool")
    cond = BoolOpIR(op="or", left=BoolOpIR(op="and", left=left, right=right), right=other)

    check = _bool_step("check", "done")
    flow = FlowIR(name="g",
                  chain=(IfBlockIR(condition=cond,
                                   then_body=(CallIR(step_name="check", kwargs=(), line=3),),
                                   else_body=(), line=2),),
                  rescues=(), line=1)

    src = _emit(FlowGraph(steps=(check,), flow=flow, flows=(flow,)), tmp_path)

    assert ("((state['done'].value === true) && (state['score'].value > 3)) "
            "|| (state['done'].value !== false)") in src
    assert_valid_js(src, tmp_path)


def test_match_renders_a_switch_with_one_break_per_arm(tmp_path):
    """A switch arm without `break` falls through into the next one — a real bug,
    not a style nit: `CASE low` would run `archive` AND then `flag`. Counted, not
    merely `'break' in src`, because a single break somewhere would satisfy that
    while three of the fixture's arms still fell through."""
    src = _emit_fixture("swift_control_flow.clio", tmp_path)
    lines = src.splitlines()

    labels = [ln for ln in lines if ln.strip().startswith(("case ", "default:"))]
    breaks = [ln for ln in lines if ln.strip() == "break"]

    assert "switch (state['r'].level) {" in src
    assert [ln.strip() for ln in labels] == ["case 'low':", "case 'mid':", "case 'high':"]
    assert len(breaks) == len(labels), "every arm must break, or it falls through"


def test_match_default_arm_becomes_default(tmp_path):
    """MatchCaseIR.value is None for DEFAULT (graph.py:336)."""
    archive = _bool_step("archive", "archived")
    flag = _bool_step("flag", "flagged")
    block = MatchBlockIR(
        state_field="r", sub_field="level",
        cases=(MatchCaseIR(value="low",
                           body=(CallIR(step_name="archive", kwargs=(), line=3),), line=3),
               MatchCaseIR(value=None,
                           body=(CallIR(step_name="flag", kwargs=(), line=4),), line=4)),
        line=2,
    )
    flow = FlowIR(name="g", chain=(block,), rescues=(), line=1)

    src = _emit(FlowGraph(steps=(archive, flag), flow=flow, flows=(flow,)), tmp_path)

    assert "case 'low':" in src
    assert "default:" in src
    assert_valid_js(src, tmp_path)


def test_while_emits_the_mandatory_max_bound(tmp_path):
    """WhileBlockIR.max_iters is mandatory. A `while` whose only exit is the
    condition is a runaway — a latent bug of exactly this shape is already on
    file against the go target. Built by hand so the bound is a known value."""
    cond = ConditionIR(step_name="done", field="value", op="==",
                       literal_value=False, literal_kind="bool")
    check = _bool_step("check", "done")
    work = _step(name="work")
    flow = FlowIR(
        name="loop",
        chain=(WhileBlockIR(condition=cond, max_iters=7,
                            body=(CallIR(step_name="work", kwargs=(), line=5),),
                            line=4),),
        rescues=(), line=1,
    )
    graph = FlowGraph(steps=(check, work), flow=flow, flows=(flow,))

    src = _emit(graph, tmp_path)

    assert "let _i_4 = 0" in src, "the counter is suffixed with the WHILE's source line"
    assert "while ((state['done'].value === false) && _i_4 < 7) {" in src
    assert "_i_4++" in src, "the bound needs a counter that actually increments"
    assert_valid_js(src, tmp_path)


def test_nested_while_counters_do_not_collide(tmp_path):
    """Two WHILEs, one inside the other. A shared counter name would make the
    inner loop's `let` a redeclaration (a SyntaxError only if in the same block —
    here it shadows instead, and the OUTER bound would then never be reached: its
    counter stops incrementing the moment the inner loop shadows it). The source
    line is what keeps the two apart."""
    cond = ConditionIR(step_name="done", field="value", op="==",
                       literal_value=False, literal_kind="bool")
    check = _bool_step("check", "done")
    work = _step(name="work")
    inner = WhileBlockIR(condition=cond, max_iters=2,
                         body=(CallIR(step_name="work", kwargs=(), line=7),), line=6)
    outer = WhileBlockIR(condition=cond, max_iters=9, body=(inner,), line=4)
    flow = FlowIR(name="loop", chain=(outer,), rescues=(), line=1)

    src = _emit(FlowGraph(steps=(check, work), flow=flow, flows=(flow,)), tmp_path)

    assert "let _i_4 = 0" in src and "let _i_6 = 0" in src
    assert "_i_4 < 9" in src and "_i_6 < 2" in src
    assert_valid_js(src, tmp_path)


def test_agents_inside_a_block_carry_the_blocks_phase(tmp_path):
    """§4.3: `phase()` is global state in the Workflow runtime, so only the top
    level moves it — a step nested in an IF gets the BLOCK's phase passed through
    the call, and the flow never calls phase() from inside the block."""
    cond = ConditionIR(step_name="done", field="value", op="==",
                       literal_value=True, literal_kind="bool")
    check = _bool_step("check", "done")
    flow = FlowIR(name="g",
                  chain=(IfBlockIR(condition=cond,
                                   then_body=(CallIR(step_name="check", kwargs=(), line=3),),
                                   else_body=(), line=2),),
                  rescues=(), line=1)

    src = _emit(FlowGraph(steps=(check,), flow=flow, flows=(flow,)), tmp_path)
    body = src.split("export const meta")[0]

    assert "phase('if:done')" in src           # declared once, at the top level
    assert "await check(state, 'if:done')" in src   # the nested call carries it
    assert body.count("phase('if:done')") <= 1, "phase() must not be called in-block"


# ---------------------------------------------------------------------------
# Task 8 — FOR EACH: a plain loop, parallel() for one call, pipeline() for a chain
# ---------------------------------------------------------------------------


def _foreach_graph(*, parallel: bool, n_steps: int, collector: str | None) -> FlowGraph:
    """A flow whose only element is a FOR EACH over `docs`, with a 1- or 2-call body.

    `score` reads `@verdict` — *review*'s GIVES field, i.e. the output of the stage
    right before it. That is the whole point of a multi-stage body, and the only
    kwarg shape pipeline() can serve: a stage callback receives (prevResult,
    originalItem, index) and nothing else.
    """
    review = _step(name="review", takes=(FieldIR(name="doc", type=_STR),),
                   gives=FieldIR(name="verdict", type=_STR))
    score = _step(name="score", takes=(FieldIR(name="r", type=_STR),),
                  gives=FieldIR(name="rating", type=_STR))
    body: list[CallIR] = [CallIR(step_name="review", kwargs=(("doc", "@doc"),), line=6)]
    if n_steps == 2:
        body.append(CallIR(step_name="score", kwargs=(("r", "@verdict"),), line=7))
    fe = ForEachIR(loop_var="doc", collection="docs", body=tuple(body), line=5,
                   parallel=parallel, collector=collector)
    flow = FlowIR(name="rev", chain=(fe,), rescues=(), line=1)
    return FlowGraph(steps=(review, score), flow=flow, flows=(flow,))


def test_sequential_for_each_is_a_plain_loop(tmp_path):
    src = _emit(_foreach_graph(parallel=False, n_steps=1, collector=None), tmp_path)

    assert "for (const doc of state['docs'])" in src
    assert "await parallel(" not in src
    assert "await pipeline(" not in src
    assert_valid_js(src, tmp_path)


def test_the_loop_variable_is_bound_from_the_loop_not_from_state(tmp_path):
    """swift_parallel.clio's `FOR EACH item IN items: classify(item=item)` builds the
    kwarg ('item', '@item') — an identity ref, which OUTSIDE a loop means "already
    in state under that key" and lets the call site pass `state` untouched. Inside
    a loop it means the exact opposite: the loop variable is a JS binding and never
    a state key, so state['item'] is undefined and every item would be classified
    on nothing — at run time, with no syntax error to catch it."""
    src = _emit_fixture("swift_parallel.clio", tmp_path)

    assert "{ ...state, 'item': item }" in src


def test_parallel_for_each_with_one_step_uses_parallel(tmp_path):
    """A single-call body: parallel() and pipeline() are equivalent; use parallel()."""
    src = _emit(_foreach_graph(parallel=True, n_steps=1, collector="reviews"), tmp_path)

    assert "await parallel(" in src
    assert "state['reviews'] =" in src
    assert_valid_js(src, tmp_path)


def test_parallel_is_handed_thunks_not_already_running_promises(tmp_path):
    """parallel() takes THUNKS — `(doc) => () => review(…)`. Dropping the inner arrow
    (`.map((doc) => review(doc))`) starts every call during the map and hands
    parallel() a list of promises that are already in flight: the concurrency limit
    it exists to enforce would be bypassed, and the emitted text would still look
    plausible."""
    src = _emit(_foreach_graph(parallel=True, n_steps=1, collector="reviews"), tmp_path)

    assert "(doc) => () => review(" in src


def test_parallel_for_each_with_several_steps_uses_pipeline(tmp_path):
    """A multi-step body is a per-item stage chain — pipeline() runs each item
    through all stages with NO barrier between them. parallel() would force one,
    idling fast items behind the slowest of each stage. The Workflow tool's own
    guidance is: default to pipeline()."""
    src = _emit(_foreach_graph(parallel=True, n_steps=2, collector="scores"), tmp_path)

    assert "await pipeline(" in src
    assert "await parallel(" not in src
    assert_valid_js(src, tmp_path)


def test_a_pipeline_stage_reads_its_predecessor_from_prev_not_from_state(tmp_path):
    """`score(r=@verdict)` reads *review*'s GIVES. Inside a pipeline that value is
    the stage callback's `prevResult` — it is NOT in state, and it must not be: the
    items run concurrently, so writing each one's output into the shared state key
    would race. A renderer that emitted state['verdict'] here would compile, parse,
    and score every document on `undefined`."""
    src = _emit(_foreach_graph(parallel=True, n_steps=2, collector="scores"), tmp_path)

    assert "(doc) => review({ ...state, 'doc': doc }, 'each:docs')" in src
    assert "(prev, doc) => score({ ...state, 'r': prev }, 'each:docs')" in src
    assert "state['verdict']" not in src


def test_a_failed_item_does_not_flow_into_the_collector(tmp_path):
    """A thunk that throws resolves to `null` in the result array — parallel() and
    pipeline() never reject. Unfiltered, those nulls land in state[collector] and
    fail somewhere else, later.

    Filtered on null/undefined and NOT with `.filter(Boolean)`: a step that GIVES a
    bool or a str legitimately produces `false` / `''`, and Boolean would silently
    drop those successful items alongside the failed ones."""
    src = _emit(_foreach_graph(parallel=True, n_steps=1, collector="reviews"), tmp_path)

    assert "filter((r) => r !== null && r !== undefined)" in src
    assert "filter(Boolean)" not in src


def test_parallel_agents_carry_phase_via_opts_not_the_global(tmp_path):
    """phase() is global state and racy inside parallel()/pipeline() stages — the
    last writer wins. Agents spawned there receive `phase` through agent({phase}),
    which is what the step wrapper's `phaseName` parameter carries. The global moves
    once, at the top level."""
    src = _emit(_foreach_graph(parallel=True, n_steps=2, collector="scores"), tmp_path)

    assert re.findall(r"^phase\('([^']+)'\)", src, re.M) == ["each:docs"]
    # Both stages take the phase as an ARGUMENT (the wrapper hands it to
    # agent({phase})); neither calls the global from inside the pipeline.
    assert src.count(", 'each:docs')") == 2


def test_a_condition_on_the_loop_variable_reads_the_loop_variable(tmp_path):
    """swift_foreach_seq.clio: `FOR EACH a IN assessments: MATCH a.level` builds
    MatchBlockIR(state_field='a') — and `a` is the LOOP VARIABLE, not a state key.
    state['a'] is undefined, `undefined.level` throws, and a switch on it would take
    no arm at all. The renderer has to know what is in scope."""
    src = _emit_fixture("swift_foreach_seq.clio", tmp_path)

    assert "switch (a.level) {" in src
    assert "if (b.level === 'high') {" in src
    assert "state['a']" not in src and "state['b']" not in src
    assert_valid_js(src, tmp_path)


def test_a_nested_loop_iterates_the_enclosing_loop_variable(tmp_path):
    """`FOR EACH b IN a` nested in `FOR EACH a IN rows`: the inner collection is the
    outer loop's variable, not a state key. state['a'] would be undefined, and
    `for (const b of undefined)` throws."""
    tag = _step(name="tag", takes=(FieldIR(name="cell", type=_STR),),
                gives=FieldIR(name="tagged", type=_STR))
    inner = ForEachIR(loop_var="b", collection="a", line=4, parallel=False,
                      body=(CallIR(step_name="tag", kwargs=(("cell", "@b"),), line=5),))
    outer = ForEachIR(loop_var="a", collection="rows", body=(inner,), line=3,
                      parallel=False)
    flow = FlowIR(name="grid", chain=(outer,), rescues=(), line=1)

    src = _emit(FlowGraph(steps=(tag,), flow=flow, flows=(flow,)), tmp_path)

    assert "for (const a of state['rows']) {" in src
    assert "for (const b of a) {" in src
    assert "{ ...state, 'cell': b }" in src
    assert_valid_js(src, tmp_path)


def test_parallel_fixture_emits_valid_js(tmp_path):
    """The real thing, end to end: `load(file="in.csv") -> FOR EACH item IN items
    PARALLEL AS labels: classify(item=item)`."""
    src = _emit_fixture("swift_parallel.clio", tmp_path)

    assert "await parallel(" in src
    assert "state['labels'] =" in src
    assert_valid_js(src, tmp_path)


# ---------------------------------------------------------------------------
# Task 9 — sub-flows, inlined as local async functions
# ---------------------------------------------------------------------------


def test_sub_flows_are_inlined_not_nested(tmp_path):
    """workflow_subflow.clio chains FLOW pipeline -> level_a -> level_b -> level_c:
    three levels of nesting, which is exactly what `workflow({scriptPath})` cannot
    express (the tool caps nesting at one level: a workflow() inside a child
    throws). Inlining each called flow as a local async function sidesteps the cap
    and keeps the script self-contained (§4.2)."""
    src = _emit_fixture("workflow_subflow.clio", tmp_path, flow="pipeline")

    assert "async function flow_" in src
    assert "workflow(" not in src, "must inline, not delegate to a nested workflow()"
    assert_valid_js(src, tmp_path)


def test_every_reachable_sub_flow_gets_one_function(tmp_path):
    src = _emit_fixture("workflow_subflow.clio", tmp_path, flow="pipeline")

    for name in ("level_a", "level_b", "level_c"):
        assert f"async function flow_${name}(state, phase$) {{" in src
    # The entry flow is the script body, not a function: emitting it as one too
    # would leave a function nothing calls.
    assert "async function flow_$pipeline" not in src


def test_sub_flow_publishes_its_gives_into_the_parent_state(tmp_path):
    """A sub-flow's declared GIVES land as TOP-LEVEL parent state keys, exactly as
    python (`state.update(run_x(...))`, python.py:686-693) and go emit them.

    This is what makes a downstream read resolve: in `s2(b=b) -> level_c(c=c)` the
    `->` sugar binds level_c's kwarg from s2's GIVES, and level_b's own GIVES `d`
    is produced INSIDE level_c. Binding the result under the call-site name
    (`state['level_c'] = …`) instead would leave `state['d']` undefined — JS reads
    it silently, so the flow would return `{d: undefined}` rather than fail."""
    src = _emit_fixture("workflow_subflow.clio", tmp_path, flow="pipeline")

    assert "Object.assign(state, await flow_$level_a(" in src
    assert "state['level_a']" not in src
    assert "  return { 'd': state['d'] }" in src


def test_sub_flow_call_never_passes_the_parent_state_object(tmp_path):
    """The callee WRITES into the object it is handed (every step in its chain
    binds its GIVES there), unlike a step function, which only reads. Handing it
    `state` itself would leak the sub-flow's intermediate keys into the parent —
    clobbering a parent key of the same name — and, inside parallel(), concurrent
    items would race on the shared object. So the input is always a fresh copy,
    even when every kwarg is an identity ref and the copy looks like a no-op."""
    src = _emit_fixture("workflow_subflow.clio", tmp_path, flow="pipeline")

    assert "await flow_$level_a({ ...state }" in src
    # Anchored on `await `: the DECLARATION reads `flow_$level_a(state, phase$)`,
    # where `state` is the parameter — it is the CALL that must not hand over the
    # parent's object. A step call may (and does): it only reads.
    assert "await flow_$level_a(state," not in src
    assert "await s1(state, phase$)" in src


def test_sub_flow_steps_are_emitted(tmp_path):
    """The entry flow calls no step at all — every step lives in a sub-flow. The
    step collector must follow FlowCallIR boundaries, or the script would call
    functions it never declares."""
    src = _emit_fixture("workflow_subflow.clio", tmp_path, flow="pipeline")

    assert "function s1(state) {" in src          # exact stub
    assert "function s2(state) {" in src
    assert "async function s3(state, phaseName) {" in src   # judgment
    assert_valid_js(src, tmp_path)


def test_agent_inside_a_sub_flow_carries_the_call_site_phase(tmp_path):
    """`phase()` is only moved at the top level of the entry flow (§4.3), and
    `meta.phases` declares exactly those titles. A sub-flow is called from a phase
    it does not know at emit time — the same function can be called from two sites
    — so the phase travels as an argument and is threaded down to the agent, rather
    than being frozen into a literal that could name an undeclared phase."""
    src = _emit_fixture("workflow_subflow.clio", tmp_path, flow="pipeline")

    # Top level: the literal, which is a phase meta declares.
    assert "phase('level_a')" in src
    assert "Object.assign(state, await flow_$level_a({ ...state }, 'level_a'))" in src
    # Inside a sub-flow: the parameter, propagated to the callee and to the agent.
    assert "Object.assign(state, await flow_$level_c({ ...state }, phase$))" in src
    assert "await s3(state, phase$)" in src        # the judgment step, 3 levels deep
    assert "phase: phaseName," in src              # …which hands it to agent()
    # Exactly one phase() call: the entry flow's single top-level element. A
    # sub-flow never moves the global (it does not own a phase).
    assert src.count("phase('") == 1


def test_unreachable_flow_gets_no_function(tmp_path):
    """workflow_two_flows.clio declares alpha and beta, neither calling the other.
    Compiling alpha must not emit a function for beta: dead code in a file the
    author has to read and fill in."""
    src = _emit_fixture("workflow_two_flows.clio", tmp_path, flow="alpha")

    assert "async function flow_" not in src
    assert "summarize" not in src, "beta's step must not be emitted either"
    assert_valid_js(src, tmp_path)


def _flow_calling(name: str, callee: str) -> FlowIR:
    return FlowIR(
        name=name, rescues=(), line=1,
        chain=(FlowCallIR(flow_name=callee, kwargs=(), line=2),),
    )


def test_self_recursive_flow_is_refused(tmp_path):
    """E_WF_007. The IR builder rejects flow recursion for any source it parses
    (builder.py:976-1009), so this graph is hand-built — the emitter is a public
    seam, and its own inliner is what would break: a flow calling itself inlines to
    a function calling itself, which overflows the stack at run time rather than
    failing at compile time."""
    loop = _flow_calling("a", "a")
    graph = FlowGraph(steps=(), flow=loop, flows=(loop,))

    with pytest.raises(ValueError, match="E_WF_007"):
        _emit(graph, tmp_path)


def test_flow_call_cycle_is_refused(tmp_path):
    """Same refusal for an indirect cycle: a -> b -> a."""
    a, b = _flow_calling("a", "b"), _flow_calling("b", "a")
    graph = FlowGraph(steps=(), flow=a, flows=(a, b))

    with pytest.raises(ValueError, match="E_WF_007"):
        _emit(graph, tmp_path)


def test_sub_flow_as_parallel_body_collects_its_gives_objects(tmp_path):
    """`FOR EACH u IN urls PARALLEL AS results: enrich(url=u)` — the body is a
    sub-flow call, which the IR builder explicitly allows (builder.py:2057-2069:
    "the collector receives a list of the sub-flow's GIVES dicts at runtime").

    The thunk is the inlined function, so the collector fills with `{summary: …}`
    objects — no extraction, which is what that IR rule says a collector holds."""
    src = _emit_fixture("workflow_subflow_parallel.clio", tmp_path, flow="batch")

    assert "state['results'] = (await parallel(" in src
    assert "state['urls'].map((u) => () => flow_$enrich({ ...state, 'url': u }," in src
    assert "workflow(" not in src
    assert_valid_js(src, tmp_path)


# ---------------------------------------------------------------------------
# Task 10 — ON_FAIL chains, RESCUE handlers, RESUME
# ---------------------------------------------------------------------------


def _one_call_flow(step_name: str, name: str = "g") -> FlowIR:
    return FlowIR(
        name=name, rescues=(), line=1,
        chain=(CallIR(step_name=step_name, kwargs=(), line=2),),
    )


def test_on_fail_retry_loops_without_backoff(tmp_path):
    """Retries run back-to-back — the sandbox has no clock. W_WF_002 says so at
    compile time; the emitted code must not reach for one anyway."""
    chain = OnFailChainIR(strategies=(OnFailStrategyIR(kind="retry", max_retries=3),))
    step = _step(name="flaky", on_fail=chain)
    flow = _one_call_flow("flaky")

    src = _emit(FlowGraph(steps=(step,), flow=flow, flows=(flow,)), tmp_path)

    assert "attempt < 3" in src
    # …on the CODE: the script header names Date.now() in prose, to warn the
    # author away from it. See _code_only.
    assert "Date.now" not in _code_only(src)
    assert "setTimeout" not in _code_only(src)
    assert_valid_js(src, tmp_path)


def test_on_fail_retry_only_rethrows_the_last_error(tmp_path):
    """A retry chain that exhausts must FAIL, not fall through.

    Swallowing the error would hand `undefined` to the next step — the failure
    then surfaces somewhere else, on a step that is not the broken one."""
    chain = OnFailChainIR(strategies=(OnFailStrategyIR(kind="retry", max_retries=2),))
    step = _step(name="flaky", on_fail=chain)
    flow = _one_call_flow("flaky")

    src = _emit(FlowGraph(steps=(step,), flow=flow, flows=(flow,)), tmp_path)

    assert "throw lastError" in src
    assert_valid_js(src, tmp_path)


def test_on_fail_lives_in_the_step_function_not_at_the_call_site(tmp_path):
    """ON_FAIL is declared on the STEP, so it must hold at EVERY call site.

    Rendered at the call site instead, it would be silently dropped inside a
    `parallel()` / `pipeline()` body: that path builds a thunk EXPRESSION from
    `call_js` (_workflow_loops) and never walks the statement dispatcher. Same
    placement as python / go / swift, which all render the chain inside the step.
    """
    chain = OnFailChainIR(strategies=(OnFailStrategyIR(kind="retry", max_retries=3),))
    step = _step(name="flaky", on_fail=chain)

    fn = render_judgment_step_js(step, {})

    assert "attempt < 3" in fn, "the retry loop belongs to the step function"


def test_on_fail_fallback_calls_the_fallback_step_then_aborts(tmp_path):
    """swift_judgment_onfail.clio: retry(2) then escalate then fallback(naive)
    then abort("detection exhausted")."""
    src = _emit_fixture("swift_judgment_onfail.clio", tmp_path)

    assert "attempt < 2" in src
    assert "await naive(state, phaseName)" in src   # the fallback, same inputs
    assert "function naive(state)" in src           # …and its function is emitted
    assert "throw new Error('detection exhausted')" in src
    assert_valid_js(src, tmp_path)


def test_on_fail_abort_only_throws_the_declared_message(tmp_path):
    """swift_onfail_abort_only.clio: ON_FAIL: abort("boom"). No retry clause, so
    the attempt runs once and the abort message is what the flow sees."""
    src = _emit_fixture("swift_onfail_abort_only.clio", tmp_path)

    assert "throw new Error('boom')" in src
    assert_valid_js(src, tmp_path)


def test_rescue_catches_and_resumes(tmp_path):
    src = _emit_fixture("workflow_rescue.clio", tmp_path)

    assert "try {" in src
    assert "catch (_err) {" in src
    assert_valid_js(src, tmp_path)


def test_rescue_body_reads_the_caught_error(tmp_path):
    """`risky.error.message` / `.type` (ErrorAccessIR) are the fields of the error
    the catch actually binds — `err.name` is the JS analog of python's
    `type(_err).__name__` (python.py:608-616)."""
    src = _emit_fixture("workflow_rescue.clio", tmp_path)

    assert "'reason': _err.message" in src
    assert "'err_type': _err.name" in src
    assert_valid_js(src, tmp_path)


def test_resume_binds_the_fallback_value_under_the_rescued_step_key(tmp_path):
    """RESUME(recover.z) where `risky` GIVES `y`: the flow continues, and every
    downstream reader keys on `y`.

    The two names differ in the fixture on purpose. State is keyed by the GIVES
    FIELD name and holds the UNWRAPPED value (_workflow_flow_renderer:_render_call,
    _workflow_step_renderers), so the value is at `state['z']` — not under the
    step's name, and not nested. `state['recover']['z']` would be `undefined`,
    silently, at run time."""
    src = _emit_fixture("workflow_rescue.clio", tmp_path)

    assert "state['y'] = state['z']" in src
    assert "state['recover']" not in src
    assert_valid_js(src, tmp_path)


def test_rescue_wraps_the_step_that_can_actually_throw(tmp_path):
    """The whole chain rests on the T4 guard: agent() returns null on terminal
    failure, it does NOT throw. The wrapper converts that null into a throw — so
    this catch can see it. Without that line the handler is dead code."""
    src = _emit_fixture("workflow_rescue.clio", tmp_path)

    assert "if (result === null || result === undefined) {" in src
    protected = src[src.index("try {"):src.index("catch (_err) {")]
    assert "await risky(" in protected, "the rescued call must be inside the try"


def test_rescue_body_ending_in_abort_throws(tmp_path):
    """`abort("msg")` is a synthetic CallIR the IR builder injects into RESCUE
    bodies only (builder.py:1530-1533) — it names no STEP, so the dispatcher must
    catch it before it looks the name up."""
    step = _step(name="risky")
    rescue = RescueBlockIR(
        step_name="risky",
        body=(CallIR(step_name="abort", kwargs=(("message", "no way back"),), line=5),),
        line=4,
    )
    flow = FlowIR(
        name="g", rescues=(rescue,), line=1,
        chain=(CallIR(step_name="risky", kwargs=(), line=2),),
    )

    src = _emit(FlowGraph(steps=(step,), flow=flow, flows=(flow,)), tmp_path)

    assert "catch (_err) {" in src
    assert "throw new Error('no way back')" in src
    assert_valid_js(src, tmp_path)


def test_resume_with_no_rescued_key_in_scope_is_refused():
    """A RESUME the renderer cannot bind must fail at compile time.

    It has one key to write and it comes from context — the GIVES of the step the
    enclosing handler protects. The dispatcher only carries that key through a
    RESCUE body; a FOR EACH body, whose render_body callback drops it, is the case
    that would otherwise be guessed at. Emitting nothing there would drop the
    recovery silently and let the chain continue on a stale value.
    """
    from clio.emitters._workflow_flow_renderer import _render_item

    resume = ResumeIR(fallback_step="recover", field_name="z", line=9)

    with pytest.raises(NotImplementedError, match="RESUME"):
        _render_item(resume, {}, "'p'", "", {}, None)


# Every fixture this target compiles, with the entry FLOW where the source has
# more than one. The sweep below is only as good as this list is complete.
_ALL_FIXTURES = [
    ("swift_minimal.clio", None),
    ("swift_judgment.clio", None),
    ("swift_judgment_cache.clio", None),
    ("swift_judgment_onfail.clio", None),
    ("swift_onfail_abort_only.clio", None),
    ("swift_contract.clio", None),
    ("swift_control_flow.clio", None),
    ("swift_foreach_seq.clio", None),
    ("swift_foreach_take.clio", None),
    ("swift_parallel.clio", None),
    ("swift_parallel_shared.clio", None),
    ("swift_sideeffect.clio", None),
    ("workflow_rescue.clio", None),
    ("workflow_subflow.clio", "pipeline"),
    ("workflow_subflow_parallel.clio", "batch"),
    ("workflow_two_flows.clio", "alpha"),
]


def _code_only(src: str) -> str:
    """The script with its full-line `//` comments removed.

    The exact-step stub WARNS the author, in prose, that `Date.now()` and friends
    throw (_workflow_step_renderers) — so a raw substring sweep would flag the very
    comment that exists to prevent the bug. What must not appear is a CALL."""
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


@pytest.mark.parametrize("fixture,flow", _ALL_FIXTURES)
def test_no_emitted_script_calls_a_forbidden_global(fixture, flow, tmp_path):
    """Date.now(), new Date() and Math.random() THROW in the workflow sandbox
    (§6.2), and there are no timers. This is a whole-output guard across every
    fixture, not a per-node check: the trap is that any renderer might reach for a
    timestamp, a retry jitter or a generated id, and each one only fails at run
    time, in the user's session."""
    code = _code_only(_emit_fixture(fixture, tmp_path, flow=flow))

    for forbidden in ("Date.now(", "new Date(", "Math.random(", "setTimeout("):
        assert forbidden not in code, f"{fixture} emits {forbidden} — it throws in the sandbox"
    assert_valid_js(code, tmp_path)


# ---------------------------------------------------------------------------
# Task 11 — .clio/ sidecar + install README
# ---------------------------------------------------------------------------


def _compile_fixture(name: str, tmp_path: Path, flow: str | None = None) -> Path:
    """Compile a fixture and return the OUTPUT DIRECTORY.

    `_emit_fixture` returns the script text, which is what a test about the *code*
    wants. The sidecar and the README are files beside the script, so a test about
    them needs the directory. Both go through `cli.main`, not the emitter directly:
    `source_path` / `sources` are threaded by the CLI (cli.py:160-165), and an
    emitter called in-process never sees them — which is exactly the bug this
    section has to be able to catch.
    """
    from clio.cli import main

    out = tmp_path / "out"
    argv = ["compile", f"tests/fixtures/{name}",
            "--target", "claude-workflow", "--output", str(out)]
    if flow is not None:
        argv += ["--flow", flow]
    rc = main(argv)
    assert rc == 0, f"{name} failed to compile"
    return out


def test_emits_sidecar_and_readme(tmp_path):
    """The sidecar is what makes `clio import` round-trip. Layout is dictated by
    _sidecar.py:write_sidecar — `.clio/source.clio`, NOT `.clio/source/<name>.clio`.

    Byte-identical, not text-identical: `clio import` recovers the source verbatim,
    and the manifest hashes the bytes. A README-shaped test would pass on a
    re-serialized source that lost its trailing newline; `clio import --mode strict`
    would then report drift on a file nobody touched.
    """
    out = _compile_fixture("swift_judgment.clio", tmp_path)

    original = Path("tests/fixtures/swift_judgment.clio").read_bytes()
    assert (out / ".clio" / "source.clio").read_bytes() == original
    assert (out / ".clio" / "manifest.json").exists()

    readme = (out / "README.md").read_text()
    assert ".claude/workflows/" in readme      # how to install it
    assert "no API key" in readme              # the point of a host-orchestrated target


def test_sidecar_manifest_hashes_the_emitted_files(tmp_path):
    """`file_hashes` is what `clio import` compares against to detect drift, so it
    has to cover the files this target actually writes. An empty (or script-less)
    map would make every hand-edit invisible and `--mode strict` a rubber stamp."""
    out = _compile_fixture("swift_judgment.clio", tmp_path)

    manifest = json.loads((out / ".clio" / "manifest.json").read_text())
    assert set(manifest["file_hashes"]) == {"classifier.workflow.js", "README.md"}
    assert manifest["clio_version"]
    assert manifest["source_hash"].startswith("sha256:")


def test_sidecar_round_trips_through_clio_import(tmp_path):
    """The reason the sidecar exists, asserted end to end rather than by proxy:
    `clio import --mode strict` on a freshly emitted workflow gives the source back,
    byte for byte. It refuses on any hash drift, so this also proves the manifest
    describes the emitted tree and not some other one."""
    from clio.cli import main

    out = _compile_fixture("swift_judgment.clio", tmp_path)
    recovered = tmp_path / "recovered.clio"

    rc = main(["import", str(out), "--mode", "strict", "--output", str(recovered)])

    assert rc == 0
    assert recovered.read_bytes() == Path("tests/fixtures/swift_judgment.clio").read_bytes()


def test_multi_file_source_is_stored_whole_in_the_sidecar(tmp_path):
    """A FROM…IMPORT project only round-trips if EVERY source is stored, not just
    the entry: recovering `main.clio` alone would give back a file whose import
    points at a `lib.clio` that is not there. cli.py hands this target
    `sources=tuple(parsed)` (the same argument it hands claude-skill) — this is the
    test that the emitter forwards it instead of dropping it on the floor.

    `--flow` is required: two files, two EXPOSE FLOWs, and this target refuses to
    guess which one to compile (E_WF_006).
    """
    from clio.cli import main

    (tmp_path / "lib.clio").write_text(
        "EXPOSE CONTRACT Article\n"
        "  SHAPE: {title: str, body: str}\n"
        "\n"
        "STEP score\n"
        "  MODE: judgment\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "\n"
        "EXPOSE FLOW classify\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "  score(article=article)\n"
    )
    (tmp_path / "main.clio").write_text(
        'FROM "./lib.clio" IMPORT Article, classify\n'
        "\n"
        "EXPOSE FLOW pipeline\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "  classify(article=article)\n"
    )
    out = tmp_path / "out"

    rc = main(["compile", str(tmp_path / "main.clio"), "--target", "claude-workflow",
               "--flow", "pipeline", "--output", str(out)])
    assert rc == 0

    manifest = json.loads((out / ".clio" / "manifest.json").read_text())
    assert manifest["entry"] == "main.clio"
    assert set(manifest["sources"]) == {"main.clio", "lib.clio"}
    assert (out / ".clio" / "sources" / "lib.clio").read_bytes() == (
        tmp_path / "lib.clio"
    ).read_bytes()


def test_no_sidecar_when_there_is_no_source_path(tmp_path):
    """An emitter called in-process (tests, scripts) has no `.clio` file to copy.
    It must still emit the script — not crash, and not write a sidecar claiming to
    hold a source it never saw."""
    flow = FlowIR(name="triage", chain=(), rescues=(), line=1)
    graph = FlowGraph(steps=(), flow=flow, flows=(flow,))

    WorkflowEmitter().emit(graph, tmp_path)

    assert (tmp_path / "triage.workflow.js").exists()
    assert not (tmp_path / ".clio").exists()


@pytest.mark.parametrize("fixture,flow", _ALL_FIXTURES)
def test_every_compiled_flow_gets_an_install_readme(fixture, flow, tmp_path):
    """The README is the only place the install step is written down: the emitter
    never writes into `.claude/workflows/` itself (§3), so a flow shipped without it
    is a script the author has no instructions for."""
    out = _compile_fixture(fixture, tmp_path, flow=flow)

    readme = (out / "README.md").read_text()
    script = next(iter(out.glob("*.workflow.js")))
    assert script.name in readme, "the README must name the script it tells you to copy"
    assert ".claude/workflows/" in readme


def test_readme_states_that_exact_stubs_must_stay_pure(tmp_path):
    """swift_minimal is exact-only. Its stubs THROW until the author fills them in,
    and what they fill in has to be pure — the sandbox has no filesystem, no
    network, no process and no clock. The stub says so in a comment; the README is
    where the author looks BEFORE opening the script."""
    out = _compile_fixture("swift_minimal.clio", tmp_path)

    readme = (out / "README.md").read_text()
    assert "pure" in readme.lower()
    assert "no filesystem, no network, no process and no clock" in readme
    # The exact steps are named, so the author knows what is left to implement.
    assert "`load`" in readme and "`summarize`" in readme


def test_readme_warns_that_this_flows_cache_is_ignored(tmp_path):
    """swift_judgment_cache declares `CACHE: ttl(24h)`. The compiler prints W_WF_001
    at compile time — a line the author will not see again once the script is on
    disk. The README is the durable copy, and it must carry the code so the two are
    greppably the same fact."""
    out = _compile_fixture("swift_judgment_cache.clio", tmp_path)

    readme = (out / "README.md").read_text()
    assert "W_WF_001" in readme
    assert "W_WF_002" not in readme, "this flow declares no ON_FAIL retry"
    assert "W_WF_003" not in readme, "this flow declares no CONTRACT ASSERT"


def test_readme_warns_that_this_flows_retries_have_no_backoff(tmp_path):
    """swift_judgment_onfail declares `retry(2)`. Retries run back-to-back here: the
    sandbox has no clock. An author who reads 'retry' and assumes exponential
    backoff will hammer a flaky dependency — hence W_WF_002, in writing."""
    out = _compile_fixture("swift_judgment_onfail.clio", tmp_path)

    readme = (out / "README.md").read_text()
    assert "W_WF_002" in readme
    assert "W_WF_001" not in readme, "this flow declares no CACHE"


def test_readme_warns_that_this_flows_asserts_are_not_enforced(tmp_path):
    """swift_judgment's CONTRACT carries `ASSERT: confidence >= 0.0`. The host
    enforces the JSON Schema; nothing enforces the ASSERT predicate. This is the
    degradation with teeth — an author who believes the ASSERT holds will not
    re-check the value downstream — so silence here would be the dishonest kind."""
    out = _compile_fixture("swift_judgment.clio", tmp_path)

    readme = (out / "README.md").read_text()
    assert "W_WF_003" in readme
    assert "W_WF_001" not in readme, "this flow declares no CACHE"


def test_readme_of_an_undegraded_flow_claims_no_degradation(tmp_path):
    """The negative control, and the whole point of deriving the section from the
    graph: swift_minimal has no CACHE, no ON_FAIL and no ASSERT. A README that
    listed all three warnings unconditionally would be a generic disclaimer — it
    would tell this author their cache is ignored when they never wrote one, and
    the section would stop being read on the flow where it matters."""
    out = _compile_fixture("swift_minimal.clio", tmp_path)

    readme = (out / "README.md").read_text()
    assert "W_WF_001" not in readme
    assert "W_WF_002" not in readme
    assert "W_WF_003" not in readme


@pytest.mark.parametrize("fixture,flow", _ALL_FIXTURES)
def test_readme_never_warns_about_a_degradation_the_compiler_did_not(fixture, flow, tmp_path, capsys):
    """README ≡ stderr, swept across every fixture.

    These are the same three predicates evaluated twice — once by
    validate_graph_for_workflow into `warn`, once by render_readme into prose — and
    nothing but this test keeps them in step. Drift either way is a lie the author
    cannot detect: a README that stays silent about a warning they saw scroll past
    reads as 'it got fixed', and one that invents a warning the compiler never
    raised sends them hunting for a CACHE they never wrote.
    """
    out = _compile_fixture(fixture, tmp_path, flow=flow)

    # ONE readouterr(): it DRAINS the captured buffer, so calling it per code (say,
    # inside the comprehension below) would hand the first code the whole stderr and
    # every later one an empty string — a sweep that can only ever see W_WF_001.
    stderr = capsys.readouterr().err
    readme = (out / "README.md").read_text()

    codes = ("W_WF_001", "W_WF_002", "W_WF_003")
    warned = {code for code in codes if code in stderr}
    documented = {code for code in codes if code in readme}

    assert documented == warned


# ---------------------------------------------------------------------------
# Task 12 — the shipped example
# ---------------------------------------------------------------------------


def test_example_parallel_review_fans_out_for_real(tmp_path):
    """examples/parallel_review.clio IS this target's argument, so the test is that
    the argument holds: both FOR EACH … PARALLEL blocks must reach the host's
    fan-out primitive.

    Serialize them into `for…of` — which is exactly what claude-skill does, with a
    warning — and the example still compiles, still passes `node --check`, still
    reads plausibly, and demonstrates nothing at all. That failure is invisible to
    every other assertion in this file, which is why the count is asserted here.

    `parallel()` and not `pipeline()`: the IR builder refuses a PARALLEL body with
    more than one call for ANY source (builder.py:2043, "exactly one step or
    sub-flow call in v1"), so no .clio file can express the multi-stage body that
    pipeline() renders. The example fans out TWICE instead — review, then triage —
    which is the multi-step-per-item shape the language can actually state.
    """
    from clio.cli import main

    rc = main(["compile", "examples/parallel_review.clio",
               "--target", "claude-workflow", "--output", str(tmp_path)])
    assert rc == 0

    src = (tmp_path / "parallel-review.workflow.js").read_text()
    assert src.count("await parallel(") == 2
    assert "for (const f of" not in src
    assert "for (const d of" not in src
    assert "state['drafts'] =" in src and "state['notes'] =" in src
    assert_valid_js(src, tmp_path)
