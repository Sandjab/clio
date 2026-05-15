"""Tests for the LangGraph emitter (v0 scope: linear FLOW, judgment.api.anthropic,
exact code/shell/rest, CACHE, retry+abort. Rejects FOR EACH, invoke.cli, openai/
bedrock/vertex, escalate/fallback)."""
from __future__ import annotations

from pathlib import Path

import pytest

from clio.emitters.langgraph import LangGraphEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse


def _emit(src: str, tmp_path: Path) -> Path:
    LangGraphEmitter().emit(build_ir(parse(src)), tmp_path)
    return tmp_path


_LINEAR_SRC = (
    "CONTRACT topic\n"
    "  SHAPE: {label: enum(news|tech|sport|other), confidence: float}\n"
    "  ASSERT: confidence > 0.0\n"
    "STEP load_text\n"
    "  TAKES: file: str\n  GIVES: text: str\n  MODE: exact\n"
    "STEP detect_topic\n"
    "  TAKES:   text: str\n  GIVES: result: topic\n  MODE: judgment\n"
    "  CACHE:   ttl(1h)\n"
    "  ON_FAIL: retry(3) then abort(\"topic detection failed\")\n"
    "FLOW classify\n"
    "  load_text(file=\"article.txt\")\n"
    "    -> detect_topic(text)\n"
)


def test_emit_produces_expected_tree(tmp_path):
    out = _emit(_LINEAR_SRC, tmp_path)
    pkg = out / "classify"
    expected = {
        "pyproject.toml",
        "README.md",
        "classify/__init__.py",
        "classify/__main__.py",
        "classify/flow.py",
        "classify/contracts.py",
        "classify/steps/__init__.py",
        "classify/steps/load_text.py",
        "classify/steps/detect_topic.py",
        "classify/clio_runtime/__init__.py",
        "classify/clio_runtime/logging.py",
        "classify/clio_runtime/cache.py",
    }
    actual = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert actual == expected


def test_pyproject_includes_langgraph_and_anthropic(tmp_path):
    _emit(_LINEAR_SRC, tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert '"langgraph>=1.0"' in pyproject
    assert '"anthropic>=0.40"' in pyproject
    assert '"pydantic>=2"' in pyproject
    # No requests dep when no impl.rest is used.
    assert '"requests' not in pyproject


def test_flow_module_state_typeddict_has_all_fields(tmp_path):
    _emit(_LINEAR_SRC, tmp_path)
    flow = (tmp_path / "classify" / "flow.py").read_text()
    assert "class State(TypedDict, total=False):" in flow
    # External input (TAKES with no upstream GIVES).
    assert "file: str" in flow
    # Intermediate (GIVES of upstream, TAKES of downstream).
    assert "text: str" in flow
    # Final output, references contract class.
    assert "result: contracts.Topic" in flow


def test_flow_module_node_wrappers_translate_state(tmp_path):
    _emit(_LINEAR_SRC, tmp_path)
    flow = (tmp_path / "classify" / "flow.py").read_text()
    # Literal kwarg is inlined.
    assert "load_text_mod.load_text(file='article.txt')" in flow
    # Upstream-produced kwarg reads from state.
    assert "detect_topic_mod.detect_topic(text=state['text'])" in flow
    # Each node returns {gives_name: result}.
    assert "return {'text': _result}" in flow
    assert "return {'result': _result}" in flow


def test_flow_module_graph_build_with_retry_policy(tmp_path):
    _emit(_LINEAR_SRC, tmp_path)
    flow = (tmp_path / "classify" / "flow.py").read_text()
    assert "from langgraph.graph import START, END, StateGraph" in flow
    assert "from langgraph.types import RetryPolicy" in flow
    assert "workflow = StateGraph(State)" in flow
    # Step without retry — bare add_node.
    assert "workflow.add_node('load_text', load_text_node)" in flow
    # Step with retry(3) — RetryPolicy wired.
    assert (
        "workflow.add_node('detect_topic', detect_topic_node, "
        "retry_policy=RetryPolicy(max_attempts=3))"
    ) in flow
    # Linear edges.
    assert "workflow.add_edge(START, 'load_text')" in flow
    assert "workflow.add_edge('load_text', 'detect_topic')" in flow
    assert "workflow.add_edge('detect_topic', END)" in flow


def test_main_module_invokes_and_persists_state(tmp_path):
    _emit(_LINEAR_SRC, tmp_path)
    main = (tmp_path / "classify" / "__main__.py").read_text()
    assert "from .flow import run" in main
    assert "json.dump(payload" in main
    assert '"flow": ' in main
    assert "os.environ.get(\"CLIO_STATE_FILE\"" in main


def test_judgment_step_file_reuses_python_target_body(tmp_path):
    """The step file is byte-identical to the python target's emit_judgment_step
    output: same params (kw-only), same Anthropic SDK calls, same cache logic.
    Only the langgraph node wrapper in flow.py adapts state-dict → kwargs."""
    _emit(_LINEAR_SRC, tmp_path)
    step = (tmp_path / "classify" / "steps" / "detect_topic.py").read_text()
    assert "import anthropic" in step
    assert "from .. import contracts" in step
    assert "def detect_topic(*, text: str) -> contracts.Topic:" in step
    # cache_active: ttl(1h)
    assert "from ..clio_runtime import cache as _cache" in step


# ---------- v0 reject paths ----------


def _expect_reject(src: str, contains: list[str], tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc:
        _emit(src, tmp_path)
    msg = str(exc.value)
    for token in contains:
        assert token in msg, f"expected {token!r} in error: {msg}"


def test_reject_for_each_sequential(tmp_path):
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    _expect_reject(src, ["FOR EACH (sequential)", "langgraph", "v0.7"], tmp_path)


def test_reject_for_each_parallel(tmp_path):
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS rs:\n"
        "         process(x=item)\n"
    )
    _expect_reject(src, ["FOR EACH (PARALLEL)", "langgraph"], tmp_path)


def test_reject_invoke_cli(tmp_path):
    src = (
        "STEP s\n"
        "  TAKES: text: str\n  GIVES: out: str\n  MODE: judgment\n"
        "  invoke:\n    mode: cli\n"
        "FLOW f\n  s(text=\"x\")\n"
    )
    _expect_reject(src, ["invoke.mode: cli", "langgraph", "claude-cli"], tmp_path)


def test_reject_invoke_api_openai(tmp_path):
    src = (
        "STEP s\n"
        "  TAKES: text: str\n  GIVES: out: str\n  MODE: judgment\n"
        "  invoke:\n    mode: api\n    protocol: openai\n    model: \"gpt-4o\"\n"
        "FLOW f\n  s(text=\"x\")\n"
    )
    _expect_reject(src, ["openai", "langgraph", "anthropic"], tmp_path)


def test_reject_on_fail_escalate(tmp_path):
    src = (
        "STEP s\n"
        "  TAKES: text: str\n  GIVES: out: str\n  MODE: judgment\n"
        "  ON_FAIL: retry(3) then escalate then abort(\"x\")\n"
        "FLOW f\n  s(text=\"x\")\n"
    )
    _expect_reject(src, ["escalate", "langgraph", "retry"], tmp_path)


def test_reject_on_fail_fallback(tmp_path):
    src = (
        "STEP fb\n  TAKES: text: str\n  GIVES: out: str\n  MODE: exact\n"
        "STEP s\n"
        "  TAKES: text: str\n  GIVES: out: str\n  MODE: judgment\n"
        "  ON_FAIL: retry(3) then fallback(fb) then abort(\"x\")\n"
        "FLOW f\n  s(text=\"x\")\n"
    )
    _expect_reject(src, ["fallback", "langgraph"], tmp_path)


def test_reject_no_flow(tmp_path):
    src = "STEP s\n  GIVES: out: str\n  MODE: exact\n"
    _expect_reject(src, ["langgraph", "FLOW"], tmp_path)


# ---------- CLI integration ----------


def test_cli_compile_langgraph_target(tmp_path):
    """The --target langgraph flag dispatches to LangGraphEmitter."""
    from clio.cli import main
    src = tmp_path / "f.clio"
    src.write_text(_LINEAR_SRC)
    out = tmp_path / "out"
    rc = main(["compile", str(src), "--target", "langgraph", "--output", str(out)])
    assert rc == 0
    assert (out / "classify" / "flow.py").exists()
    assert "StateGraph(State)" in (out / "classify" / "flow.py").read_text()


def test_langgraph_rejects_rescue(tmp_path):
    """LangGraph rejects RESCUE handlers in v0.8 — message must point users to
    --target python or --target mcp-server."""
    src = (
        "STEP a\n"
        "  TAKES: x: int\n"
        "  GIVES: y: int\n"
        "  MODE:  exact\n"
        "FLOW p\n"
        "  a(x=1)\n"
        "\n"
        "  RESCUE a:\n"
        "    -> abort(\"x\")\n"
    )
    with pytest.raises(ValueError, match="RESCUE.*not supported.*langgraph"):
        LangGraphEmitter().emit(build_ir(parse(src)), tmp_path)


def test_reject_sql(tmp_path):
    src = (
        "STEP get\n"
        "  TAKES: email: str\n"
        "  GIVES: orders: List<{id: int}>\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "SELECT id FROM orders WHERE email = :email"\n'
        "FLOW f\n"
        '  get(email="x")\n'
        "RESOURCES\n"
        "  target: langgraph\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: sqlite\n"
        '      url:    ":memory:"\n'
    )
    _expect_reject(src, ["impl.mode: sql", "langgraph", "python"], tmp_path)


def test_langgraph_state_typeddict_includes_declared_flow_takes(tmp_path):
    """Declared FLOW.TAKES surface in State; run() returns only declared FLOW.GIVES."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  TAKES: x: str, threshold: float
  GIVES: y: str
  s(x=x)
"""
    _emit(src, tmp_path)
    flow_py = (tmp_path / "pipeline" / "flow.py").read_text()
    # Declared FLOW.TAKES must appear in State (both fields)
    assert "class State(TypedDict" in flow_py
    assert "x:" in flow_py
    assert "threshold:" in flow_py
    # Step GIVES also in State
    assert "y:" in flow_py
    # run() returns only declared GIVES
    assert "'y': result['y']" in flow_py or '"y": result["y"]' in flow_py
    # run() must NOT fall back to full-state return
    assert "return dict(result)" not in flow_py


def test_langgraph_falls_back_when_no_flow_signature(tmp_path):
    """v0.15 backward-compat: no FLOW.TAKES/GIVES → existing inference + full-state return."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  s(x=x)
"""
    _emit(src, tmp_path)
    flow_py = (tmp_path / "p" / "flow.py").read_text()
    # v0.15 inference: x is not produced upstream, so it appears in State
    assert "x:" in flow_py
    assert "y:" in flow_py
    # run() returns the full state
    assert "return dict(result)" in flow_py


def test_langgraph_emits_subgraph_node(tmp_path):
    src = """
STEP s
  TAKES: x: str
  GIVES: y: str
  MODE: exact

FLOW inner
  TAKES: x: str
  GIVES: y: str
  s(x=x)

FLOW outer
  TAKES: x: str
  GIVES: y: str
  inner(x=x)
"""
    g = build_ir(parse(src), flow_name="outer")
    LangGraphEmitter().emit(g, tmp_path)
    flow_py = (tmp_path / "outer" / "flow.py").read_text()
    # Either we expose the sub-graph as a builder function or compile it inline.
    # Accept either spelling; the key requirement is the sub-flow's logic
    # becomes its own StateGraph and the outer graph adds it as a node.
    assert any(token in flow_py for token in ("build_inner_graph", "inner_graph", "subgraph_inner")), (
        "expected a sub-graph builder named like build_inner_graph / inner_graph / subgraph_inner; "
        f"flow.py contents:\n{flow_py}"
    )
    assert '"inner"' in flow_py or "'inner'" in flow_py
    # The outer graph should add a node for the sub-flow call.
    assert "add_node(" in flow_py


def test_langgraph_compiles_subflow_once_at_module_load(tmp_path):
    """v0.17: each sub-flow's StateGraph is compiled exactly once into a
    module-level `_compiled_<flow>` constant; the FlowCallIR node wrapper
    invokes that cached instance rather than re-compiling per call."""
    src = """
STEP s
  TAKES: x: str
  GIVES: y: str
  MODE: exact

FLOW inner
  TAKES: x: str
  GIVES: y: str
  s(x=x)

FLOW outer
  TAKES: x: str
  GIVES: y: str
  inner(x=x)
"""
    g = build_ir(parse(src), flow_name="outer")
    LangGraphEmitter().emit(g, tmp_path)
    flow_py = (tmp_path / "outer" / "flow.py").read_text()
    # Module-level cache constant exists and is built via the sub-flow builder.
    assert "_compiled_inner = build_inner_graph()" in flow_py
    # The wrapper uses the cached instance, not a fresh build.
    assert "_compiled_inner.invoke(" in flow_py
    # And does NOT re-compile inside the wrapper body.
    assert "build_inner_graph().invoke(" not in flow_py


def test_langgraph_state_typeddict_uses_functional_syntax_for_keyword_fields(
    tmp_path,
):
    """v0.17: when a FLOW.TAKES/GIVES field name collides with a Python keyword
    (e.g., `from`), the State TypedDict is declared with functional syntax so
    the dict key retains its original form (matching the `state[<orig>]` access
    used by the emitted node wrappers). Class syntax would force a sanitized
    field name (`from_`), causing a KeyError at runtime."""
    src = """
STEP s
  TAKES: from: str
  GIVES: y: str
  MODE: exact

FLOW inner
  TAKES: from: str
  GIVES: y: str
  s(from=from)

FLOW outer
  TAKES: from: str
  GIVES: y: str
  inner(from=from)
"""
    g = build_ir(parse(src), flow_name="outer")
    LangGraphEmitter().emit(g, tmp_path)
    flow_py = (tmp_path / "outer" / "flow.py").read_text()
    # Parent State must declare 'from' as a string key, not a class field.
    assert "State = TypedDict('State'" in flow_py
    assert "'from': str" in flow_py
    # Sub-flow State (used by build_inner_graph) too.
    assert "_State_inner = TypedDict('_State_inner'" in flow_py
    # Critical: the runtime dict access must match the TypedDict key, not be
    # rewritten to the sanitized form (i.e., no `state['from_']` lookups).
    assert "state['from_']" not in flow_py
    assert "_result['from_']" not in flow_py


def test_node_wrappers_use_loose_state_type_when_sub_flow_present(tmp_path):
    """v0.17 polish (issue #29 item 4): step / sub-flow node wrappers are
    emitted at module level and reused across the main graph AND every sub-
    graph builder. The `state: State` annotation was therefore a type lie —
    `s_node` is called with `State` in `build_graph()` but with `_State_<sub>`
    inside `build_<sub>_graph()`. To stop the lie without duplicating wrappers
    per containing flow (deferred as future work — `[[v0.18-langgraph-per-flow-wrappers]]`),
    wrappers now type `state` as `dict[str, Any]`. We assert the looser type
    is in place AND `Any` is imported (`from __future__ import annotations`
    makes the annotation a string, but mypy still resolves the symbol)."""
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "FLOW helper\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  s(x=x)\n"
        "FLOW entry\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  helper(x=x)\n"
        "RESOURCES\n"
        "  target: langgraph\n"
    )
    g = build_ir(parse(src), flow_name="entry")
    LangGraphEmitter().emit(g, tmp_path)
    flow_py = (tmp_path / "entry" / "flow.py").read_text()
    # The step wrapper and the sub-flow wrapper both lose the `State` lie.
    assert "def s_node(state: dict[str, Any]) -> dict:" in flow_py, (
        "step node wrapper must declare loose `dict[str, Any]` to remain honest "
        "across main + sub-graph reuse; got:\n" + flow_py
    )
    assert "def helper_node(state: dict[str, Any]) -> dict:" in flow_py, (
        "sub-flow node wrapper must also declare `dict[str, Any]`; got:\n" + flow_py
    )
    # `Any` must be imported (PEP 563 string annotations still require the
    # symbol resolvable for mypy strict).
    assert "from typing import Any" in flow_py, (
        "`Any` must be imported when wrappers reference it"
    )
    # `state: State` (the lie) must not appear anywhere in flow.py.
    assert "state: State" not in flow_py, (
        "no wrapper should still use the lie `state: State`"
    )
