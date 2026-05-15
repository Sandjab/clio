"""python emitter — multifile (v0.18) tests.

Verifies that the python emitter handles multi-file inputs correctly:
1. Simple 2-file project: both 'pipeline' (entry) and 'classify' (imported)
   flows are present in flow.py; internal 'score' is alpha-renamed to
   'lib__score' and appears in the steps/ directory.
2. Diamond 4-file project compiles cleanly.
3. Re-export 3-file project (lib -> facade -> main) compiles cleanly.
"""
from __future__ import annotations

from pathlib import Path

from clio.cli import _cmd_compile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports"


def test_python_multifile_simple(tmp_path: Path) -> None:
    """A 2-file project: flow.py references both 'pipeline' and 'classify';
    the imported internal step 'score' is alpha-renamed to 'lib__score'."""
    rc = _cmd_compile(
        str(FIXTURES / "simple" / "main.clio"),
        target="python",
        output=str(tmp_path / "out"),
        flow="pipeline",
    )
    assert rc == 0

    out = tmp_path / "out"
    # The package dir is named after the selected FLOW.
    pkg_dir = out / "pipeline"
    assert pkg_dir.is_dir()

    flow_py = (pkg_dir / "flow.py").read_text()
    # The imported sub-flow 'classify' must appear as a sub-flow function.
    assert "run_classify(" in flow_py
    # The main run() drives the 'pipeline' flow.
    assert "pipeline" in flow_py

    # Internal step alpha-renamed from 'score' to 'lib__score'.
    steps_dir = pkg_dir / "steps"
    assert (steps_dir / "lib__score.py").exists()
    # The original non-prefixed name must NOT appear as a step file.
    assert not (steps_dir / "score.py").exists()

    # The step import in flow.py must reference the renamed step.
    assert "lib__score" in flow_py

    # contracts.py must define Article (imported from lib.clio).
    contracts_py = (pkg_dir / "contracts.py").read_text()
    assert "class Article" in contracts_py


def test_python_multifile_diamond(tmp_path: Path) -> None:
    """A 4-file diamond project compiles cleanly to a python package."""
    (tmp_path / "shared.clio").write_text(
        "EXPOSE CONTRACT Doc\n"
        "  SHAPE: {text: str}\n"
        "\n"
        "STEP tokenise\n"
        "  MODE: judgment\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: tokens: str\n"
        "\n"
        "EXPOSE FLOW prepare\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: tokens: str\n"
        "  tokenise(doc=doc)\n"
    )
    (tmp_path / "left.clio").write_text(
        'FROM "./shared.clio" IMPORT Doc, prepare\n'
        "\n"
        "EXPOSE Doc\n"
        "\n"
        "STEP analyse_left\n"
        "  MODE: judgment\n"
        "  TAKES: tokens: str\n"
        "  GIVES: left_result: str\n"
        "\n"
        "EXPOSE FLOW left_branch\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: left_result: str\n"
        "  prepare(doc=doc)\n"
        "  -> analyse_left(tokens=tokens)\n"
    )
    (tmp_path / "right.clio").write_text(
        'FROM "./shared.clio" IMPORT Doc, prepare\n'
        "\n"
        "STEP analyse_right\n"
        "  MODE: judgment\n"
        "  TAKES: tokens: str\n"
        "  GIVES: right_result: str\n"
        "\n"
        "EXPOSE FLOW right_branch\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: right_result: str\n"
        "  prepare(doc=doc)\n"
        "  -> analyse_right(tokens=tokens)\n"
    )
    (tmp_path / "main.clio").write_text(
        "RESOURCES\n"
        "  target: python\n"
        "\n"
        'FROM "./left.clio" IMPORT Doc, left_branch\n'
        'FROM "./right.clio" IMPORT right_branch\n'
        "\n"
        "STEP merge\n"
        "  MODE: judgment\n"
        "  TAKES: left_result: str\n"
        "  GIVES: summary: str\n"
        "\n"
        "EXPOSE FLOW pipeline\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: summary: str\n"
        "  left_branch(doc=doc)\n"
        "  -> merge(left_result=left_result)\n"
    )
    rc = _cmd_compile(
        str(tmp_path / "main.clio"),
        target="python",
        output=str(tmp_path / "out"),
        flow="pipeline",
    )
    assert rc == 0

    out = tmp_path / "out"
    pkg_dir = out / "pipeline"
    assert pkg_dir.is_dir()
    assert (pkg_dir / "flow.py").exists()
    # contracts.py must define Doc (transitively imported through diamond).
    contracts_py = (pkg_dir / "contracts.py").read_text()
    assert "class Doc" in contracts_py


def test_python_reexport(tmp_path: Path) -> None:
    """A 3-file project (lib -> facade re-export -> main) compiles cleanly."""
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
    (tmp_path / "facade.clio").write_text(
        'FROM "./lib.clio" IMPORT Article, classify\n'
        "\n"
        "EXPOSE Article\n"
        "EXPOSE classify\n"
    )
    (tmp_path / "main.clio").write_text(
        "RESOURCES\n"
        "  target: python\n"
        "\n"
        'FROM "./facade.clio" IMPORT Article, classify\n'
        "\n"
        "STEP summarise\n"
        "  MODE: judgment\n"
        "  TAKES: article: Article\n"
        "  GIVES: summary: str\n"
        "\n"
        "EXPOSE FLOW pipeline\n"
        "  TAKES: article: Article\n"
        "  GIVES: summary: str\n"
        "  summarise(article=article)\n"
    )
    rc = _cmd_compile(
        str(tmp_path / "main.clio"),
        target="python",
        output=str(tmp_path / "out"),
        flow="pipeline",
    )
    assert rc == 0

    out = tmp_path / "out"
    pkg_dir = out / "pipeline"
    assert pkg_dir.is_dir()
    assert (pkg_dir / "flow.py").exists()
    contracts_py = (pkg_dir / "contracts.py").read_text()
    assert "class Article" in contracts_py
