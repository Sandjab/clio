"""target: langgraph — compiles a .clio source to a runnable Python package
whose flow.py builds a LangGraph StateGraph instead of a custom orchestrator.

Step files are reused verbatim from the python target; the langgraph layer
adds a node-wrapper layer (`<step>_node(state) -> dict`) inside flow.py.

Scope (v0):
- Linear FLOW only.
- judgment.api.anthropic only (default invoke).
- exact.{code, shell, rest}.
- CONTRACT (Pydantic), CACHE (runtime/cache), retry(N) via RetryPolicy, abort.

Rejected (v0):
- FOR EACH (any) — planned via Send API in v0.7.
- invoke.cli — LangGraph runs server-side; use --target claude-cli or invoke.api.
- invoke.api.openai/bedrock/vertex — v0 supports anthropic only.
- ON_FAIL escalate / fallback — v0 supports retry+abort only.
"""
from __future__ import annotations

from pathlib import Path

from clio.emitters._langgraph_helpers import (
    emit_flow_module,
    emit_main_module,
    emit_pyproject,
)
from clio.emitters._python_helpers import (
    emit_contracts,
    emit_default_exact_step,
    emit_rest_step,
    emit_shell_step,
)
from clio.emitters._shared_utils import _safe_package_name, _to_field_name
from clio.emitters.base import BaseEmitter
from clio.emitters.python import PythonEmitter
from clio.ir.graph import (
    ApiInvokeIR,
    CliInvokeIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
    WhileBlockIR,
)

_LANGGRAPH_LOGGING_STUB = '''"""No-op stub of clio_runtime.logging for the langgraph target.

The langgraph emitter reuses step bodies from the python emitter, which call
_log.emit() / _log.set_flow() for structured JSON-Line observability. On
target: langgraph, observability is delegated to LangSmith (see
docs/POSITIONING.md, "LangGraph — shipped (bridge target, delegated
observability)" section), so these calls are intentionally no-ops here.

If you need step-level JSON-Line events, compile to --target python or
--target mcp-server, where the full clio_runtime.logging module ships and
CLIO_LOG=1 activates emission.
"""
from __future__ import annotations


def set_flow(name: str | None) -> None:
    """No-op: see module docstring."""
    return None


def emit(event: str, **fields) -> None:
    """No-op: see module docstring."""
    return None
'''


class LangGraphEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        self._validate_for_langgraph(graph)
        output_dir.mkdir(parents=True, exist_ok=True)

        pkg_name = _safe_package_name(graph, default="clio_langgraph")
        pkg_dir = output_dir / pkg_name
        steps_dir = pkg_dir / "steps"
        runtime_dir = pkg_dir / "clio_runtime"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        steps_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (steps_dir / "__init__.py").write_text("")
        (runtime_dir / "__init__.py").write_text("")
        (pkg_dir / "__init__.py").write_text("")

        # Capabilities to drive pyproject deps
        needs_pydantic = bool(graph.contracts)
        needs_requests = any(isinstance(s.impl, RestImplIR) for s in graph.steps)
        needs_anthropic = any(
            s.mode == "judgment" and not isinstance(s.invoke, CliInvokeIR)
            for s in graph.steps
        )

        (output_dir / "pyproject.toml").write_text(
            emit_pyproject(
                pkg_name,
                needs_anthropic=needs_anthropic,
                needs_pydantic=needs_pydantic,
                needs_requests=needs_requests,
            )
        )
        (output_dir / "README.md").write_text(self._emit_readme(pkg_name, graph))

        contracts_by_name = {c.name: c for c in graph.contracts}
        (pkg_dir / "contracts.py").write_text(emit_contracts(graph))
        (pkg_dir / "flow.py").write_text(emit_flow_module(graph, contracts_by_name))
        (pkg_dir / "__main__.py").write_text(emit_main_module(pkg_name, graph))

        # Logging: ship a no-op stub. The langgraph target delegates
        # observability to LangSmith (see docs/POSITIONING.md, "LangGraph —
        # shipped (bridge target, delegated observability)" section). Step
        # bodies reused from the python emitter still call _log.emit() and
        # _log.set_flow(); the stub keeps the import surface so they don't
        # crash. To get JSON-Line events, compile to python or mcp-server.
        (runtime_dir / "logging.py").write_text(_LANGGRAPH_LOGGING_STUB)

        # Cache: reused verbatim from the python target when caching is active.
        cache_active = any(
            s.cache is not None and s.cache.mode in ("on", "ttl")
            for s in graph.steps
        )
        if cache_active:
            from clio import runtime as src_pkg
            src_dir = Path(src_pkg.__file__).parent
            (runtime_dir / "cache.py").write_text((src_dir / "cache.py").read_text())

        # Reuse step bodies from the python target — same signature
        # `def name(*, kw...) -> ret`. The langgraph node wrapper in flow.py
        # bridges from state-dict to keyword-arg semantics.
        py_emitter = PythonEmitter()
        for step in graph.steps:
            if step.mode == "judgment":
                body = py_emitter._emit_judgment_step(step, graph, contracts_by_name)
            elif isinstance(step.impl, RestImplIR):
                body = emit_rest_step(step, contracts_by_name, step.impl)
            elif isinstance(step.impl, ShellImplIR):
                body = emit_shell_step(step, contracts_by_name, step.impl)
            else:
                body = emit_default_exact_step(step, contracts_by_name)
            (steps_dir / f"{_to_field_name(step.name)}.py").write_text(body)

    def _validate_for_langgraph(self, graph: FlowGraph) -> None:
        """Reject scopes the v0 LangGraph emitter does not yet support."""
        if graph.flow is None:
            raise ValueError(
                "langgraph target requires at least one FLOW (the FLOW becomes the StateGraph)"
            )

        # FOR EACH — any kind, sequential or PARALLEL — still rejected.
        # IfBlockIR is now supported (mono-step branches; ELSE required) but
        # we surface the constraints with friendly errors before reaching the
        # emit_flow_module walker.
        def _reject_unsupported(chain) -> None:
            for elem in chain:
                if isinstance(elem, ForEachIR):
                    kind = "PARALLEL" if elem.parallel else "sequential"
                    raise ValueError(
                        f"FOR EACH ({kind}) is not yet supported by the langgraph target in v0; "
                        "use --target python for FOR EACH today (LangGraph Send-API support "
                        "is planned for v0.7)"
                    )
                if isinstance(elem, WhileBlockIR):
                    raise ValueError(
                        f"WHILE is not supported by the langgraph target in v0.7 "
                        f"(line {elem.line}); a bounded loop in LangGraph requires "
                        "cyclic edges + state reducers (planned for v0.8). Use "
                        "--target python or --target mcp-server today."
                    )
                if isinstance(elem, IfBlockIR):
                    _reject_unsupported(elem.then_body)
                    _reject_unsupported(elem.else_body)
                if isinstance(elem, MatchBlockIR):
                    for arm in elem.cases:
                        _reject_unsupported(arm.body)

        _reject_unsupported(graph.flow.chain)

        if graph.flow.rescues:
            rb = graph.flow.rescues[0]
            raise ValueError(
                f"RESCUE handlers are not supported by the langgraph target in v0.8 "
                f"(needs cyclic edges + state reducer; planned for the multi-step "
                f"branches sprint). Use --target python or --target mcp-server. "
                f"Rescue at line {rb.line}."
            )

        for step in graph.steps:
            if isinstance(step.impl, SqlImplIR):
                raise ValueError(
                    f"step {step.name!r}: impl.mode: sql is not supported by "
                    "the langgraph target in v0.11; use --target python or "
                    "--target mcp-server"
                )
            if isinstance(step.invoke, CliInvokeIR):
                raise ValueError(
                    f"step {step.name!r}: invoke.mode: cli is not supported by the "
                    "langgraph target (LangGraph runs server-side); use --target "
                    "claude-cli for CLI invocation, or switch to invoke.mode: api"
                )
            if isinstance(step.invoke, ApiInvokeIR):
                if step.invoke.protocol != "anthropic":
                    raise ValueError(
                        f"step {step.name!r}: invoke.protocol: "
                        f"{step.invoke.protocol!r} is not supported by the langgraph "
                        "target in v0 (only 'anthropic' is wired); use --target python "
                        "for openai-compat / bedrock / vertex"
                    )
            if step.on_fail is not None:
                for s in step.on_fail.strategies:
                    if s.kind in ("escalate", "fallback"):
                        raise ValueError(
                            f"step {step.name!r}: ON_FAIL {s.kind!r} is not supported "
                            "by the langgraph target in v0 (only `retry(N)` and "
                            "`abort(...)` are wired); use --target python for "
                            "escalate/fallback chains"
                        )

    def _emit_readme(self, pkg_name: str, graph: FlowGraph) -> str:
        return (
            f"# {pkg_name}\n\n"
            f"Compiled CLIO flow for `{pkg_name}` — LangGraph target.\n\n"
            "## Run\n\n"
            "```bash\n"
            "uv pip install .\n"
            f"ANTHROPIC_API_KEY=sk-... {pkg_name} --kwargs '{{\"file\": \"input.txt\"}}'\n"
            "cat state.json\n"
            "```\n\n"
            "The flow is exposed as a `langgraph.graph.StateGraph` in `flow.py`. Import\n"
            "and customise it programmatically:\n\n"
            "```python\n"
            f"from {pkg_name}.flow import build_graph, run\n\n"
            "app = build_graph()                   # the compiled StateGraph\n"
            'state = app.invoke({"file": "input.txt"})\n'
            "# or just:\n"
            'state = run(file="input.txt")\n'
            "```\n"
        )
