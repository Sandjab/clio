"""target: claude-workflow — emitter tests."""
import json
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
    CacheConfigIR,
    CliInvokeIR,
    CodeImplIR,
    ContractIR,
    FieldIR,
    FlowGraph,
    FlowIR,
    ImplIR,
    McpToolImplIR,
    OnFailChainIR,
    OnFailStrategyIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
    StepIR,
)
from clio.parser.ast_nodes import ContractRef, ListType, PrimitiveType, RecordType
from tests.conftest import assert_valid_js


def _emit(graph: FlowGraph, tmp_path: Path) -> str:
    """Emit a hand-built graph and return the script text."""
    WorkflowEmitter().emit(graph, tmp_path)
    scripts = list(tmp_path.glob("*.workflow.js"))
    assert len(scripts) == 1, f"expected 1 script, got {scripts}"
    return scripts[0].read_text()


def _emit_fixture(name: str, tmp_path: Path) -> str:
    """Compile a real fixture from tests/fixtures/ and return the script text.

    Later tasks use this rather than hand-building IR: the `.clio` grammar has
    traps, and these fixtures already parse. Assert on structure, not on step
    names you have not read.
    """
    from clio.cli import main

    out = tmp_path / "out"
    rc = main(["compile", f"tests/fixtures/{name}",
               "--target", "claude-workflow", "--output", str(out)])
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
    predicate is not rendered. Five other targets drop it silently — this one
    warns."""
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
