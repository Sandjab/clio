"""Tests for the claude-skill emitter.

Granular tests (existence + parsed content) for tasks 1-12.
Golden snapshots (full tree equality) for task 14 only.
To regenerate goldens after intentional changes:

    python -m clio compile tests/fixtures/<name>.clio \
        --target claude-skill --output tests/fixtures/expected_skill/<name>
"""

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
