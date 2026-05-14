"""target: claude-skill — emits a Claude Code skill directory.

See docs/superpowers/specs/2026-05-14-target-claude-skill-design.md
for the validated design.
"""

from __future__ import annotations

import sys
from pathlib import Path

from clio.emitters._claude_skill_helpers import render_skill_md
from clio.emitters.base import BaseEmitter
from clio.ir.graph import FlowGraph


class ClaudeSkillEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        warn = lambda m: print(m, file=sys.stderr)  # noqa: E731
        (output_dir / "SKILL.md").write_text(render_skill_md(graph, warn=warn))
