"""langgraph emitter — multifile (v0.18) tests.

Verifies that the langgraph emitter handles multi-file inputs correctly:
- A 2-file project: flow.py references both 'pipeline' (entry) and
  'classify' (imported sub-flow), with the internal step 'score'
  alpha-renamed to 'lib__score'.
"""
from __future__ import annotations

from pathlib import Path

from clio.cli import _cmd_compile


def test_langgraph_multifile_simple(tmp_path: Path) -> None:
    """A 2-file project: flow.py references both 'pipeline' and 'classify';
    the internal 'score' step is alpha-renamed to 'lib__score' in the
    steps/ directory."""
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
        "RESOURCES\n"
        "  target: langgraph\n"
        "\n"
        'FROM "./lib.clio" IMPORT Article, classify\n'
        "\n"
        "STEP run_pipeline\n"
        "  MODE: judgment\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "\n"
        "EXPOSE FLOW pipeline\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "  classify(article=article)\n"
    )
    rc = _cmd_compile(
        str(tmp_path / "main.clio"),
        target="langgraph",
        output=str(tmp_path / "out"),
        flow="pipeline",
    )
    assert rc == 0

    out = tmp_path / "out"
    # The package dir is named after the selected FLOW.
    pkg_dir = out / "pipeline"
    assert pkg_dir.is_dir()

    flow_py = (pkg_dir / "flow.py").read_text()
    # The entry flow produces a top-level StateGraph builder and run() function.
    assert "build_graph(" in flow_py
    assert "def run(" in flow_py
    # The imported 'classify' sub-flow is compiled as a sub-graph.
    assert "classify" in flow_py
    assert "build_classify_graph(" in flow_py

    # Internal step alpha-renamed from 'score' to 'lib__score'.
    steps_dir = pkg_dir / "steps"
    assert (steps_dir / "lib__score.py").exists()
    assert not (steps_dir / "score.py").exists()

    # contracts.py must define Article.
    contracts_py = (pkg_dir / "contracts.py").read_text()
    assert "class Article" in contracts_py
