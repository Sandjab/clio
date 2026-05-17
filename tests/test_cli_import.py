from pathlib import Path

import pytest

_TINY_SOURCE = (
    "STEP foo\n  MODE: exact\n  LANG: python\n"
    "FLOW pipe\n  foo()\n"
)


def _emit_skill(tmp_path: Path) -> tuple[Path, Path]:
    """Return (source_path, skill_dir). Emits a complete CLIO skill with sidecar."""
    from clio.cli import _cmd_compile

    src = tmp_path / "src.clio"
    src.write_text(_TINY_SOURCE)
    skill = tmp_path / "skill"
    rc = _cmd_compile(str(src), "claude-skill", str(skill), None)
    assert rc == 0
    assert (skill / ".clio" / "source.clio").exists()
    return src, skill


def test_import_returns_sidecar_when_hashes_match(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.cli import main

    _, skill = _emit_skill(tmp_path)
    out_file = tmp_path / "recovered.clio"
    rc = main(["import", str(skill), "--output", str(out_file)])
    assert rc == 0
    assert out_file.read_text() == _TINY_SOURCE


def test_import_default_writes_to_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.cli import main

    _, skill = _emit_skill(tmp_path)
    rc = main(["import", str(skill)])
    assert rc == 0
    assert capsys.readouterr().out == _TINY_SOURCE


def test_import_strict_mode_exits_2_when_sidecar_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.cli import main

    skill = tmp_path / "no_sidecar_skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# hand-written\n")
    rc = main(["import", str(skill), "--mode", "strict"])
    assert rc == 2
    assert "strict" in capsys.readouterr().err


def test_import_strict_mode_exits_2_when_drift_detected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.cli import main

    _, skill = _emit_skill(tmp_path)
    (skill / "SKILL.md").write_text("# tampered\n")
    rc = main(["import", str(skill), "--mode", "strict"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "drift" in err.lower()
    assert "SKILL.md" in err


def test_import_missing_directory_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.cli import main

    rc = main(["import", str(tmp_path / "does_not_exist")])
    assert rc == 2
    assert "not a directory" in capsys.readouterr().err
