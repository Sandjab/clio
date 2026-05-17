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

from clio.emitters._go_helpers import render_go_mod
from clio.emitters.base import BaseEmitter
from clio.ir.graph import FlowGraph


class GoEmitter(BaseEmitter):
    def emit(
        self,
        graph: FlowGraph,
        output_dir: Path,
        *,
        source_path: Path | None = None,
    ) -> None:
        """Emit a Go module under `output_dir`.

        `source_path` is accepted and ignored (consistent with python,
        mcp-server, langgraph emitters)."""
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "go.mod").write_text(render_go_mod(graph))
