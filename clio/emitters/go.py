"""Emitter for `target: go`.

Produces a runnable Go module (Anthropic SDK Go + jsonschema/v6 + errgroup)
from a target-independent IR. Embeds Go runtime templates (cache, validate)
under the emitted package's `clio_runtime/`.

Module-level helpers live in `_go_helpers.py`; this file holds only
the GoEmitter class.

Scope (v0.23): exact + judgment with Anthropic SDK, CACHE, control flow
(IF/MATCH/WHILE/FOR EACH + PARALLEL), RESCUE, ON_FAIL chain, impl.mode
{rest,shell}, and FLOW composition (sub-flow → run<Name>() funcs). Refuses at
compile time: OpenAI judgment, impl.mode {sql,mcp_tool} (deferred to v0.24),
RESUME-shape declarations, TEST blocks, and a multi-GIVES sub-flow used as a
FOR EACH PARALLEL body. See E_GO_001..012 in
docs/manual/06-troubleshooting.md.
"""
from __future__ import annotations

from pathlib import Path

from clio.emitters._go_flow_renderer import render_flow_go
from clio.emitters._go_helpers import (
    _flow_uses_cache,
    _flow_uses_rest,
    _flow_uses_substitute,
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
from clio.ir.graph import (
    CallIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    RestImplIR,
    ShellImplIR,
    StepIR,
    WhileBlockIR,
)


def _collect_reachable_steps(graph: FlowGraph) -> list[StepIR]:
    """Return every StepIR reachable from any flow in the graph.

    Walks each flow's chain plus all nested control-flow bodies
    (IF / MATCH / WHILE / FOR EACH) and RESCUE handlers, resolving each
    CallIR.step_name against the graph's steps. Dedups by step name,
    preserving stable first-seen order so steps/NN_<name>.go numbering is
    deterministic. FlowCallIR nodes are skipped — they reference a flow,
    not a step (sub-flow step bodies are reached via their own flow's
    chain, which this walk covers because it iterates graph.flows).

    Replaces the prior top-level-only loop (go.py:74-96) that walked only
    graph.flow.chain and only top-level CallIR, so steps nested in a
    control-flow body or a rescue — or in a sub-flow — got no stub file.
    """
    steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}
    seen: set[str] = set()
    ordered: list[StepIR] = []

    def visit_chain(items: tuple) -> None:  # type: ignore[type-arg]
        for it in items:
            if isinstance(it, CallIR):
                if it.step_name in seen:
                    continue
                step = steps_by_name.get(it.step_name)
                if step is None:
                    continue
                seen.add(it.step_name)
                ordered.append(step)
            elif isinstance(it, IfBlockIR):
                visit_chain(it.then_body)
                visit_chain(it.else_body)
            elif isinstance(it, MatchBlockIR):
                for case in it.cases:
                    visit_chain(case.body)
            elif isinstance(it, WhileBlockIR):
                visit_chain(it.body)
            elif isinstance(it, ForEachIR):
                visit_chain(it.body)
            # FlowCallIR and any other node type: skip (no step to collect).

    for fl in graph.flows:
        visit_chain(fl.chain)
        for rescue in fl.rescues:
            visit_chain(rescue.body)

    return ordered


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
        # substitute.go is shared by REST (rest.go imports it) and shell (the
        # step body calls substitute.Apply); write it once when either is present.
        if _flow_uses_substitute(graph):
            runtime_subst_dir = output_dir / "clio_runtime" / "substitute"
            runtime_subst_dir.mkdir(parents=True, exist_ok=True)
            (runtime_subst_dir / "substitute.go").write_text(render_clio_runtime_substitute())

        # Emit step stubs under steps/NN_<name>.go (exact and judgment).
        # _collect_reachable_steps walks every flow's chain, nested control-
        # flow bodies, and rescues, so a step reachable only through a
        # FOR EACH / IF / MATCH / WHILE body or a RESCUE handler (or a
        # sub-flow) still gets its file. Use the collector's first-seen
        # order so numbering is stable; skip control-flow-only steps that
        # aren't exact/judgment.
        contracts_by_name = {c.name: c for c in graph.contracts}
        steps_dir: Path | None = None
        step_idx = 0
        for step in _collect_reachable_steps(graph):
            if step.mode not in ("exact", "judgment"):
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
