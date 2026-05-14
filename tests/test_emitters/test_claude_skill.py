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
