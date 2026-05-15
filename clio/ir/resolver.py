"""Multi-file resolver for CLIO v0.18.

Phase 1 (discovery): recursive parse of all .clio files reachable from
the entry, with cycle detection. Returns dict[Path, Program] keyed by
the resolved absolute path of each file.

Subsequent phases (validation, exposed sets, import validation) are
added in later tasks.
"""
from __future__ import annotations

from pathlib import Path

from clio.parser.ast_nodes import Program
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
