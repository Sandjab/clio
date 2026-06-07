from pathlib import Path

from clio.cli import _cmd_compile

FIXTURES = Path(__file__).parent.parent / "fixtures"
EXPECTED_SWIFT = FIXTURES / "expected_swift"


def _compile(source_path: Path, output_dir: Path) -> int:
    return _cmd_compile(str(source_path), "swift", str(output_dir), None)


def test_swift_target_is_a_valid_choice(tmp_path: Path) -> None:
    src = tmp_path / "f.clio"
    src.write_text(
        "STEP load\n"
        "  TAKES: path: str\n"
        "  GIVES: data: str\n"
        "  MODE:  exact\n\n"
        'FLOW pipeline\n'
        '  load(path="input.txt")\n'
    )
    rc = _compile(src, tmp_path / "out")
    assert rc == 0
    assert (tmp_path / "out" / "Package.swift").exists()


def test_swift_refuses_source_without_flow(tmp_path: Path) -> None:
    src = tmp_path / "noflow.clio"
    src.write_text("STEP only\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n")
    rc = _compile(src, tmp_path / "out")
    assert rc != 0   # E_SWIFT_004
