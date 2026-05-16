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


def test_file_stem_sanitizes_dots(tmp_path: Path) -> None:
    """Internal symbols from a file named like 'my.lib.clio' must produce
    valid Python identifiers (dots replaced by underscores)."""
    (tmp_path / "my.lib.clio").write_text(
        "EXPOSE CONTRACT Article\n"
        "  SHAPE: {title: str}\n"
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
    (tmp_path / "main.clio").write_text(
        'FROM "./my.lib.clio" IMPORT Article, classify\n'
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
    parsed = resolve_imports(tmp_path / "main.clio")
    graph = build_ir(parsed, entry=(tmp_path / "main.clio").resolve())
    step_names = {s.name for s in graph.steps}
    # Internal STEP from my.lib.clio must be alpha-renamed with dots → underscores
    assert any(name.startswith("my_lib__") for name in step_names)
    # No dot must appear in any step name
    assert all("." not in name for name in step_names)


def test_build_unique_stems_cross_directory_collision(tmp_path: Path) -> None:
    """Two files sharing the same basename in different directories must produce
    distinct alpha-rename prefixes so their internal symbols don't collide.

    lib/utils.clio  → prefix 'lib_utils'  → step 'score' becomes 'lib_utils__score'
    core/utils.clio → prefix 'core_utils' → step 'score' becomes 'core_utils__score'

    Each library is imported by a separate entry-level wrapper flow so
    both scores appear in the merged FlowGraph simultaneously.
    """
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    core_dir = tmp_path / "core"
    core_dir.mkdir()

    (lib_dir / "utils.clio").write_text(
        "EXPOSE CONTRACT LibResult\n"
        "  SHAPE: {value: str}\n"
        "\n"
        "STEP score\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: value: str\n"
        "\n"
        "EXPOSE FLOW lib_classify\n"
        "  TAKES: text: str\n"
        "  GIVES: value: str\n"
        "  score(text=text)\n"
    )

    (core_dir / "utils.clio").write_text(
        "EXPOSE CONTRACT CoreResult\n"
        "  SHAPE: {out: str}\n"
        "\n"
        "STEP score\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "\n"
        "EXPOSE FLOW core_classify\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "  score(text=text)\n"
    )

    entry = tmp_path / "main.clio"
    entry.write_text(
        'FROM "./lib/utils.clio" IMPORT LibResult, lib_classify\n'
        'FROM "./core/utils.clio" IMPORT CoreResult, core_classify\n'
        "\n"
        "STEP run\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: value: str\n"
        "\n"
        "EXPOSE FLOW pipeline\n"
        "  TAKES: text: str\n"
        "  GIVES: value: str\n"
        "  lib_classify(text=text)\n"
    )

    parsed = resolve_imports(entry)
    graph = build_ir(parsed, entry=entry.resolve())
    step_names = {s.name for s in graph.steps}

    # Both 'score' steps must be renamed with distinct prefixes.
    assert "score" not in step_names, "raw 'score' should not appear — must be renamed"
    # lib/utils.clio → 'lib_utils__score', core/utils.clio → 'core_utils__score'
    assert "lib_utils__score" in step_names, f"expected 'lib_utils__score' in {step_names}"
    assert "core_utils__score" in step_names, f"expected 'core_utils__score' in {step_names}"


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


def test_reexported_flow_appears_in_exposed_flow_names(tmp_path: Path) -> None:
    """Regression for #47: an entry file that re-exports an imported FLOW
    via `EXPOSE <imported_name>` must include that FLOW in
    `exposed_flow_names`, so target: mcp-server can register it as a tool.

    Previously, `_flatten_to_program` forced `exposed=False` on every
    non-entry-file FlowDecl (because imported FLOWs keep their original
    name and are not in the entry-file rename table). The ReexportDecl
    in the entry file did not re-flip that flag, so the re-exported
    FLOW was silently absent from the public surface.
    """
    lib = tmp_path / "lib.clio"
    lib.write_text(
        "STEP process\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: result: str\n"
        "\n"
        "EXPOSE FLOW analyze\n"
        "  TAKES: text: str\n"
        "  GIVES: result: str\n"
        "  process(text=text)\n"
    )
    entry = tmp_path / "entry.clio"
    entry.write_text(
        "RESOURCES\n"
        "  target: mcp-server\n"
        "\n"
        'FROM "./lib.clio" IMPORT analyze\n'
        "\n"
        "EXPOSE analyze\n"
    )
    parsed = resolve_imports(entry)
    # Must NOT raise E_MCP_001 — the re-exported FLOW is the public surface.
    graph = build_ir(parsed, entry=entry.resolve())
    assert graph.exposed_flow_names == frozenset({"analyze"})


def test_test_block_resolves_imported_flow_alias(tmp_path: Path) -> None:
    """Regression for #49: a TEST block referencing an imported FLOW by its
    AS alias must resolve through imported_scope. Previously,
    _rename_test_decl only consulted local_renames, so the alias survived
    into _build_tests as an unknown flow name."""
    lib = tmp_path / "lib.clio"
    lib.write_text(
        "STEP process\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: result: str\n"
        "\n"
        "EXPOSE FLOW analyze\n"
        "  TAKES: text: str\n"
        "  GIVES: result: str\n"
        "  process(text=text)\n"
    )
    entry = tmp_path / "entry.clio"
    entry.write_text(
        'FROM "./lib.clio" IMPORT analyze AS myanalyze\n'
        "\n"
        "TEST smoke:\n"
        "  FLOW: myanalyze\n"
        "  WITH:\n"
        '    text: "hello"\n'
        "  EXPECTS:\n"
        "    result: not_empty\n"
    )
    parsed = resolve_imports(entry)
    # Must not raise IRBuildError 'unknown flow myanalyze' — the alias
    # has to be resolved through imported_scope to its target 'analyze'.
    graph = build_ir(parsed, entry=entry.resolve())
    assert len(graph.tests) == 1
    assert graph.tests[0].flow_name == "analyze"


def test_reexported_flow_with_alias_appears_in_exposed_flow_names(
    tmp_path: Path,
) -> None:
    """Regression for #47 (alias variant): `EXPOSE <alias>` must mark the
    underlying imported FLOW as exposed under its post-rename target name
    (the original exposed name, since exposed symbols keep their form)."""
    lib = tmp_path / "lib.clio"
    lib.write_text(
        "STEP process\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: result: str\n"
        "\n"
        "EXPOSE FLOW analyze\n"
        "  TAKES: text: str\n"
        "  GIVES: result: str\n"
        "  process(text=text)\n"
    )
    entry = tmp_path / "entry.clio"
    entry.write_text(
        "RESOURCES\n"
        "  target: mcp-server\n"
        "\n"
        'FROM "./lib.clio" IMPORT analyze AS myanalyze\n'
        "\n"
        "EXPOSE myanalyze\n"
    )
    parsed = resolve_imports(entry)
    graph = build_ir(parsed, entry=entry.resolve())
    # The re-export targets the FLOW by its original exposed name.
    assert graph.exposed_flow_names == frozenset({"analyze"})
