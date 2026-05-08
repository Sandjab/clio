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
