from __future__ import annotations

from pathlib import Path

import pytest

from clio.ir.resolver import (
    CompileError,
    resolve_imports,
)

FIXTURES = Path(__file__).parent / "fixtures" / "imports"


def test_discovery_simple():
    """main.clio + lib.clio both parsed and indexed by resolved Path."""
    parsed = resolve_imports(FIXTURES / "simple" / "main.clio")
    assert len(parsed) == 2
    keys = {p.name for p in parsed.keys()}
    assert keys == {"main.clio", "lib.clio"}


def test_discovery_entry_alone():
    """A file with no imports yields a 1-entry dict."""
    entry = FIXTURES / "simple" / "lib.clio"
    parsed = resolve_imports(entry)
    assert len(parsed) == 1


def test_e_res_002_file_not_found(tmp_path: Path) -> None:
    entry = tmp_path / "main.clio"
    entry.write_text('FROM "./missing.clio" IMPORT X\n')
    with pytest.raises(CompileError, match=r"imported file not found"):
        resolve_imports(entry)


def test_e_res_001_cycle_two_files() -> None:
    entry = FIXTURES / "cycles" / "a.clio"
    with pytest.raises(CompileError, match=r"cyclic import"):
        resolve_imports(entry)


def test_e_res_001_cycle_self_import(tmp_path: Path) -> None:
    entry = tmp_path / "self.clio"
    entry.write_text('FROM "./self.clio" IMPORT X\n')
    with pytest.raises(CompileError, match=r"cyclic import"):
        resolve_imports(entry)


def test_discovery_idempotent_caching(tmp_path: Path) -> None:
    """If file b is imported from both a and entry, it's only parsed once."""
    (tmp_path / "shared.clio").write_text(
        "EXPOSE CONTRACT Doc\n"
        "  SHAPE: {text: str}\n"
    )
    (tmp_path / "left.clio").write_text(
        'FROM "./shared.clio" IMPORT Doc\n'
        "STEP noop_l\n"
        "  MODE: judgment\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "EXPOSE FLOW left_flow\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "  noop_l(doc=doc)\n"
    )
    (tmp_path / "right.clio").write_text(
        'FROM "./shared.clio" IMPORT Doc\n'
        "STEP noop_r\n"
        "  MODE: judgment\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "EXPOSE FLOW right_flow\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "  noop_r(doc=doc)\n"
    )
    (tmp_path / "main.clio").write_text(
        "RESOURCES\n"
        "  target: python\n"
        'FROM "./left.clio" IMPORT left_flow\n'
        'FROM "./right.clio" IMPORT right_flow\n'
        'FROM "./shared.clio" IMPORT Doc\n'
        "STEP noop_m\n"
        "  MODE: judgment\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "EXPOSE FLOW main_flow\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "  noop_m(doc=doc)\n"
    )
    parsed = resolve_imports(tmp_path / "main.clio")
    assert len(parsed) == 4
