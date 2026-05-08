"""Gated end-to-end tests for examples/. Set CLIO_E2E=1 to run.

These tests actually invoke `cat` via subprocess and verify the loaders
return correctly-shaped data. They do NOT call any LLM — judgment steps
are not exercised here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="set CLIO_E2E=1 to run end-to-end example tests",
)


def test_rag_selfcontained_loaders_run_against_real_files(tmp_path):
    """Compile rag_selfcontained, copy data files into the output, then
    import and call load_corpus + load_question directly.
    Verifies impl.shell + parse:json end-to-end without an LLM."""
    out_dir = tmp_path / "out"
    subprocess.run(
        [sys.executable, "-m", "clio", "compile",
         str(REPO_ROOT / "examples/rag_selfcontained.clio"),
         "--target", "python",
         "--output", str(out_dir)],
        check=True,
    )
    # Copy the data files into the output dir (the cmd uses relative paths).
    for fname in ("faq.json", "question.txt"):
        (out_dir / fname).write_bytes(
            (REPO_ROOT / "examples" / fname).read_bytes()
        )

    sys.path.insert(0, str(out_dir))
    cwd = os.getcwd()
    try:
        from rag_faq.steps.load_corpus import load_corpus  # type: ignore
        from rag_faq.steps.load_question import load_question  # type: ignore

        os.chdir(out_dir)
        corpus = load_corpus(file="faq.json")
        question = load_question(file="question.txt")
    finally:
        os.chdir(cwd)
        sys.path.remove(str(out_dir))
        for m in [k for k in list(sys.modules) if k.startswith("rag_faq")]:
            del sys.modules[m]

    assert isinstance(corpus, list)
    assert len(corpus) == 8
    assert all("id" in c and "text" in c for c in corpus)
    assert corpus[0]["id"] == 1
    assert corpus[7]["id"] == 8
    assert isinstance(question, str)
    assert "annuler" in question.lower()
