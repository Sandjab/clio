# tests/test_skill_to_clio.py
from pathlib import Path

import pytest


def _make_skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# my skill\n\nDoes X.\n")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "01_foo.py").write_text("print('foo')\n")
    (skill / "scripts" / "_validate.py").write_text("# boilerplate\n")
    (skill / "scripts" / "_cache_key.py").write_text("# boilerplate\n")
    (skill / "prompts").mkdir()
    (skill / "prompts" / "02_explain.md").write_text("Explain X.\n")
    return skill


def test_gather_includes_skill_md_and_scripts(tmp_path: Path) -> None:
    from clio.skill_to_clio import _gather_skill_files

    skill = _make_skill(tmp_path)
    payload = _gather_skill_files(skill)
    assert "=== SKILL.md ===" in payload
    assert "# my skill" in payload
    assert "=== scripts/01_foo.py ===" in payload
    assert "print('foo')" in payload
    assert "=== prompts/02_explain.md ===" in payload


def test_gather_excludes_clio_sidecar(tmp_path: Path) -> None:
    from clio.skill_to_clio import _gather_skill_files

    skill = _make_skill(tmp_path)
    (skill / ".clio").mkdir()
    (skill / ".clio" / "source.clio").write_text("STEP foo\nMODE: exact\n")
    (skill / ".clio" / "manifest.json").write_text("{}")
    payload = _gather_skill_files(skill)
    assert ".clio/" not in payload
    assert "source.clio" not in payload
    assert "manifest.json" not in payload


def test_gather_excludes_validate_and_cache_key_boilerplate(tmp_path: Path) -> None:
    from clio.skill_to_clio import _gather_skill_files

    skill = _make_skill(tmp_path)
    payload = _gather_skill_files(skill)
    assert "_validate.py" not in payload
    assert "_cache_key.py" not in payload


def test_gather_skips_binary_files(tmp_path: Path) -> None:
    from clio.skill_to_clio import _gather_skill_files

    skill = _make_skill(tmp_path)
    (skill / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
    payload = _gather_skill_files(skill)
    assert "image.png" not in payload


def test_check_size_warns_above_100k_tokens(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.skill_to_clio import _check_size

    payload = "x" * (100_001 * 4)  # ~100k tokens (4 chars / token approx)
    _check_size(payload)  # must not raise — just warn
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert "100" in captured.err  # mentions the threshold


def test_check_size_aborts_above_180k_tokens(tmp_path: Path) -> None:
    from clio.skill_to_clio import GenerationError, _check_size

    payload = "x" * (180_001 * 4)
    with pytest.raises(GenerationError, match="too large"):
        _check_size(payload)


def test_check_size_silent_under_100k(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.skill_to_clio import _check_size

    payload = "x" * (50_000 * 4)
    _check_size(payload)
    captured = capsys.readouterr()
    assert captured.err == ""
