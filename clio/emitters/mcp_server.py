"""target: mcp-server — compiles a .clio source to a runnable MCP server.

Each FLOW is exposed as a tool registered with the MCP Python SDK. Judgment
steps delegate to the MCP client via sampling/createMessage (no API key on
the server side, no anthropic/openai dep)."""
from __future__ import annotations

from pathlib import Path

from clio.emitters._mcp_helpers import (
    _emit_flow_module_async,
    _emit_main_module,
    _emit_readme,
    _emit_server_module,
    _pyproject_for_mcp,
    emit_judgment_step_via_sampling,
)
from clio.emitters._python_helpers import (
    emit_contracts,
    emit_default_exact_step,
    emit_rest_step,
    emit_shell_step,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import ApiInvokeIR, CliInvokeIR, FlowGraph, RestImplIR, ShellImplIR


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

        needs_pydantic = bool(graph.contracts)
        needs_requests = any(isinstance(s.impl, RestImplIR) for s in graph.steps)
        (output_dir / "pyproject.toml").write_text(
            _pyproject_for_mcp(pkg_name, needs_pydantic=needs_pydantic, needs_requests=needs_requests)
        )
        (output_dir / "README.md").write_text(_emit_readme(pkg_name, graph))
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "__main__.py").write_text(_emit_main_module(pkg_name))
        (pkg_dir / "server.py").write_text(_emit_server_module(pkg_name, graph))
        (pkg_dir / "flow.py").write_text(_emit_flow_module_async(graph))
        (pkg_dir / "contracts.py").write_text(emit_contracts(graph))

        cache_active = any(
            s.cache is not None and s.cache.mode in ("on", "ttl")
            for s in graph.steps
        )
        if cache_active:
            from clio import runtime as src_pkg
            runtime_dir = pkg_dir / "clio_runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "__init__.py").write_text("")
            cache_src = Path(src_pkg.__file__).parent / "cache.py"
            (runtime_dir / "cache.py").write_text(cache_src.read_text())

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
