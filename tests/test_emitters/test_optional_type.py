"""Cross-target smoke tests for `Optional<T>` (v0.21).

A CONTRACT carrying an `Optional<int>` field is the cleanest cross-target
probe. Tests assert on the nullable rendering in each emitter's output:
  - python / mcp-server / langgraph (Pydantic) → `int | None` in contracts.py
  - claude-cli (split JSON Schemas)            → `anyOf` ... null in contracts/<name>.schema.json
  - claude-skill (per-step output schemas)     → `anyOf` ... null in schemas/<step>.output.json
  - go (Go struct)                              → `*int64` in contracts.go

Mirrors `test_dict_type.py`. Per-target sources differ only in `LANG`."""
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
        "CONTRACT user_profile\n"
        "  SHAPE: {id: int, nickname: Optional<str>}\n"
        "\n"
        "STEP load\n"
        "  TAKES: raw: str\n"
        "  GIVES: out: user_profile\n"
        "  MODE: exact\n"
        f"  LANG: {lang}\n"
        "\n"
        f"{flow_kw} main\n"
        '  load(raw="anon")\n'
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


def test_python_target_renders_optional_as_union_none(tmp_path):
    PythonEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contracts_py = _find(_tree(tmp_path), "contracts.py")
    assert "str | None" in contracts_py, contracts_py


def test_mcp_target_renders_optional_in_contracts(tmp_path):
    MCPServerEmitter().emit(build_ir(parse(_source("python", exposed=True))), tmp_path)
    contracts_py = _find(_tree(tmp_path), "contracts.py")
    assert "str | None" in contracts_py


def test_langgraph_target_renders_optional_as_union_none(tmp_path):
    LangGraphEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contracts_py = _find(_tree(tmp_path), "contracts.py")
    assert "str | None" in contracts_py


def test_claude_skill_target_renders_optional_in_step_output_schema(tmp_path):
    # judgment step exercises the per-step output-schema rendering path
    judgment_src = (
        "CONTRACT user_profile\n"
        "  SHAPE: {id: int, nickname: Optional<str>}\n"
        "\n"
        "STEP load\n"
        "  TAKES: raw: str\n"
        "  GIVES: out: user_profile\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "    model: haiku\n"
        "\n"
        "FLOW main\n"
        '  load(raw="anon")\n'
    )
    ClaudeSkillEmitter().emit(build_ir(parse(judgment_src)), tmp_path)
    output_schema = _find(_tree(tmp_path), "load", ".output.json")
    assert '"anyOf"' in output_schema
    assert '"null"' in output_schema


def test_claude_cli_target_renders_optional_in_contract_schema(tmp_path):
    ClaudeCLIEmitter().emit(build_ir(parse(_source("python"))), tmp_path)
    contract_schema = _find(_tree(tmp_path), "user_profile", ".schema.json")
    assert '"anyOf"' in contract_schema
    assert '"null"' in contract_schema


def test_go_target_renders_optional_as_pointer(tmp_path):
    src = (
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
        "\n"
        + _source("go")
    )
    GoEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_go = _find(_tree(tmp_path), "contracts.go")
    assert "*string" in contracts_go, contracts_go


def test_go_target_optional_slice_is_bare_slice(tmp_path):
    """Optional<List<int>> → `[]int64` (NOT `*[]int64`).

    Slices and maps are already nilable in Go; wrapping in a pointer is
    unidiomatic. Mirrors PR-B Gemini #3317227648 (idiomatic Go)."""
    src = (
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
        "\n"
        "CONTRACT data\n"
        "  SHAPE: {ids: Optional<List<int>>, counts: Optional<Dict<str, int>>}\n"
        "\n"
        "STEP load\n"
        "  TAKES: raw: str\n"
        "  GIVES: out: data\n"
        "  MODE: exact\n"
        "  LANG: go\n"
        "\n"
        "FLOW main\n"
        '  load(raw="x")\n'
    )
    GoEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_go = _find(_tree(tmp_path), "contracts.go")
    assert "*[]" not in contracts_go, "Optional<List<T>> must NOT wrap *[]T"
    assert "*map[" not in contracts_go, "Optional<Dict<K, V>> must NOT wrap *map[K]V"
    assert "[]int64" in contracts_go
    assert "map[string]int64" in contracts_go
