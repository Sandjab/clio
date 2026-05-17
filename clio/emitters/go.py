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

from clio.emitters._go_helpers import (
    _flow_uses_cache,
    _go_module_name,
    render_cmd_main_go,
    render_contracts_go,
    render_go_mod,
)
from clio.emitters._go_runtime_templates import (
    render_clio_runtime_cache,
    render_clio_runtime_validate,
)
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
