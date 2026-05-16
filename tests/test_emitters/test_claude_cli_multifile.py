"""Task 10 (v0.18): target claude-cli rejects sources with FROM … IMPORT."""
from pathlib import Path

from clio.cli import _cmd_compile


def test_claude_cli_rejects_import(tmp_path: Path) -> None:
    """target: claude-cli refuses sources that contain FROM ... IMPORT ..."""
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
        "FROM \"./lib.clio\" IMPORT Article, classify\n"
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
        target="claude-cli",
        output=str(tmp_path / "out"),
    )
    assert rc != 0
    # Output directory must not have been created
    assert not (tmp_path / "out").exists()
