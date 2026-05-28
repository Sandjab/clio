"""Cross-target smoke tests for `Dict<K, V>` (v0.21).

A CONTRACT carrying a Dict field is the cleanest cross-target probe: every
emitter renders the contract in its target's native form. Tests assert on
the Dict rendering in the emitted output:
  - python / mcp-server / langgraph (Pydantic) → `dict[str, int]` in contracts.py
  - claude-cli (split JSON Schemas)            → `additionalProperties` in contracts/<name>.schema.json
  - claude-skill (per-step output schemas)     → `additionalProperties` in schemas/<step>.output.json
  - go (Go struct)                              → `map[string]int64` in contracts.go

No golden snapshots — keeps maintenance low while exercising every
TypeExpr-walker site touched in PR-A. Per-target sources differ only in the
exact step's LANG (Go requires `LANG: go`; everywhere else `LANG: python`
keeps the emitter happy without dragging in invoke specifics)."""
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
        "CONTRACT metrics\n"
        "  SHAPE: {counts: Dict<str, int>}\n"
        "\n"
        "STEP score\n"
        "  TAKES: text: str\n"
        "  GIVES: out: metrics\n"
        "  MODE: exact\n"
        f"  LANG: {lang}\n"
        "\n"
        f"{flow_kw} main\n"
        '  score(text="hello world")\n'
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


def test_python_target_renders_dict_as_pydantic_dict(tmp_path):
    PythonEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contracts_py = _find(_tree(tmp_path), "contracts.py")
    assert "dict[str, int]" in contracts_py, contracts_py


def test_mcp_target_renders_dict_in_output_schema(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_source("python", exposed=True))), tmp_path)
    files = _tree(tmp_path)
    contracts_py = _find(files, "contracts.py")
    assert "dict[str, int]" in contracts_py
    # Tool outputSchema for FLOW.GIVES inlines `metrics` (contains Dict).
    server_py = _find(files, "server.py")
    assert "additionalProperties" in server_py
    assert "integer" in server_py


def test_langgraph_target_renders_dict_as_pydantic_dict(tmp_path):
    LangGraphEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contracts_py = _find(_tree(tmp_path), "contracts.py")
    assert "dict[str, int]" in contracts_py


def test_claude_skill_target_renders_dict_in_step_output_schema(tmp_path):
    # claude-skill writes a step `.output.json` only for judgment steps
    # (exact steps embed validation in the Python script). Use judgment+cli
    # to exercise the output-schema rendering path.
    judgment_src = (
        "CONTRACT metrics\n"
        "  SHAPE: {counts: Dict<str, int>}\n"
        "\n"
        "STEP score\n"
        "  TAKES: text: str\n"
        "  GIVES: out: metrics\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "    model: haiku\n"
        "\n"
        "FLOW main\n"
        '  score(text="hello world")\n'
    )
    ClaudeSkillEmitter().emit(build_ir(parse(judgment_src)), tmp_path)
    output_schema = _find(_tree(tmp_path), "score", ".output.json")
    assert '"additionalProperties"' in output_schema
    assert '"integer"' in output_schema


def test_claude_cli_target_renders_dict_in_contract_schema(tmp_path):
    ClaudeCLIEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contract_schema = _find(_tree(tmp_path), "metrics", ".schema.json")
    assert '"additionalProperties"' in contract_schema
    assert '"integer"' in contract_schema


def test_go_target_renders_dict_as_map(tmp_path):
    src = (
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
        "\n"
        + _source("go")
    )
    GoEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_go = _find(_tree(tmp_path), "contracts.go")
    assert "map[string]int64" in contracts_go, contracts_go
