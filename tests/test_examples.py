"""Smoke tests for the polished examples in examples/. Each test compiles
the .clio file via parse + build_ir + emit and asserts the expected step
files are emitted.

These are not E2E tests (no Anthropic call). They guard against regressions
in the examples themselves and the emitters when the language extends."""
from __future__ import annotations

from pathlib import Path

from clio.emitters.python import PythonEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

REPO_ROOT = Path(__file__).resolve().parent.parent


def _compile_to_tree(clio_path: Path, output_dir: Path) -> Path:
    """Parse + build IR + emit to output_dir. Returns the package root."""
    src = clio_path.read_text()
    program = parse(src)
    ir = build_ir(program)
    PythonEmitter().emit(ir, output_dir)
    return output_dir


def test_compile_rag_basic_example(tmp_path):
    """rag_basic.clio compiles cleanly and emits the 4 expected step files."""
    out = _compile_to_tree(REPO_ROOT / "examples/rag_basic.clio", tmp_path)
    step_dir = out / "rag_faq" / "steps"
    step_files = {p.stem for p in step_dir.glob("*.py") if p.stem != "__init__"}
    assert step_files == {"load_corpus", "load_question", "score_chunks", "answer"}


def test_compile_rag_selfcontained_example(tmp_path):
    """rag_selfcontained.clio compiles cleanly. The two loader steps use
    impl.shell (no manual edit) and load_corpus uses parse: json."""
    out = _compile_to_tree(REPO_ROOT / "examples/rag_selfcontained.clio", tmp_path)
    step_dir = out / "rag_faq" / "steps"
    step_files = {p.stem for p in step_dir.glob("*.py") if p.stem != "__init__"}
    assert step_files == {"load_corpus", "load_question", "score_chunks", "answer"}

    # The two loader steps must be impl.shell — no NotImplementedError stub.
    for loader in ("load_corpus", "load_question"):
        body = (step_dir / f"{loader}.py").read_text()
        assert "subprocess.run" in body, f"{loader} should use impl.shell (subprocess)"
        assert "NotImplementedError" not in body, f"{loader} should not be a stub"

    # load_corpus specifically uses parse:json → json.loads.
    load_corpus_body = (step_dir / "load_corpus.py").read_text()
    assert "import json" in load_corpus_body
    assert "json.loads(result.stdout)" in load_corpus_body


def test_compile_ticket_routing_example(tmp_path):
    """ticket_routing.clio compiles cleanly: shell parse:json loader, a PARALLEL
    FOR EACH classifying multi-field structured tickets, then a JUDGMENT summary."""
    out = _compile_to_tree(REPO_ROOT / "examples/ticket_routing.clio", tmp_path)
    step_dir = out / "ticket_routing" / "steps"
    step_files = {p.stem for p in step_dir.glob("*.py") if p.stem != "__init__"}
    assert step_files == {"load_tickets", "classify_ticket", "summarize_routing"}

    # load_tickets is impl.shell + parse:json — no manual edit.
    load_body = (step_dir / "load_tickets.py").read_text()
    assert "subprocess.run" in load_body
    assert "json.loads(result.stdout)" in load_body
    assert "NotImplementedError" not in load_body

    # The flow uses PARALLEL FOR EACH (ThreadPoolExecutor) and accumulates
    # into state['classifications'] for downstream summarize_routing.
    flow_body = (out / "ticket_routing" / "flow.py").read_text()
    assert "concurrent.futures.ThreadPoolExecutor" in flow_body
    assert "state['classifications']" in flow_body

    # The 3 contracts compile to Pydantic models with Literal enums.
    contracts_body = (out / "ticket_routing" / "contracts.py").read_text()
    assert "class SupportTicket(BaseModel)" in contracts_body
    assert "class ClassifiedTicket(BaseModel)" in contracts_body
    assert "class RoutingSummary(BaseModel)" in contracts_body
    assert "Literal['bug', 'billing', 'feature', 'account', 'other']" in contracts_body
    assert "Literal['low', 'medium', 'high', 'urgent']" in contracts_body

    # Regression guard for the contracts-import bug: load_tickets is impl.shell
    # + parse:json with `gives: List<support_ticket>`. The annotation
    # `list[contracts.SupportTicket]` requires `from .. import contracts` —
    # without it the qualifier is unresolved (harmless under
    # `from __future__ import annotations` but ugly and breaks `get_type_hints`).
    assert "from .. import contracts" in load_body
    assert "list[contracts.SupportTicket]" in load_body


def test_compile_critical_pipeline_python(tmp_path):
    """critical_pipeline.clio compiles cleanly to python target with
    ON_FAIL: retry(3) then escalate on detect_churn AND a RESCUE block
    that notifies + aborts. Verifies the emitted flow.py wraps the
    protected call in try/except and defines _rescue_detect_churn."""
    out = _compile_to_tree(REPO_ROOT / "examples/critical_pipeline.clio", tmp_path)
    step_dir = out / "pipeline" / "steps"
    step_files = {p.stem for p in step_dir.glob("*.py") if p.stem != "__init__"}
    assert step_files == {"load_clients", "detect_churn", "notify_slack"}

    flow_body = (out / "pipeline" / "flow.py").read_text()
    # Try/except wrap around detect_churn
    assert "try:" in flow_body
    assert "_rescue_detect_churn(state" in flow_body
    # FlowAborted defined locally
    assert "class FlowAborted(Exception)" in flow_body
    # Rescue helper present
    assert "def _rescue_detect_churn(state" in flow_body
    # Abort renders correctly
    assert "raise FlowAborted" in flow_body
    assert "see #alerts" in flow_body


def test_compile_rest_advanced_example(tmp_path):
    """rest_advanced.clio exercises the v0.9 impl.rest extensions:
    templated query/headers, all 5 body forms (json/raw/file/form/multipart),
    and retry: {...} (both default and constant-backoff variants)."""
    out = _compile_to_tree(REPO_ROOT / "examples/rest_advanced.clio", tmp_path)
    step_dir = out / "pipeline" / "steps"
    step_files = {p.stem for p in step_dir.glob("*.py") if p.stem != "__init__"}
    assert step_files == {
        "geocode", "create_user", "login", "upload_cv", "echo", "push_payload",
    }

    # The runtime helper module is bundled.
    assert (out / "pipeline" / "clio_runtime" / "rest.py").exists()

    # geocode: GET with query/headers + retry exponential.
    geocode = (step_dir / "geocode.py").read_text()
    assert "_kwargs['params'] = _rest.render_dict(" in geocode
    assert "_kwargs['headers'] = _rest.render_dict(" in geocode
    assert "_attempts = 3" in geocode
    assert "_backoff = 'exponential'" in geocode

    # create_user: JSON body.
    create = (step_dir / "create_user.py").read_text()
    assert "_kwargs['json'] = _rest.render_dict(" in create

    # login: form body — `data=` dict, no `files=`.
    login = (step_dir / "login.py").read_text()
    assert "_kwargs['data'] = _rest.render_dict(" in login

    # upload_cv: multipart body — both `_files` and `_form` paths.
    upload = (step_dir / "upload_cv.py").read_text()
    assert "_files: dict = {}" in upload
    assert "_form: dict = {}" in upload

    # echo: raw body — text/plain content-type set when no headers given.
    echo = (step_dir / "echo.py").read_text()
    assert "_kwargs['data'] = _rest.subst('raw text ${msg}'" in echo
    assert "text/plain" in echo

    # push_payload: file body + constant-backoff retry covering network.
    push = (step_dir / "push_payload.py").read_text()
    assert "_rest.read_file_body('./payload.json'" in push
    assert "_backoff = 'constant'" in push
    assert "'network'" in push


def test_compile_critical_pipeline_mcp_server(tmp_path):
    """critical_pipeline.clio also compiles to mcp-server target with
    async equivalents (async _rescue_detect_churn, await wrapping)."""
    from clio.emitters.mcp_server import MCPServerEmitter

    src = (REPO_ROOT / "examples/critical_pipeline.clio").read_text()
    # Override target to mcp-server for this test only.
    src_mcp = src.replace("target:   python", "target:   mcp-server")
    program = parse(src_mcp)
    ir = build_ir(program)
    MCPServerEmitter().emit(ir, tmp_path)
    flow_body = (tmp_path / "pipeline" / "flow.py").read_text()
    assert "async def _rescue_detect_churn(state" in flow_body
    assert "await _rescue_detect_churn(state" in flow_body
    assert "class FlowAborted(Exception)" in flow_body


def test_compile_flow_composition_example_python_batch(tmp_path):
    """flow_composition.clio (v0.17 example): the `batch` flow uses a
    signed sub-flow inside FOR EACH PARALLEL — compiles cleanly to python
    and the per-iteration body invokes the in-module sub-flow function."""
    from clio.emitters.python import PythonEmitter

    src = (REPO_ROOT / "examples/flow_composition.clio").read_text()
    program = parse(src)
    ir = build_ir(program, flow_name="batch")
    PythonEmitter().emit(ir, tmp_path)
    flow_py = (tmp_path / "batch" / "flow.py").read_text()
    # Sub-flow function is defined in the same module.
    assert "def run_enrich(" in flow_py
    # The PARALLEL body invokes the sub-flow per iteration.
    assert "return run_enrich(url=u)" in flow_py
    # ThreadPoolExecutor is wired in.
    assert "ThreadPoolExecutor(max_workers=10)" in flow_py


def test_compile_flow_composition_example_mcp_batch(tmp_path):
    """flow_composition.clio compiles to mcp-server with the same
    PARALLEL+sub-flow pattern (async invocation, _session threaded in)."""
    from clio.emitters.mcp_server import MCPServerEmitter

    src = (REPO_ROOT / "examples/flow_composition.clio").read_text()
    src_mcp = src.replace("target: python", "target: mcp-server")
    program = parse(src_mcp)
    ir = build_ir(program, flow_name="batch")
    MCPServerEmitter().emit(ir, tmp_path)
    flow_py = (tmp_path / "batch" / "flow.py").read_text()
    assert "async def enrich(" in flow_py
    assert "await enrich(_session=_session, url=u)" in flow_py


def test_compile_flow_composition_example_langgraph_single(tmp_path):
    """flow_composition.clio compiles to langgraph when the entrypoint is
    `single` (langgraph rejects FOR EACH PARALLEL pre-v0.17, so `batch` is
    not usable on this target — documented in the cookbook recipe)."""
    from clio.emitters.langgraph import LangGraphEmitter

    src = (REPO_ROOT / "examples/flow_composition.clio").read_text()
    src_lg = src.replace("target: python", "target: langgraph")
    program = parse(src_lg)
    ir = build_ir(program, flow_name="single")
    LangGraphEmitter().emit(ir, tmp_path)
    # langgraph emits a sub-graph for `enrich` and a node calling it.
    flow_py = (tmp_path / "single" / "flow.py").read_text()
    assert "enrich" in flow_py


def test_compile_flow_composition_example_claude_skill_batch(tmp_path):
    """flow_composition.clio compiles to claude-skill (the host serializes
    iterations — a documented warning is emitted)."""
    from clio.emitters.claude_skill import ClaudeSkillEmitter

    src = (REPO_ROOT / "examples/flow_composition.clio").read_text()
    src_sk = src.replace("target: python", "target: claude-skill")
    program = parse(src_sk)
    ir = build_ir(program, flow_name="batch")
    ClaudeSkillEmitter().emit(ir, tmp_path)
    assert (tmp_path / "SKILL.md").exists()
    # The sub-flow script is emitted alongside the step scripts.
    scripts_dir = tmp_path / "scripts"
    sub_scripts = [p.name for p in scripts_dir.glob("sub_*.py")]
    assert "sub_enrich.py" in sub_scripts


def test_compile_flow_composition_example_claude_cli_rejects(tmp_path):
    """flow_composition.clio MUST be rejected by claude-cli with the
    Task 11 v0.17 deferred-target message (FLOW composition unsupported)."""
    import pytest

    from clio.emitters.claude_cli import ClaudeCLIEmitter

    src = (REPO_ROOT / "examples/flow_composition.clio").read_text()
    src_cli = src.replace("target: python", "target: claude-cli")
    program = parse(src_cli)
    # Either main flow exercises a sub-flow call site; both must reject.
    for flow_name in ("single", "batch"):
        ir = build_ir(program, flow_name=flow_name)
        with pytest.raises(ValueError, match="does not support FLOW composition"):
            ClaudeCLIEmitter().emit(ir, tmp_path)
