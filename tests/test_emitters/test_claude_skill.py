"""Tests for the claude-skill emitter.

Granular tests (existence + parsed content) for tasks 1-12.
Golden snapshots (full tree equality) for task 14 only.
To regenerate goldens after intentional changes:

    python -m clio compile tests/fixtures/<name>.clio \
        --target claude-skill --output tests/fixtures/expected_skill/<name>
"""

import json
from pathlib import Path

from clio.cli import _cmd_compile
from clio.emitters.claude_skill import ClaudeSkillEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _parse_frontmatter(body: str) -> dict[str, str]:
    """Parse a simple YAML-style frontmatter block (key: value lines only)."""
    assert body.startswith("---\n"), "SKILL.md must start with ---"
    end = body.index("\n---\n", 4)
    block = body[4:end]
    result: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def test_smoke_emit_phase1_creates_skill_md(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    skill_md = tmp_path / "SKILL.md"
    assert skill_md.exists()
    body = skill_md.read_text()
    assert body.startswith("---\n")
    front = _parse_frontmatter(body)
    assert front["name"]
    assert front["description"]


def test_cli_compile_claude_skill_produces_skill_md(tmp_path):
    """Exercise the _cmd_compile dispatch path for claude-skill.

    Guards against regressions in the elif branch in clio/cli.py. (The argparse
    choices guard is exercised separately at CLI-entry time and is not in scope
    for this test.)
    """
    source = str(FIXTURES / "mvp_phase1.clio")
    rc = _cmd_compile(source, "claude-skill", str(tmp_path))
    assert rc == 0, f"_cmd_compile returned {rc}"
    skill_md = tmp_path / "SKILL.md"
    assert skill_md.exists(), "SKILL.md not produced by CLI path"


def test_frontmatter_uses_flow_description_when_present(tmp_path):
    """Option B taken in Task 2: FlowIR has no description field as of v0.14.

    The parser grammar does not capture a FLOW description string, so
    FlowIR.description does not exist.  This test is skipped until
    TODO(post-v0.14) is resolved and Option A is implemented.
    """
    import pytest

    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    if not getattr(getattr(graph, "flow", None), "description", None):
        pytest.skip("FlowIR.description not yet wired (Option B taken in Task 2)")
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    front = _parse_frontmatter(body)
    assert front["description"] == graph.flow.description.strip()


def test_frontmatter_warns_when_no_description(tmp_path, capsys):
    """A warning is emitted to stderr when the flow has no description."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    captured = capsys.readouterr()
    assert "claude-skill warning" in captured.err
    assert "no description" in captured.err.lower()
    body = (tmp_path / "SKILL.md").read_text()
    front = _parse_frontmatter(body)
    assert front["description"].startswith("Execute flow ")


def test_frontmatter_allowed_tools_baseline(tmp_path):
    """allowed-tools is the static v1 baseline list."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    front = _parse_frontmatter(body)
    tools = [t.strip() for t in front["allowed-tools"].split(",")]
    assert "Bash" in tools
    assert "Read" in tools
    assert "Write" in tools
    assert "TodoWrite" in tools


def test_emits_process_flow_dot(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    dot = (tmp_path / "process_flow.dot").read_text()
    assert dot.startswith("digraph "), "DOT output must start with 'digraph '"
    # Last non-empty line should contain the closing brace
    non_empty = [ln for ln in dot.splitlines() if ln.strip()]
    assert non_empty[-1].strip().endswith("}")


def test_emits_state_example_json_valid(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    state = json.loads((tmp_path / "state.example.json").read_text())
    assert isinstance(state, dict)
    # Each top-level step name should be a key with an empty dict value
    for step in graph.steps:
        assert step.name in state, f"Missing step {step.name!r} in state.example.json"
        assert state[step.name] == {}, f"Expected empty dict for step {step.name!r}"


def test_emits_readme(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    readme = (tmp_path / "README.md").read_text()
    # Should mention "claude-skill" and be non-trivial
    assert "claude-skill" in readme.lower()
    assert len(readme.strip()) >= 100, "README should be a few sentences"


def test_exact_step_emits_script(tmp_path):
    """A FLOW with one exact STEP must produce scripts/01_<name>.py."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    # Exclude bundled runtime helpers (underscore-prefixed).
    scripts = sorted(
        p for p in (tmp_path / "scripts").glob("*.py") if not p.name.startswith("_")
    )
    assert len(scripts) == 1
    assert scripts[0].name.startswith("01_")


def test_exact_step_script_is_autonomous(tmp_path):
    """Emitted script reads stdin JSON, writes stdout JSON."""
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    script = next((tmp_path / "scripts").glob("*.py")).read_text()
    assert "import sys" in script
    assert "import json" in script
    assert "json.load(sys.stdin)" in script
    assert "json.dump(" in script


def test_skill_md_references_exact_step_script(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    # Some kind of section header for step 01 (Step or Étape)
    assert "## Step 01" in body or "## Étape 01" in body
    # The script reference must appear
    assert "scripts/01_" in body
    assert "python scripts/01_" in body


def test_validate_helper_is_bundled(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    validate_py = tmp_path / "scripts" / "_validate.py"
    assert validate_py.exists()
    body = validate_py.read_text()
    assert "import json" in body
    assert "import sys" in body
    # Must not blow up if jsonschema is missing — stdlib fallback
    assert "try:" in body and "jsonschema" in body


def test_cache_key_helper_is_bundled(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    key_py = tmp_path / "scripts" / "_cache_key.py"
    assert key_py.exists()
    body = key_py.read_text()
    assert "hashlib" in body
    assert "sha256" in body


def test_validate_helper_runs_against_real_schema(tmp_path):
    """Layer 2 check: the bundled validator must actually work."""
    import subprocess

    # Build a trivially-valid instance against a simple schema.
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    # Write a temp schema and a matching instance into tmp_path.
    schema_path = tmp_path / "test_schema.json"
    schema_path.write_text(json.dumps({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}))
    instance_path = tmp_path / "instance.json"
    instance_path.write_text(json.dumps({"name": "hello"}))
    result = subprocess.run(
        [".venv/bin/python", str(tmp_path / "scripts" / "_validate.py"),
         str(instance_path), str(schema_path)],
        capture_output=True, text=True, timeout=10,
        cwd="/Users/jean-paulgavini/Documents/Dev/clio",
    )
    assert result.returncode == 0, f"validator failed: {result.stderr}"


def test_cache_key_helper_produces_sha256_hex(tmp_path):
    """Layer 2 check: the bundled cache-key generator must actually work."""
    import subprocess

    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"customer": {"id": "c1"}, "order": {"items": [1, 2, 3]}}))
    result = subprocess.run(
        [".venv/bin/python", str(tmp_path / "scripts" / "_cache_key.py"),
         str(state_path), "fetch_customer", '["customer.id", "order.items"]'],
        capture_output=True, text=True, timeout=10,
        cwd="/Users/jean-paulgavini/Documents/Dev/clio",
    )
    assert result.returncode == 0, f"cache-key failed: {result.stderr}"
    key = result.stdout.strip()
    # SHA256 hex digest is 64 hex chars
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_judgment_step_emits_prompt_template(tmp_path):
    # mvp_phase4.clio has one judgment STEP (detect_churn)
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    prompts = sorted((tmp_path / "prompts").glob("*.md"))
    assert len(prompts) >= 1
    body = prompts[0].read_text()
    # Template must be non-trivial
    assert len(body.strip()) > 0


def test_judgment_step_emits_output_schema(tmp_path):
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    out_schemas = sorted((tmp_path / "schemas").glob("*.output.json"))
    assert len(out_schemas) >= 1
    schema = json.loads(out_schemas[0].read_text())
    # JSON Schema must declare type=object with properties
    assert schema.get("type") == "object"
    assert "properties" in schema


def test_skill_md_judgment_section_has_prompt_and_schema_refs(tmp_path):
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    body = (tmp_path / "SKILL.md").read_text()
    assert "MODE: judgment" in body
    assert "prompts/" in body
    assert "schemas/" in body
    assert "_validate.py" in body


def _assert_no_external_refs(node, fname):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("../"):
            raise AssertionError(f"{fname}: external $ref still present: {ref}")
        for v in node.values():
            _assert_no_external_refs(v, fname)
    elif isinstance(node, list):
        for item in node:
            _assert_no_external_refs(item, fname)


def test_output_schema_has_no_external_refs(tmp_path):
    """Every $ref in emitted schemas must be self-contained (no file paths)."""
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    for schema_file in (tmp_path / "schemas").glob("*.output.json"):
        schema = json.loads(schema_file.read_text())
        _assert_no_external_refs(schema, schema_file.name)


def test_step_with_takes_emits_input_schema(tmp_path):
    """When a STEP has TAKES <contract>, schemas/NN_<name>.input.json is emitted."""
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    input_schemas = sorted((tmp_path / "schemas").glob("*.input.json"))
    assert len(input_schemas) >= 1
    schema = json.loads(input_schemas[0].read_text())
    assert schema.get("type") == "object" or "$ref" in str(schema) or "properties" in schema


def test_input_schema_has_no_external_refs(tmp_path):
    """No external $ref must survive inlining in emitted input schemas."""
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeSkillEmitter().emit(graph, tmp_path)
    for schema_file in (tmp_path / "schemas").glob("*.input.json"):
        schema = json.loads(schema_file.read_text())
        _assert_no_external_refs(schema, schema_file.name)
