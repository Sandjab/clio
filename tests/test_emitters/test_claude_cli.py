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


def test_emit_v02_fallback(tmp_path):
    src = (FIXTURES / "mvp_v02_fallback.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v02_fallback")
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


def test_emit_fallback_does_not_cache_under_main_key(tmp_path):
    """Per spec §6: fallback's output must not be cached under the main step's key.
    Verified by inspecting the cache-store guard for the FALLBACK_USED tracking."""
    src = (
        "STEP main\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:    judgment\n"
        "  CACHE:   ttl(1h)\n"
        '  ON_FAIL: fallback(naive) then abort("nope")\n'
        "STEP naive\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW f\n"
        "  main(x=1)\n"
    )
    import tempfile
    from clio.emitters.claude_cli import ClaudeCLIEmitter
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse
    out = Path(tempfile.mkdtemp())
    ClaudeCLIEmitter().emit(build_ir(parse(src)), out)
    run_sh = (out / "run.sh").read_text()
    # The FALLBACK_USED tracking flag must be present.
    assert "FALLBACK_USED_" in run_sh, "fallback chain must emit FALLBACK_USED tracking"
    # The cache-store gate must check FALLBACK_USED == 0.
    assert 'FALLBACK_USED_' in run_sh
    assert '= "0"' in run_sh, "cache-store gate must check FALLBACK_USED == 0"


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


# --- impl: mode: rest emission ---------------------------------------------

_REST_SRC = (
    "STEP geocode\n"
    "  TAKES: address: str\n"
    "  GIVES: location: str\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode: rest\n"
    "    method: GET\n"
    '    url: "https://api.example.com/geocode"\n'
    '    response_path: "results[0].location"\n'
    "    timeout: 30s\n"
    "FLOW geo\n"
    '  geocode(address="123 Main St")\n'
)


def test_claude_cli_emit_rest_step_calls_requests(tmp_path):
    ClaudeCLIEmitter().emit(build_ir(parse(_REST_SRC)), tmp_path)
    body = (tmp_path / "steps" / "01_geocode.py").read_text()
    assert "import requests" in body
    assert "requests.request(" in body
    assert "method='GET'" in body
    assert "url='https://api.example.com/geocode'" in body
    assert "timeout=30" in body


def test_claude_cli_emit_rest_step_traverses_response_path(tmp_path):
    ClaudeCLIEmitter().emit(build_ir(parse(_REST_SRC)), tmp_path)
    body = (tmp_path / "steps" / "01_geocode.py").read_text()
    assert "_traverse(response.json(), 'results[0].location')" in body
    assert "import re as _re" in body


def test_claude_cli_emit_rest_step_parses_as_python(tmp_path):
    import ast
    ClaudeCLIEmitter().emit(build_ir(parse(_REST_SRC)), tmp_path)
    body = (tmp_path / "steps" / "01_geocode.py").read_text()
    ast.parse(body)


def test_claude_cli_emit_rest_step_writes_state_with_gives_name(tmp_path):
    ClaudeCLIEmitter().emit(build_ir(parse(_REST_SRC)), tmp_path)
    body = (tmp_path / "steps" / "01_geocode.py").read_text()
    # The result is stored under the GIVES name in state.json.
    assert 'state["location"] = location' in body


# --- FOR EACH bash emission ------------------------------------------------

_FOREACH_BASH_SRC = (
    "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
    "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH item IN items:\n"
    "         process(x=item)\n"
)


def test_claude_cli_emit_for_each_generates_bash_loop(tmp_path):
    ClaudeCLIEmitter().emit(build_ir(parse(_FOREACH_BASH_SRC)), tmp_path)
    run_sh = (tmp_path / "run.sh").read_text()
    # The emitted run.sh should contain a `for ... in ... do` loop bound to a
    # mapfile-populated array reading from state.json via jq.
    assert "FOR EACH item IN items" in run_sh
    assert "mapfile -t _CLIO_ITER_0" in run_sh
    assert "jq -r '.items[]' state.json" in run_sh   # primitive (str) → -r
    assert 'for item in "${_CLIO_ITER_0[@]}"; do' in run_sh
    assert "done" in run_sh


def test_claude_cli_emit_for_each_passes_loop_var_as_bash_var(tmp_path):
    ClaudeCLIEmitter().emit(build_ir(parse(_FOREACH_BASH_SRC)), tmp_path)
    run_sh = (tmp_path / "run.sh").read_text()
    # The body call should reference the bash variable, not jq state.json
    assert '--x="$item"' in run_sh
    # And the loop body should NOT do `jq -r .item state.json` (the loop var
    # is loop-local, not a state field).
    assert "jq -r .item state.json" not in run_sh


def test_claude_cli_emit_for_each_uses_jq_c_for_object_collection(tmp_path):
    """When iterating over List<{...}>, values must stay as JSON literals
    (jq -c) so they can be passed back through the pipeline unaltered."""
    src = (
        "STEP load\n"
        "  GIVES: rows: List<{name: str, age: int}>\n"
        "  MODE: exact\n"
        "STEP process\n"
        "  TAKES: row: {name: str, age: int}\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH row IN rows:\n"
        "         process(row=row)\n"
    )
    ClaudeCLIEmitter().emit(build_ir(parse(src)), tmp_path)
    run_sh = (tmp_path / "run.sh").read_text()
    assert "jq -c '.rows[]' state.json" in run_sh   # object → -c


def test_claude_cli_emit_for_each_judgment_in_body_raises(tmp_path):
    """v0.2: judgment steps inside a FOR EACH body are not yet supported by
    the claude-cli emitter (cache + escalate + state.json substitution would
    need to be reworked for loop-local variables)."""
    import pytest as _pytest
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP classify\n"
        "  TAKES: x: str\n"
        "  GIVES: label: str\n"
        "  MODE: judgment\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         classify(x=item)\n"
    )
    with _pytest.raises(NotImplementedError) as exc:
        ClaudeCLIEmitter().emit(build_ir(parse(src)), tmp_path)
    assert "judgment" in str(exc.value)
    assert "FOR EACH" in str(exc.value)
