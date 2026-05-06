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
    from clio.cli import main
    from clio import nl_to_clio

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
    from clio.cli import main
    from clio import nl_to_clio

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
    from clio.cli import main
    from clio import nl_to_clio

    captured: dict[str, str] = {}

    def fake_generate(description, *, model, client=None):
        captured["model"] = model
        return "STEP m\n  MODE: exact\n"

    monkeypatch.setattr(nl_to_clio, "generate", fake_generate)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    rc = main(["gen", "describe X", "--model", "claude-opus-4-7"])
    assert rc == 0
    assert captured["model"] == "claude-opus-4-7"
