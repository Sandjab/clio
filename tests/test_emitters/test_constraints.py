"""Cross-target smoke tests for extended constraints (v0.21).

A CONTRACT with str(min, max), int(min, max), float(min, max), and
float(precision) probes each emitter. Tests assert on the rendering of
constraint metadata in the emitted contract / schema:
  - Pydantic targets: `Field(...)` kwargs (`min_length`, `ge`, `le`,
    `multiple_of`)
  - JSON Schema targets (claude-cli, claude-skill): `minLength`,
    `minimum`, `maximum`, `multipleOf`
  - Go target: jsonschema/v6 handles all constraints in the embedded
    schema; the Go field type is unchanged (constraints enforced at
    runtime, not at the type level)

Each constraint family gets its own test for traceable failure messages."""
from pathlib import Path

from clio.emitters.claude_cli import ClaudeCLIEmitter
from clio.emitters.claude_skill import ClaudeSkillEmitter
from clio.emitters.go import GoEmitter
from clio.emitters.langgraph import LangGraphEmitter
from clio.emitters.mcp_server import MCPServerEmitter
from clio.emitters.python import PythonEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse


def _source(lang: str, exposed: bool = False) -> str:
    flow_kw = "EXPOSE FLOW" if exposed else "FLOW"
    return (
        "CONTRACT measurement\n"
        "  SHAPE: {label: str(min=1, max=80), "
        "count: int(min=0, max=1000), "
        "ratio: float(min=0.0, max=1.0), "
        "price: float(precision=2)}\n"
        "\n"
        "STEP measure\n"
        "  TAKES: raw: str\n"
        "  GIVES: out: measurement\n"
        "  MODE: exact\n"
        f"  LANG: {lang}\n"
        "\n"
        f"{flow_kw} main\n"
        '  measure(raw="x")\n'
    )


def _tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                out[str(p.relative_to(root))] = p.read_text()
            except UnicodeDecodeError:
                out[str(p.relative_to(root))] = "<binary>"
    return out


def _find(files: dict[str, str], *needles: str) -> str:
    for k, v in files.items():
        if all(n in k for n in needles):
            return v
    raise AssertionError(f"no file matched {needles!r} in {sorted(files)}")


def test_python_target_emits_pydantic_field_kwargs(tmp_path):
    PythonEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contracts_py = _find(_tree(tmp_path), "contracts.py")
    # str(min, max) → min_length / max_length
    assert "min_length=1" in contracts_py
    assert "max_length=80" in contracts_py
    # int(min, max) → ge / le (inclusive)
    assert "ge=0" in contracts_py
    assert "le=1000" in contracts_py
    # float(min, max) → ge / le on the same Field
    assert "ge=0.0" in contracts_py
    assert "le=1.0" in contracts_py
    # float(precision=2) → multiple_of=0.01
    assert "multiple_of=0.01" in contracts_py


def test_mcp_target_emits_pydantic_field_kwargs(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_source("python", exposed=True))), tmp_path)
    contracts_py = _find(_tree(tmp_path), "contracts.py")
    assert "min_length=1" in contracts_py
    assert "ge=0" in contracts_py
    assert "multiple_of=0.01" in contracts_py


def test_langgraph_target_emits_pydantic_field_kwargs(tmp_path):
    LangGraphEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contracts_py = _find(_tree(tmp_path), "contracts.py")
    assert "min_length=1" in contracts_py
    assert "ge=0" in contracts_py
    assert "multiple_of=0.01" in contracts_py


def test_claude_cli_target_emits_json_schema_constraints(tmp_path):
    ClaudeCLIEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contract_schema = _find(_tree(tmp_path), "measurement", ".schema.json")
    assert '"minLength": 1' in contract_schema
    assert '"maxLength": 80' in contract_schema
    assert '"minimum": 0' in contract_schema
    assert '"maximum": 1000' in contract_schema
    assert '"multipleOf": 0.01' in contract_schema


def test_claude_skill_target_emits_json_schema_constraints(tmp_path):
    judgment_src = _source("python").replace(
        "MODE: exact\n  LANG: python",
        "MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "    model: haiku",
    )
    ClaudeSkillEmitter().emit(build_ir(parse(judgment_src)), tmp_path)
    output_schema = _find(_tree(tmp_path), "measure", ".output.json")
    assert '"minLength": 1' in output_schema
    assert '"minimum": 0' in output_schema
    assert '"multipleOf": 0.01' in output_schema


def test_go_target_embeds_json_schema_constraints(tmp_path):
    src = (
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
        "\n"
        + _source("go")
    )
    GoEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_go = _find(_tree(tmp_path), "contracts.go")
    # Go field types are unchanged (constraints enforced at runtime via
    # jsonschema/v6). The embedded schema literal must carry every constraint.
    assert '"minLength": 1' in contracts_go
    assert '"maxLength": 80' in contracts_go
    assert '"minimum": 0' in contracts_go
    assert '"maximum": 1000' in contracts_go
    assert '"multipleOf": 0.01' in contracts_go
