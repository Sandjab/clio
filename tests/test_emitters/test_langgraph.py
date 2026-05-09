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
