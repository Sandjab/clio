"""target: mcp-server — compiles a .clio source to a runnable MCP server.

Each FLOW is exposed as a tool registered with the MCP Python SDK. Judgment
steps delegate to the MCP client via sampling/createMessage (no API key on
the server side, no anthropic/openai dep)."""
from __future__ import annotations

from pathlib import Path

from clio.emitters._mcp_helpers import (
    _emit_exact_step_stub,
    _emit_flow_module_async,
    _emit_main_module,
    _emit_server_module,
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
        (steps_dir / "__init__.py").write_text("")

        (output_dir / "pyproject.toml").write_text(
            _pyproject_for_mcp(pkg_name, needs_pydantic=False, needs_requests=False)
        )
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "__main__.py").write_text(_emit_main_module(pkg_name))
        (pkg_dir / "server.py").write_text(_emit_server_module(pkg_name, graph))
        (pkg_dir / "flow.py").write_text(_emit_flow_module_async(graph))

        for step in graph.steps:
            (steps_dir / f"{step.name}.py").write_text(_emit_exact_step_stub(step.name))
