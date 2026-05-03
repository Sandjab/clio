from pathlib import Path

from clio.emitters.claude_cli import ClaudeCLIEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse


FIXTURES = Path(__file__).parent.parent / "fixtures"


def _read_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_text()
    return out


def test_emit_phase1(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase1")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase2(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase2")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase4(tmp_path):
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase4")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase6(tmp_path):
    src = (FIXTURES / "mvp_phase6.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase6")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase7(tmp_path):
    src = (FIXTURES / "mvp_phase7.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase7")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase7_run_sh_is_executable(tmp_path):
    src = (FIXTURES / "mvp_phase7.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)
    assert (tmp_path / "run.sh").stat().st_mode & 0o111


def test_emit_phase8(tmp_path):
    src = (FIXTURES / "mvp_phase8.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase8")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase9_full_mvp(tmp_path):
    src = (FIXTURES / "mvp_phase9.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase9")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_v02_cache(tmp_path):
    src = (FIXTURES / "mvp_v02_cache.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v02_cache")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_v02_onfail(tmp_path):
    src = (FIXTURES / "mvp_v02_onfail.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v02_onfail")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_abort_message_is_shell_quoted():
    """Bash injection guard: abort messages from CLIO source must be shlex-quoted."""
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE:  judgment\n"
        '  ON_FAIL: abort("nasty $(rm -rf /) backtick`x`")\n'
        "FLOW f\n"
        "  s()\n"
    )
    import tempfile
    from clio.emitters.claude_cli import ClaudeCLIEmitter
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse
    out = Path(tempfile.mkdtemp())
    ClaudeCLIEmitter().emit(build_ir(parse(src)), out)
    run_sh = (out / "run.sh").read_text()
    # The injection vectors must NOT appear unquoted in the emitted echo.
    # Specifically, `$(...)` and `` `...` `` must be inside single quotes.
    assert "$(rm -rf /)" not in run_sh.split("'")[0::2], \
        "abort message must be inside single quotes — found $(...) outside"
    # Sanity: the message text IS in the file (just safely quoted).
    assert "nasty" in run_sh and "rm -rf" in run_sh


def test_emit_escalate_recomputes_cache_key(tmp_path):
    """Per spec §6: escalate must recompute the cache key for the new model
    and lookup/store under that key."""
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE:    judgment\n"
        "  CACHE:   ttl(1h)\n"
        '  ON_FAIL: escalate then abort("nope")\n'
        "FLOW f\n"
        "  s()\n"
        "RESOURCES\n"
        "  target: claude-cli\n"
        "  models: [haiku, sonnet]\n"
    )
    import tempfile
    from clio.emitters.claude_cli import ClaudeCLIEmitter
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse
    out = Path(tempfile.mkdtemp())
    ClaudeCLIEmitter().emit(build_ir(parse(src)), out)
    run_sh = (out / "run.sh").read_text()
    # Escalate must recompute the key.
    assert "KEY_01_ESC=" in run_sh, "escalate must recompute the cache key"
    # And lookup against it.
    assert 'cache lookup "$CACHE_DIR_01" s "$KEY_01_ESC"' in run_sh
    # And store under it on success.
    assert 'cache store "$CACHE_DIR_01" s "$KEY_01_ESC"' in run_sh
