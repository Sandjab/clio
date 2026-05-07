"""target: mcp-server — compiles a .clio source to a runnable MCP server.

Each FLOW is exposed as a tool registered with the MCP Python SDK. Judgment
steps delegate to the MCP client via sampling/createMessage (no API key on
the server side, no anthropic/openai dep)."""
from __future__ import annotations

from pathlib import Path

from clio.emitters._mcp_helpers import (
    _emit_flow_module_async,
    _emit_main_module,
    _emit_server_module,
    _pyproject_for_mcp,
    emit_judgment_step_via_sampling,
)
from clio.emitters._python_helpers import emit_default_exact_step
from clio.emitters.base import BaseEmitter
from clio.ir.graph import ApiInvokeIR, CliInvokeIR, FlowGraph


class MCPServerEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        self._validate_for_mcp(graph)
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

        contracts_by_name = {c.name: c for c in graph.contracts}
        for step in graph.steps:
            if step.mode == "exact":
                body = emit_default_exact_step(step, contracts_by_name)
            else:
                body = emit_judgment_step_via_sampling(step, graph, contracts_by_name)
            (steps_dir / f"{step.name}.py").write_text(body)

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
