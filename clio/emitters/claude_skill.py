"""target: claude-skill — emits a Claude Code skill directory.

See docs/superpowers/specs/2026-05-14-target-claude-skill-design.md
for the validated design.
"""

from __future__ import annotations

import sys
from pathlib import Path

from clio.emitters._claude_skill_helpers import (
    render_bundled_cache_key_script,
    render_bundled_validate_script,
    render_exact_script,
    render_judgment_prompt,
    render_output_schema,
    render_process_flow_dot,
    render_readme,
    render_skill_md,
    render_state_example,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import FlowGraph


class ClaudeSkillEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "scripts").mkdir(exist_ok=True)
        (output_dir / "scripts" / "_validate.py").write_text(render_bundled_validate_script())
        (output_dir / "scripts" / "_cache_key.py").write_text(render_bundled_cache_key_script())
        warn = lambda m: print(m, file=sys.stderr)  # noqa: E731
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
        (output_dir / "SKILL.md").write_text(render_skill_md(graph, warn=warn))
        (output_dir / "process_flow.dot").write_text(render_process_flow_dot(graph))
        (output_dir / "state.example.json").write_text(render_state_example(graph))
        (output_dir / "README.md").write_text(render_readme(graph))
