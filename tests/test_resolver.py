from __future__ import annotations

from pathlib import Path

import pytest

from clio.ir.resolver import (
    CompileError,
    compute_exposed_sets,
    resolve_imports,
    validate_per_file,
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


# ---------------------------------------------------------------------------
# validate_per_file — Task 5
# ---------------------------------------------------------------------------


def test_e_vis_003_exposed_flow_without_signature(tmp_path: Path) -> None:
    """EXPOSE FLOW must declare TAKES and GIVES (E_VIS_003)."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        "STEP step1\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "EXPOSE FLOW broken\n"
        "  step1(text=text)\n"
    )
    parsed = resolve_imports(entry)
    with pytest.raises(CompileError, match=r"exposed FLOW 'broken' must declare explicit TAKES and GIVES"):
        validate_per_file(parsed)


def test_e_vis_004_same_name_flow_and_contract(tmp_path: Path) -> None:
    """A name cannot be both EXPOSE FLOW and EXPOSE CONTRACT in the same file."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        "EXPOSE CONTRACT X\n"
        "  SHAPE: {val: str}\n"
        "STEP step1\n"
        "  MODE: judgment\n"
        "  TAKES: x: X\n"
        "  GIVES: out: str\n"
        "EXPOSE FLOW X\n"
        "  TAKES: x: X\n"
        "  GIVES: out: str\n"
        "  step1(x=x)\n"
    )
    parsed = resolve_imports(entry)
    with pytest.raises(CompileError, match=r"'X' is exposed as both FLOW and CONTRACT"):
        validate_per_file(parsed)


def test_e_mod_001_resources_in_imported_file(tmp_path: Path) -> None:
    """Only the entry file may declare RESOURCES (E_MOD_001)."""
    lib = tmp_path / "lib.clio"
    lib.write_text(
        "RESOURCES\n"
        "  target: python\n"
        "EXPOSE CONTRACT Doc\n"
        "  SHAPE: {text: str}\n"
    )
    main = tmp_path / "main.clio"
    main.write_text(
        'FROM "./lib.clio" IMPORT Doc\n'
        "STEP noop\n"
        "  MODE: judgment\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "EXPOSE FLOW run\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "  noop(doc=doc)\n"
    )
    parsed = resolve_imports(main)
    with pytest.raises(CompileError, match=r"only the entry file may declare"):
        validate_per_file(parsed, entry=main.resolve())


def test_valid_file_passes(tmp_path: Path) -> None:
    """A well-formed file with EXPOSE FLOW + TAKES/GIVES is silently accepted."""
    entry = tmp_path / "main.clio"
    entry.write_text(
        "STEP step1\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "EXPOSE FLOW run\n"
        "  TAKES: text: str\n"
        "  GIVES: out: str\n"
        "  step1(text=text)\n"
    )
    parsed = resolve_imports(entry)
    validate_per_file(parsed)  # must not raise


# ---------------------------------------------------------------------------
# compute_exposed_sets — Task 6
# ---------------------------------------------------------------------------


def test_exposed_set_local_only(tmp_path: Path) -> None:
    """A file with locally-defined EXPOSE FLOW and EXPOSE CONTRACT exposes both names."""
    (tmp_path / "main.clio").write_text(
        "EXPOSE CONTRACT Doc\n"
        "  SHAPE: {text: str}\n"
        "STEP step1\n"
        "  MODE: judgment\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "EXPOSE FLOW run\n"
        "  TAKES: doc: Doc\n"
        "  GIVES: out: str\n"
        "  step1(doc=doc)\n"
    )
    entry = tmp_path / "main.clio"
    parsed = resolve_imports(entry)
    sets = compute_exposed_sets(parsed)
    assert set(sets[entry.resolve()].keys()) == {"Doc", "run"}


def test_exposed_set_reexport() -> None:
    """facade.clio re-exports Article and classify from lib.clio."""
    entry = FIXTURES / "reexport" / "main.clio"
    parsed = resolve_imports(entry)
    sets = compute_exposed_sets(parsed)
    facade_path = (entry.parent / "facade.clio").resolve()
    assert set(sets[facade_path].keys()) == {"Article", "classify"}


def test_reexport_of_nonimported_name(tmp_path: Path) -> None:
    """EXPOSE <name> for a name that wasn't imported is rejected."""
    entry = tmp_path / "main.clio"
    entry.write_text("EXPOSE NotImported\n")
    parsed = resolve_imports(entry)
    with pytest.raises(CompileError, match=r"'NotImported' is not imported"):
        compute_exposed_sets(parsed)
