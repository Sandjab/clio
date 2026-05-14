"""target: claude-skill — emits a Claude Code skill directory.

See docs/superpowers/specs/2026-05-14-target-claude-skill-design.md
for the validated design.
"""

from __future__ import annotations

import sys
from pathlib import Path

from clio.emitters._claude_skill_helpers import (
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
        warn = lambda m: print(m, file=sys.stderr)  # noqa: E731
        (output_dir / "SKILL.md").write_text(render_skill_md(graph, warn=warn))
        (output_dir / "process_flow.dot").write_text(render_process_flow_dot(graph))
        (output_dir / "state.example.json").write_text(render_state_example(graph))
        (output_dir / "README.md").write_text(render_readme(graph))
