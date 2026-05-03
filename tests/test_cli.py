from pathlib import Path

from clio.cli import main


def test_compile_creates_output_tree(tmp_path):
    src = tmp_path / "input.clio"
    src.write_text("STEP foo\n  MODE: exact\n")
    out = tmp_path / "out"

    rc = main(["compile", str(src), "--target", "claude-cli", "--output", str(out)])

    assert rc == 0
    assert (out / "CLAUDE.md").exists()
    assert (out / ".claude" / "hooks.json").exists()
    assert (out / "steps" / "01_foo.py").exists()


def test_compile_unknown_target_rejected_by_argparse(tmp_path, capsys):
    src = tmp_path / "input.clio"
    src.write_text("STEP foo\n  MODE: exact\n")

    import pytest
    with pytest.raises(SystemExit):
        main(["compile", str(src), "--target", "python", "--output", str(tmp_path / "out")])
