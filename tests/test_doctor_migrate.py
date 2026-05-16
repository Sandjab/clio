"""Tests for `clio doctor --migrate-v018` (Task 15, v0.18).

The migration tool scans a v0.17 .clio file and proposes EXPOSE
prefixes for FLOWs that would have been auto-exposed under the old
mcp-server heuristic (signed FLOW not called by a sibling), plus any
CONTRACTs referenced in those FLOWs' signatures.
"""
from __future__ import annotations

from pathlib import Path

from clio.diagnostics import migrate_v018

FIXTURES = Path(__file__).parent / "fixtures" / "imports" / "migration_v017_to_v018"


def test_migrate_v018_proposes_exposes() -> None:
    """Before fixture: classify_article (signed, not called by sibling)
    and Article (referenced in its signature) each get EXPOSE proposed."""
    _new_text, changes = migrate_v018(FIXTURES / "before.clio")
    # Exactly 2 changes: CONTRACT Article + FLOW classify_article
    assert len(changes) == 2
    # Both prefixes are "EXPOSE "
    assert all(prefix == "EXPOSE " for _, prefix in changes)


def test_migrate_v018_internal_flow_not_exposed() -> None:
    """internal_helper is called by classify_article → must NOT appear in changes."""
    _new_text, changes = migrate_v018(FIXTURES / "before.clio")
    changed_lines = {ln for ln, _ in changes}
    # internal_helper is on line 14 in before.clio
    assert 14 not in changed_lines


def test_migrate_v018_output_matches_expected() -> None:
    """Full text output must equal expected_after.clio exactly."""
    new_text, _ = migrate_v018(FIXTURES / "before.clio")
    expected = (FIXTURES / "expected_after.clio").read_text()
    assert new_text == expected


def test_migrate_v018_idempotent_on_already_exposed(tmp_path: Path) -> None:
    """Running migration on an already-migrated file produces zero changes."""
    already = tmp_path / "already.clio"
    already.write_text((FIXTURES / "expected_after.clio").read_text())
    original_text = already.read_text()
    new_text, changes = migrate_v018(already)
    assert changes == []
    assert new_text == original_text


def test_migrate_v018_unsigned_flow_not_exposed(tmp_path: Path) -> None:
    """A FLOW without TAKES+GIVES (unsigned) is never auto-exposed."""
    src = tmp_path / "unsigned.clio"
    src.write_text(
        "RESOURCES\n"
        "  target: mcp-server\n"
        "  models: [sonnet]\n"
        "\n"
        "STEP detect\n"
        "  MODE: judgment\n"
        "  TAKES: text: str\n"
        "  GIVES: label: str\n"
        "\n"
        "FLOW pipeline\n"
        "  detect(text=text)\n"
    )
    _new_text, changes = migrate_v018(src)
    assert changes == []


def test_migrate_v018_write_flag(tmp_path: Path) -> None:
    """Verifies the write path: after migrate_v018 the file text is updated."""
    import shutil
    target = tmp_path / "test.clio"
    shutil.copy(FIXTURES / "before.clio", target)
    new_text, changes = migrate_v018(target)
    assert len(changes) > 0
    # Simulate --write
    target.write_text(new_text)
    # Re-running should be idempotent
    _new_text2, changes2 = migrate_v018(target)
    assert changes2 == []
