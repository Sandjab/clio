"""target: claude-skill — emits a Claude Code skill directory.

See docs/superpowers/specs/2026-05-14-target-claude-skill-design.md
for the validated design.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

from clio.emitters._claude_skill_helpers import (
    render_bundled_cache_key_script,
    render_bundled_validate_script,
    render_exact_script,
    render_input_schema,
    render_judgment_prompt,
    render_output_schema,
    render_process_flow_dot,
    render_readme,
    render_skill_md,
    render_state_example,
    render_sub_flow_script,
)
from clio.emitters._sidecar import write_sidecar
from clio.emitters.base import BaseEmitter
from clio.ir.graph import (
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    WhileBlockIR,
)


class ClaudeSkillEmitter(BaseEmitter):
    SUPPORTED_EXACT_LANGUAGES: ClassVar[set[str]] = {"python", "bash"}

    def _validate(self, graph: FlowGraph, warn) -> None:
        """Compile-time checks that surface user-facing errors/warnings."""
        # 1. Unsupported exact languages — hard error with source line.
        for step in graph.steps:
            if step.mode == "exact":
                lang = step.lang or "python"
                if lang not in self.SUPPORTED_EXACT_LANGUAGES:
                    raise ValueError(
                        f"claude-skill v1 supports python and bash for exact steps; "
                        f"got '{lang}' at line {step.line}"
                    )
        # 2. Parallel construct — warning, emitter still produces serialized output.
        flow = graph.flow
        if flow is not None:
            for item in flow.chain:
                if isinstance(item, ForEachIR) and item.parallel:
                    warn(
                        "claude-skill warning: source flow contains PARALLEL FOR EACH; "
                        "the emitted skill serializes iterations "
                        "(the LLM host does not execute concurrently)."
                    )
                    break
        # 3. v0.17 — sub-flow orchestrator only supports linear chains. Reject
        # any control structure (IF / FOR EACH / MATCH / WHILE) inside a signed
        # sub-flow so users get a clear compile-time error rather than a runtime
        # NotImplementedError from the emitted script.
        main_flow_name = flow.name if flow is not None else None
        for sub_flow in graph.flows:
            if sub_flow.name == main_flow_name:
                continue
            if not sub_flow.takes or not sub_flow.gives:
                continue
            for sub_item in sub_flow.chain:
                if isinstance(sub_item, (ForEachIR, IfBlockIR, MatchBlockIR, WhileBlockIR)):
                    kind = type(sub_item).__name__.removesuffix("IR").removesuffix("Block")
                    raise ValueError(
                        f"claude-skill v0.17 requires sub-flows to be linear chains "
                        f"of step / sub-flow calls; sub-flow {sub_flow.name!r} contains "
                        f"a {kind} block at line {sub_item.line}. Move the control "
                        f"structure to the main FLOW, or split the sub-flow."
                    )

    def emit(self, graph: FlowGraph, output_dir: Path, *, source_path: Path | None = None) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "scripts").mkdir(exist_ok=True)
        (output_dir / "scripts" / "_validate.py").write_text(render_bundled_validate_script())
        (output_dir / "scripts" / "_cache_key.py").write_text(render_bundled_cache_key_script())
        warn = lambda m: print(m, file=sys.stderr)  # noqa: E731
        self._validate(graph, warn)
        contracts = {c.name: c for c in (graph.contracts or ())}
        for idx, step in enumerate(graph.steps, start=1):
            if step.mode == "exact":
                script = render_exact_script(step, contracts, idx)
                (output_dir / "scripts" / f"{idx:02d}_{step.name}.py").write_text(script)
            elif step.mode == "judgment":
                (output_dir / "prompts").mkdir(exist_ok=True)
                (output_dir / "schemas").mkdir(exist_ok=True)
                (output_dir / "prompts" / f"{idx:02d}_{step.name}.md").write_text(
                    render_judgment_prompt(step)
                )
                (output_dir / "schemas" / f"{idx:02d}_{step.name}.output.json").write_text(
                    render_output_schema(step, contracts)
                )
            input_schema = render_input_schema(step, contracts)
            if input_schema is not None:
                (output_dir / "schemas").mkdir(exist_ok=True)
                (output_dir / "schemas" / f"{idx:02d}_{step.name}.input.json").write_text(
                    input_schema
                )
        # v0.17 sub-flows: emit one orchestrator script per signed FLOW other
        # than the main one. Signed = has both TAKES and GIVES. The main flow
        # is narrated by SKILL.md (the LLM host orchestrates); each sub-flow
        # is a self-contained Python script the host invokes via subprocess.
        main_flow_name = graph.flow.name if graph.flow is not None else None
        for sub_flow in graph.flows:
            if sub_flow.name == main_flow_name:
                continue
            if not sub_flow.takes or not sub_flow.gives:
                continue
            (output_dir / "scripts" / f"sub_{sub_flow.name}.py").write_text(
                render_sub_flow_script(sub_flow, graph)
            )
        (output_dir / "SKILL.md").write_text(render_skill_md(graph, warn=warn))
        (output_dir / "process_flow.dot").write_text(render_process_flow_dot(graph))
        (output_dir / "state.example.json").write_text(render_state_example(graph))
        (output_dir / "README.md").write_text(render_readme(graph))

        if source_path is not None:
            from clio import __version__ as _clio_version
            try:
                write_sidecar(source_path, output_dir, clio_version=_clio_version)
            except (OSError, FileNotFoundError) as e:
                print(
                    f"claude-skill warning: failed to write .clio/ sidecar ({e}); "
                    f"main skill emission unaffected. Future `clio import` calls "
                    f"will fall back to the LLM path.",
                    file=sys.stderr,
                )
