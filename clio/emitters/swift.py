"""target: swift — emits a SwiftPM project (Go v0.23 parity, zero-dep)."""
from __future__ import annotations

from pathlib import Path

from clio.emitters._swift_flow_renderer import render_flow_swift
from clio.emitters._swift_helpers import (
    _flow_uses_cache,
    _flow_uses_judgment,
    _swift_module_name,
    render_contracts_swift,
    render_main_swift,
    render_package_swift,
    validate_graph_for_swift,
)
from clio.emitters._swift_runtime_templates import (
    render_runtime_anthropic_swift,
    render_runtime_cache_swift,
    render_runtime_sha256_swift,
    render_runtime_validate_swift,
)
from clio.emitters._swift_step_renderers import (
    render_exact_step_swift,
    render_judgment_step_swift,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import (
    CallIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    StepIR,
    WhileBlockIR,
)


def _collect_reachable_steps(graph: FlowGraph) -> list[StepIR]:
    """Return every StepIR reachable from any flow in the graph.

    Walks each flow's chain plus all nested control-flow bodies and RESCUE
    handlers.  Dedups by step name, preserving first-seen order so step
    file numbering is deterministic.  FlowCallIR nodes are skipped (they
    reference a flow, not a step).
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
            # FlowCallIR and other node types: skip.

    for fl in graph.flows:
        visit_chain(fl.chain)
        for rescue in fl.rescues:
            visit_chain(rescue.body)

    return ordered


class SwiftEmitter(BaseEmitter):
    def emit(
        self,
        graph: FlowGraph,
        output_dir: Path,
        *,
        source_path: Path | None = None,
        sources: tuple[Path, ...] | None = None,
    ) -> None:
        validate_graph_for_swift(graph)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "Package.swift").write_text(render_package_swift(graph))

        exe = _swift_module_name(graph)
        contracts_by_name = {c.name: c for c in graph.contracts}

        # Sources/<exe>/Main.swift
        exe_dir = output_dir / "Sources" / exe
        exe_dir.mkdir(parents=True, exist_ok=True)
        (exe_dir / "Main.swift").write_text(render_main_swift(graph))

        # Collect reachable steps and assign stable 1-based indices.
        reachable = _collect_reachable_steps(graph)
        step_to_idx = {step.name: i + 1 for i, step in enumerate(reachable)}

        # Sources/ClioFlow/Steps/StepNN_<name>.swift
        # Phase 1: exact steps only; Phase 2: judgment steps (Anthropic) added.
        steps_dir: Path | None = None
        for step in reachable:
            if steps_dir is None:
                steps_dir = output_dir / "Sources" / "ClioFlow" / "Steps"
                steps_dir.mkdir(parents=True, exist_ok=True)
            idx = step_to_idx[step.name]
            filename = f"Step{idx:02d}_{step.name}.swift"
            if step.mode == "exact":
                src = render_exact_step_swift(step, contracts_by_name, idx)
            else:
                # judgment (anthropic) — only supported mode after validate_graph_for_swift
                src = render_judgment_step_swift(step, contracts_by_name, graph, idx)
            (steps_dir / filename).write_text(src)

        # Sources/ClioFlow/Flow.swift
        clio_flow_dir = output_dir / "Sources" / "ClioFlow"
        clio_flow_dir.mkdir(parents=True, exist_ok=True)
        (clio_flow_dir / "Flow.swift").write_text(
            render_flow_swift(graph, step_to_idx)
        )

        # Sources/ClioFlow/Contracts.swift + Sources/ClioFlow/Runtime/Validate.swift
        contracts_src = render_contracts_swift(graph)
        if contracts_src is not None:
            (clio_flow_dir / "Contracts.swift").write_text(contracts_src)
            runtime_dir = clio_flow_dir / "Runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "Validate.swift").write_text(render_runtime_validate_swift())

        # Sources/ClioFlow/Runtime/Anthropic.swift (emitted when ≥1 judgment step)
        if _flow_uses_judgment(graph):
            runtime_dir = clio_flow_dir / "Runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "Anthropic.swift").write_text(render_runtime_anthropic_swift())

        # Sources/ClioFlow/Runtime/SHA256.swift + Cache.swift (emitted when ≥1 cached step)
        if _flow_uses_cache(graph):
            runtime_dir = clio_flow_dir / "Runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "SHA256.swift").write_text(render_runtime_sha256_swift())
            (runtime_dir / "Cache.swift").write_text(render_runtime_cache_swift())
