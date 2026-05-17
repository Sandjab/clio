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
