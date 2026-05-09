"""Smoke tests for the polished examples in examples/. Each test compiles
the .clio file via parse + build_ir + emit and asserts the expected step
files are emitted.

These are not E2E tests (no Anthropic call). They guard against regressions
in the examples themselves and the emitters when the language extends."""
from __future__ import annotations

from pathlib import Path

from clio.ir.builder import build_ir
from clio.parser.parser import parse
from clio.emitters.python import PythonEmitter


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
