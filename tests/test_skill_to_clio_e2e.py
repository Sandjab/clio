"""Opt-in end-to-end tests for clio import. Skipped unless ANTHROPIC_API_KEY is set."""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e_llm


@pytest.fixture(autouse=True)
def _require_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")


_HERE = Path(__file__).parent


def test_e2e_import_clio_emitted_skill_trivial_path(tmp_path):
    """Round-trip: emit a skill from a known source, then import → byte-identical."""
    from clio.cli import _cmd_compile, main

    src = _HERE / "fixtures" / "skills_for_import" / "source_for_emitted_simple.clio"
    skill = tmp_path / "skill"
    _cmd_compile(str(src), "claude-skill", str(skill), None)

    recovered = tmp_path / "recovered.clio"
    rc = main(["import", str(skill), "--output", str(recovered)])
    assert rc == 0
    assert recovered.read_text() == src.read_text()


def test_e2e_import_clio_emitted_with_drift_falls_back_to_llm(tmp_path):
    from clio.cli import _cmd_compile, main

    src = _HERE / "fixtures" / "skills_for_import" / "source_for_emitted_simple.clio"
    skill = tmp_path / "skill"
    _cmd_compile(str(src), "claude-skill", str(skill), None)
    # Tamper with SKILL.md to force drift
    (skill / "SKILL.md").write_text(
        (skill / "SKILL.md").read_text() + "\n<!-- tampered -->\n"
    )
    recovered = tmp_path / "recovered.clio"
    rc = main(["import", str(skill), "--output", str(recovered)])
    assert rc == 0
    # The LLM output is not expected to be byte-identical; it just must parse + build.
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    build_ir(parse(recovered.read_text()))


@pytest.mark.parametrize(
    "skill_subdir,expected_word",
    [
        ("handwritten_en_pipeline", "summary"),
        ("handwritten_fr_pipeline", "résumé"),
        ("handwritten_es_pipeline", "resumen"),
    ],
)
def test_e2e_import_handwritten_skill_preserves_language(tmp_path, skill_subdir, expected_word):
    from clio.cli import main
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    skill = _HERE / "fixtures" / "skills_for_import" / skill_subdir
    recovered = tmp_path / "recovered.clio"
    rc = main(["import", str(skill), "--output", str(recovered)])
    assert rc == 0
    text = recovered.read_text()
    build_ir(parse(text))  # must compile
    assert expected_word.lower() in text.lower(), \
        f"expected '{expected_word}' (preserved language) in output"
