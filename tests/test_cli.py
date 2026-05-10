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
        main(["compile", str(src), "--target", "rust", "--output", str(tmp_path / "out")])


def test_cli_compile_python_target(tmp_path):
    from clio.cli import main
    fixture = Path(__file__).parent / "fixtures" / "mvp_v03_skeleton.clio"
    out = tmp_path / "out"
    rc = main(["compile", str(fixture), "--target", "python", "--output", str(out)])
    assert rc == 0
    assert (out / "pyproject.toml").exists()
    assert (out / "classify" / "__init__.py").exists()
    assert (out / "classify" / "clio_runtime" / "cache.py").exists()


def test_gen_inline_argument_writes_to_stdout(tmp_path, monkeypatch, capsys):
    from clio import nl_to_clio
    from clio.cli import main

    captured: dict[str, str] = {}

    def fake_generate(description, *, model, client=None):
        captured["description"] = description
        captured["model"] = model
        return "STEP gen_out\n  MODE: exact\n"

    monkeypatch.setattr(nl_to_clio, "generate", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    rc = main(["gen", "describe X"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == "STEP gen_out\n  MODE: exact\n"
    assert captured["description"] == "describe X"
    assert captured["model"] == "claude-sonnet-4-6"


def test_gen_writes_to_output_file(tmp_path, monkeypatch):
    from clio import nl_to_clio
    from clio.cli import main

    monkeypatch.setattr(
        nl_to_clio,
        "generate",
        lambda description, *, model, client=None: "STEP w\n  MODE: exact\n",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    target = tmp_path / "flow.clio"
    rc = main(["gen", "describe X", "--output", str(target)])
    assert rc == 0
    assert target.read_text() == "STEP w\n  MODE: exact\n"


def test_gen_passes_custom_model(tmp_path, monkeypatch):
    from clio import nl_to_clio
    from clio.cli import main

    captured: dict[str, str] = {}

    def fake_generate(description, *, model, client=None):
        captured["model"] = model
        return "STEP m\n  MODE: exact\n"

    monkeypatch.setattr(nl_to_clio, "generate", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    rc = main(["gen", "describe X", "--model", "claude-opus-4-7"])
    assert rc == 0
    assert captured["model"] == "claude-opus-4-7"


def test_gen_from_stdin(tmp_path, monkeypatch, capsys):
    import io
    import sys

    from clio import nl_to_clio
    from clio.cli import main

    captured: dict[str, str] = {}

    def fake_generate(description, *, model, client=None):
        captured["description"] = description
        return "STEP s\n  MODE: exact\n"

    monkeypatch.setattr(nl_to_clio, "generate", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin"))

    rc = main(["gen"])
    assert rc == 0
    assert captured["description"] == "from stdin"


def test_gen_from_file(tmp_path, monkeypatch, capsys):
    from clio import nl_to_clio
    from clio.cli import main

    captured: dict[str, str] = {}

    def fake_generate(description, *, model, client=None):
        captured["description"] = description
        return "STEP f\n  MODE: exact\n"

    monkeypatch.setattr(nl_to_clio, "generate", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    desc_path = tmp_path / "desc.txt"
    desc_path.write_text("from file body")

    rc = main(["gen", "--from-file", str(desc_path)])
    assert rc == 0
    assert captured["description"] == "from file body"


def test_gen_missing_api_key(monkeypatch, capsys):
    from clio.cli import main
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = main(["gen", "describe X"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err


def test_gen_empty_description_returns_2(monkeypatch, capsys):
    import io
    import sys

    from clio.cli import main
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = main(["gen"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "empty" in err.lower()


def test_gen_generation_error_prints_last_attempt_commented(monkeypatch, capsys):
    from clio import nl_to_clio
    from clio.cli import main

    def boom(description, *, model, client=None):
        raise nl_to_clio.GenerationError(
            last_attempt="STEP bad\nMODE: exact\n",
            last_error="line 2:1: unexpected token",
        )

    monkeypatch.setattr(nl_to_clio, "generate", boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    rc = main(["gen", "describe X"])
    assert rc == 1
    captured = capsys.readouterr()
    # stdout must be empty so a shell redirect doesn't get a partial file
    assert captured.out == ""
    # stderr carries the error and the failed attempt commented out
    assert "line 2:1: unexpected token" in captured.err
    assert "# STEP bad" in captured.err
    assert "# MODE: exact" in captured.err


def test_gen_inline_and_from_file_conflict_returns_2(tmp_path, monkeypatch, capsys):
    from clio.cli import main
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    desc_path = tmp_path / "desc.txt"
    desc_path.write_text("from file body")

    rc = main(["gen", "describe X", "--from-file", str(desc_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "either" in err.lower() and "--from-file" in err
