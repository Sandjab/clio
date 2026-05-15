"""target: mcp-server — compiles a .clio source to a runnable MCP server.

Each FLOW is exposed as a tool registered with the MCP Python SDK. Judgment
steps delegate to the MCP client via sampling/createMessage (no API key on
the server side, no anthropic/openai dep)."""
from __future__ import annotations

from pathlib import Path

from clio.emitters._mcp_helpers import (
    _emit_flow_module_async,
    _emit_flow_module_async_multi,
    _emit_main_module,
    _emit_readme,
    _emit_server_module,
    _emit_server_module_multi,
    _pyproject_for_mcp,
    emit_judgment_step_via_sampling,
)
from clio.emitters._python_helpers import (
    emit_contracts,
    emit_default_exact_step,
    emit_mcp_tool_step,
    emit_rest_step,
    emit_shell_step,
    emit_sql_step,
)
from clio.emitters._shared_utils import _safe_package_name
from clio.emitters.base import BaseEmitter
from clio.ir.graph import (
    ApiInvokeIR,
    CliInvokeIR,
    FlowGraph,
    McpToolImplIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
)


class MCPServerEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        self._validate_for_mcp(graph)
        output_dir.mkdir(parents=True, exist_ok=True)

        pkg_name = _safe_package_name(graph, default="clio_mcp")
        pkg_dir = output_dir / pkg_name
        steps_dir = pkg_dir / "steps"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        steps_dir.mkdir(parents=True, exist_ok=True)
        (steps_dir / "__init__.py").write_text("")

        needs_pydantic = bool(graph.contracts)
        needs_requests = any(isinstance(s.impl, RestImplIR) for s in graph.steps)
        needs_mcp = any(isinstance(s.impl, McpToolImplIR) for s in graph.steps)
        needs_sql = any(isinstance(s.impl, SqlImplIR) for s in graph.steps)
        (output_dir / "pyproject.toml").write_text(
            _pyproject_for_mcp(pkg_name, needs_pydantic=needs_pydantic, needs_requests=needs_requests)
        )
        (output_dir / "README.md").write_text(_emit_readme(pkg_name, graph))
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "__main__.py").write_text(_emit_main_module(pkg_name))
        # v0.17: multi-FLOW sources emit one tool per exposed FLOW (each as
        # its own async function in flow.py). Single-FLOW sources keep the
        # v0.16 byte-identical layout (one `def run(...)` in flow.py).
        if len(graph.flows) > 1:
            (pkg_dir / "server.py").write_text(_emit_server_module_multi(pkg_name, graph))
            (pkg_dir / "flow.py").write_text(_emit_flow_module_async_multi(graph))
        else:
            (pkg_dir / "server.py").write_text(_emit_server_module(pkg_name, graph))
            (pkg_dir / "flow.py").write_text(_emit_flow_module_async(graph))
        (pkg_dir / "contracts.py").write_text(emit_contracts(graph))

        from clio import runtime as src_pkg
        runtime_dir = pkg_dir / "clio_runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "__init__.py").write_text("")
        src_dir = Path(src_pkg.__file__).parent
        (runtime_dir / "logging.py").write_text((src_dir / "logging.py").read_text())
        cache_active = any(
            s.cache is not None and s.cache.mode in ("on", "ttl")
            for s in graph.steps
        )
        if cache_active:
            (runtime_dir / "cache.py").write_text((src_dir / "cache.py").read_text())
        if needs_requests or needs_mcp:
            (runtime_dir / "rest.py").write_text((src_dir / "rest.py").read_text())
        if needs_mcp:
            (runtime_dir / "mcp_client.py").write_text(
                (src_dir / "mcp_client.py").read_text()
            )
        if needs_sql:
            (runtime_dir / "sql.py").write_text((src_dir / "sql.py").read_text())

        contracts_by_name = {c.name: c for c in graph.contracts}
        mcp_servers_by_name = {
            s.name: s
            for s in (graph.resources.mcp_servers if graph.resources is not None else ())
        }
        databases_by_name = {
            d.name: d
            for d in (graph.resources.databases if graph.resources is not None else ())
        }
        for step in graph.steps:
            if step.mode == "judgment":
                body = emit_judgment_step_via_sampling(step, graph, contracts_by_name)
            elif isinstance(step.impl, RestImplIR):
                body = emit_rest_step(step, contracts_by_name, step.impl)
            elif isinstance(step.impl, ShellImplIR):
                body = emit_shell_step(step, contracts_by_name, step.impl)
            elif isinstance(step.impl, McpToolImplIR):
                spec = mcp_servers_by_name[step.impl.server]   # validated upstream
                body = emit_mcp_tool_step(
                    step, contracts_by_name, step.impl, spec, async_call=False,
                )
            elif isinstance(step.impl, SqlImplIR):
                db_spec = databases_by_name[step.impl.db]      # validated upstream
                body = emit_sql_step(step, contracts_by_name, step.impl, db_spec)
            else:
                body = emit_default_exact_step(step, contracts_by_name)
            (steps_dir / f"{step.name}.py").write_text(body)

    def _validate_for_mcp(self, graph: FlowGraph) -> None:
        # v0.17: with multi-FLOW sources, `graph.flow` may be None (no `--flow`
        # selected), but `graph.flows` still carries every declared FLOW. We
        # only reject when NO FLOW exists at all.
        if not graph.flows and graph.flow is None:
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
