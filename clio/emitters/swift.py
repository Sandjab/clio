"""target: swift — emits a SwiftPM project (Go v0.23 parity, zero-dep)."""
from __future__ import annotations

from pathlib import Path

from clio.emitters._swift_helpers import (
    render_package_swift,
    validate_graph_for_swift,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import FlowGraph


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
        # Sources/ tree written in a later task
