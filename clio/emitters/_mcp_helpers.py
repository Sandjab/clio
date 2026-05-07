"""Helpers for the mcp-server emitter. Module-level functions only — emitters
import from here, never from each other."""
from __future__ import annotations

from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import CallIR, FlowGraph, ForEachIR, StepIR


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


def _emit_flow_module_async(graph: FlowGraph) -> str:
    """Emit flow.py: an async run() that chains steps in flow order."""
    if graph.flow is None:
        return (
            '"""Async FLOW orchestrator. Auto-generated; do not edit."""\n'
            "from __future__ import annotations\n"
            "\n"
            "\n"
            "async def run(*, _session=None, **initial: object) -> dict:\n"
            "    return dict(initial)\n"
        )

    chain_lines: list[str] = []
    imported_steps: list[str] = []

    def _emit_call(call: CallIR, indent: str, scope_local: set[str]) -> None:
        step = next(s for s in graph.steps if s.name == call.step_name)
        if step.name not in imported_steps:
            imported_steps.append(step.name)
        kw_parts = []
        for name, value in call.kwargs:
            if isinstance(value, str) and value.startswith("@"):
                ref = value[1:]
                if ref in scope_local:
                    kw_parts.append(f"{name}={ref}")
                else:
                    kw_parts.append(f"{name}=state[{ref!r}]")
            else:
                kw_parts.append(f"{name}={value!r}")
        kwargs_str = ", ".join(kw_parts)
        out_name = step.gives.name if step.gives is not None else "_result"
        if scope_local:
            chain_lines.append(f"{indent}{step.name}_mod.{step.name}({kwargs_str})")
        else:
            chain_lines.append(
                f"{indent}state[{out_name!r}] = {step.name}_mod.{step.name}({kwargs_str})"
            )

    def _emit_item(item, indent: str, scope_local: set[str]) -> None:
        if isinstance(item, ForEachIR):
            source = (
                item.collection
                if item.collection in scope_local
                else f"state[{item.collection!r}]"
            )
            chain_lines.append(f"{indent}for {item.loop_var} in {source}:")
            inner_scope = scope_local | {item.loop_var}
            inner_indent = indent + "    "
            if not item.body:
                chain_lines.append(f"{inner_indent}pass")
            for sub in item.body:
                _emit_item(sub, inner_indent, inner_scope)
            return
        if isinstance(item, CallIR):
            _emit_call(item, indent, scope_local)
            return
        raise ValueError(f"unknown flow item: {type(item).__name__}")

    for item in graph.flow.chain:
        _emit_item(item, "    ", set())

    imports = "\n".join(f"from .steps import {n} as {n}_mod" for n in imported_steps)

    return (
        '"""Async FLOW orchestrator. Auto-generated; do not edit."""\n'
        "from __future__ import annotations\n"
        "\n"
        f"{imports}\n"
        "\n"
        "\n"
        "async def run(*, _session=None, **initial: object) -> dict:\n"
        "    state: dict = dict(initial)\n"
        + "\n".join(chain_lines)
        + "\n    return state\n"
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
