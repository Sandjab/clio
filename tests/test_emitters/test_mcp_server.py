import ast
import re
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
    # When the FLOW call provides literal kwargs, they become optional defaults
    # and are NOT in required. Both name="World" and count=3 are literals here.
    MCPServerEmitter().emit(build_ir(parse(_FLOW_WITH_TAKES_SRC)), tmp_path)
    server_py = (tmp_path / "hello" / "server.py").read_text()
    schema = _extract_input_schema(server_py, "hello")
    # Literals become defaults → not in required; required is empty
    assert schema["required"] == []
    assert schema["properties"]["name"]["default"] == "World"
    assert schema["properties"]["count"]["default"] == 3


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


def test_emit_judgment_step_uses_session_create_message(tmp_path):
    src = (
        "STEP classify\n"
        "  TAKES: text: str\n"
        "  GIVES: label: str\n"
        "  MODE:  judgment\n"
        "FLOW f\n  classify(text=\"hi\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "classify.py").read_text()
    assert "session.create_message" in body
    assert "import anthropic" not in body
    assert "import openai" not in body
    assert "async def classify" in body


def test_emit_flow_awaits_judgment_steps(tmp_path):
    src = (
        "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: judgment\n"
        "FLOW f\n  classify(text=\"hi\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "f" / "flow.py").read_text()
    assert "await classify_mod.classify" in flow_py


def test_emit_server_threads_session_to_flow_run(tmp_path):
    src = (
        "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: judgment\n"
        "FLOW f\n  classify(text=\"hi\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    server_py = (tmp_path / "f" / "server.py").read_text()
    assert "_session=" in server_py
    assert "ctx.session" in server_py or "request_context.session" in server_py


def test_emit_writes_contracts_module_when_contracts_present(tmp_path):
    src = (
        "CONTRACT classification\n"
        "  SHAPE: {label: str, confidence: float}\n"
        "  ASSERT: confidence > 0.0\n"
        "STEP classify\n  TAKES: text: str\n  GIVES: result: classification\n  MODE: judgment\n"
        "FLOW f\n  classify(text=\"hi\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_py = (tmp_path / "f" / "contracts.py").read_text()
    assert "from pydantic import BaseModel" in contracts_py
    assert "class Classification(BaseModel)" in contracts_py


def test_emit_pyproject_includes_pydantic_only_when_contracts_present(tmp_path):
    src_no = "STEP s\n  GIVES: r: str\n  MODE: exact\nFLOW f\n  s()\n"
    src_yes = (
        "CONTRACT c\n  SHAPE: {x: int}\n"
        "STEP s\n  GIVES: r: c\n  MODE: exact\nFLOW f\n  s()\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src_no)), tmp_path / "no")
    MCPServerEmitter().emit(build_ir(parse(src_yes)), tmp_path / "yes")
    assert "pydantic" not in (tmp_path / "no" / "pyproject.toml").read_text()
    assert "pydantic>=2" in (tmp_path / "yes" / "pyproject.toml").read_text()


def test_emit_pyproject_includes_requests_only_when_rest_step_present(tmp_path):
    src = (
        "STEP fetch\n"
        "  TAKES: url: str\n  GIVES: body: str\n  MODE: exact\n"
        "  impl:\n    mode: rest\n    method: GET\n    url: \"${url}\"\n"
        "FLOW f\n  fetch(url=\"https://example.com\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    assert "requests>=2.31" in (tmp_path / "pyproject.toml").read_text()


def test_emit_rest_step_body_uses_requests_request(tmp_path):
    src = (
        "STEP fetch\n"
        "  TAKES: url: str\n  GIVES: body: str\n  MODE: exact\n"
        "  impl:\n    mode: rest\n    method: GET\n    url: \"${url}\"\n"
        "FLOW f\n  fetch(url=\"https://example.com\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "fetch.py").read_text()
    assert "requests.request" in body
    assert "_url.replace('${url}', str(url))" in body


def test_emit_shell_step_body_uses_subprocess_run(tmp_path):
    src = (
        "STEP cat\n"
        "  TAKES: file: str\n  GIVES: text: str\n  MODE: exact\n"
        "  impl:\n    mode: shell\n    cmd: \"cat ${file}\"\n"
        "FLOW f\n  cat(file=\"/tmp/x\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "cat.py").read_text()
    assert "subprocess.run" in body
    assert "_t.replace('${file}', str(file))" in body


def test_emit_input_schema_uses_default_for_literal_kwarg(tmp_path):
    src = (
        "STEP greet\n  TAKES: name: str\n  GIVES: msg: str\n  MODE: exact\n"
        "FLOW hello\n  greet(name=\"World\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    server_py = (tmp_path / "hello" / "server.py").read_text()
    schema = _extract_input_schema(server_py, "hello")
    assert schema["properties"]["name"]["default"] == "World"
    assert "name" not in schema["required"]


def test_emit_output_schema_reflects_last_step_gives_contract(tmp_path):
    src = (
        "CONTRACT classification\n  SHAPE: {label: str, confidence: float}\n"
        "STEP classify\n  TAKES: text: str\n  GIVES: result: classification\n  MODE: judgment\n"
        "FLOW f\n  classify(text=\"hi\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    server_py = (tmp_path / "f" / "server.py").read_text()
    # Extract outputSchema via AST walk (handles nested dicts correctly)
    tree = ast.parse(server_py)
    output_schema = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "outputSchema":
                    output_schema = ast.literal_eval(kw.value)
    assert output_schema is not None, "outputSchema not present in server.py"
    assert "label" in output_schema["properties"]
    assert output_schema["properties"]["confidence"]["type"] == "number"


def test_emit_for_each_works_with_judgment_body(tmp_path):
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: judgment\n"
        "FLOW pipe\n  load()\n    -> FOR EACH item IN items:\n         classify(text=item)\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "for item in state['items']:" in flow_py or 'for item in state["items"]:' in flow_py
    assert "await classify_mod.classify(text=item, _session=_session)" in flow_py


def test_emit_with_cache_directive_includes_cache_runtime(tmp_path):
    src = (
        "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: judgment\n  CACHE: ttl(24h)\n"
        "FLOW f\n  classify(text=\"hi\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    assert (tmp_path / "f" / "clio_runtime" / "cache.py").exists()


def test_emit_on_fail_chain_appears_in_judgment_body(tmp_path):
    src = (
        "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: judgment\n"
        "  ON_FAIL: retry(3) then abort(\"failed\")\n"
        "FLOW f\n  classify(text=\"hi\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "classify.py").read_text()
    assert "retry" in body.lower() or "_attempt" in body


def test_input_schema_keeps_state_ref_kwarg_in_required(tmp_path):
    """A kwarg whose value is a state-ref (@-prefixed) must stay in `required`
    — it has no compile-time literal default.  The parser/builder rejects @-refs
    in the first call (nothing is in scope yet), so we build the IR directly."""
    from clio.ir.graph import CallIR, FieldIR, FlowGraph, FlowIR, StepIR
    from clio.parser.ast_nodes import PrimitiveType

    step = StepIR(
        name="greet",
        mode="exact",
        takes=(
            FieldIR(name="name", type=PrimitiveType(name="str")),
            FieldIR(name="count", type=PrimitiveType(name="int")),
        ),
        gives=FieldIR(name="msg", type=PrimitiveType(name="str")),
        cache=None,
        on_fail=None,
        lang=None,
        impl=None,
        invoke=None,
        line=1,
    )
    # "name" has a literal default; "count" is a state-ref — no literal default.
    call = CallIR(
        step_name="greet",
        kwargs=(("name", "World"), ("count", "@some_count")),
        line=5,
    )
    graph = FlowGraph(
        steps=(step,),
        flow=FlowIR(name="hello", chain=(call,), line=5),
    )
    MCPServerEmitter().emit(graph, tmp_path)
    server_py = (tmp_path / "hello" / "server.py").read_text()
    schema = _extract_input_schema(server_py, "hello")
    assert schema["properties"]["name"]["default"] == "World"
    assert "name" not in schema["required"]
    assert "count" in schema["required"]
    assert "default" not in schema["properties"]["count"]


def test_emit_writes_readme_with_mcp_client_config(tmp_path):
    src = (
        "STEP greet\n  TAKES: name: str\n  GIVES: msg: str\n  MODE: exact\n"
        "FLOW hello\n  greet(name=\"World\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    readme = (tmp_path / "README.md").read_text()
    assert "MCP" in readme
    assert "hello" in readme
    assert '"command": "python"' in readme
    assert '"-m"' in readme
