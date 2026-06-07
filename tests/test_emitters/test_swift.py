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


def test_swift_minimal_emits_steps_and_flow(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_minimal.clio", out)
    assert (out / "Sources/ClioFlow/Steps/Step01_load.swift").exists()
    flow = (out / "Sources/ClioFlow/Flow.swift").read_text()
    assert "func run(kwargs: [String: Any]) async throws" in flow
    assert "try await step_summarize(" in flow


def test_swift_emits_contract_struct_and_validate(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_contract.clio", out)
    contracts_path = out / "Sources/ClioFlow/Contracts.swift"
    assert contracts_path.exists(), "Contracts.swift was not emitted"
    contracts = contracts_path.read_text()
    assert "struct CustomerRisk: Codable" in contracts
    assert "static let jsonSchema" in contracts
    assert "func validate() throws" in contracts
    assert (out / "Sources/ClioFlow/Runtime/Validate.swift").exists()
