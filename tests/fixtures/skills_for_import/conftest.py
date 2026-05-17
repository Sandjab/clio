"""Shared fixtures for skill-importer tests.

Emitted skill fixtures are produced on-demand (not committed) so the
embedded `emitted_at` timestamp doesn't churn the repo. The hand-written
fixtures are committed because they don't include a sidecar."""
from pathlib import Path

import pytest

_HERE = Path(__file__).parent


@pytest.fixture
def clio_emitted_simple(tmp_path):
    """Emit the simple source into tmp_path/skill. Returns the path."""
    from clio.cli import _cmd_compile

    src = _HERE / "source_for_emitted_simple.clio"
    out = tmp_path / "clio_emitted_simple"
    rc = _cmd_compile(str(src), "claude-skill", str(out), None)
    assert rc == 0
    return out


@pytest.fixture
def clio_emitted_drifted(clio_emitted_simple):
    """Same as clio_emitted_simple but with SKILL.md altered post-emission."""
    skill_md = clio_emitted_simple / "SKILL.md"
    skill_md.write_text(skill_md.read_text() + "\n<!-- tampered -->\n")
    return clio_emitted_simple
