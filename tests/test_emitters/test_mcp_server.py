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


import pytest


def test_emit_rejects_anthropic_protocol(tmp_path):
    src = (
        "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
        "  invoke:\n    mode: api\n    protocol: anthropic\n    model: \"claude-sonnet-4-6\"\n"
        "FLOW f\n  s()\n"
    )
    with pytest.raises(ValueError, match="sampling-only"):
        MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)


def test_emit_rejects_openai_protocol(tmp_path):
    src = (
        "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
        "  invoke:\n    mode: api\n    protocol: openai\n    model: \"gpt-4o\"\n    base_url: \"http://localhost:4000\"\n"
        "FLOW f\n  s()\n"
    )
    with pytest.raises(ValueError, match="sampling-only"):
        MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)


def test_emit_rejects_cli_invoke_mode(tmp_path):
    src = (
        "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
        "  invoke:\n    mode: cli\n    cli: \"claude\"\n"
        "FLOW f\n  s()\n"
    )
    with pytest.raises(ValueError, match="invoke.mode: cli"):
        MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)


def test_emit_rejects_no_flow(tmp_path):
    src = "STEP s\n  GIVES: r: str\n  MODE: exact\n"  # no FLOW
    with pytest.raises(ValueError, match="requires at least one FLOW"):
        MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)


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
