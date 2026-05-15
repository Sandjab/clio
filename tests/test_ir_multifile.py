"""Multi-file build_ir tests (v0.18).

Verifies the polymorphic dispatcher: build_ir accepts either a single
Program (v0.17 callers, unchanged) or a dict[Path, Program] from the
resolver (v0.18 multi-file). For the dict path, internal symbols are
alpha-renamed '{file_stem}__{name}'; exposed names keep their original
form. exposed_flow_names is derived from the explicit EXPOSE marker
on entry-file FLOWs only. target: mcp-server with no EXPOSE FLOW in
the entry file raises E_MCP_001.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clio.ir.builder import build_ir
from clio.ir.resolver import CompileError, resolve_imports
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent / "fixtures" / "imports"


def test_build_simple_multifile():
    """main.clio imports classify+Article from lib.clio; the internal
    STEP score in lib.clio is alpha-renamed to lib__score."""
    entry = FIXTURES / "simple" / "main.clio"
    parsed = resolve_imports(entry)
    graph = build_ir(parsed, entry=entry.resolve())
    flow_names = {f.name for f in graph.flows}
    # Local pipeline + imported classify (exposed names kept as-is)
    assert "pipeline" in flow_names
    assert "classify" in flow_names
    # Internal STEP from lib.clio is alpha-renamed
    step_names = {s.name for s in graph.steps}
    assert "lib__score" in step_names
    assert "score" not in step_names  # internal, renamed


def test_build_ir_backward_compat_single_program():
    """v0.17 callers: build_ir(Program) still works without `entry`."""
    src = (
        "STEP score\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: label: str\n"
        "FLOW pipeline\n"
        "  TAKES: text: str\n"
        "  GIVES: label: str\n"
        "  score(text=text)\n"
    )
    program = parse(src)
    graph = build_ir(program)
    assert graph.flow is not None
    assert graph.flow.name == "pipeline"


def test_exposed_flow_names_from_explicit_marker():
    """Only the entry file's EXPOSE FLOWs count; imported exposed FLOWs
    do not transitively re-expose."""
    entry = FIXTURES / "simple" / "main.clio"
    parsed = resolve_imports(entry)
    graph = build_ir(parsed, entry=entry.resolve())
    assert graph.exposed_flow_names == frozenset({"pipeline"})


def test_alpha_rename_in_match_scrutinee(tmp_path: Path) -> None:
    """MATCH scrutinee referencing an imported FLOW's output step should
    build without IRBuildError after alpha-renaming.

    lib.clio exposes a FLOW 'classify' whose internal STEP 'score' is
    alpha-renamed to 'lib__score' during flatten. The MATCH block in
    main.clio uses the GIVES field name 'verdict' as the state key — the
    rename_expr path on the scrutinee must not corrupt that field reference.
    """
    lib = tmp_path / "lib.clio"
    lib.write_text(
        "EXPOSE CONTRACT Verdict\n"
        "  SHAPE: {label: enum(ok|bad)}\n"
        "\n"
        "STEP score\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: verdict: Verdict\n"
        "\n"
        "EXPOSE FLOW classify\n"
        "  TAKES: text: str\n"
        "  GIVES: verdict: Verdict\n"
        "  score(text=text)\n"
    )
    main = tmp_path / "main.clio"
    main.write_text(
        'FROM "./lib.clio" IMPORT Verdict, classify\n'
        "\n"
        "STEP handle_ok\n"
        "  MODE: judgment\n"
        "  TAKES: verdict: Verdict\n"
        "  GIVES: done: str\n"
        "\n"
        "STEP handle_bad\n"
        "  MODE: judgment\n"
        "  TAKES: verdict: Verdict\n"
        "  GIVES: done: str\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: text: str\n"
        "  classify(text=text)\n"
        "  -> MATCH verdict.label:\n"
        "       CASE ok:  handle_ok(verdict=verdict)\n"
        "       DEFAULT:  handle_bad(verdict=verdict)\n"
    )
    parsed = resolve_imports(main)
    # Must not raise IRBuildError — rename_expr must leave the GIVES-field
    # state key 'verdict' intact when walking the MATCH scrutinee.
    graph = build_ir(parsed, entry=main.resolve())
    # Internal FLOW gets alpha-renamed; the imported FLOW stays exposed.
    flow_names = {f.name for f in graph.flows}
    assert "classify" in flow_names        # imported, kept as-is
    assert "main__pipeline" in flow_names  # local non-exposed, renamed


def test_e_mcp_001_mcp_target_without_expose(tmp_path: Path) -> None:
    """target: mcp-server with no EXPOSE FLOW in the entry file is
    rejected by E_MCP_001."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        "STEP step1\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "FLOW pipeline\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "  step1(text=text)\n"
        "RESOURCES\n"
        "  target: mcp-server\n"
    )
    parsed = resolve_imports(entry)
    with pytest.raises(CompileError, match=r"requires at least one EXPOSE FLOW"):
        build_ir(parsed, entry=entry.resolve())
