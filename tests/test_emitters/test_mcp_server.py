import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from clio.emitters.mcp_server import MCPServerEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


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
    # URL templating now goes through the runtime helper.
    assert "_rest.subst('${url}', _takes)" in body
    assert "'url': url" in body


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


def test_emit_copies_logging_verbatim(tmp_path):
    """clio_runtime/logging.py must be byte-equal to the source, even when
    no CACHE directive is present (logging is independent of caching)."""
    MCPServerEmitter().emit(build_ir(parse(_SIMPLE_FLOW_SRC)), tmp_path)
    src_logging = (
        Path(__file__).parent.parent.parent / "clio" / "runtime" / "logging.py"
    ).read_text()
    out_logging = (tmp_path / "hello" / "clio_runtime" / "logging.py").read_text()
    assert out_logging == src_logging


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
        flow=FlowIR(name="hello", chain=(call,), rescues=(), line=5),
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


def test_emit_output_schema_for_list_of_contract_inlines_items(tmp_path):
    """List<ContractRef> in last step's GIVES must inline the items schema —
    no $ref leaks (clients can't resolve it)."""
    src = (
        "CONTRACT classification\n"
        "  SHAPE: {label: str, confidence: float}\n"
        "STEP classify_all\n  TAKES: texts: List<str>\n  GIVES: results: List<classification>\n"
        "  MODE: judgment\n"
        'FLOW f\n  classify_all(texts="placeholder")\n'
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    server_py = (tmp_path / "f" / "server.py").read_text()
    tree = ast.parse(server_py)
    output_schema = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "outputSchema":
                    output_schema = ast.literal_eval(kw.value)
    assert output_schema is not None, "outputSchema not present in server.py"
    assert output_schema["type"] == "array"
    items = output_schema["items"]
    # No $ref — inlined.
    assert "$ref" not in items
    # Items schema reflects the contract's fields.
    assert "label" in items["properties"]
    assert items["properties"]["confidence"]["type"] == "number"


@pytest.mark.skipif(
    os.environ.get("CLIO_MCP_E2E") != "1",
    reason="MCP smoke test gated; set CLIO_MCP_E2E=1 to run",
)
def test_e2e_compiled_server_responds_to_initialize(tmp_path):
    src = (
        "STEP greet\n"
        "  TAKES: name: str\n"
        "  GIVES: msg: str\n"
        "  MODE:  exact\n"
        "FLOW hello\n"
        "  greet(name=\"World\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(tmp_path)],
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "hello"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        request = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize",'
            '"params":{"protocolVersion":"2024-11-05","capabilities":{},'
            '"clientInfo":{"name":"smoke","version":"0"}}}\n'
        )
        proc.stdin.write(request)
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert "jsonrpc" in line
    finally:
        proc.terminate()
        proc.wait(timeout=5)


_PARALLEL_EXACT_SRC = (
    "STEP load\n  GIVES: docs: List<str>\n  MODE: exact\n"
    "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH doc IN docs PARALLEL AS labels:\n"
    "         classify(text=doc)\n"
)


_PARALLEL_JUDGMENT_SRC = (
    "STEP load\n  GIVES: docs: List<str>\n  MODE: exact\n"
    "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: judgment\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH doc IN docs PARALLEL AS labels:\n"
    "         classify(text=doc)\n"
)


def test_mcp_emits_asyncio_gather_for_parallel_for_each(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_PARALLEL_EXACT_SRC)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "import asyncio" in flow_py
    assert "asyncio.Semaphore(10)" in flow_py
    assert "asyncio.gather" in flow_py
    assert "state['labels']" in flow_py or 'state["labels"]' in flow_py


def test_mcp_parallel_judgment_threads_session_per_iteration(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_PARALLEL_JUDGMENT_SRC)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    # Each task awaits the judgment step with _session.
    assert "await classify_mod.classify" in flow_py
    assert "_session=_session" in flow_py


def test_mcp_does_not_import_asyncio_in_flow_when_no_parallel(tmp_path):
    """Sequential-only flow.py must not gain a top-level `import asyncio`
    (not strictly harmful, but pollutes the output unnecessarily)."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "import asyncio" not in flow_py


def test_mcp_flow_py_imports_logging_and_time(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "import time" in flow_py
    assert "from .clio_runtime import logging as _log" in flow_py


def test_mcp_flow_py_emits_set_flow_and_flow_events(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert '_log.set_flow("classify")' in flow_py
    assert '_log.emit("flow_start")' in flow_py
    assert '_log.emit("flow_end"' in flow_py
    assert "try:" in flow_py
    assert "finally:" in flow_py
    assert "_log.set_flow(None)" in flow_py


def test_mcp_judgment_step_imports_logging_and_time(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [
        f for f in step_files
        if "(judgment" in f.read_text() or "mcp_sampling" in f.read_text()
    ]
    assert judgment_files, "expected at least one judgment step in mcp output"
    body = judgment_files[0].read_text()
    assert "import time" in body
    assert "from ..clio_runtime import logging as _log" in body


def test_mcp_judgment_step_has_step_events(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [
        f for f in step_files
        if "(judgment" in f.read_text() or "mcp_sampling" in f.read_text()
    ]
    body = judgment_files[0].read_text()
    assert '_log.emit("step_start"' in body
    assert 'mode="judgment"' in body
    assert '_log.emit("step_end"' in body
    assert "_last_model" in body
    assert "_last_usage" in body
    assert "**_last_usage" in body


def test_mcp_parallel_block_emits_block_events(tmp_path):
    """A FOR EACH ... PARALLEL in mcp-server target emits parallel_block_start/end."""
    parallel_src = Path("examples/parallel_classify.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(parallel_src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    assert '_log.emit("parallel_block_start"' in flow_py
    assert '_log.emit("parallel_block_end"' in flow_py
    assert "total_iterations=" in flow_py
    assert "max_workers=10" in flow_py


def test_mcp_judgment_step_no_onfail_wraps_in_try_except(tmp_path):
    """No-ON_FAIL judgment step must wrap body in try/except so a failed
    sampling call still emits step_end with success=False before re-raising."""
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [
        f for f in step_files
        if "(judgment" in f.read_text() or "mcp_sampling" in f.read_text()
    ]
    assert judgment_files
    body = judgment_files[0].read_text()
    # The body should now contain try: ... except: emit step_end success=False; raise
    assert "try:" in body
    assert "except Exception" in body
    # And both success and failure step_end paths should be present
    assert body.count('_log.emit("step_end"') >= 2
    # success=False must appear in a step_end (the failure path)
    assert "success=False" in body
    # And there should be a `raise` to re-throw the exception
    assert "    raise" in body or "raise\n" in body


# ---------------------------------------------------------------------------
# RESCUE handler (v0.8) — async mirror of the python target's lowering
# ---------------------------------------------------------------------------

RESCUE_MCP_SRC = """STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE: exact

STEP detect
  TAKES: rows: List<int>
  GIVES: result: int
  MODE: judgment

FLOW pipeline
  load(path="x.csv")
    -> detect(rows=rows)

  RESCUE detect:
    -> abort("detection failed")

RESOURCES
  target: mcp-server
"""


def test_mcp_emit_rescue_basic(tmp_path):
    """The emitted mcp-server flow module must wrap the protected (judgment)
    step in try/except, define an async _rescue_<step> helper that threads
    _session, render abort as raise FlowAborted, and gate the FlowAborted
    class on a RESCUE block being present."""
    MCPServerEmitter().emit(build_ir(parse(RESCUE_MCP_SRC)), tmp_path)

    flow_path = tmp_path / "pipeline" / "flow.py"
    assert flow_path.exists(), f"flow.py missing in {sorted(tmp_path.rglob('*'))}"
    flow_text = flow_path.read_text()

    # FlowAborted is defined in the emitted flow module, gated on rescues.
    assert "class FlowAborted(Exception)" in flow_text
    # Async helper present, threading _session for judgment-mode rescues.
    assert "async def _rescue_detect(state" in flow_text
    assert "_session" in flow_text
    # Abort renders as raise FlowAborted (single or double quoted literal).
    assert (
        "raise FlowAborted('detection failed')" in flow_text
        or 'raise FlowAborted("detection failed")' in flow_text
    )
    # Try/except around the call site, awaiting the async rescue helper.
    assert "try:" in flow_text
    assert "except FlowAborted:" in flow_text
    assert "await _rescue_detect(state" in flow_text


# --- impl.mode: mcp_tool emission for mcp-server (v0.10) --------------------

_MCP_SRV_SRC = (
    "STEP search\n"
    "  TAKES: query: str\n"
    "  GIVES: r: str\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode:    mcp_tool\n"
    "    server:  docs\n"
    "    tool:    search\n"
    "    args:    {q: \"${query}\"}\n"
    "FLOW f\n"
    '  search(query="x")\n'
    "RESOURCES\n"
    "  target: mcp-server\n"
    "  mcp_servers:\n"
    "    docs:\n"
    '      command: "mcp-server-docs"\n'
)


def test_mcp_server_emit_mcp_tool_step_uses_call_tool_sync(tmp_path):
    import ast

    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(_MCP_SRV_SRC)), tmp_path)
    body = (tmp_path / "f" / "steps" / "search.py").read_text()
    assert "_mcp.call_tool_sync(" in body
    assert "from ..clio_runtime import mcp_client as _mcp" in body
    ast.parse(body)


def test_mcp_server_emit_mcp_tool_bundles_runtime(tmp_path):
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(_MCP_SRV_SRC)), tmp_path)
    rt = tmp_path / "f" / "clio_runtime"
    assert (rt / "mcp_client.py").exists()
    assert (rt / "rest.py").exists()    # mcp_client imports subst from rest


# --- impl.mode: sql emission on mcp-server target (v0.11) -------------------

_SQL_SRV_SRC = (
    "STEP get_orders\n"
    "  TAKES: email: str\n"
    "  GIVES: orders: List<{id: int, status: str}>\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode:  sql\n"
    "    db:    crm\n"
    '    query: "SELECT id, status FROM orders WHERE email = :email"\n'
    "FLOW f\n"
    '  get_orders(email="x@y")\n'
    "RESOURCES\n"
    "  target: mcp-server\n"
    "  databases:\n"
    "    crm:\n"
    "      driver: sqlite\n"
    '      url:    "./crm.sqlite"\n'
)


def test_mcp_server_emit_sql_step(tmp_path):
    import ast

    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(_SQL_SRV_SRC)), tmp_path)
    body = (tmp_path / "f" / "steps" / "get_orders.py").read_text()
    assert "from ..clio_runtime import sql as _sql" in body
    assert "_sql.execute(_db_spec, _query, _params, gives_shape='list_of_records')" in body
    ast.parse(body)


def test_mcp_server_emit_sql_bundles_runtime(tmp_path):
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(_SQL_SRV_SRC)), tmp_path)
    rt = tmp_path / "f" / "clio_runtime"
    assert (rt / "sql.py").exists()


# ---------------------------------------------------------------------------
# T13: async rescue helper takes _err + substitutions mirror python (v0.13)
# ---------------------------------------------------------------------------

_RESCUE_ERROR_ACCESS_SRC = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP notify
  TAKES: channel: str, reason: str
  GIVES: sent: bool
  MODE:  exact

FLOW pipeline
  load(path="x") -> detect(rows=rows)

  RESCUE detect:
    -> notify(channel="#a", reason=detect.error.message)
    -> abort("boom")

RESOURCES
  target: mcp-server
  models: [haiku]
"""


def test_mcp_emitter_rescue_error_access_async(tmp_path):
    """async helper signature takes _err; substitutions identical to python."""
    MCPServerEmitter().emit(build_ir(parse(_RESCUE_ERROR_ACCESS_SRC)), tmp_path)
    flow_py = (tmp_path / "pipeline" / "flow.py").read_text()
    assert "async def _rescue_detect(state: dict, _err: BaseException, _session=None) -> None:" in flow_py
    assert "except Exception as _err:" in flow_py
    assert "await _rescue_detect(state, _err, _session=_session)" in flow_py
    assert "reason=str(_err)" in flow_py


# T14: async RESUME case — helper return + wrapper assign (v0.13)
# ---------------------------------------------------------------------------

_RESCUE_RESUME_SRC = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP recover
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  exact

STEP downstream
  TAKES: report: str
  GIVES: ok: bool
  MODE:  exact

FLOW pipeline
  load(path="x") -> detect(rows=rows) -> downstream(report=report)

  RESCUE detect:
    -> recover(rows=rows)
    -> RESUME(recover.report)

RESOURCES
  target: mcp-server
  models: [haiku]
"""


def test_mcp_emitter_rescue_resume_terminator_async(tmp_path):
    """RESUME terminator: wrapper assigns result, helper returns state field, -> object annotation."""
    MCPServerEmitter().emit(build_ir(parse(_RESCUE_RESUME_SRC)), tmp_path)
    flow_py = (tmp_path / "pipeline" / "flow.py").read_text()
    assert "state['report'] = await _rescue_detect(state, _err, _session=_session)" in flow_py
    assert "return state['report']" in flow_py
    assert "async def _rescue_detect(state: dict, _err: BaseException, _session=None) -> object:" in flow_py


# -- DESCRIPTION / STRATEGIES injection (v0.15) — was python-only before fix #2 --


def test_mcp_emit_injects_description_into_system_prompt(tmp_path):
    """A judgment step's DESCRIPTION must surface inside the emitted
    `_SYSTEM_PROMPT`, same as the python target. Pre-fix #2 the mcp-server
    target compiled a hard-coded legacy prompt that silently dropped the
    field, causing same-source / different-target behaviour drift."""
    src = (
        "STEP analyze\n"
        '  DESCRIPTION: "score risk on a cohort"\n'
        "  TAKES: rows: str\n  GIVES: risks: str\n  MODE: judgment\n"
        "FLOW p\n  analyze(rows=\"x\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "p" / "steps" / "analyze.py").read_text()
    assert "Step intent: score risk on a cohort" in body


def test_mcp_emit_injects_strategies_into_system_prompt(tmp_path):
    src = (
        "STEP analyze\n"
        "  STRATEGIES: |\n"
        "    - prefer high-recency signals\n"
        "  TAKES: rows: str\n  GIVES: risks: str\n  MODE: judgment\n"
        "FLOW p\n  analyze(rows=\"x\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "p" / "steps" / "analyze.py").read_text()
    assert "Heuristics:" in body
    assert "prefer high-recency signals" in body


def test_mcp_emit_no_description_keeps_legacy_prompt(tmp_path):
    """Without DESCRIPTION/STRATEGIES the mcp-server target keeps emitting
    the v0.14 legacy `_SYSTEM_PROMPT` literal verbatim."""
    src = (
        "STEP analyze\n"
        "  TAKES: rows: str\n  GIVES: risks: str\n  MODE: judgment\n"
        "FLOW p\n  analyze(rows=\"x\")\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "p" / "steps" / "analyze.py").read_text()
    assert "Step intent:" not in body
    assert "Heuristics:" not in body
    assert "    'You are a strict JSON-only API." in body


# -- v0.16: schemas from declared FLOW.TAKES / GIVES -----------------------


def test_mcp_server_schemas_from_declared_flow_signature(tmp_path):
    """inputSchema / outputSchema must reflect FLOW.TAKES / FLOW.GIVES when
    declared, overriding the first-step / last-step inference."""
    src = (
        "STEP classify\n"
        "  TAKES: item: str\n"
        "  GIVES: label: str\n"
        "  MODE:  judgment\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: items: List<str>\n"
        "  GIVES: labels: List<str>\n"
        "  FOR EACH item IN items PARALLEL AS labels:\n"
        "    classify(item=item)\n"
        "\n"
        "RESOURCES\n"
        "  target: mcp-server\n"
        "  models: [haiku]\n"
    )
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    server_py = (tmp_path / "pipeline" / "server.py").read_text()

    # inputSchema must reflect declared FLOW.TAKES (items: List<str>),
    # NOT the first step's TAKES (item: str).
    schema = _extract_input_schema(server_py, "pipeline")
    assert "items" in schema["properties"], (
        "inputSchema must expose declared FLOW.TAKES field 'items', not first-step 'item'"
    )
    assert schema["properties"]["items"]["type"] == "array"
    assert schema["properties"]["items"]["items"]["type"] == "string"
    assert "items" in schema["required"]
    assert "item" not in schema["properties"], (
        "inputSchema must NOT fall back to first-step 'item' when FLOW.TAKES is declared"
    )

    # outputSchema must reflect declared FLOW.GIVES (labels: List<str>).
    # Use string search to avoid duplicating the AST-walk helper.
    assert "outputSchema=" in server_py, "outputSchema must be present when FLOW.GIVES is declared"
    assert "'type': 'array'" in server_py or '"type": "array"' in server_py
    assert "'type': 'string'" in server_py or '"type": "string"' in server_py


def test_mcp_emits_one_tool_per_exposed_flow(tmp_path):
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW a\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  s(x=x)\n"
        "\n"
        "FLOW b\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  s(x=x)\n"
        "\n"
        "RESOURCES\n"
        "  target: mcp-server\n"
    )
    g = build_ir(parse(src))
    MCPServerEmitter().emit(g, tmp_path)
    # Find the generated project directory.
    proj = next(p for p in tmp_path.iterdir() if p.is_dir())
    server_py = (proj / "server.py").read_text()
    # Both FLOWs are exposed (neither calls the other).
    assert "@mcp.tool" in server_py or "Tool(" in server_py
    # The mcp lowlevel API registers tools as `Tool(name=...)` entries in
    # `list_tools()`. Each exposed FLOW gets exactly one such entry.
    assert server_py.count("name='a'") + server_py.count('name="a"') >= 1
    assert server_py.count("name='b'") + server_py.count('name="b"') >= 1
    # Both tool dispatch branches must exist in call_tool.
    assert "name == 'a'" in server_py or 'name == "a"' in server_py
    assert "name == 'b'" in server_py or 'name == "b"' in server_py


def test_mcp_called_subflow_is_not_exposed(tmp_path):
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE: exact\n"
        "\n"
        "FLOW helper\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  s(x=x)\n"
        "\n"
        "FLOW entry\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  helper(x=x)\n"
        "\n"
        "RESOURCES\n"
        "  target: mcp-server\n"
    )
    g = build_ir(parse(src))
    MCPServerEmitter().emit(g, tmp_path)
    proj = next(p for p in tmp_path.iterdir() if p.is_dir())
    server_py = (proj / "server.py").read_text()
    # Only `entry` is exposed; `helper` is called internally.
    assert "name == 'entry'" in server_py or 'name == "entry"' in server_py
    assert "name == 'helper'" not in server_py and 'name == "helper"' not in server_py
    # `helper` must NOT appear as a Tool() entry.
    assert "name='helper'" not in server_py and 'name="helper"' not in server_py
    # `helper` should be present as an orchestrator function in flow.py.
    flow_py = (proj / "flow.py").read_text()
    assert "async def helper(" in flow_py or "def helper(" in flow_py
