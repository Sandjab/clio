"""Multi-file resolver for CLIO v0.18.

Phase 1 (discovery): recursive parse of all .clio files reachable from
the entry, with cycle detection. Returns dict[Path, Program] keyed by
the resolved absolute path of each file.

Subsequent phases (validation, exposed sets, import validation) are
added in later tasks.
"""
from __future__ import annotations

from pathlib import Path

from clio.parser.ast_nodes import (
    ContractDecl,
    FlowDecl,
    Program,
    ReexportDecl,
    ResourcesDecl,
    TestDecl,
)
from clio.parser.parser import parse


class CompileError(Exception):
    """Raised by the resolver for build-time errors (cycles, missing
    files, validation failures). Distinct from parser-level ParseError."""


def resolve_imports(entry: Path) -> dict[Path, Program]:
    """Recursively parse all files reachable from `entry`.

    Returns a dict keyed by the resolved absolute path of each file.
    Raises CompileError on cyclic imports or missing files.
    """
    parsed: dict[Path, Program] = {}
    stack: list[Path] = []
    _visit(entry.resolve(), parsed, stack)
    return parsed


def _visit(path: Path, parsed: dict[Path, Program], stack: list[Path]) -> None:
    if path in stack:
        chain = " → ".join(str(p) for p in stack[stack.index(path) :]) + f" → {path}"
        raise CompileError(f"cyclic import: {chain}")
    if path in parsed:
        return
    if not path.exists():
        raise CompileError(f"imported file not found: {path}")
    text = path.read_text()
    program = parse(text)
    # Thread the source_path onto the Program for downstream error messages.
    program = Program(
        decls=program.decls,
        imports=program.imports,
        source_path=path,
    )
    stack.append(path)
    try:
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            _visit(child, parsed, stack)
    finally:
        stack.pop()
    parsed[path] = program


def validate_per_file(
    parsed: dict[Path, Program],
    entry: Path | None = None,
) -> None:
    """Phase 2: per-file integrity checks.

    - EXPOSE FLOW must declare TAKES and GIVES (E_VIS_003).
    - The same name cannot be both EXPOSE FLOW and EXPOSE CONTRACT
      in the same file (E_VIS_004).
    - RESOURCES blocks only allowed in the entry file (E_MOD_001).
    - TEST blocks only allowed in the entry file (E_MOD_002).

    If `entry` is None, RESOURCES/TEST restrictions are not enforced
    (allows the function to be called outside the full compile flow).
    """
    for path, program in parsed.items():
        exposed_flow_names: set[str] = set()
        exposed_contract_names: set[str] = set()
        for decl in program.decls:
            if isinstance(decl, FlowDecl):
                if decl.exposed:
                    if not decl.takes or not decl.gives:
                        raise CompileError(
                            f"{path}:{decl.line}:{decl.col}: "
                            f"exposed FLOW {decl.name!r} must declare explicit "
                            f"TAKES and GIVES"
                        )
                    exposed_flow_names.add(decl.name)
            elif isinstance(decl, ContractDecl):
                if decl.exposed:
                    exposed_contract_names.add(decl.name)
            elif isinstance(decl, ResourcesDecl):
                if entry is not None and path != entry:
                    raise CompileError(
                        f"{path}:{decl.line}:{decl.col}: "
                        f"only the entry file may declare RESOURCES "
                        f"(found in {path.name})"
                    )
            elif isinstance(decl, TestDecl):
                if entry is not None and path != entry:
                    raise CompileError(
                        f"{path}:{decl.line}: "
                        f"only the entry file may declare TEST blocks "
                        f"(found in {path.name})"
                    )
        overlap = exposed_flow_names & exposed_contract_names
        if overlap:
            name = next(iter(overlap))
            raise CompileError(
                f"{path}: {name!r} is exposed as both FLOW and CONTRACT"
            )


def compute_exposed_sets(
    parsed: dict[Path, Program],
) -> dict[Path, dict[str, object]]:
    """Phase 3: per-file set of transitively exposed symbols.

    Returns a dict {file_path: {symbol_name: decl_or_reexport_target}}.
    Resolution is topological over imports so re-exports are
    resolved by the time their declaring file is visited.
    """
    # Build the import graph for topo sort (edges point from dependency → dependent)
    in_degree: dict[Path, int] = {p: 0 for p in parsed}
    edges: dict[Path, list[Path]] = {p: [] for p in parsed}
    for path, program in parsed.items():
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            if child in parsed:
                edges[child].append(path)
                in_degree[path] += 1

    queue: list[Path] = [p for p, d in in_degree.items() if d == 0]
    topo: list[Path] = []
    while queue:
        current = queue.pop(0)
        topo.append(current)
        for nxt in edges[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    result: dict[Path, dict[str, object]] = {}

    for path in topo:
        program = parsed[path]
        exposed: dict[str, object] = {}

        for decl in program.decls:
            if isinstance(decl, FlowDecl) and decl.exposed:
                exposed[decl.name] = decl
            elif isinstance(decl, ContractDecl) and decl.exposed:
                exposed[decl.name] = decl

        import_local_names: dict[str, tuple[Path, str]] = {}
        for imp in program.imports:
            child = (path.parent / imp.path).resolve()
            for item in imp.items:
                local_name = item.alias or item.name
                import_local_names[local_name] = (child, item.name)

        for decl in program.decls:
            if isinstance(decl, ReexportDecl):
                if decl.name not in import_local_names:
                    raise CompileError(
                        f"{path}:{decl.line}:{decl.col}: "
                        f"{decl.name!r} is not imported (cannot EXPOSE without IMPORT)"
                    )
                source_path, source_name = import_local_names[decl.name]
                source_exposed = result.get(source_path, {})
                if source_name not in source_exposed:
                    raise CompileError(
                        f"{path}:{decl.line}:{decl.col}: "
                        f"{source_name!r} is not exposed by {source_path.name!r}"
                    )
                exposed[decl.name] = source_exposed[source_name]

        result[path] = exposed
    return result
