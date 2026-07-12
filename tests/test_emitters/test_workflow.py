"""target: claude-workflow — emitter tests."""
import json
from pathlib import Path

import pytest

from clio.emitters._workflow_helpers import (
    inline_schema,
    schema_literal,
    validate_graph_for_workflow,
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
