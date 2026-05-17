"""Tests for the go emitter.

Granular tests (existence + parsed content) for tasks 1-19.
Golden snapshots (full-tree equality) for tasks 9, 17, 21.

To regenerate goldens after intentional changes:

    python -m clio compile tests/fixtures/<name>.clio \\
        --target go --output tests/fixtures/expected_go/<name>
"""
from __future__ import annotations

from pathlib import Path

from clio.cli import _cmd_compile


def _compile(source_path: Path, output_dir: Path) -> None:
    """Run `clio compile <source> --target go --output <out>` in-process."""
    _cmd_compile(str(source_path), "go", str(output_dir), None)


def test_target_go_is_registered_in_cli(tmp_path: Path) -> None:
    src = tmp_path / "trivial.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "go.mod").exists(), "go emitter must write go.mod"


def test_go_mod_uses_safe_package_name(tmp_path: Path) -> None:
    """Module name is lowercased and normalised for Go (no uppercase, no
    special chars).  CamelCase flow name 'CustomerRetention' becomes
    'customerretention'."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW CustomerRetention\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert content.startswith("module customerretention\n"), content
    assert "go 1.22\n" in content


def test_go_mod_omits_sdk_when_no_judgment(tmp_path: Path) -> None:
    """A flow with no judgment step does not require anthropic-sdk-go."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert "anthropic-sdk-go" not in content
    # jsonschema is always required (Validate methods)
    assert "santhosh-tekuri/jsonschema/v6" in content


def test_go_mod_pins_sdk_when_judgment_present(tmp_path: Path) -> None:
    """A flow with a judgment step pulls in anthropic-sdk-go."""
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    content = (out / "go.mod").read_text()
    assert "github.com/anthropics/anthropic-sdk-go" in content


def test_cmd_main_go_exists(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "cmd" / "pipeline" / "main.go").exists()


def test_cmd_main_go_parses_kwargs_json(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "cmd" / "pipeline" / "main.go").read_text()
    assert "package main" in body
    assert 'flag.String("kwargs"' in body
    assert "json.Unmarshal" in body
    assert "flow.Run(ctx, kwargs)" in body


_CONTRACT_SRC = (
    "CONTRACT customer_risk\n"
    "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str(max=300)}\n"
    "STEP detect\n"
    "  TAKES: x: str\n"
    "  GIVES: risk: customer_risk\n"
    "  MODE:  judgment\n"
    "FLOW pipeline\n"
    "  detect(x=\"hi\")\n"
    "RESOURCES\n"
    "  target: go\n"
    "  models: [haiku]\n"
)


def test_contracts_file_written_when_contracts_present(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(_CONTRACT_SRC)
    out = tmp_path / "out"
    _compile(src, out)
    assert (out / "contracts" / "contracts.go").exists()


def test_contracts_struct_uses_json_tags(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(_CONTRACT_SRC)
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    assert "package contracts" in body
    assert "type CustomerRisk struct {" in body
    assert 'Client string `json:"client"`' in body
    assert 'Risk string `json:"risk"`' in body
    assert 'Reason string `json:"reason"`' in body


def test_contracts_json_schema_embedded_as_const(tmp_path: Path) -> None:
    """Each contract carries its JSON Schema as a `const` string so
    Validate() can call jsonschema/v6 without filesystem reads."""
    src = tmp_path / "src.clio"
    src.write_text(_CONTRACT_SRC)
    out = tmp_path / "out"
    _compile(src, out)
    body = (out / "contracts" / "contracts.go").read_text()
    assert "const customerRiskSchema = `" in body
    assert '"client"' in body  # field in schema
    assert "low" in body  # enum value


def test_contracts_file_omitted_when_no_contract_used(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP noop\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW pipeline\n"
        "  noop(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)
    assert not (out / "contracts" / "contracts.go").exists()
