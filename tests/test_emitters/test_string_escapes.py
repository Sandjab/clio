from pathlib import Path

from clio.emitters.go import GoEmitter
from clio.emitters.python import PythonEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _read_tree(root: Path) -> str:
    return "\n".join(
        p.read_text() for p in sorted(root.rglob("*")) if p.is_file()
    )


def test_python_emit_shell_json_cmd_reescapes(tmp_path):
    graph = build_ir(parse((FIXTURES / "shell_json_cmd.clio").read_text()))
    PythonEmitter().emit(graph, tmp_path)
    text = _read_tree(tmp_path)
    # The JSON blob survives as one argv token, re-escaped by Python repr.
    assert '{"available": true}' in text


def test_go_emit_shell_json_cmd_reescapes(tmp_path):
    graph = build_ir(parse((FIXTURES / "shell_json_cmd.clio").read_text()))
    GoEmitter().emit(graph, tmp_path)
    text = _read_tree(tmp_path)
    # Go renders argv tokens via json.dumps -> the quotes are backslash-escaped.
    assert "available" in text


def test_python_emit_if_string_literal_with_escaped_quote(tmp_path):
    # IF/MATCH regression guard: a condition string literal carrying an escaped
    # quote must flow through condition rendering (repr) without crashing. The
    # flow is structurally complete (IF + ELSE) so any failure is the escape, not
    # a missing branch.
    src = (
        "CONTRACT r\n"
        "  SHAPE: {msg: str}\n"
        "\n"
        "STEP classify\n"
        "  TAKES: text: str\n"
        "  GIVES: result: r\n"
        "  MODE: judgment\n"
        "\n"
        "STEP handle\n"
        "  TAKES: x: r\n"
        "  GIVES: out: r\n"
        "  MODE: judgment\n"
        "\n"
        "FLOW f\n"
        "  TAKES: text: str\n"
        "  classify(text=text)\n"
        "    -> IF result.msg == \"a\\\"b\":\n"
        "         handle(x=result)\n"
        "    ELSE:\n"
        "         handle(x=result)\n"
    )
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)  # must not raise
    text = _read_tree(tmp_path)
    # repr() renders a string containing a double-quote with single quotes: 'a"b'
    assert 'a"b' in text


def test_claude_skill_sidecar_preserves_escaped_cmd_verbatim(tmp_path):
    # The claude-skill .clio/ sidecar stores the source verbatim, so the escaped
    # cmd line round-trips byte-exactly (this is what `clio import --mode strict`
    # recovers — no re-lex involved).
    from clio.emitters.claude_skill import ClaudeSkillEmitter

    src_path = FIXTURES / "shell_json_cmd.clio"
    # The sidecar is only written when source_path is supplied (it stores that
    # file verbatim) — mirror how the CLI invokes the emitter.
    ClaudeSkillEmitter().emit(
        build_ir(parse(src_path.read_text())), tmp_path, source_path=src_path
    )
    sidecar = "\n".join(
        p.read_text()
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file() and ".clio" in p.parts
    )
    assert 'cmd: "echo \'{\\"available\\": true}\'"' in sidecar
