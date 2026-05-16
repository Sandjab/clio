"""mcp-server emitter — multifile (v0.18) tests.

Verifies that:
1. A 2-file project compiles to a server where only the entry-file's
   EXPOSE FLOWs become public MCP tools. Imported FLOWs are present as
   async functions in flow.py but do NOT appear in the tool registry.
2. A single-file mcp-server source with no EXPOSE FLOW raises E_MCP_001
   (CompileError) during build_ir.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clio.emitters.mcp_server import MCPServerEmitter
from clio.ir.builder import build_ir
from clio.ir.resolver import CompileError, resolve_imports

FIXTURES = Path(__file__).parent.parent / "fixtures" / "imports"


def test_mcp_multifile_emits_tool_per_exposed_flow(tmp_path: Path) -> None:
    """A 2-file project: only the entry-file EXPOSE FLOW 'pipeline' becomes
    a Tool entry in server.py. The imported 'classify' flow is an async
    function in flow.py but is NOT listed as a tool."""
    lib = tmp_path / "lib.clio"
    lib.write_text(
        "EXPOSE CONTRACT Article\n"
        "  SHAPE: {title: str, body: str}\n"
        "\n"
        "STEP score\n"
        "  MODE: judgment\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "\n"
        "EXPOSE FLOW classify\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "  score(article=article)\n"
    )
    main = tmp_path / "main.clio"
    main.write_text(
        "RESOURCES\n"
        "  target: mcp-server\n"
        "\n"
        'FROM "./lib.clio" IMPORT Article, classify\n'
        "\n"
        "STEP run_pipeline\n"
        "  MODE: judgment\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "\n"
        "EXPOSE FLOW pipeline\n"
        "  TAKES: article: Article\n"
        "  GIVES: label: str\n"
        "  classify(article=article)\n"
    )
    parsed = resolve_imports(main)
    graph = build_ir(parsed, entry=main.resolve())

    # Only the entry-file EXPOSE FLOW is in exposed_flow_names.
    assert graph.exposed_flow_names == frozenset({"pipeline"})

    out = tmp_path / "server"
    MCPServerEmitter().emit(graph, out)

    # server.py exists and registers exactly one tool.
    server_py = (out / "pipeline" / "server.py").read_text()
    assert "name='pipeline'" in server_py
    # 'classify' is NOT a registered tool.
    assert "name='classify'" not in server_py

    # flow.py contains async functions for both flows.
    flow_py = (out / "pipeline" / "flow.py").read_text()
    assert "async def pipeline(" in flow_py
    assert "async def classify(" in flow_py


def test_mcp_rejects_no_expose(tmp_path: Path) -> None:
    """target: mcp-server with no EXPOSE FLOW in the entry file raises
    CompileError (E_MCP_001) during build_ir, before the emitter runs."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        "RESOURCES\n"
        "  target: mcp-server\n"
        "\n"
        "STEP step1\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "  step1(text=text)\n"
    )
    parsed = resolve_imports(entry)
    with pytest.raises(CompileError, match="requires at least one EXPOSE FLOW"):
        build_ir(parsed, entry=entry.resolve())
