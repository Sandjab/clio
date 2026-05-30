"""claude-skill emitter — multifile (v0.18) tests.

Verifies that the claude-skill emitter handles multi-file inputs correctly:
1. Simple 2-file project: SKILL.md contains the entry flow name and the
   scripts/ directory is created.
2. Re-export 3-file project (lib -> facade -> main) compiles cleanly.
"""
from __future__ import annotations

from pathlib import Path

from clio.cli import _cmd_compile


def test_claude_skill_multifile_simple(tmp_path: Path) -> None:
    """A 2-file project: SKILL.md mentions 'pipeline' (entry flow) and
    scripts/ directory exists with the bundled helpers."""
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
        "  target: claude-skill\n"
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
        target="claude-skill",
        output=str(tmp_path / "out"),
        flow="pipeline",
    )
    assert rc == 0

    out = tmp_path / "out"
    # SKILL.md must exist and reference the selected flow.
    skill_md = (out / "SKILL.md").read_text()
    assert "pipeline" in skill_md

    # scripts/ directory with the bundled helpers must exist.
    scripts_dir = out / "scripts"
    assert scripts_dir.is_dir()
    assert (scripts_dir / "_validate.py").exists()
    assert (scripts_dir / "_cache_key.py").exists()

    # The imported classify sub-flow produces a sub-orchestrator script.
    assert (scripts_dir / "sub_classify.py").exists()

    # Step files: alpha-renamed lib__score appears in schemas/.
    schemas_dir = out / "schemas"
    assert schemas_dir.is_dir()
    # At least one schema file references the renamed step.
    schema_names = [p.name for p in schemas_dir.iterdir()]
    assert any("lib__score" in n for n in schema_names)


def test_claude_skill_multifile_reexport(tmp_path: Path) -> None:
    """A 3-file re-export project (lib -> facade -> main) compiles cleanly
    to a claude-skill output directory."""
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
        "  target: claude-skill\n"
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
        target="claude-skill",
        output=str(tmp_path / "out"),
        flow="pipeline",
    )
    assert rc == 0

    out = tmp_path / "out"
    assert (out / "SKILL.md").exists()
    skill_md = (out / "SKILL.md").read_text()
    assert "pipeline" in skill_md
    assert (out / "scripts").is_dir()


def test_claude_skill_multifile_commonpath_valueerror_warns(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A cross-drive ValueError from the sidecar's os.path.commonpath must
    degrade to a warning, not crash the compiler.

    On Windows, os.path.commonpath raises ValueError when imported sources
    span different drive letters (C: vs D:); Path.relative_to raises it too.
    The .clio/ sidecar is best-effort, so emit() must catch it and leave the
    main skill intact. Regression for the Gemini PR #80 review (medium): the
    sidecar handler caught only (OSError, FileNotFoundError), and ValueError
    is not an OSError subclass, so it escaped and crashed emit().
    """
    import os

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
        "  target: claude-skill\n"
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

    # commonpath is reached only by the multi-file sidecar branch (the two
    # imported sources); raise the exact error Windows raises for cross-drive
    # paths. monkeypatch auto-restores after the test.
    def _cross_drive(_paths: object) -> str:
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(os.path, "commonpath", _cross_drive)

    rc = _cmd_compile(
        str(tmp_path / "main.clio"),
        target="claude-skill",
        output=str(tmp_path / "out"),
        flow="pipeline",
    )

    # Compile still succeeds; only the .clio/ sidecar source tree is skipped.
    assert rc == 0
    out = tmp_path / "out"
    assert (out / "SKILL.md").exists()
    assert "failed to write .clio/ sidecar" in capsys.readouterr().err
