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


# -- doctor / status --


def test_doctor_no_source_returns_0_when_env_warns(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = main(["doctor"])
    out = capsys.readouterr().out
    assert "python version" in out
    assert "anthropic SDK" in out
    assert "ANTHROPIC_API_KEY" in out
    # WARN doesn't fail the run when no source is given.
    assert rc == 0


def test_doctor_with_source_judgment_fails_without_api_key(tmp_path, monkeypatch, capsys):
    src = tmp_path / "f.clio"
    src.write_text(
        "STEP foo\n  MODE: judgment\n  TAKES: x: str\n  GIVES: y: str\n"
        "FLOW p\n  foo(x=\"a\")\n"
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = main(["doctor", str(src)])
    out = capsys.readouterr().out
    assert "source compiles" in out
    assert "ANTHROPIC_API_KEY" in out
    assert rc == 1


def test_doctor_multi_flow_without_selector_compiles(tmp_path, monkeypatch, capsys):
    """v0.17: build_ir no longer requires --flow for multi-FLOW sources.
    Each FLOW is now built unconditionally (so emitters like mcp-server
    can expose all of them); doctor reports a clean PASS on the compile
    check. Per-target compile-time errors (langgraph, claude-cli, ...)
    still surface in their own emit pass."""
    src = tmp_path / "two.clio"
    src.write_text(
        "STEP foo\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
        "FLOW beta\n  foo(x=\"b\")\n"
    )
    rc = main(["doctor", str(src)])
    out = capsys.readouterr().out
    assert "source compiles" in out
    assert "FAIL" not in out
    assert rc == 0


def test_doctor_multi_flow_with_selector_passes(tmp_path, monkeypatch, capsys):
    src = tmp_path / "two.clio"
    src.write_text(
        "STEP foo\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
        "FLOW beta\n  foo(x=\"b\")\n"
    )
    rc = main(["doctor", str(src), "--flow", "beta"])
    out = capsys.readouterr().out
    assert "source compiles" in out
    # No FAIL when the right flow is selected and there are no judgment steps.
    assert "FAIL" not in out
    assert rc == 0


def test_doctor_source_compile_error_reports_fail(tmp_path, monkeypatch, capsys):
    src = tmp_path / "bad.clio"
    src.write_text("STEP\n  MODE: bogus\n")  # missing IDENT after STEP
    rc = main(["doctor", str(src)])
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert rc == 1


def test_status_no_state_file_reports_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLIO_STATE_FILE", raising=False)
    monkeypatch.delenv("CLIO_LOG_FILE", raising=False)
    rc = main(["status"])
    out = capsys.readouterr().out
    assert "state file" in out
    assert "missing" in out
    assert rc == 0


def test_compile_multi_flow_without_selector_python_succeeds(tmp_path, capsys):
    """v0.17: with the python target, build_ir succeeds and the emitter
    falls back to an empty entry point when no main FLOW is selected.
    Targets that need a main (langgraph, claude-cli) still error in
    their own emit pass."""
    src = tmp_path / "two.clio"
    src.write_text(
        "STEP foo\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
        "FLOW beta\n  foo(x=\"b\")\n"
    )
    out_dir = tmp_path / "o"
    rc = main(["compile", str(src), "--target", "python", "--output", str(out_dir)])
    assert rc == 0
    assert (out_dir / "pyproject.toml").exists()


def test_compile_multi_flow_with_selector_picks_one(tmp_path):
    src = tmp_path / "two.clio"
    src.write_text(
        "STEP foo\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
        "FLOW beta\n  foo(x=\"b\")\n"
    )
    out = tmp_path / "o"
    rc = main(["compile", str(src), "--target", "python", "--output", str(out), "--flow", "beta"])
    assert rc == 0
    # Python target produces pyproject + package dir; flow name 'beta' drives the entry.
    assert (out / "pyproject.toml").exists()


def test_status_reads_state_and_log(tmp_path, monkeypatch, capsys):
    import json
    sf = tmp_path / "state.json"
    sf.write_text(json.dumps({
        "version": 1, "flow": "demo", "step_index": 3,
        "state": {"a": 1, "b": 2},
    }))
    lf = tmp_path / "log.jsonl"
    lf.write_text(
        '{"ts": "2026-05-14T10:00:00Z", "event": "flow_start", "flow": "demo"}\n'
        '{"ts": "2026-05-14T10:00:01Z", "event": "step_end", "step": "load", '
        '"mode": "exact", "success": true, "duration_ms": 42}\n'
    )
    rc = main(["status", "--state-file", str(sf), "--log-file", str(lf)])
    out = capsys.readouterr().out
    assert "demo" in out
    assert "step_index" in out
    assert "flow_start" in out
    assert "step_end" in out
    assert rc == 0


# -- v0.18 multi-file CLI tests --


def test_cli_compile_multifile(tmp_path):
    """_cmd_compile resolves imports and compiles a two-file project."""
    from clio.cli import _cmd_compile

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
        "  target: python\n"
        "\n"
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
        target="python",
        output=str(tmp_path / "out"),
    )
    assert rc == 0
    assert (tmp_path / "out" / "pyproject.toml").exists()


def test_cli_check_multifile(tmp_path):
    """_cmd_check resolves imports and returns 0 for a valid two-file project."""
    from clio.cli import _cmd_check

    (tmp_path / "lib.clio").write_text(
        "EXPOSE CONTRACT Tag\n"
        "  SHAPE: {label: str}\n"
        "\n"
        "STEP tag\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: result: Tag\n"
        "\n"
        "EXPOSE FLOW tagger\n"
        "  TAKES: text: str\n"
        "  GIVES: result: Tag\n"
        "  tag(text=text)\n"
    )
    (tmp_path / "main.clio").write_text(
        "FROM \"./lib.clio\" IMPORT Tag, tagger\n"
        "\n"
        "STEP use_tag\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: result: Tag\n"
        "\n"
        "EXPOSE FLOW pipeline\n"
        "  TAKES: text: str\n"
        "  GIVES: result: Tag\n"
        "  tagger(text=text)\n"
    )
    rc = _cmd_check(str(tmp_path / "main.clio"))
    assert rc == 0


def test_cli_check_reports_missing_import(tmp_path):
    """_cmd_check returns non-zero when a FROM ... IMPORT references a missing file."""
    from clio.cli import _cmd_check

    src = tmp_path / "main.clio"
    src.write_text('FROM "./missing.clio" IMPORT X\n')
    rc = _cmd_check(str(src))
    assert rc != 0
