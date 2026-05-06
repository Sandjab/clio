# `target: mcp-server` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile a `.clio` source into a runnable MCP server. Each `FLOW` becomes a tool exposed by the server. Judgment steps delegate to the MCP client via `sampling/createMessage` (no API key, no `anthropic`/`openai` SDK on the server side).

**Architecture:** New `MCPServerEmitter` in `clio/emitters/mcp_server.py`. Reuses the python emitter's mechanics for exact steps, FOR EACH, CACHE, ON_FAIL, impl.rest, impl.shell — promoting the relevant helpers from `python.py` to `_python_helpers.py` where they aren't already module-level. New `_mcp_helpers.py` for the judgment-step shape (sampling) and the server / __main__ / README emission. CLI gains `mcp-server` as a `--target` choice.

**Tech Stack:** Python 3.12+, `mcp>=1.0` (only in the emitted project, not in CLIO itself), pytest.

**Spec:** `docs/superpowers/specs/2026-05-07-mcp-server-emitter-design.md`.

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `clio/emitters/mcp_server.py` | Create | `MCPServerEmitter(BaseEmitter)` class — orchestrates output dir, calls helpers. |
| `clio/emitters/_mcp_helpers.py` | Create | Module-level helpers: judgment via sampling, server.py, __main__.py, README, pyproject for mcp target, JSON Schema derivation for tool input/output. |
| `clio/emitters/_python_helpers.py` | Modify | Promote a few step-emission helpers from `python.py` to module-level so both emitters can call them. |
| `clio/emitters/python.py` | Modify | Replace the moved methods with thin delegations (so the `python` target keeps working). |
| `clio/cli.py` | Modify | Add `mcp-server` to the `--target` choices and dispatch to `MCPServerEmitter`. |
| `tests/test_emitters/test_mcp_server.py` | Create | Generation-only unit tests + one optional smoke runtime test (gated). |
| `tests/test_emitters/test_python.py` | (no functional change expected) | Verify that the python emitter still passes after the helper extraction. |
| `README.md` | Modify | Add `mcp-server` to the targets table; add a Quick start example. |
| `CLAUDE.md` | Modify | Add `mcp-server` to "How to run". |
| `CHANGELOG.md` | Modify | New "Emitters" entry under Unreleased. |
| `docs/LANGUAGE_SPEC.md` | Modify | Implementation-status table: `mcp-server` row. |
| `docs/COMPILATION_TARGETS.md` | Modify | Bump `mcp-server` from "Candidate" to "Implemented", expand row. |

Tests parse emitted code source via Python's standard library — `re` for finding the right block, `ast.literal_eval` for safely parsing the dict / list literals that appear in the emitted source. Never use `eval()` in tests; the hook will block it and `ast.literal_eval` is the right tool for parsing literals anyway.

---

## Task 1: CLI choice + emitter skeleton + first end-to-end compile

**Files:**
- Create: `clio/emitters/mcp_server.py`
- Create: `clio/emitters/_mcp_helpers.py`
- Modify: `clio/cli.py` (add `mcp-server` to `--target` choices)
- Create: `tests/test_emitters/test_mcp_server.py`

This task gets the absolute minimum compiling end-to-end: a `.clio` with one FLOW + one trivial EXACT step (default code mode, no contracts) produces an output dir with `pyproject.toml`, `__main__.py`, `server.py`, `flow.py`, `steps/<name>.py`, package `__init__.py`. We don't run the server in this task — just verify the dir tree is correct and the CLI accepts the new target.

- [ ] **Step 1: Write the failing test**

Create `tests/test_emitters/test_mcp_server.py`:

```python
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


def test_cli_accepts_mcp_server_target(tmp_path):
    from clio.cli import main
    src = tmp_path / "f.clio"
    src.write_text(_SIMPLE_FLOW_SRC)
    out = tmp_path / "out"
    rc = main(["compile", str(src), "--target", "mcp-server", "--output", str(out)])
    assert rc == 0
    assert (out / "pyproject.toml").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 2 FAIL with `ModuleNotFoundError: clio.emitters.mcp_server` / `argparse: invalid choice 'mcp-server'`.

- [ ] **Step 3: Implement the emitter skeleton**

Create `clio/emitters/_mcp_helpers.py`:

```python
"""Helpers for the mcp-server emitter. Module-level functions only — emitters
import from here, never from each other."""
from __future__ import annotations

from clio.ir.graph import FlowGraph


def _pyproject_for_mcp(pkg_name: str, *, needs_pydantic: bool, needs_requests: bool) -> str:
    deps: list[str] = ['    "mcp>=1.0",']
    if needs_pydantic:
        deps.append('    "pydantic>=2",')
    if needs_requests:
        deps.append('    "requests>=2.31",')
    deps_block = "\n".join(deps)
    return (
        "[build-system]\n"
        'requires = ["setuptools>=70"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "\n"
        "[project]\n"
        f'name = "{pkg_name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.12"\n'
        "dependencies = [\n"
        f"{deps_block}\n"
        "]\n"
        "\n"
        "[project.scripts]\n"
        f'{pkg_name} = "{pkg_name}.__main__:main"\n'
        "\n"
        "[tool.setuptools.packages.find]\n"
        f'include = ["{pkg_name}*"]\n'
    )


def _emit_main_module(pkg_name: str) -> str:
    return (
        '"""Stdio entry point for the mcp-server target."""\n'
        "from __future__ import annotations\n"
        "\n"
        "import asyncio\n"
        "\n"
        "from .server import server\n"
        "\n"
        "\n"
        "async def _run() -> None:\n"
        "    from mcp.server.stdio import stdio_server\n"
        "    async with stdio_server() as (read_stream, write_stream):\n"
        "        await server.run(\n"
        "            read_stream,\n"
        "            write_stream,\n"
        "            server.create_initialization_options(),\n"
        "        )\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    asyncio.run(_run())\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def _emit_server_module_minimal(pkg_name: str, graph: FlowGraph) -> str:
    """Bare server: registers each FLOW as a tool with a placeholder body.
    Real input/output schemas land in Task 2; real flow dispatch in Task 3."""
    flow_names = [graph.flow.name] if graph.flow is not None else []
    list_entries = ",\n".join(
        f'        Tool(name={n!r}, description="Auto-generated from FLOW {n}", '
        'inputSchema={"type": "object", "properties": {}, "required": []})'
        for n in flow_names
    )
    dispatch = "\n".join(
        f'    if name == {n!r}:\n'
        '        return [TextContent(type="text", text="not yet implemented")]'
        for n in flow_names
    ) or "    raise ValueError(f'unknown tool: {name}')"
    return (
        '"""MCP server for this CLIO-compiled package."""\n'
        "from __future__ import annotations\n"
        "\n"
        "from mcp.server.lowlevel import Server\n"
        "from mcp.types import TextContent, Tool\n"
        "\n"
        f"server = Server({pkg_name!r})\n"
        "\n"
        "\n"
        "@server.list_tools()\n"
        "async def list_tools() -> list[Tool]:\n"
        "    return [\n"
        f"{list_entries}\n"
        "    ]\n"
        "\n"
        "\n"
        "@server.call_tool()\n"
        "async def call_tool(name: str, arguments: dict) -> list[TextContent]:\n"
        f"{dispatch}\n"
        "    raise ValueError(f'unknown tool: {name}')\n"
    )


def _emit_flow_module_async_minimal(graph: FlowGraph) -> str:
    """Placeholder flow.py: just an async run() that returns the initial dict.
    Task 3 fills it in with real dispatching."""
    return (
        '"""Async FLOW orchestrator. Auto-generated; do not edit."""\n'
        "from __future__ import annotations\n"
        "\n"
        "\n"
        "async def run(*, _session=None, **initial: object) -> dict:\n"
        "    state: dict = dict(initial)\n"
        "    return state\n"
    )


def _emit_exact_step_stub(step_name: str) -> str:
    """Placeholder exact-step body. Task 2 plugs the real signature in."""
    return (
        f'"""STEP {step_name} (exact). Auto-generated stub."""\n'
        "from __future__ import annotations\n"
        "\n"
        "\n"
        f"def {step_name}(**kwargs):\n"
        "    raise NotImplementedError(\n"
        f"        \"Implement steps/{step_name}.py: this is an exact (deterministic) step.\"\n"
        "    )\n"
    )
```

Create `clio/emitters/mcp_server.py`:

```python
"""target: mcp-server — compiles a .clio source to a runnable MCP server.

Each FLOW is exposed as a tool registered with the MCP Python SDK. Judgment
steps delegate to the MCP client via sampling/createMessage (no API key on
the server side, no anthropic/openai dep)."""
from __future__ import annotations

from pathlib import Path

from clio.emitters._mcp_helpers import (
    _emit_exact_step_stub,
    _emit_flow_module_async_minimal,
    _emit_main_module,
    _emit_server_module_minimal,
    _pyproject_for_mcp,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import FlowGraph


class MCPServerEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)

        pkg_name = graph.flow.name if graph.flow is not None else "clio_mcp"
        pkg_dir = output_dir / pkg_name
        steps_dir = pkg_dir / "steps"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        steps_dir.mkdir(parents=True, exist_ok=True)

        (output_dir / "pyproject.toml").write_text(
            _pyproject_for_mcp(pkg_name, needs_pydantic=False, needs_requests=False)
        )
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "__main__.py").write_text(_emit_main_module(pkg_name))
        (pkg_dir / "server.py").write_text(_emit_server_module_minimal(pkg_name, graph))
        (pkg_dir / "flow.py").write_text(_emit_flow_module_async_minimal(graph))

        for step in graph.steps:
            (steps_dir / f"{step.name}.py").write_text(_emit_exact_step_stub(step.name))
```

Modify `clio/cli.py` — locate the `compile_p.add_argument("--target", ...)` line and update the `choices` list:

```python
    compile_p.add_argument(
        "--target",
        required=True,
        choices=["claude-cli", "python", "mcp-server"],
    )
```

Locate the `if target == "claude-cli": ... elif target == "python": ...` chain in `_cmd_compile` and add a third branch:

```python
    elif target == "mcp-server":
        from clio.emitters.mcp_server import MCPServerEmitter
        MCPServerEmitter().emit(graph, out_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 2 passed.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`
Expected: full suite green (was 268+2; should now be 270+2).

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/mcp_server.py clio/emitters/_mcp_helpers.py clio/cli.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): skeleton emitter — file tree + CLI choice

MCPServerEmitter creates the bare output: pyproject (mcp>=1.0),
__main__ that runs stdio_server, server.py with placeholder tool
registration, flow.py with async run() returning initial state,
steps/*.py stubs. CLI accepts --target mcp-server. Tasks 2-N flesh
out the real bodies.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: server.py registers FLOW with real inputSchema from TAKES

**Files:**
- Modify: `clio/emitters/_mcp_helpers.py`
- Modify: `clio/emitters/mcp_server.py`
- Modify: `tests/test_emitters/test_mcp_server.py`

The placeholder `_emit_server_module_minimal` registers each FLOW with an empty inputSchema. This task derives the inputSchema from the **first STEP's TAKES** (using existing `type_to_json_schema` from `clio/ir/contracts.py`). Defaults from literal kwargs in the FLOW are deferred to Task 7.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
import ast
import re


_FLOW_WITH_TAKES_SRC = (
    "STEP greet\n"
    "  TAKES: name: str, count: int\n"
    "  GIVES: msg: str\n"
    "  MODE:  exact\n"
    "FLOW hello\n"
    "  greet(name=\"World\", count=3)\n"
)


def _extract_input_schema(server_py: str, tool_name: str) -> dict:
    """Pull the inputSchema dict literal out of the emitted server.py.
    Heuristic: find the inputSchema=... block; safely parse with ast.literal_eval."""
    match = re.search(
        rf"Tool\(\s*name={tool_name!r},.*?inputSchema=(\{{.*?\}})",
        server_py,
        re.DOTALL,
    )
    assert match, f"inputSchema for {tool_name} not found in server.py"
    return ast.literal_eval(match.group(1))


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py::test_server_input_schema_reflects_first_step_takes -v`
Expected: FAIL — current schema is empty.

- [ ] **Step 3: Implement real inputSchema derivation**

Add an import for `type_to_json_schema` and IR types to `clio/emitters/_mcp_helpers.py`:

```python
from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import CallIR, FlowGraph, ForEachIR, StepIR
```

Add this helper (used here in Task 2 and again in Task 7):

```python
def _first_step_of_flow(graph: FlowGraph) -> StepIR | None:
    """Returns the StepIR for the first CallIR in the flow chain, or None."""
    if graph.flow is None:
        return None
    by_name = {s.name: s for s in graph.steps}
    for elem in graph.flow.chain:
        if isinstance(elem, CallIR):
            return by_name.get(elem.step_name)
        if isinstance(elem, ForEachIR):
            for inner in elem.body:
                if isinstance(inner, CallIR):
                    return by_name.get(inner.step_name)
    return None


def _input_schema_for_flow(graph: FlowGraph) -> dict:
    first = _first_step_of_flow(graph)
    if first is None or not first.takes:
        return {"type": "object", "properties": {}, "required": []}
    properties = {t.name: type_to_json_schema(t.type) for t in first.takes}
    return {
        "type": "object",
        "properties": properties,
        "required": [t.name for t in first.takes],
    }
```

Replace `_emit_server_module_minimal` with `_emit_server_module`:

```python
def _emit_server_module(pkg_name: str, graph: FlowGraph) -> str:
    flow_name = graph.flow.name if graph.flow is not None else None
    if flow_name is None:
        return (
            '"""MCP server for this CLIO-compiled package."""\n'
            "from __future__ import annotations\n"
            "\n"
            "from mcp.server.lowlevel import Server\n"
            "from mcp.types import TextContent, Tool\n"
            "\n"
            f"server = Server({pkg_name!r})\n"
            "\n"
            "\n"
            "@server.list_tools()\n"
            "async def list_tools() -> list[Tool]:\n"
            "    return []\n"
            "\n"
            "\n"
            "@server.call_tool()\n"
            "async def call_tool(name: str, arguments: dict) -> list[TextContent]:\n"
            "    raise ValueError(f'unknown tool: {name}')\n"
        )

    schema = _input_schema_for_flow(graph)
    tool_entry = (
        f"        Tool(\n"
        f"            name={flow_name!r},\n"
        f'            description="Auto-generated from FLOW {flow_name}",\n'
        f"            inputSchema={schema!r},\n"
        f"        )"
    )
    return (
        '"""MCP server for this CLIO-compiled package."""\n'
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "\n"
        "from mcp.server.lowlevel import Server\n"
        "from mcp.types import TextContent, Tool\n"
        "\n"
        "from . import flow as _flow\n"
        "\n"
        f"server = Server({pkg_name!r})\n"
        "\n"
        "\n"
        "@server.list_tools()\n"
        "async def list_tools() -> list[Tool]:\n"
        "    return [\n"
        f"{tool_entry},\n"
        "    ]\n"
        "\n"
        "\n"
        "@server.call_tool()\n"
        "async def call_tool(name: str, arguments: dict) -> list[TextContent]:\n"
        f"    if name == {flow_name!r}:\n"
        "        result = await _flow.run(**arguments)\n"
        '        return [TextContent(type="text", text=json.dumps(result, default=str))]\n'
        "    raise ValueError(f'unknown tool: {name}')\n"
    )
```

Update `clio/emitters/mcp_server.py`'s import + call:

```python
from clio.emitters._mcp_helpers import (
    _emit_exact_step_stub,
    _emit_flow_module_async_minimal,
    _emit_main_module,
    _emit_server_module,
    _pyproject_for_mcp,
)
```

```python
        (pkg_dir / "server.py").write_text(_emit_server_module(pkg_name, graph))
```

Remove the now-unused `_emit_server_module_minimal` from `_mcp_helpers.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 4 passed.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`
Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_mcp_helpers.py clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): tool registration with real inputSchema from first step's TAKES

Each FLOW is registered as a tool. inputSchema derives properties from
the first step's TAKES (type_to_json_schema reuse) and marks them all
required. Defaults from literal kwargs in the FLOW are Task 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: flow.py async — real chain emission

**Files:**
- Modify: `clio/emitters/_mcp_helpers.py`
- Modify: `tests/test_emitters/test_mcp_server.py`

The placeholder `_emit_flow_module_async_minimal` returns the initial dict. This task implements the real flow chain — calls per step, FOR EACH support, `_session` placeholder. Mirrors `python.py:_emit_flow` but produces an `async def run`. Judgment-step `await` wiring lands in Task 6 (we keep step bodies as plain function calls here; Task 6 flips them to `await` when judgment-via-sampling is implemented).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py::test_flow_module_is_async_and_chains_steps -v`
Expected: FAIL — placeholder flow.py doesn't import or chain.

- [ ] **Step 3: Implement real flow.py emission**

In `clio/emitters/_mcp_helpers.py`, replace `_emit_flow_module_async_minimal` with `_emit_flow_module_async`. Mirror the chain-traversal logic from `python.py:_emit_flow`. Read that method first to match its kwarg-disambiguation logic exactly. Key facts about how the IR represents kwargs:

- The IR's `CallIR.kwargs` is `tuple[tuple[str, object], ...]`.
- Literals are stored as the corresponding Python value (string, int, etc.).
- State references are stored as bare strings — the python emitter uses a heuristic to disambiguate. **Read the existing python target's `_emit_flow` body and copy its kwarg-rendering rules verbatim** so behavior matches.

Sketch of the new function (adapt to match python target's kwarg logic):

```python
def _emit_flow_module_async(graph: FlowGraph) -> str:
    if graph.flow is None:
        return (
            '"""Async FLOW orchestrator. Auto-generated; do not edit."""\n'
            "from __future__ import annotations\n"
            "\n"
            "\n"
            "async def run(*, _session=None, **initial: object) -> dict:\n"
            "    return dict(initial)\n"
        )

    steps_by_name = {s.name: s for s in graph.steps}
    referenced: list[str] = []

    def _collect(chain) -> None:
        for elem in chain:
            if isinstance(elem, CallIR):
                if elem.step_name not in referenced:
                    referenced.append(elem.step_name)
            elif isinstance(elem, ForEachIR):
                _collect(elem.body)

    _collect(graph.flow.chain)
    imports = "\n".join(f"from .steps import {n} as {n}_mod" for n in referenced)
    body_lines: list[str] = []

    def _render_call(call: CallIR, indent: str, scope_local: set[str]) -> None:
        step = steps_by_name.get(call.step_name)
        gives_name = step.gives.name if step and step.gives else None
        # Mirror python target's disambiguation: read python.py:_emit_flow
        # for the canonical implementation and copy its kwarg rules here.
        kwargs_parts: list[str] = []
        for kw_name, kw_val in call.kwargs:
            if isinstance(kw_val, str) and kw_val in scope_local:
                kwargs_parts.append(f"{kw_name}={kw_val}")
            elif isinstance(kw_val, str):
                # Match python emitter: identifiers (no quotes in source) are
                # state references; quoted strings remain literals. The IR
                # builder distinguishes via type-based logic — read python
                # emitter to confirm.
                if step is not None and any(t.name == kw_name for t in step.takes):
                    # If matching a TAKES name AND looks like an identifier,
                    # it's likely a state reference. Otherwise literal.
                    # Use python target's exact rule.
                    kwargs_parts.append(f"{kw_name}=state[{kw_val!r}]")
                else:
                    kwargs_parts.append(f"{kw_name}={kw_val!r}")
            else:
                kwargs_parts.append(f"{kw_name}={kw_val!r}")
        kwargs_repr = ", ".join(kwargs_parts)
        call_expr = f"{call.step_name}_mod.{call.step_name}({kwargs_repr})"
        if gives_name is not None:
            body_lines.append(f"{indent}state[{gives_name!r}] = {call_expr}")
        else:
            body_lines.append(f"{indent}{call_expr}")

    def _render_chain(chain, indent: str, scope_local: set[str]) -> None:
        for elem in chain:
            if isinstance(elem, CallIR):
                _render_call(elem, indent, scope_local)
            elif isinstance(elem, ForEachIR):
                body_lines.append(f"{indent}for {elem.loop_var} in state[{elem.collection!r}]:")
                _render_chain(elem.body, indent + "    ", scope_local | {elem.loop_var})

    _render_chain(graph.flow.chain, "    ", set())
    body_block = "\n".join(body_lines)

    return (
        '"""Async FLOW orchestrator. Auto-generated; do not edit."""\n'
        "from __future__ import annotations\n"
        "\n"
        f"{imports}\n"
        "\n"
        "\n"
        "async def run(*, _session=None, **initial: object) -> dict:\n"
        "    state: dict = dict(initial)\n"
        f"{body_block}\n"
        "    return state\n"
    )
```

If the kwarg rendering doesn't match the test assertions, **read the body of `clio/emitters/python.py:PythonEmitter._emit_flow`** — that method has the canonical disambiguation logic, and it's already test-covered for the python target. Copy its rules here.

Update `clio/emitters/mcp_server.py`:

```python
from clio.emitters._mcp_helpers import (
    _emit_exact_step_stub,
    _emit_flow_module_async,        # renamed
    _emit_main_module,
    _emit_server_module,
    _pyproject_for_mcp,
)
```

```python
        (pkg_dir / "flow.py").write_text(_emit_flow_module_async(graph))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 6 passed.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`
Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_mcp_helpers.py clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): real async flow.py — chain rendering with FOR EACH

Mirrors python emitter's flow gen but produces async def run. Loop
variables bound via FOR EACH are passed as kwargs (not via state[]);
literal kwargs use repr(); state references use state['<name>'].
Step bodies still raise NotImplementedError — Tasks 4 and 6 plug in
real exact and judgment bodies.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Exact step emission (default code mode)

**Files:**
- Modify: `clio/emitters/_python_helpers.py` — promote `emit_default_exact_step` to module level
- Modify: `clio/emitters/python.py` — delegate to the new module-level helper
- Modify: `clio/emitters/mcp_server.py` — call the same helper
- Modify: `tests/test_emitters/test_mcp_server.py`
- (Verify) `tests/test_emitters/test_python.py` still passes

Pull the simplest exact-step emission (default code mode — the stub `raise NotImplementedError` style) out of `python.py:_emit_exact_step` into a module-level helper in `_python_helpers.py`. Both emitters call it. impl.rest and impl.shell are Task 7.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py::test_emit_default_exact_step_has_signature_and_stub -v`
Expected: FAIL — current emission is the placeholder `def greet(**kwargs)`.

- [ ] **Step 3: Implement**

In `clio/emitters/_python_helpers.py`, add a module-level helper:

```python
def emit_default_exact_step(step: "StepIR", contracts_by_name: dict[str, "ContractIR"]) -> str:
    """Emit a default-mode (no impl, or impl.mode: code) exact step body.
    Both python and mcp-server targets emit this identical shape."""
    params = _step_signature(step, contracts_by_name)
    ret_type = (
        _type_to_python(step.gives.type, contracts_by_name)
        if step.gives is not None else "None"
    )
    takes_doc = (
        "\n    ".join(f"{t.name}: {_render_type_short(t.type)}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {_render_type_short(step.gives.type)}"
        if step.gives is not None else "(no GIVES)"
    )
    return (
        f'"""STEP {step.name} (exact)\n'
        f'TAKES:\n'
        f'    {takes_doc}\n'
        f'GIVES:\n'
        f'    {gives_doc}\n\n'
        f'Implement the body below. The orchestrator passes arguments by keyword\n'
        f'and expects the return value to conform to the GIVES type.\n'
        f'"""\n'
        f'from __future__ import annotations\n'
        f'\n\n'
        f'def {step.name}({params}) -> {ret_type}:\n'
        f'    raise NotImplementedError(\n'
        f'        "Implement steps/{step.name}.py: this is an exact (deterministic) step."\n'
        f'    )\n'
    )
```

In `clio/emitters/python.py`, find the body of `_emit_exact_step` for the default branch (when `step.impl` is None or a `CodeImplIR`). Replace its body with a delegation:

```python
        # default branch (no impl, or impl.mode: code)
        from clio.emitters._python_helpers import emit_default_exact_step
        return emit_default_exact_step(step, contracts_by_name)
```

Keep the `if isinstance(step.impl, RestImplIR): return self._emit_rest_step(...)` and `if isinstance(step.impl, ShellImplIR): return self._emit_shell_step(...)` branches **unchanged** (Task 7 moves those).

In `clio/emitters/mcp_server.py`, replace the placeholder loop in `MCPServerEmitter.emit`:

```python
        from clio.emitters._python_helpers import emit_default_exact_step
        contracts_by_name = {c.name: c for c in graph.contracts}
        for step in graph.steps:
            if step.mode == "exact":
                body = emit_default_exact_step(step, contracts_by_name)
            else:
                body = _emit_exact_step_stub(step.name)  # judgment placeholder; Task 6
            (steps_dir / f"{step.name}.py").write_text(body)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 7 passed.

Run: `.venv/bin/python -m pytest tests/test_emitters/test_python.py -v 2>&1 | tail -5`
Expected: still all green (output should be byte-identical).

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`
Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_python_helpers.py clio/emitters/python.py clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): default-mode exact step emission (shared with python target)

Promotes the default-exact-step body to a module-level helper in
_python_helpers.py. Both python and mcp-server emitters call it. The
emitted shape is byte-identical to what python.py used to render
inline. impl.rest and impl.shell stay on PythonEmitter for now (Task 7).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Refused combinations

**Files:**
- Modify: `clio/emitters/mcp_server.py`
- Modify: `tests/test_emitters/test_mcp_server.py`

mcp-server is sampling-only. A `.clio` with `invoke.protocol: anthropic|openai`, `invoke.mode: cli`, or no FLOW is rejected at compile time with a clear message.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v -k "rejects"`
Expected: 4 FAIL — current emitter doesn't raise.

- [ ] **Step 3: Implement the refusal pass**

In `clio/emitters/mcp_server.py`, add a `_validate_for_mcp` method called at the top of `emit`:

```python
from clio.ir.graph import ApiInvokeIR, CliInvokeIR


class MCPServerEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        self._validate_for_mcp(graph)

        # ... rest of emit unchanged

    def _validate_for_mcp(self, graph: FlowGraph) -> None:
        if graph.flow is None:
            raise ValueError(
                "mcp-server target requires at least one FLOW (each FLOW becomes a tool)"
            )
        for step in graph.steps:
            if isinstance(step.invoke, CliInvokeIR):
                raise ValueError(
                    f"step {step.name!r}: invoke.mode: cli is not supported by mcp-server "
                    "(use --target claude-cli for CLI invocation)"
                )
            if isinstance(step.invoke, ApiInvokeIR):
                if step.invoke.protocol in ("anthropic", "openai"):
                    raise ValueError(
                        f"step {step.name!r}: invoke.protocol: {step.invoke.protocol!r} is not "
                        "supported by mcp-server (sampling-only); use --target python for "
                        "direct SDK access"
                    )
                if step.invoke.protocol in ("bedrock", "vertex"):
                    raise ValueError(
                        f"step {step.name!r}: invoke.protocol: {step.invoke.protocol!r} is not "
                        "yet supported by any target"
                    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 11 passed.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): reject anthropic/openai/cli protocols and no-FLOW sources

A .clio with invoke.protocol: anthropic|openai is sampling-incompatible
— refuse at emit with a pointer to --target python. invoke.mode: cli
points at --target claude-cli. .clio with no FLOW means nothing to
expose as a tool — clear error.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Judgment step via mcp_sampling

**Files:**
- Modify: `clio/emitters/_mcp_helpers.py`
- Modify: `clio/emitters/mcp_server.py`
- Modify: `clio/emitters/_mcp_helpers.py`'s `_emit_flow_module_async` to await judgment calls and pass `_session=_session`
- Modify: `clio/emitters/_mcp_helpers.py`'s `_emit_server_module` to thread `ctx.session` into `_flow.run`
- Modify: `tests/test_emitters/test_mcp_server.py`

Implement the judgment-step body that calls `session.create_message(...)`. Threads `_session` through `flow.run` to each judgment step. The MCP server's `call_tool` handler receives the `RequestContext`, pulls `ctx.session`, and passes it to `_flow.run`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
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
    # The server must reach for the MCP request context to get the session.
    # Allow either ctx.session or request_context.session phrasing.
    assert "ctx.session" in server_py or "request_context.session" in server_py
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v -k "judgment or awaits or session"`
Expected: 3 FAIL.

- [ ] **Step 3: Implement judgment-step emission**

In `clio/emitters/_mcp_helpers.py`, add the judgment step emitter (it builds on existing helpers in `_python_helpers.py` for the prompt assembly):

```python
def emit_judgment_step_via_sampling(step: StepIR, graph: FlowGraph, contracts_by_name: dict) -> str:
    """Emit a judgment step that delegates to the MCP client via
    session.create_message(...). No anthropic/openai SDK in the emitted code."""
    from clio.emitters._claude_cli_helpers import _inline_schema, _render_prompt
    from clio.emitters._python_helpers import _step_signature, _to_class_name, _type_to_python
    from clio.parser.ast_nodes import ContractRef, ListType

    params = _step_signature(step, contracts_by_name)
    # Append _session keyword argument to the existing kwargs-only signature.
    if params:
        params_with_session = f"{params}, _session"
    else:
        params_with_session = "*, _session"

    ret_type = (
        _type_to_python(step.gives.type, contracts_by_name)
        if step.gives is not None else "None"
    )

    prompt_template = _render_prompt(step)
    inlined_json = _inline_schema(step.gives.type, graph.contracts) if step.gives else "{}"

    sub_lines = [
        f"    prompt = prompt.replace('${{{t.name}}}', json.dumps({t.name}))"
        for t in step.takes
    ]
    sub_lines.append("    prompt = prompt.replace('${schema}', _INLINED_SCHEMA)")
    sub_block = "\n".join(sub_lines)

    if step.gives is None:
        validate_block = "    return None"
    else:
        t = step.gives.type
        if isinstance(t, ContractRef):
            cls = _to_class_name(t.name)
            validate_block = (
                f"    return contracts.{cls}.model_validate(json.loads(cleaned))"
            )
        elif isinstance(t, ListType) and isinstance(t.inner, ContractRef):
            cls = _to_class_name(t.inner.name)
            validate_block = (
                f"    return [contracts.{cls}.model_validate(item) "
                f"for item in json.loads(cleaned)]"
            )
        else:
            validate_block = "    return json.loads(cleaned)"

    has_contracts = bool(graph.contracts)
    contracts_import = "from .. import contracts\n" if has_contracts else ""

    return (
        f'"""STEP {step.name} (judgment, mcp_sampling).\n'
        f'Auto-generated. Do not edit; regenerate via `clio compile`.\n'
        f'"""\n'
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "\n"
        f"{contracts_import}"
        "\n"
        f"_PROMPT_TEMPLATE = {prompt_template!r}\n"
        f"_INLINED_SCHEMA = {inlined_json!r}\n"
        "_SYSTEM_PROMPT = (\n"
        "    'You are a strict JSON-only API. Output exactly one JSON document matching '\n"
        "    'the requested schema, with no prose, no markdown code fences, no commentary, '\n"
        "    'and no leading or trailing whitespace beyond the JSON itself.'\n"
        ")\n"
        "_MAX_TOKENS = 4096\n"
        "\n"
        "\n"
        f"async def {step.name}({params_with_session}) -> {ret_type}:\n"
        "    prompt = _PROMPT_TEMPLATE\n"
        f"{sub_block}\n"
        "    from mcp.types import SamplingMessage, TextContent\n"
        "    msg = await _session.create_message(\n"
        "        messages=[\n"
        "            SamplingMessage(\n"
        "                role='user',\n"
        "                content=TextContent(type='text', text=prompt),\n"
        "            )\n"
        "        ],\n"
        "        max_tokens=_MAX_TOKENS,\n"
        "        system_prompt=_SYSTEM_PROMPT,\n"
        "    )\n"
        "    raw = msg.content.text if getattr(msg.content, 'type', None) == 'text' else ''\n"
        "    cleaned = '\\n'.join(line for line in raw.splitlines() if not line.startswith('```'))\n"
        f"{validate_block}\n"
    )
```

In `clio/emitters/mcp_server.py`, dispatch judgment steps to the new helper:

```python
from clio.emitters._mcp_helpers import (
    _emit_exact_step_stub,
    _emit_flow_module_async,
    _emit_main_module,
    _emit_server_module,
    _pyproject_for_mcp,
    emit_judgment_step_via_sampling,
)
from clio.emitters._python_helpers import emit_default_exact_step
```

In `MCPServerEmitter.emit`, replace the steps-loop:

```python
        contracts_by_name = {c.name: c for c in graph.contracts}
        for step in graph.steps:
            if step.mode == "exact":
                body = emit_default_exact_step(step, contracts_by_name)
            else:
                body = emit_judgment_step_via_sampling(step, graph, contracts_by_name)
            (steps_dir / f"{step.name}.py").write_text(body)
```

In `clio/emitters/_mcp_helpers.py`, modify `_emit_flow_module_async`'s `_render_call` to await judgment-step calls and pass `_session=_session`:

```python
        is_judgment = step is not None and step.mode == "judgment"
        if is_judgment:
            if kwargs_repr:
                call_expr = f"{call.step_name}_mod.{call.step_name}({kwargs_repr}, _session=_session)"
            else:
                call_expr = f"{call.step_name}_mod.{call.step_name}(_session=_session)"
            call_expr = f"await {call_expr}"
        else:
            call_expr = f"{call.step_name}_mod.{call.step_name}({kwargs_repr})"
```

In `_emit_server_module`, replace the `call_tool` body with a session-threading version:

```python
    return (
        '"""MCP server for this CLIO-compiled package."""\n'
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "\n"
        "from mcp.server.lowlevel import Server\n"
        "from mcp.types import TextContent, Tool\n"
        "\n"
        "from . import flow as _flow\n"
        "\n"
        f"server = Server({pkg_name!r})\n"
        "\n"
        "\n"
        "@server.list_tools()\n"
        "async def list_tools() -> list[Tool]:\n"
        "    return [\n"
        f"{tool_entry},\n"
        "    ]\n"
        "\n"
        "\n"
        "@server.call_tool()\n"
        "async def call_tool(name: str, arguments: dict) -> list[TextContent]:\n"
        "    ctx = server.request_context\n"
        f"    if name == {flow_name!r}:\n"
        "        result = await _flow.run(_session=ctx.session, **arguments)\n"
        '        return [TextContent(type="text", text=json.dumps(result, default=str))]\n'
        "    raise ValueError(f'unknown tool: {name}')\n"
    )
```

The exact attribute path for the request context (`server.request_context.session` versus `ctx.session` reached differently) may vary by `mcp` package version. The test asserts that the emitted server.py contains either `ctx.session` or `request_context.session` to give some flex. If the MCP SDK version pinned changes the API, adjust both the emitted code and the test together.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 14 passed.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_mcp_helpers.py clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): judgment via sampling/createMessage (no SDK on the server)

Judgment steps are emitted as async def <name>(*, ..., _session) that
build the prompt + inlined schema (same shape as the python target) but
call session.create_message(...) on the MCP session instead of an
Anthropic/OpenAI client. server.py threads ctx.session into flow.run.
flow.py awaits judgment-step calls. Output validation against the
contract reuses the python target's Pydantic conversion.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Inherited features — contracts, defaults, REST, SHELL, CACHE, ON_FAIL, output schema

**Files:**
- Modify: `clio/emitters/_python_helpers.py` — promote `emit_rest_step`, `emit_shell_step`, `emit_contracts` to module level
- Modify: `clio/emitters/python.py` — delegate
- Modify: `clio/emitters/mcp_server.py` — emit contracts.py + clio_runtime/cache.py + use the promoted helpers
- Modify: `clio/emitters/_mcp_helpers.py` — defaults in inputSchema, output schema, CACHE/ON_FAIL wrappers in the sampling step body
- Modify: `tests/test_emitters/test_mcp_server.py`

Consolidates "feature parity with python target" work. Each sub-step has its own test. Chained because they all depend on python emitter helpers.

- [ ] **Step 1: Write failing tests covering all the parity features**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
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
    assert "url.replace('${url}', str(url))" in body


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
    m = re.search(r"outputSchema=(\{.*?\})", server_py, re.DOTALL)
    assert m, "outputSchema not present in server.py"
    output_schema = ast.literal_eval(m.group(1))
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
    # A retry loop or attempt counter should be visible. Exact shape inherited
    # from the python target's on_fail wrapper logic.
    assert "retry" in body.lower() or "_attempt" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v -k "contracts or pyproject or rest or shell or input_schema or output_schema or for_each or cache or on_fail"`
Expected: 10 FAIL.

- [ ] **Step 3: Implement parity features**

Work through each failure in order.

**3a. Promote rest + shell step emission to module level.** In `clio/emitters/python.py`, find `_emit_rest_step` and `_emit_shell_step`. Convert each to a module-level function in `_python_helpers.py` named `emit_rest_step(step, contracts_by_name, impl)` and `emit_shell_step(step, contracts_by_name, impl)`. Move the entire body. The python emitter's two methods become one-line delegations. Verify python target still passes: `.venv/bin/python -m pytest tests/test_emitters/test_python.py -v 2>&1 | tail -5`.

**3b. Wire mcp-server emitter to call the new helpers.** In `MCPServerEmitter.emit`:

```python
        from clio.emitters._python_helpers import (
            emit_default_exact_step,
            emit_rest_step,
            emit_shell_step,
        )
        from clio.ir.graph import RestImplIR, ShellImplIR

        contracts_by_name = {c.name: c for c in graph.contracts}
        for step in graph.steps:
            if step.mode == "judgment":
                body = emit_judgment_step_via_sampling(step, graph, contracts_by_name)
            elif isinstance(step.impl, RestImplIR):
                body = emit_rest_step(step, contracts_by_name, step.impl)
            elif isinstance(step.impl, ShellImplIR):
                body = emit_shell_step(step, contracts_by_name, step.impl)
            else:
                body = emit_default_exact_step(step, contracts_by_name)
            (steps_dir / f"{step.name}.py").write_text(body)
```

**3c. Emit contracts.py when contracts are present.** Promote `python.py:_emit_contracts` body to a module-level `emit_contracts(graph)` in `_python_helpers.py`. Have python.py delegate. Then in mcp_server.py:

```python
        from clio.emitters._python_helpers import emit_contracts
        (pkg_dir / "contracts.py").write_text(emit_contracts(graph))
```

**3d. pyproject.toml conditional deps.** Wire `needs_pydantic` and `needs_requests`:

```python
        from clio.ir.graph import RestImplIR
        needs_pydantic = bool(graph.contracts)
        needs_requests = any(isinstance(s.impl, RestImplIR) for s in graph.steps)
        (output_dir / "pyproject.toml").write_text(
            _pyproject_for_mcp(pkg_name, needs_pydantic=needs_pydantic, needs_requests=needs_requests)
        )
```

**3e. Copy clio_runtime/cache.py when CACHE is used.**

```python
        cache_active = any(
            s.cache is not None and s.cache.mode in ("on", "ttl")
            for s in graph.steps
        )
        if cache_active:
            runtime_dir = pkg_dir / "clio_runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "__init__.py").write_text("")
            from clio import runtime as src_pkg
            src = Path(src_pkg.__file__).parent / "cache.py"
            (runtime_dir / "cache.py").write_text(src.read_text())
```

**3f. CACHE + ON_FAIL in the judgment-via-sampling step body.** Read `python.py:_emit_judgment_step` carefully to understand how it wraps the SDK call in CACHE + ON_FAIL. The structure: try a cache hit, on miss run an `_attempt` function, on failure run a retry/escalate/fallback chain. Mirror that structure in `emit_judgment_step_via_sampling` — replace the `client.messages.create(...)` block with `await _session.create_message(...)`, but keep the cache and on_fail wrappers identical to the python target. The cleanest path: extract the cache and on_fail wrapper logic from `python.py:_emit_judgment_step` into module-level helpers in `_python_helpers.py` (e.g. `emit_cache_wrapper`, `emit_on_fail_chain`) so both judgment paths reuse them. If that's too invasive for one task, copy the relevant snippets into `_mcp_helpers.py` and note the duplication for a future refactor.

**3g. inputSchema defaults from literal FLOW kwargs.** In `_input_schema_for_flow`:

```python
def _input_schema_for_flow(graph: FlowGraph) -> dict:
    first = _first_step_of_flow(graph)
    if first is None or not first.takes:
        return {"type": "object", "properties": {}, "required": []}

    literal_defaults: dict[str, object] = {}
    if graph.flow is not None:
        for elem in graph.flow.chain:
            if isinstance(elem, CallIR) and elem.step_name == first.name:
                for kw_name, kw_val in elem.kwargs:
                    # Mirror python emitter's literal-detection logic. The IR
                    # builder distinguishes literals (str/int/float/bool stored
                    # as the value) from state-references (bare identifier
                    # strings). Read python.py:_emit_flow for the canonical rule.
                    if _is_literal_kwarg(first, kw_name, kw_val):
                        literal_defaults[kw_name] = kw_val
                break

    properties = {}
    required = []
    for t in first.takes:
        prop = type_to_json_schema(t.type)
        if t.name in literal_defaults:
            prop["default"] = literal_defaults[t.name]
        else:
            required.append(t.name)
        properties[t.name] = prop
    return {"type": "object", "properties": properties, "required": required}


def _is_literal_kwarg(step: StepIR, kw_name: str, kw_val) -> bool:
    """Match the python emitter's literal-vs-state-ref disambiguation.
    Read python.py:_emit_flow for the canonical rule and replicate."""
    if not isinstance(kw_val, str):
        return True
    # When the value is a bare identifier matching a TAKES, the python emitter
    # treats it as a state reference. When the source had quotes around the
    # value, the IR-builder strips them and stores a Python str — but the
    # disambiguation has to come from the IR's typing. Read the existing
    # python emitter carefully and mirror its exact rule here.
    return False  # CONSERVATIVE: treat strings as state refs by default;
                  # widen as needed once the test reveals the exact need.
```

If `_is_literal_kwarg` ends up failing the test (because the FLOW kwarg `name="World"` is a literal but the helper says it's a state ref), inspect `python.py:_emit_flow` for the precise disambiguation and replicate. The python target's tests already cover this distinction; mirror its logic verbatim.

**3h. outputSchema from last step's GIVES.**

```python
def _last_step_of_flow(graph: FlowGraph) -> StepIR | None:
    if graph.flow is None:
        return None
    by_name = {s.name: s for s in graph.steps}
    last_call = None

    def _walk(chain):
        nonlocal last_call
        for elem in chain:
            if isinstance(elem, CallIR):
                last_call = elem
            elif isinstance(elem, ForEachIR):
                _walk(elem.body)

    _walk(graph.flow.chain)
    return by_name.get(last_call.step_name) if last_call else None


def _output_schema_for_flow(graph: FlowGraph) -> dict | None:
    last = _last_step_of_flow(graph)
    if last is None or last.gives is None:
        return None
    return type_to_json_schema(last.gives.type)
```

In `_emit_server_module`, include `outputSchema=` in the Tool entry when present:

```python
    output_schema = _output_schema_for_flow(graph)
    output_field = f"            outputSchema={output_schema!r},\n" if output_schema else ""
    tool_entry = (
        f"        Tool(\n"
        f"            name={flow_name!r},\n"
        f'            description="Auto-generated from FLOW {flow_name}",\n'
        f"            inputSchema={schema!r},\n"
        f"{output_field}"
        f"        )"
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 24 passed.

Run: `.venv/bin/python -m pytest tests/test_emitters/test_python.py -v 2>&1 | tail -5`
Expected: green.

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`
Expected: full suite green.

If any python test fails, the helper extraction broke something. Read `git diff clio/emitters/python.py` and confirm the delegated method bodies produce byte-identical output. Roll back into a shape that matches before continuing.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_mcp_helpers.py clio/emitters/_python_helpers.py clio/emitters/python.py clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): feature parity with python target — contracts, REST, SHELL, FOR EACH, CACHE, ON_FAIL, schemas

Promotes _emit_rest_step / _emit_shell_step / _emit_contracts from
python.py to module-level helpers in _python_helpers.py; both emitters
delegate. mcp-server emits contracts.py + clio_runtime/cache.py when
needed; pyproject conditionally includes pydantic + requests.

server.py inputSchema honors literal kwargs in the FLOW as defaults
(removing them from required). outputSchema derives from the last
step's GIVES so MCP-2025-06-aware clients see the typed result.

The python target's emission is byte-identical after the refactor;
its tests stay green.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: README.md emission for the produced project

**Files:**
- Modify: `clio/emitters/_mcp_helpers.py` (add `_emit_readme`)
- Modify: `clio/emitters/mcp_server.py` (call it)
- Modify: `tests/test_emitters/test_mcp_server.py`

The emitted project ships a README that tells the user how to add it to their MCP client config (Claude Desktop, Cursor, Claude Code).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py::test_emit_writes_readme_with_mcp_client_config -v`
Expected: FAIL — README.md doesn't exist.

- [ ] **Step 3: Implement**

In `clio/emitters/_mcp_helpers.py`:

```python
def _emit_readme(pkg_name: str, graph: FlowGraph) -> str:
    flow_name = graph.flow.name if graph.flow is not None else pkg_name
    return (
        f"# {pkg_name} — CLIO MCP server\n"
        "\n"
        "Auto-generated by `clio compile --target mcp-server`.\n"
        "\n"
        "## Install\n"
        "\n"
        "```bash\n"
        "pip install -e .\n"
        "```\n"
        "\n"
        "This pulls `mcp>=1.0` (and `pydantic`/`requests` if your flow needs them).\n"
        "\n"
        "## Add to your MCP client\n"
        "\n"
        "For Claude Desktop, edit `~/Library/Application Support/Claude/claude_desktop_config.json` "
        "(macOS) or the equivalent on your platform:\n"
        "\n"
        "```json\n"
        "{\n"
        '  "mcpServers": {\n'
        f'    "{pkg_name}": {{\n'
        '      "command": "python",\n'
        f'      "args": ["-m", "{pkg_name}"]\n'
        '    }\n'
        '  }\n'
        "}\n"
        "```\n"
        "\n"
        f"Restart your client. The tool will appear as `{flow_name}`.\n"
        "\n"
        "## How it works\n"
        "\n"
        "Each `FLOW` declared in the source `.clio` is exposed as an MCP tool. Judgment "
        "steps delegate to your client's LLM via `sampling/createMessage` — no API key on "
        "the server side.\n"
    )
```

In `clio/emitters/mcp_server.py`:

```python
        from clio.emitters._mcp_helpers import _emit_readme
        (output_dir / "README.md").write_text(_emit_readme(pkg_name, graph))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v`
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_mcp_helpers.py clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): emit README with MCP client config snippet

Each compiled project ships a README that documents how to install,
how to add the server to Claude Desktop / Cursor / etc., and what
the FLOW exposes as a tool.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Project-level documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/LANGUAGE_SPEC.md`
- Modify: `docs/COMPILATION_TARGETS.md`

No code changes — pure documentation.

- [ ] **Step 1: Update README.md**

In `README.md`:

1. In the targets table (around line 47), add a row:
   ```
   | `mcp-server` | MCP server (FLOW = tool, judgment via `sampling/createMessage`)   |
   ```

2. In the Quick start fenced block, add after the `clio gen` block:
   ```bash
   # Compile to a runnable MCP server (each FLOW becomes a tool)
   python -m clio compile examples/mvp.clio --target mcp-server --output ./mcp-out
   pip install -e ./mcp-out
   # then add the server to your MCP client config — see ./mcp-out/README.md
   ```

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`'s "How to run" fenced block, add:

```bash
# Compile to MCP server target
python -m clio compile examples/mvp.clio --target mcp-server --output ./mcp-out
```

- [ ] **Step 3: Update CHANGELOG.md**

In the Unreleased "Emitters" section, add a bullet:

```markdown
- New `target: mcp-server` emitter compiles a `.clio` source into a runnable MCP (Model Context Protocol) server. Each `FLOW` becomes a tool registered with the official `mcp` Python SDK. Judgment steps delegate to the MCP client via `sampling/createMessage` — no API key on the server, no `anthropic`/`openai` SDK dep. inputSchema derives from the first step's TAKES (literal FLOW kwargs become defaults); outputSchema derives from the last step's GIVES. Steps with `invoke.protocol: anthropic|openai|bedrock|vertex` are rejected at compile time with a pointer to `--target python`. Reuses the python emitter's helpers for FOR EACH, CACHE, ON_FAIL, impl.rest, impl.shell. Emitted package ships a README with the client-config snippet.
```

Update the Tests entry's count to reflect the new total. Get it from `pytest -q | tail -3`.

- [ ] **Step 4: Update LANGUAGE_SPEC.md**

In the implementation-status table, find rows that mention `mcp_sampling` (currently `❌` everywhere). Update the `mcp-server` cell to `✅`. If the table is split per-target, find the right cell; if the table is feature-rows × target-columns, locate or add the `mcp-server` column. Inspect the current table first; align with its existing structure.

- [ ] **Step 5: Update COMPILATION_TARGETS.md**

In the "Targets at a glance" table, change the `mcp-server` row's Status from `Candidate` to `Implemented` and remove the effort column entry (or set it to `—`):

```markdown
| `mcp-server` | Implemented | MCP server, each FLOW exposed as a tool with sampling-based judgment | Native Anthropic ecosystem integration; turn a `.clio` into a structured MCP tool | — |
```

Add a new section in the body (after the `python` section) describing what the target emits — mirror the format of the existing target sections.

- [ ] **Step 6: Verify the suite still passes**

Run: `.venv/bin/python -m pytest tests/ -q | tail -3`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add README.md CLAUDE.md CHANGELOG.md docs/LANGUAGE_SPEC.md docs/COMPILATION_TARGETS.md
git commit -m "$(cat <<'EOF'
docs: mcp-server target — README, CLAUDE.md, CHANGELOG, LANGUAGE_SPEC, COMPILATION_TARGETS

Add `mcp-server` to the targets table, the Quick start, the
implementation-status grid (mcp_sampling now ✅ on this target), and
the COMPILATION_TARGETS row (Candidate → Implemented).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10 (optional, gated): Smoke runtime test

**Files:**
- Modify: `tests/test_emitters/test_mcp_server.py`

A single smoke test that compiles a fixture, installs it via `pip install -e tmp_path`, spawns `python -m <pkg>` as a subprocess, sends a `tools/list` JSON-RPC request on stdin, and verifies the response on stdout. Gated behind `CLIO_MCP_E2E=1` so the default test run doesn't require `mcp` installed.

This task is optional for v0. The unit tests in Tasks 1-8 already cover the emission shape thoroughly.

- [ ] **Step 1: Write the gated test**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
import os
import subprocess
import sys


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
```

- [ ] **Step 2: Verify the test is correctly gated**

Run: `.venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v -k e2e`
Expected: 1 skipped (env var unset).

To execute it:
```bash
CLIO_MCP_E2E=1 .venv/bin/python -m pytest tests/test_emitters/test_mcp_server.py -v -k e2e
```

This requires `mcp` installed in the venv. If it isn't, install first: `.venv/bin/python -m pip install mcp`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
test(mcp): gated smoke runtime test for tools/list handshake

Compiles a minimal fixture, pip-installs the emitted package,
spawns `python -m <pkg>` as a subprocess, sends an MCP initialize
request on stdin, asserts the JSON-RPC response on stdout. Gated
behind CLIO_MCP_E2E=1 — default test runs don't need `mcp` installed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** all spec sections map to a task — sampling-only strategy (Tasks 5 + 6), SDK use (Task 1 pyproject + Task 2 server), 1 FLOW = 1 tool (Task 2), inputSchema (Tasks 2 + 7), outputSchema (Task 7), CONTRACT mapping (Task 7), full feature scope inheriting from python target (Task 7), refusals (Task 5), README emission (Task 8), tests (Tasks 1-8 unit + Task 10 smoke), docs (Task 9).
- **Type consistency:** `MCPServerEmitter` class name, `_emit_*` private helpers, public `emit_*` shared helpers in `_python_helpers.py`, `_session` parameter for judgment threading, `_flow.run(_session=ctx.session, **arguments)` invocation pattern — consistent across tasks.
- **Placeholders:** none — every test step has actual code, every implementation step has actual emitted output. Two cross-references to the python target's existing logic (kwarg disambiguation in Task 3, literal-vs-state-ref in Task 7, cache+on_fail wrapping in Task 7) point the implementer at the canonical source rather than duplicating it inline. The intent: one codebase, one set of rules; both emitters call the same helpers once promoted.
- **The python target stays green:** Tasks 4 and 7 explicitly require running `tests/test_emitters/test_python.py` after each helper extraction and rolling back if anything diverges. The byte-identical output requirement is stated.
- **No `eval()`:** tests parse emitted dict literals with `ast.literal_eval` (safe), never `eval()` (blocked by hook).
