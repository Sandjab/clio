import ast
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


_FLOW_WITH_TAKES_SRC = (
    "STEP greet\n"
    "  TAKES: name: str, count: int\n"
    "  GIVES: msg: str\n"
    "  MODE:  exact\n"
    "FLOW hello\n"
    "  greet(name=\"World\", count=3)\n"
)

def _extract_input_schema(server_py: str, tool_name: str) -> dict:
    """Pull the inputSchema dict literal out of the emitted server.py via AST walk."""
    tree = ast.parse(server_py)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "inputSchema":
                    return ast.literal_eval(kw.value)
    raise AssertionError(f"inputSchema for {tool_name} not found in server.py")


def test_server_input_schema_reflects_first_step_takes(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_FLOW_WITH_TAKES_SRC)), tmp_path)
    server_py = (tmp_path / "hello" / "server.py").read_text()
    schema = _extract_input_schema(server_py, "hello")
    assert schema["type"] == "object"
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["count"]["type"] == "integer"


def test_server_input_schema_marks_takes_as_required(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_FLOW_WITH_TAKES_SRC)), tmp_path)
    server_py = (tmp_path / "hello" / "server.py").read_text()
    schema = _extract_input_schema(server_py, "hello")
    assert set(schema["required"]) == {"name", "count"}


def test_flow_module_is_async_and_chains_steps(tmp_path):
    src = (
        "STEP a\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "STEP b\n  TAKES: y: str\n  GIVES: z: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  a(x=\"hi\")\n"
        "    -> b(y)\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "async def run" in flow_py
    assert "_session" in flow_py
    assert "from .steps import a as a_mod" in flow_py
    assert "from .steps import b as b_mod" in flow_py
    # First call uses literal x="hi"; second pulls y from state.
    assert "state['y'] = a_mod.a(x='hi')" in flow_py or 'state["y"] = a_mod.a(x="hi")' in flow_py
    assert "state['z'] = b_mod.b(y=state['y'])" in flow_py or 'state["z"] = b_mod.b(y=state["y"])' in flow_py


def test_flow_module_for_each_uses_async_for(tmp_path):
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP work\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         work(x=item)\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "for item in state['items']:" in flow_py or 'for item in state["items"]:' in flow_py
    assert "work_mod.work(x=item)" in flow_py


def test_emit_default_exact_step_has_signature_and_stub(tmp_path):
    src = (
        "STEP greet\n"
        "  TAKES: name: str\n"
        "  GIVES: msg: str\n"
        "  MODE:  exact\n"
        "FLOW hello\n"
        "  greet(name=\"World\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "hello" / "steps" / "greet.py").read_text()
    assert "def greet(*, name: str) -> str:" in body
    assert "raise NotImplementedError" in body
    assert "exact (deterministic) step" in body
