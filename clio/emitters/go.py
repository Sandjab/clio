"""Emitter for `target: go`.

Produces a runnable Go module (Anthropic SDK Go + jsonschema/v6 + errgroup)
from a target-independent IR. Embeds Go runtime templates (cache, validate)
under the emitted package's `clio_runtime/`.

Module-level helpers live in `_go_helpers.py`; this file holds only
the GoEmitter class.

Scope (v0.20.0): exact + judgment with Anthropic SDK, CACHE, control flow
(IF/MATCH/WHILE/FOR EACH + PARALLEL), RESCUE, ON_FAIL chain. Refuses at
compile time: OpenAI, FLOW composition, impl.mode {rest,sql,mcp_tool,shell},
RESUME-shape declarations, TEST blocks. See E_GO_001..012 in
docs/manual/06-troubleshooting.md.
"""
from __future__ import annotations

from pathlib import Path

from clio.emitters._go_flow_renderer import render_flow_go
from clio.emitters._go_helpers import (
    _flow_uses_cache,
    _flow_uses_rest,
    _go_module_name,
    render_cmd_main_go,
    render_contracts_go,
    render_go_mod,
    validate_graph_for_go,
)
from clio.emitters._go_runtime_templates import (
    render_clio_runtime_cache,
    render_clio_runtime_rest,
    render_clio_runtime_substitute,
    render_clio_runtime_validate,
)
from clio.emitters._go_step_renderers import (
    render_exact_step_go,
    render_judgment_step_go,
    render_rest_step_go,
    render_shell_step_go,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import CallIR, FlowGraph, RestImplIR, ShellImplIR, StepIR


class GoEmitter(BaseEmitter):
    def emit(
        self,
        graph: FlowGraph,
        output_dir: Path,
        *,
        source_path: Path | None = None,
        sources: tuple[Path, ...] | None = None,
    ) -> None:
        """Emit a Go module under `output_dir`.

        `source_path` is accepted and ignored (consistent with python,
        mcp-server, langgraph emitters)."""
        validate_graph_for_go(graph)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "go.mod").write_text(render_go_mod(graph))
        pkg = _go_module_name(graph)  # NOT _safe_package_name — Go needs lowercase
        cmd_dir = output_dir / "cmd" / pkg
        cmd_dir.mkdir(parents=True, exist_ok=True)
        (cmd_dir / "main.go").write_text(render_cmd_main_go(graph))
        contracts_src = render_contracts_go(graph)
        if contracts_src is not None:
            contracts_dir = output_dir / "contracts"
            contracts_dir.mkdir(parents=True, exist_ok=True)
            (contracts_dir / "contracts.go").write_text(contracts_src)
            runtime_validate_dir = output_dir / "clio_runtime" / "validate"
            runtime_validate_dir.mkdir(parents=True, exist_ok=True)
            (runtime_validate_dir / "validate.go").write_text(render_clio_runtime_validate())
        if _flow_uses_cache(graph):
            runtime_cache_dir = output_dir / "clio_runtime" / "cache"
            runtime_cache_dir.mkdir(parents=True, exist_ok=True)
            (runtime_cache_dir / "cache.go").write_text(render_clio_runtime_cache())
        if _flow_uses_rest(graph):
            runtime_rest_dir = output_dir / "clio_runtime" / "rest"
            runtime_rest_dir.mkdir(parents=True, exist_ok=True)
            (runtime_rest_dir / "rest.go").write_text(render_clio_runtime_rest(pkg))
            runtime_subst_dir = output_dir / "clio_runtime" / "substitute"
            runtime_subst_dir.mkdir(parents=True, exist_ok=True)
            (runtime_subst_dir / "substitute.go").write_text(render_clio_runtime_substitute())
        if any(
            isinstance(s, StepIR) and isinstance(s.impl, ShellImplIR)
            for s in graph.steps
        ):
            runtime_subst_dir = output_dir / "clio_runtime" / "substitute"
            runtime_subst_dir.mkdir(parents=True, exist_ok=True)
            (runtime_subst_dir / "substitute.go").write_text(render_clio_runtime_substitute())

        # Emit step stubs under steps/NN_<name>.go (exact and judgment).
        # Use step_idx (not enumerate) so control-flow elements that are
        # skipped (T12+) don't produce numbering gaps like 01_, 03_, ...
        if graph.flow is not None:
            steps_by_name = {
                s.name: s for s in graph.steps if isinstance(s, StepIR)
            }
            contracts_by_name = {c.name: c for c in graph.contracts}
            steps_dir: Path | None = None
            step_idx = 0
            for elem in graph.flow.chain:
                if not isinstance(elem, CallIR):
                    continue
                step = steps_by_name.get(elem.step_name)
                if step is None or step.mode not in ("exact", "judgment"):
                    continue
                step_idx += 1
                if steps_dir is None:
                    steps_dir = output_dir / "steps"
                    steps_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{step_idx:02d}_{step.name}.go"
                if step.mode == "exact" and isinstance(step.impl, RestImplIR):
                    src = render_rest_step_go(step, contracts_by_name, graph)
                elif step.mode == "exact" and isinstance(step.impl, ShellImplIR):
                    src = render_shell_step_go(step, contracts_by_name, graph)
                elif step.mode == "exact":
                    src = render_exact_step_go(step, contracts_by_name, graph)
                else:
                    src = render_judgment_step_go(step, graph)
                (steps_dir / filename).write_text(src)

        # Emit flow/flow.go — top-level orchestrator (unconditional).
        flow_dir = output_dir / "flow"
        flow_dir.mkdir(parents=True, exist_ok=True)
        (flow_dir / "flow.go").write_text(render_flow_go(graph))
