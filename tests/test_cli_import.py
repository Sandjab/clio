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


def test_import_calls_llm_when_sidecar_absent(tmp_path, monkeypatch, capsys):
    from clio import skill_to_clio
    from clio.cli import main

    skill = tmp_path / "handwritten"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# hand-written\n")
    expected = "STEP infer\n  MODE: exact\n  LANG: python\nFLOW f\n  infer()\n"

    captured = {}

    def fake_generate(skill_dir, *, model, client=None):
        captured["skill_dir"] = skill_dir
        captured["model"] = model
        return expected

    monkeypatch.setattr(skill_to_clio, "generate", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    rc = main(["import", str(skill)])
    assert rc == 0
    assert capsys.readouterr().out == expected
    assert captured["skill_dir"] == skill
    assert captured["model"] == "claude-sonnet-4-6"


def test_import_falls_back_to_llm_on_drift_in_auto_mode(tmp_path, monkeypatch, capsys):
    from clio import skill_to_clio
    from clio.cli import main

    _, skill = _emit_skill(tmp_path)
    (skill / "SKILL.md").write_text("# tampered\n")

    fallback_source = "STEP recovered\n  MODE: exact\n  LANG: python\nFLOW f\n  recovered()\n"
    monkeypatch.setattr(
        skill_to_clio, "generate",
        lambda skill_dir, *, model, client=None: fallback_source,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rc = main(["import", str(skill)])
    assert rc == 0
    assert capsys.readouterr().out == fallback_source


def test_import_mode_infer_ignores_sidecar_and_calls_llm(tmp_path, monkeypatch, capsys):
    from clio import skill_to_clio
    from clio.cli import main

    _, skill = _emit_skill(tmp_path)
    called = {"count": 0}

    def fake_generate(skill_dir, *, model, client=None):
        called["count"] += 1
        return "STEP forced\n  MODE: exact\n  LANG: python\nFLOW f\n  forced()\n"

    monkeypatch.setattr(skill_to_clio, "generate", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rc = main(["import", str(skill), "--mode", "infer"])
    assert rc == 0
    assert called["count"] == 1  # LLM was called even though sidecar exists


def test_import_missing_api_key_exits_1(tmp_path, monkeypatch, capsys):
    from clio.cli import main

    skill = tmp_path / "handwritten"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# hand-written\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    rc = main(["import", str(skill)])
    assert rc == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_import_generation_error_exits_1_with_diagnostic(tmp_path, monkeypatch, capsys):
    from clio import skill_to_clio
    from clio.cli import main

    skill = tmp_path / "handwritten"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# hand-written\n")

    def boom(skill_dir, *, model, client=None):
        raise skill_to_clio.GenerationError(
            last_attempt="STEP bad\n", last_error="line 1: oops",
        )

    monkeypatch.setattr(skill_to_clio, "generate", boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rc = main(["import", str(skill)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "line 1: oops" in err
    assert "STEP bad" in err


def test_import_strict_with_partial_sidecar_exits_2(tmp_path, capsys):
    """source.clio present, manifest.json missing → exit 2 with clear message."""
    from clio.cli import main

    skill = tmp_path / "skill"
    (skill / ".clio").mkdir(parents=True)
    (skill / ".clio" / "source.clio").write_text("STEP foo\n  MODE: exact\n")
    # NO manifest.json
    (skill / "SKILL.md").write_text("# skill\n")
    rc = main(["import", str(skill), "--mode", "strict"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "manifest missing" in err or "partial sidecar" in err


def test_import_auto_with_partial_sidecar_falls_back_to_llm(tmp_path, monkeypatch, capsys):
    """source.clio present, manifest.json missing → warn + LLM fallback."""
    from clio import skill_to_clio
    from clio.cli import main

    skill = tmp_path / "skill"
    (skill / ".clio").mkdir(parents=True)
    (skill / ".clio" / "source.clio").write_text("STEP cheat\n  MODE: exact\n")
    # NO manifest.json
    (skill / "SKILL.md").write_text("# skill\n")

    expected = "STEP recovered\n  MODE: exact\n  LANG: python\nFLOW f\n  recovered()\n"
    monkeypatch.setattr(
        skill_to_clio, "generate",
        lambda skill_dir, *, model, client=None: expected,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    rc = main(["import", str(skill)])
    assert rc == 0
    assert capsys.readouterr().out == expected
