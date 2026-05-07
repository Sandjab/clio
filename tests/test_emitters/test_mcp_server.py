from pathlib import Path

from clio.emitters.mcp_server import MCPServerEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse


_SIMPLE_FLOW_SRC = (
    "STEP greet\n"
    "  TAKES: name: str\n"
    "  GIVES: msg: str\n"
    "  MODE:  exact\n"
    "FLOW hello\n"
    '  greet(name="World")\n'
)


def test_emit_creates_expected_file_tree(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_SIMPLE_FLOW_SRC)), tmp_path)
    assert (tmp_path / "pyproject.toml").exists()
    assert (tmp_path / "hello" / "__init__.py").exists()
    assert (tmp_path / "hello" / "__main__.py").exists()
    assert (tmp_path / "hello" / "server.py").exists()
    assert (tmp_path / "hello" / "flow.py").exists()
    assert (tmp_path / "hello" / "steps" / "greet.py").exists()
    assert (tmp_path / "hello" / "steps" / "__init__.py").exists()


def test_emit_no_flow_server_has_no_duplicate_raise(tmp_path):
    """When the source has no FLOW, server.py must not emit two consecutive raises."""
    src_no_flow = "STEP solo\n  GIVES: x: int\n  MODE: exact\n"
    MCPServerEmitter().emit(build_ir(parse(src_no_flow)), tmp_path)
    # Package name is the fallback "clio_mcp" when no FLOW
    server_py = (tmp_path / "clio_mcp" / "server.py").read_text()
    # Count occurrences of the raise line — should be exactly 1.
    assert server_py.count("raise ValueError(f'unknown tool:") == 1


def test_cli_accepts_mcp_server_target(tmp_path):
    from clio.cli import main
    src = tmp_path / "f.clio"
    src.write_text(_SIMPLE_FLOW_SRC)
    out = tmp_path / "out"
    rc = main(["compile", str(src), "--target", "mcp-server", "--output", str(out)])
    assert rc == 0
    assert (out / "pyproject.toml").exists()
