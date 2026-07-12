"""target: claude-workflow — emits a Claude Code Workflow script (JS)."""
from __future__ import annotations

from pathlib import Path

from clio.emitters._workflow_helpers import (
    render_meta,
    validate_graph_for_workflow,
    workflow_name,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import FlowGraph


class WorkflowEmitter(BaseEmitter):
    def emit(
        self,
        graph: FlowGraph,
        output_dir: Path,
        *,
        source_path: Path | None = None,
        sources: tuple[Path, ...] | None = None,
    ) -> None:
        validate_graph_for_workflow(graph)
        output_dir.mkdir(parents=True, exist_ok=True)
        name = workflow_name(graph)
        script = render_meta(graph) + "\n"
        (output_dir / f"{name}.workflow.js").write_text(script)
